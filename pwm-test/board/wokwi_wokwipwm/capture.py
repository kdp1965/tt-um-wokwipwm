'''
PIO logic-analyzer capture of the WokwiPWM outputs.

The TTDBv3 demoboard (RP2350B) wires the ASIC bidirectional pins
uio0..uio7 to RP2350 GPIO25..GPIO32 (GPIOMapTTDBv3).  The WokwiPWM
design drives its four PWM outputs on uio_out[7:4], i.e. GPIO29..32,
which are conveniently consecutive - perfect for a PIO 'in pins, 4'
sampler.

One wrinkle: GPIO32 (uio7 / PWM3) is outside the PIO's default 0..31
pin window on the RP2350B, so we shift the whole PIO block's window
to GPIO16..47 with PIO.gpio_base(16) before loading the program.

Samples are packed 8-per-word (4 bits each, first sample in the low
nibble; bit0=PWM0 .. bit3=PWM3) and moved to RAM by DMA, with a pure
Python polling fallback if rp2.DMA is unavailable.
'''

import array
import gc
import time

import rp2
from machine import Pin

# RP2350 PIO register bases and RX FIFO offset
_PIO_BASE = {0: 0x50200000, 1: 0x50300000, 2: 0x50400000}
_RXF0_OFFSET = 0x020

FIRST_PWM_GPIO = 29   # uio4 on TTDBv3 = PWM0


@rp2.asm_pio(in_shiftdir=rp2.PIO.SHIFT_RIGHT,
             autopush=True, push_thresh=32,
             fifo_join=rp2.PIO.JOIN_RX)
def _sampler():
    in_(pins, 4)          # one sample per PIO clock


class PwmCapture:
    '''
    4-channel sampler on GPIO first_gpio..first_gpio+3.

    sm_id 4 = PIO1 state machine 0 (PIO0 left alone in case the SDK
    or another app is using it).  Pass sm_id=0..11 to relocate.
    '''

    def __init__(self, sample_hz=10_000, first_gpio=FIRST_PWM_GPIO, sm_id=4):
        self.sample_hz = int(sample_hz)
        self.first_gpio = first_gpio
        self.sm_id = sm_id
        pio_num = sm_id // 4
        # GPIO32 (uio7) needs the 16..47 window on RP2350B
        try:
            rp2.PIO(pio_num).gpio_base(16)
        except Exception as e:
            print('warning: could not set PIO gpio_base(16):', e)
        self.sm = self._make_sm()
        self._rxf_addr = _PIO_BASE[pio_num] + _RXF0_OFFSET + 4 * (sm_id % 4)
        self._rx_dreq = pio_num * 8 + 4 + (sm_id % 4)

    def _make_sm(self):
        try:
            return rp2.StateMachine(self.sm_id, _sampler,
                                    freq=self.sample_hz,
                                    in_base=Pin(self.first_gpio))
        except Exception:
            # PIO instruction memory may be full from prior runs
            rp2.PIO(self.sm_id // 4).remove_program()
            return rp2.StateMachine(self.sm_id, _sampler,
                                    freq=self.sample_hz,
                                    in_base=Pin(self.first_gpio))

    def capture(self, seconds=2.0, before=None):
        '''
        Capture for `seconds`.  If `before` is given it is called right
        after sampling starts (e.g. the driver's sync() to re-arm the
        phase delays) so the sync event lands inside the capture.

        Returns (buf, n_samples): buf is an array('I') of packed words.
        '''
        n_samples = int(seconds * self.sample_hz)
        n_samples = (n_samples + 7) & ~7          # whole words
        words = n_samples // 8
        buf = array.array('I', [0] * words)

        sm = self.sm
        sm.active(0)
        sm.restart()
        while sm.rx_fifo():
            sm.get()
        gc.collect()

        dma = None
        try:
            dma = rp2.DMA()
        except Exception:
            pass

        if dma is not None:
            try:
                ctrl = dma.pack_ctrl(size=2, inc_read=False, inc_write=True,
                                     treq_sel=self._rx_dreq)
                dma.config(read=self._rxf_addr, write=buf, count=words,
                           ctrl=ctrl, trigger=True)
                sm.active(1)
                if before:
                    before()
                deadline = time.ticks_add(time.ticks_ms(),
                                          int(seconds * 1000) + 1000)
                while dma.active() and time.ticks_diff(deadline, time.ticks_ms()) > 0:
                    time.sleep_ms(5)
                incomplete = dma.active()
                sm.active(0)
                dma.close()
                if incomplete:
                    print('warning: DMA capture did not complete in time')
                return buf, n_samples
            except Exception as e:
                try:
                    dma.close()
                except Exception:
                    pass
                print('DMA capture failed (%s); falling back to polling' % e)
                sm.active(0)
                sm.restart()
                while sm.rx_fifo():
                    sm.get()

        # Polling fallback: at 10 kHz sampling, words arrive at only
        # ~1.25 kHz and the joined RX FIFO buffers 8 of them, so a
        # Python loop keeps up comfortably.
        idx = 0
        deadline = time.ticks_add(time.ticks_ms(), int(seconds * 1000) + 2000)
        sm.active(1)
        if before:
            before()
        while idx < words and time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if sm.rx_fifo():
                buf[idx] = sm.get()
                idx += 1
        sm.active(0)
        if idx < words:
            print('warning: polled capture incomplete (%d/%d words)' % (idx, words))
            n_samples = idx * 8
        return buf, n_samples
