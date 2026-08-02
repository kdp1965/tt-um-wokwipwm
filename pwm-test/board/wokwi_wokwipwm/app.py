'''
WokwiPWM (tt_um_wokwi_445338187869298689) hardware test app.

Design summary (ttsky25b, 10 kHz default project clock):

  * 4 PWM channels.  Each has an 8-bit period and duty register;
    PWM1..PWM3 also have an 8-bit phase register (relative to PWM0).
  * Counter counts 0..period.  Output goes HIGH when the counter
    reaches `duty`, LOW when it rolls over to 0.
  * Register programming via uio pins + data byte on ui_in[7:0]:
        uio_in[0] = load   (rising edge: load reg at addr, addr+=1)
        uio_in[1] = skip   (rising edge: addr+=1, no load)
        uio_in[2] = zero   (rising edge: addr=0, re-arms phase delays)
        uio_in[3] = disp   (7-seg decoder source: 0 = reg addr index,
                            1 = the 4 PWM output states as a hex digit)
  * uo_out[6:0] is always the 7-segment pattern; disp only selects
    what the 4-bit-to-7-seg decoder displays.
  * PWM outputs on uio_out[7:4] = PWM0..PWM3.

Register load order (addr counter), per docs/info.md in the design
repo (github.com/kdp1965/tt-um-wokwipwm), translated to 0-based names:

    0x0 PWM0 duty     0x1 PWM0 period
    0x2 PWM1 duty     0x3 PWM1 period
    0x4 PWM2 duty     0x5 PWM2 period
    0x6 PWM3 duty     0x7 PWM3 period
    0x8 PWM1 phase    0x9 PWM2 phase    0xA PWM3 phase
    0xB control       (bit i resets PWM i's period counter)

NOTE: goal.md describes period-before-duty, but the design repo's
docs/info.md says duty-before-period.  Duty-first is the default here;
if hardware measurements come out swapped, pass duty_first=False.

The load/skip/zero edge detectors are sampled by the project clock, so
programming needs a reasonably fast clock: configure() programs at
>= 10 kHz and then restores any slower requested clock afterwards,
and the driver stretches its pulses to >= 3 project clocks.
'''

import time

try:
    import json
except ImportError:
    import ujson as json

from ttboard.demoboard import DemoBoard
from ttboard.mode import RPMode

from .capture import PwmCapture

APP_VERSION = '1.4.0'

PROJECT_ATTR = 'wokwi_wokwipwm_689'
PROJECT_MACRO = 'tt_um_wokwi_445338187869298689'
DEFAULT_CLOCK_HZ = 10_000
MIN_PROG_CLOCK_HZ = 10_000     # min clock while programming registers
MAX_SAMPLES = 240_000          # capture RAM guard (~120 KB packed)

# uio_in bit positions (driven by the RP2350)
LOAD = 0
SKIP = 1
ZERO = 2
DISP = 3

# Demo config used when nothing else is specified:
# (period, duty, phase) - all 100-clock periods except PWM3.
DEMO_PWMS = [(99, 50, 0), (99, 50, 25), (99, 50, 50), (199, 150, 0)]

_tt = None
_pwm = None
_clock_hz = DEFAULT_CLOCK_HZ


def get_tt():
    '''
    The DemoBoard object.  main.py already probed the board at boot and
    exposed the initialized `tt` global at the REPL - reuse that instead
    of re-probing (a second DemoboardDetect.probe() muxes the chip ROM
    and disturbs the live project state).  Only probe + construct when
    the board is genuinely uninitialized (e.g. after a soft reset that
    skipped main.py, as mpremote does).
    '''
    global _tt
    if _tt is not None:
        return _tt
    try:
        import __main__
        tt = getattr(__main__, 'tt', None)
        if tt is not None and hasattr(tt, 'shuttle'):
            _tt = tt
            return _tt
    except Exception:
        pass
    try:
        from ttboard.boot.demoboard_detect import DemoboardDetect
        DemoboardDetect.probe()
    except Exception as e:
        print('demoboard probe issue (may already be initialized):', e)
    _tt = DemoBoard.get()
    return _tt


