#!/usr/bin/env python3
'''
Host-side driver for the WokwiPWM test app.

Talks to the TinyTapeout demoboard over USB serial via mpremote,
programs the four PWM channels, captures the outputs with the
on-board PIO logic analyzer, then measures and plots them.

Examples:
    ./capture_pwm.py                                # demo config, 2 s
    ./capture_pwm.py --pwm0 99,50 --pwm1 99,50,25 --seconds 1
    ./capture_pwm.py --zoom 50 --png waves.png
    ./capture_pwm.py --csv capture.csv --no-plot

PWM args are period,duty[,phase] (8-bit each).  Requires mpremote
(pip install mpremote); plotting needs matplotlib but degrades to a
text report without it.
'''

import argparse
import json
import subprocess
import sys


def parse_pwm(text):
    if text.lower() in ('none', 'skip', '-'):
        return None
    parts = [int(x, 0) for x in text.split(',')]
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError('expected period,duty[,phase]')
    for v in parts:
        if not 0 <= v <= 255:
            raise argparse.ArgumentTypeError('values must be 0..255')
    return parts


def run_board_capture(args):
    cfg = {
        'seconds': args.seconds,
        'sample_hz': args.sample_hz,
        'clock_hz': args.clock_hz,
        'duty_first': not args.period_first,
        'ctrl': args.ctrl,
        'pwms': [args.pwm0, args.pwm1, args.pwm2, args.pwm3],
    }
    code = ('try:\n'
            '    import examples.wokwi_wokwipwm as W\n'
            'except ImportError:\n'
            '    import wokwi_wokwipwm as W\n'
            'import json\n'
            'W.capture_and_dump(json.loads(%r))\n' % json.dumps(cfg))
    cmd = ['mpremote']
    if args.device:
        cmd += ['connect', args.device]
    cmd += ['exec', code]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=args.seconds + 60)
    except FileNotFoundError:
        sys.exit('mpremote not found - install with: pip install mpremote')
    except subprocess.TimeoutExpired:
        sys.exit('board did not respond in time')
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        sys.exit('mpremote exec failed (is the board plugged in / port right?)')
    return proc.stdout


def parse_dump(text):
    meta = None
    hex_lines = []
    in_data = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('META '):
            meta = json.loads(line[5:])
        elif line == 'DATA_BEGIN':
            in_data = True
        elif line == 'DATA_END':
            in_data = False
        elif in_data:
            hex_lines.append(line)
        else:
            if line:
                print('[board]', line)
    if meta is None or not hex_lines:
        sys.exit('no capture data found in board output')

    words = []
    for line in hex_lines:
        for i in range(0, len(line), 8):
            words.append(int(line[i:i + 8], 16))

    n = min(meta['n_samples'], len(words) * 8)
    channels = [[0] * n for _ in range(4)]
    for i in range(n):
        nib = (words[i >> 3] >> ((i & 7) << 2)) & 0xF
        for ch in range(4):
            channels[ch][i] = (nib >> ch) & 1
    return meta, channels


def measure(channels, meta):
    sample_hz = meta['sample_hz']
    clock_hz = meta['clock_hz']
    results = []
    first_rises = []
    for ch, data in enumerate(channels):
        rises = [i for i in range(1, len(data)) if data[i] and not data[i - 1]]
        r = {'ch': ch, 'edges': len(rises), 'freq_hz': None, 'duty_pct': None,
             'period_clocks': None, 'phase_clocks': None}
        first_rises.append(rises[0] if rises else None)
        if len(rises) >= 2:
            period_samples = (rises[-1] - rises[0]) / (len(rises) - 1)
            r['freq_hz'] = sample_hz / period_samples
            r['period_clocks'] = period_samples * clock_hz / sample_hz
            span = data[rises[0]:rises[-1]]
            if span:
                r['duty_pct'] = 100.0 * sum(span) / len(span)
        results.append(r)
    base = first_rises[0]
    if base is not None:
        for ch, fr in enumerate(first_rises):
            if fr is not None:
                results[ch]['phase_clocks'] = (fr - base) * clock_hz / sample_hz
    return results


