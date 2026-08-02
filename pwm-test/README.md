# WokwiPWM test app

Test harness for the **WokwiPWM** project (`tt_um_wokwi_445338187869298689`,
mux address 748) on the ttsky25b TinyTapeout demoboard (TTDBv3, RP2350B).

Three parts:

- `board/wokwi_wokwipwm/` — MicroPython package that runs on the demoboard:
  a register-programming driver plus a PIO logic analyzer that samples the
  four PWM outputs and DMAs them to RAM.
- `web/index.html` — **WokwiPWM Commander**: dark-theme web app (Chrome/Edge,
  Web Serial) that connects to the board, auto-installs/updates the board
  app, programs the PWMs, and shows captures in a zoomable Plotly scope view.
- `host/capture_pwm.py` — CLI alternative that (via `mpremote`) does the
  same and plots with matplotlib.

## Web app

Open `web/index.html` in Chrome or Edge — double-clicking the file works
(the board sources ship embedded in `web/board_src.js`). Serving it also
works and always uses the live sources from `board/`:

```sh
cd pwm-test
python3 -m http.server 8000
# open http://localhost:8000/web/
```

**If you edit anything in `board/wokwi_wokwipwm/`, run
`python3 web/build_src.py`** to refresh the embedded snapshot used in
file:// mode (served mode picks up edits automatically).

Click **Connect board**, pick the board's serial port, and the app will
install (or update) the board-side package on the board automatically.
Then:

- **PWM channels** — period/duty/phase per channel; uncheck to leave a
  channel's registers untouched (loaded via `skip`).
- **Design clock** — sets the project clock immediately via
  `set_clock_hz()`. **Configure** always programs the registers at
  ≥10 kHz (the load/skip/zero edge detectors are sampled by the project
  clock, so a slow clock would miss the pulses) and then restores a slower
  requested clock automatically. The driver also stretches its pulses to
  ≥3 project clocks as a second safety margin.
- **Configure** programs the registers; **Capture** re-arms the phases
  (`zero` toggle), records the 4 outputs on uio_out[7:4], and renders them
  o-scope style — scroll to zoom, drag to pan, camera icon saves a PNG —
  with measured vs expected frequency/duty/phase below.

Close any `mpremote`/REPL session first — only one program can hold the
serial port. Web Serial needs Chrome or Edge on `http://localhost` or
`https` (not `file://`).

## Install on the board

```sh
pip install mpremote
cd pwm-test/board
mpremote cp -r wokwi_wokwipwm :/examples/
```

## Quick test at the REPL

```sh
mpremote repl
```

```python
import examples.wokwi_wokwipwm as w
w.run()                    # demo config, 0.5 s capture, prints a report
w.run(pwms=[(199, 50, 0), None, None, None], seconds=1.0)
```

`run()` enables the project, clocks it at 10 kHz, programs the registers,
re-arms the phases (toggles `zero`), captures the outputs, and prints
measured frequency / duty / rise offsets per channel.

Lower-level use:

```python
tt, pwm = w.setup()
pwm.program([(99, 50, 0), (99, 50, 25), (99, 50, 50), (199, 150, 0)])
pwm.write_control(0xF)     # pulse control-reg resets to re-sync counters
pwm.sync()                 # toggle 'zero' to re-arm phase delays
pwm.disp(True)             # 7-seg shows live PWM states (False: reg addr index)
w.set_clock_hz(1000)       # change the project clock any time
w.configure({'clock_hz': 500, 'pwms': [[99, 50, 0], None, None, None]})
```

## Capture + plot from the host

```sh
cd pwm-test/host
pip install matplotlib          # optional, for plots
./capture_pwm.py                                    # demo config, 2 s
./capture_pwm.py --pwm0 99,50 --pwm1 99,50,25 --zoom 50
./capture_pwm.py --png waves.png --csv capture.csv
```

PWM arguments are `period,duty[,phase]` (8-bit each); `--pwm2 none` leaves
a channel unprogrammed (registers keep their previous values, via `skip`).

## Design/register reference

| addr | register | | addr | register |
|------|-------------|-|------|-------------|
| 0x0  | PWM0 duty   | | 0x6  | PWM3 duty   |
| 0x1  | PWM0 period | | 0x7  | PWM3 period |
| 0x2  | PWM1 duty   | | 0x8  | PWM1 phase  |
| 0x3  | PWM1 period | | 0x9  | PWM2 phase  |
| 0x4  | PWM2 duty   | | 0xA  | PWM3 phase  |
| 0x5  | PWM2 period | | 0xB  | control     |

- `uio_in[0..3]` = load / skip / zero / disp (driven by the RP2350,
  `uio_oe_pico = 0x0F`); `uio_out[4..7]` = PWM0..PWM3.
- `uo_out[6:0]` is always the 7-segment pattern; `disp` selects the
  decoder's source — 0: register address index, 1: the 4 PWM output states
  (shown as a live hex digit).
- Counter counts 0..period; output HIGH at counter==duty, LOW at rollover.
  So freq = clock/(period+1) and high time = period−duty+1 clocks.
- Control register bit *i* resets PWM *i*'s period counter (for sync).
- Phase registers (PWM1–3 only) delay the counter's first start after a
  `zero` toggle, relative to PWM0.
- The load/skip/zero edge detectors are clocked by the project clock —
  programming below 10 kHz is unreliable, hence the configure-time clock
  guard (program at ≥10 kHz, then restore the slower clock).

> **Register order caveat:** goal.md says *period then duty* per channel,
> but the design repo's `docs/info.md` says *duty then period* — the app
> defaults to duty-first. If measurements come out swapped (e.g. programmed
> period 199 / duty 50 doesn't measure ≈50 Hz), use `--period-first` on the
> host tool or `duty_first=False` on the board, and fix goal.md or the docs.

## How the capture works

- TTDBv3 wires uio4..uio7 to RP2350 **GPIO29..32** (consecutive), so a
  2-line PIO program (`in pins, 4`, autopush at 32 bits) samples all four
  PWM outputs at exactly `sample_hz` (PIO clock = sample rate).
- GPIO32 sits outside PIO's default 0..31 pin window on the RP2350B, so the
  capture sets `PIO.gpio_base(16)` (window 16..47). It uses PIO1 SM0
  (`sm_id=4`) to stay clear of anything else; pass `sm_id=` to move it.
- Words are DMA'd to RAM (`rp2.DMA`, RX DREQ paced), with a Python polling
  fallback. 2 s @ 10 kHz = 20 000 samples = 10 KB.
- The `zero` toggle (phase re-arm) happens *after* sampling starts, so the
  first-start phase relationship is inside the capture window.
- Default 10 kHz sampling matches the project clock (1 sample/clock); the
  PIO can go far faster if you later crank the project clock. Minimum
  sample rate is ~1.9 kHz (sys_clk/65536 divider limit).

## Publishing the web app

The page is a single static file; to host it (e.g. GitHub Pages), publish
the whole `pwm-test/` tree so the app can still fetch `board/wokwi_wokwipwm/*`
for the auto-installer — Pages is `https`, which Web Serial accepts. Plotly
loads from its CDN, so the page needs internet access (or vendor the
`plotly-*.min.js` file next to `index.html` and update the `<script>` tag).