def enable_project(tt):
    if tt.shuttle.has(PROJECT_ATTR):
        getattr(tt.shuttle, PROJECT_ATTR).enable()
        return
    # fall back: hunt for the wokwi id in the shuttle listing
    for name in dir(tt.shuttle):
        if 'wokwipwm' in name.lower() or name.endswith('689'):
            print('enabling shuttle project', name)
            getattr(tt.shuttle, name).enable()
            return
    raise RuntimeError('WokwiPWM (%s) not found in this shuttle' % PROJECT_MACRO)


def set_clock_hz(hz):
    '''Set the project clock.  Safe any time; returns the value set.'''
    global _clock_hz
    hz = int(hz)
    tt = get_tt()
    tt.clock_project_PWM(hz)
    _clock_hz = hz
    if _pwm is not None:
        _pwm.clock_hz = hz
    return hz


class WokwiPWM:
    '''Register-programming driver for the WokwiPWM design.'''

    def __init__(self, tt, duty_first=True, settle_ms=1,
                 clock_hz=DEFAULT_CLOCK_HZ):
        self.tt = tt
        self.duty_first = duty_first
        self.settle_ms = settle_ms
        self.clock_hz = clock_hz
        self._uio = 0
        tt.uio_oe_pico.value = 0x0F   # we drive load/skip/zero/disp, read PWMs
        tt.uio_in.value = 0
        tt.ui_in.value = 0

    def _hold_ms(self):
        # hold each level >= ~3 project clocks so the design's clocked
        # edge detectors can't miss the pulse at slow clock rates
        ms = (3000 + self.clock_hz - 1) // self.clock_hz
        return ms if ms > self.settle_ms else self.settle_ms

    def _write_uio(self):
        self.tt.uio_in.value = self._uio

    def _pulse(self, bit):
        hold = self._hold_ms()
        self._uio |= (1 << bit)
        self._write_uio()
        time.sleep_ms(hold)
        self._uio &= ~(1 << bit)
        self._write_uio()
        time.sleep_ms(hold)

    def zero(self):
        '''Reset the address counter (also re-arms the phase delays).'''
        self._pulse(ZERO)

    def skip(self, n=1):
        for _ in range(n):
            self._pulse(SKIP)

    def load(self, value):
        self.tt.ui_in.value = value & 0xFF
        time.sleep_ms(self._hold_ms())
        self._pulse(LOAD)

    def disp(self, pwm_mode):
        '''disp=True: 7-seg decoder shows the 4 PWM output states (as a
        hex digit); False: it shows the register address index.'''
        if pwm_mode:
            self._uio |= (1 << DISP)
        else:
            self._uio &= ~(1 << DISP)
        self._write_uio()

    def program(self, pwms, ctrl=0x0):
        '''
        Load all registers.  pwms is a list of up to 4 entries, each
        (period, duty) or (period, duty, phase); None skips a channel
        (its registers keep their current values).  PWM0's phase is
        fixed at 0 by the design.
        '''
        cfg = []
        for i in range(4):
            p = pwms[i] if i < len(pwms) else None
            if p is None:
                cfg.append(None)
            else:
                phase = p[2] if len(p) > 2 else 0
                cfg.append((p[0], p[1], phase))

        self.zero()
        for i in range(4):                      # addr 0x0..0x7
            if cfg[i] is None:
                self.skip(2)
                continue
            period, duty, _ = cfg[i]
            first, second = (duty, period) if self.duty_first else (period, duty)
            self.load(first)
            self.load(second)
        for i in (1, 2, 3):                     # addr 0x8..0xA
            if cfg[i] is None:
                self.skip()
                continue
            self.load(cfg[i][2])
        self.load(ctrl & 0xF)                   # addr 0xB

    def write_control(self, mask):
        '''Write only the control register: zero addr, skip to 0xB, load.'''
        self.zero()
        self.skip(11)
        self.load(mask & 0xF)

    def sync(self):
        '''Toggle `zero` to re-arm the first-cycle phase delays.'''
        self._pulse(ZERO)


def setup(clock_hz=DEFAULT_CLOCK_HZ, duty_first=True):
    '''Enable the project, clock it, release reset; returns (tt, driver).'''
    global _pwm, _clock_hz
    tt = get_tt()
    enable_project(tt)
    if tt.mode != RPMode.ASIC_RP_CONTROL:
        tt.mode = RPMode.ASIC_RP_CONTROL
    tt.reset_project(True)
    time.sleep_ms(10)
    tt.clock_project_PWM(clock_hz)
    _clock_hz = clock_hz
    time.sleep_ms(10)
    tt.reset_project(False)
    time.sleep_ms(10)
    _pwm = WokwiPWM(tt, duty_first=duty_first, clock_hz=clock_hz)
    return tt, _pwm