def print_report(results, meta):
    pwms = meta.get('pwms') or []
    print()
    print('ch  programmed(per,duty,ph)    freq Hz   period clks   duty %   rise offset clks')
    for r in results:
        ch = r['ch']
        p = pwms[ch] if ch < len(pwms) and pwms[ch] else None
        ptxt = '%s,%s,%s' % (p[0], p[1], p[2] if len(p) > 2 else 0) if p else '(unchanged)'
        def fmt(v, w=9, d=1):
            return ('%*.*f' % (w, d, v)) if v is not None else ' ' * (w - 3) + 'n/a'
        print(' %d  %-24s %s   %s   %s   %s' % (
            ch, ptxt, fmt(r['freq_hz']), fmt(r['period_clocks'], 11),
            fmt(r['duty_pct'], 6), fmt(r['phase_clocks'], 12)))
    print()
    print('expected: freq = clock/(period+1); high time = period-duty+1 clocks;')
    print('rise offsets vs PWM0 include any duty-cycle differences between channels.')


def write_csv(path, channels, meta):
    dt_ms = 1000.0 / meta['sample_hz']
    with open(path, 'w') as f:
        f.write('t_ms,pwm0,pwm1,pwm2,pwm3\n')
        for i in range(len(channels[0])):
            f.write('%.4f,%d,%d,%d,%d\n' % (i * dt_ms, channels[0][i],
                                            channels[1][i], channels[2][i],
                                            channels[3][i]))
    print('wrote', path)


def plot(channels, meta, zoom_ms=None, png=None):
    try:
        import matplotlib
        if png:
            matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('matplotlib not installed - skipping plot (pip install matplotlib)')
        return
    dt_ms = 1000.0 / meta['sample_hz']
    n = len(channels[0])
    t = [i * dt_ms for i in range(n)]
    fig, ax = plt.subplots(figsize=(12, 4.5))
    for ch in range(4):
        offset = (3 - ch) * 1.5
        ax.step(t, [v + offset for v in channels[ch]], where='post',
                linewidth=1.0, label='PWM%d' % ch)
    ax.set_yticks([(3 - ch) * 1.5 + 0.5 for ch in range(4)])
    ax.set_yticklabels(['PWM%d' % ch for ch in range(4)])
    ax.set_xlabel('time (ms)')
    ax.set_title('WokwiPWM capture: %d samples @ %d Hz (project clock %d Hz)'
                 % (meta['n_samples'], meta['sample_hz'], meta['clock_hz']))
    if zoom_ms:
        ax.set_xlim(0, zoom_ms)
    ax.grid(True, axis='x', alpha=0.3)
    fig.tight_layout()
    if png:
        fig.savefig(png, dpi=130)
        print('wrote', png)
    else:
        plt.show()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--device', help='serial port for mpremote (default: auto)')
    ap.add_argument('--seconds', type=float, default=2.0, help='capture length (default 2)')
    ap.add_argument('--sample-hz', type=int, default=10000, help='sample rate (default 10000)')
    ap.add_argument('--clock-hz', type=int, default=10000, help='project clock (default 10000)')
    ap.add_argument('--pwm0', type=parse_pwm, default=[99, 50, 0], metavar='P,D[,PH]')
    ap.add_argument('--pwm1', type=parse_pwm, default=[99, 50, 25], metavar='P,D[,PH]')
    ap.add_argument('--pwm2', type=parse_pwm, default=[99, 50, 50], metavar='P,D[,PH]')
    ap.add_argument('--pwm3', type=parse_pwm, default=[199, 150, 0], metavar='P,D[,PH]')
    ap.add_argument('--ctrl', type=lambda x: int(x, 0), default=0,
                    help='control register value (bit i resets PWM i counter)')
    ap.add_argument('--period-first', action='store_true',
                    help='load period before duty (goal.md order; default is '
                         'duty-first per the design repo docs)')
    ap.add_argument('--zoom', type=float, metavar='MS', help='x-axis limit in ms')
    ap.add_argument('--png', help='save plot to file instead of showing it')
    ap.add_argument('--csv', help='also write samples to CSV')
    ap.add_argument('--no-plot', action='store_true')
    args = ap.parse_args()

    out = run_board_capture(args)
    meta, channels = parse_dump(out)
    results = measure(channels, meta)
    print_report(results, meta)
    if args.csv:
        write_csv(args.csv, channels, meta)
    if not args.no_plot:
        plot(channels, meta, zoom_ms=args.zoom, png=args.png)


if __name__ == '__main__':
    main()
