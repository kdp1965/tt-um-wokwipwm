## How it works

After reset, there is an 4-bit counter that is the address where the next 8-bit input data
at in[7:0] will be loaded.  The 'load' signal loads the 8-bit input to the register at that
current counter address and then increments the counter.

The 'skip' input will skip an address without loading it.  And the 'zero' input will reset
the counter back to zero.

None of the 'load', 'skip', 'zero' or 'disp' inputs are debounced, but they are riging edge
detected (at least the load and skip are).

The current count (register address) is display on the 7-segment display via a 4 to 7 decoder.
Each of 4 PWM channels use two 8-bit control registers.  The first (lowest address) register
controls the duty cycle and the second (highest address) controls the period of that channel.
Only channels with non-zero period value will operate (the counter does not count when 
period_val is zero).

Program PWM channel control registers to create independent PWMs:

Address     Meaning
0           PWM1 duty
1           PWM1 period
2           PWM2 duty
3           PWM2 period
4           PWM3 duty
5           PWM3 period
5           PWM4 duty
7           PWM4 period
8           Control
            BIT0:
            BIT1:
            BIT2:
            BIT3:  Write '1' to clear and synchronize all PWM channels.  Self clearing.

## How to test

1.  Start the clock and reset the circuit. 
2.  Set the DIP switches so only switch 7 is on (8'h40)
3.  Press 'load'.  The display should now show '1'.
4.  Set the DIS switches so only switch 8 is on (8'h80).
5.  Press 'load'.  The display should show '2' and the first LED should start blinking at about 50% rate.
6.  Continue programming values for the other 3 PWMs by setting DIP switch value and pressing 'load'
    even values (duty cycle) must be less that the associated odd address value (period).


## External hardware

LEDs should be connected to the PWM channel outputs.