def configure(cfg=None):
    '''
    Enable + clock the project and program the PWM registers.

    cfg keys (all optional): clock_hz, duty_first, ctrl, disp,
    pwms = list of 4 x [period, duty, phase] or None.

    If the requested clock is below 10 kHz, programming happens at
    10 kHz (the load/skip/zero edge detectors are clocked by the
    project clock) and the slower clock is restored afterwards.
    '''
    cfg = cfg or {}
    clock_hz = int(cfg.get('clock_hz', DEFAULT_CLOCK_HZ))
    duty_first = bool(cfg.get('duty_first', True))
    ctrl = int(cfg.get('ctrl', 0))
    pwms = cfg.get('pwms')
    if pwms is None:
        pwms = [list(p) for p in DEMO_PWMS]
    pwms_t = [tuple(p) if p is not None else None for p in pwms]

    prog_hz = clock_hz if clock_hz >= MIN_PROG_CLOCK_HZ else MIN_PROG_CLOCK_HZ
    tt, pwm = setup(prog_hz, duty_first)
    pwm.program(pwms_t, ctrl)
    pwm.disp(bool(cfg.get('disp', False)))
    if clock_hz != prog_hz:
        set_clock_hz(clock_hz)      # restore the requested slower clock
    # zero the address counter so the 7-seg reads "0" (and arm the phases)
    pwm.sync()
    print('CONFIGURED ' + json.dumps({'clock_hz': clock_hz,
                                      'prog_clock_hz': prog_hz,
                                      'pwms': pwms, 'ctrl': ctrl,
                                      'duty_first': duty_first}))
    return pwm


def set_disp(on):
    '''Select the 7-seg decoder source right now: True = live PWM
    states, False = register address index.'''
    global _pwm
    tt = get_tt()
    if _pwm is None:
        _pwm = WokwiPWM(tt, clock_hz=_clock_hz)
    _pwm.disp(bool(on))
    return bool(on)


def status():
    '''One-line JSON health snapshot (used by the web app's Status button).'''
    tt = get_tt()
    info = {'app_version': APP_VERSION, 'clock_hz': _clock_hz}
    try:
        info['enabled'] = str(tt.shuttle.enabled)
    except Exception as e:
        info['enabled'] = 'unknown (%r)' % e
    for name in ('uo_out', 'uio_out', 'ui_in', 'uio_in', 'uio_oe_pico'):
        try:
            info[name] = '0x%02x' % (int(getattr(tt, name).value) & 0xFF)
        except Exception:
            info[name] = 'err'
    try:
        info['mode'] = str(tt.mode_str) if hasattr(tt, 'mode_str') else str(tt.mode)
    except Exception:
        pass
    print('STATUS ' + json.dumps(info))
    return info


def capture_dump(seconds=2.0, sample_hz=DEFAULT_CLOCK_HZ, extra_meta=None):
    '''
    Capture the 4 PWM outputs (re-arming phases first) and dump the
    raw buffer as hex between DATA_BEGIN/DATA_END, with a META line.
    Assumes configure() (or setup()) already ran.
    '''
    global _pwm
    tt = get_tt()
    if _pwm is None:
        _pwm = WokwiPWM(tt, clock_hz=_clock_hz)
    seconds = float(seconds)
    sample_hz = int(sample_hz)
    if seconds * sample_hz > MAX_SAMPLES:
        seconds = MAX_SAMPLES / sample_hz
        print('note: capture clamped to %.1f s (%d samples RAM limit)'
              % (seconds, MAX_SAMPLES))
    cap = PwmCapture(sample_hz=sample_hz)
    buf, n = cap.capture(seconds, before=_pwm.sync)

    meta = {'n_samples': n, 'sample_hz': sample_hz, 'clock_hz': _clock_hz,
            'channels': 4, 'app_version': APP_VERSION}
    if extra_meta:
        meta.update(extra_meta)
    print('META ' + json.dumps(meta))
    print('DATA_BEGIN')
    line = []
    for w in buf:
        line.append('%08x' % w)
        if len(line) == 16:
            print(''.join(line))
            line = []
    if line:
        print(''.join(line))
    print('DATA_END')
    return n


