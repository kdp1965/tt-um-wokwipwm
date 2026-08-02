'''
WokwiPWM test app for the TinyTapeout demoboard.

Install:  mpremote cp -r wokwi_wokwipwm :/examples/

REPL use:
    import examples.wokwi_wokwipwm as w
    w.run()                                   # demo config, 0.5 s capture
    w.run(pwms=[(199, 50, 0), None, None, None], seconds=1.0)

Lower level:
    tt, pwm = w.setup()
    pwm.program([(99, 50, 0), (99, 50, 25), (99, 50, 50), (199, 150, 0)])
    pwm.sync()
'''

from .app import (run, setup, get_tt, enable_project, configure,
                  capture_dump, capture_and_dump, set_clock_hz, set_disp,
                  status, analyze, WokwiPWM, DEMO_PWMS, APP_VERSION)
from .capture import PwmCapture
