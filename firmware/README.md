# Arm firmware

For organizers. Each Arduino needs this once. The program and its calibration stay on the
board after that, so attendees never need the Arduino IDE.

## Upload

1. Install the [Arduino IDE](https://www.arduino.cc/en/software/).
2. Open `arm_firmware/arm_firmware.ino`.
3. Choose **Arduino UNO** and its port from the board menu at the top.
4. Click the **Upload** arrow and wait for it to finish.

Uploading to a new Arduino means it also needs calibrating, below.

## Wiring

Signal pins are 3 base, 5 shoulder, 6 elbow and 9 wrist. Power the servos from an external
supply, never the UNO 5V pin, and tie its ground to Arduino ground.

## Commands

The firmware speaks 115200 baud, one command per line. Each command answers
`ok` or `err <reason>`.

| Command | Does |
| --- | --- |
| `M a1 a2 a3 a4 [ms]` | Queue a move of all servos |
| `J n angle` | Move one servo |
| `A` | Attach at the park pose |
| `D` | Detach, servos go limp |
| `X` | Stop now and go limp |
| `Z` | Answer `ok` when motion has finished |
| `P` | Print position |
| `K` | Save the current pose as park |
| `G` | Print calibration |
| `L n min max` | Set limits |
| `R n ref dir` | Set reference angle and direction |
| `W` | Save calibration to EEPROM |
| `F` | Reset calibration to defaults |
| `C n [angle]` | Enter calibration mode for a servo |

## Calibration

Servo limits, references and directions live on the Arduino and are read on
every connect. Change them from the Arduino IDE Serial Monitor. Send `C n`, then
use `+` and `-` to step, `s 2.5` to set the step, `g 90` to go to an angle, `[`
and `]` to mark min and max, `0` to mark the reference, `^` and `v` to set the
direction, and `x` to leave. Send `W` to save.

Everything else is in
[`calibration.json`](../src/bv_llm_sdk/toolkits/arm/calibration.json):
arm lengths, `paper_z`, speeds, backlash and each servo's `scale`.