def capture_and_dump(cfg=None):
    '''One-shot configure + capture (used by host/capture_pwm.py).'''
    cfg = cfg or {}
    configure(cfg)
    extra = {'pwms': cfg.get('pwms', [list(p) for p in DEMO_PWMS]),
             'ctrl': int(cfg.get('ctrl', 0)),
             'duty_first': bool(cfg.get('duty_first', True))}
    return capture_dump(cfg.get('seconds', 2.0),
                        cfg.get('sample_hz', DEFAULT_CLOCK_HZ), extra)


def analyze(buf, n_samples, sample_hz, clock_hz=DEFAULT_CLOCK_HZ, max_edges=64):
    '''
    Single pass over the packed capture; per channel returns a dict of
    measured frequency, duty %, and first-rise offset (phase) vs PWM0.
    '''
    nch = 4
    prev = [-1] * nch
    rises = [[] for _ in range(nch)]
    highs = [0] * nch
    highs_at_first = [0] * nch
    for i in range(n_samples):
        nib = (buf[i >> 3] >> ((i & 7) << 2)) & 0xF
        for ch in range(nch):
            v = (nib >> ch) & 1
            if v:
                highs[ch] += 1
            if prev[ch] == 0 and v == 1 and len(rises[ch]) < max_edges:
                if not rises[ch]:
                    highs_at_first[ch] = highs[ch] - 1
                rises[ch].append(i)
            prev[ch] = v

    results = []
    base_rise = rises[0][0] if rises[0] else None
    for ch in range(nch):
        r = {'ch': ch, 'edges': len(rises[ch]), 'freq_hz': None,
             'period_clocks': None, 'duty_pct': None, 'phase_clocks': None}
        if len(rises[ch]) >= 2:
            span = rises[ch][-1] - rises[ch][0]
            period_samples = span / (len(rises[ch]) - 1)
            r['freq_hz'] = sample_hz / period_samples
            r['period_clocks'] = period_samples * clock_hz / sample_hz
            active = n_samples - rises[ch][0]
            if active > 0:
                r['duty_pct'] = 100.0 * (highs[ch] - highs_at_first[ch]) / active
        if rises[ch] and base_rise is not None:
            r['phase_clocks'] = (rises[ch][0] - base_rise) * clock_hz / sample_hz
        results.append(r)
    return results


def _print_report(results, pwms, sample_hz, clock_hz):
    print('')
    print('ch  programmed(per,duty,ph)   freq Hz   period clks   duty %   rise offset clks')
    for r in results:
        ch = r['ch']
        p = pwms[ch] if ch < len(pwms) and pwms[ch] is not None else ('-', '-', '-')
        ptxt = '%s,%s,%s' % (p[0], p[1], p[2] if len(p) > 2 else 0)
        def fmt(v, spec='%8.1f'):
            return (spec % v) if v is not None else '     n/a'
        print('%d   %-22s %s   %s     %s   %s' % (
            ch, ptxt, fmt(r['freq_hz']), fmt(r['period_clocks']),
            fmt(r['duty_pct'], '%6.1f'), fmt(r['phase_clocks'])))
    print('')
    print('(sample %d Hz, project clock %d Hz; expected freq = clock/(period+1),' %
          (sample_hz, clock_hz))
    print(' expected high time = period-duty+1 clocks; offsets include duty differences)')


def run(seconds=0.5, sample_hz=DEFAULT_CLOCK_HZ, clock_hz=DEFAULT_CLOCK_HZ,
        pwms=None, ctrl=0x0, duty_first=True):
    '''
    Program a demo (or supplied) config, capture, and print measurements.

        import examples.wokwi_wokwipwm as w
        w.run()
        w.run(pwms=[(99,50,0), (99,50,25), None, None], seconds=1.0)
    '''
    pwms = pwms if pwms is not None else DEMO_PWMS
    pwm = configure({'clock_hz': clock_hz, 'duty_first': duty_first,
                     'ctrl': ctrl, 'disp': True,
                     'pwms': [list(p) if p is not None else None for p in pwms]})
    cap = PwmCapture(sample_hz=sample_hz)
    buf, n = cap.capture(seconds, before=pwm.sync)
    print('captured %d samples at %d Hz' % (n, sample_hz))
    results = analyze(buf, n, sample_hz, _clock_hz)
    _print_report(results, pwms, sample_hz, _clock_hz)
    return results
