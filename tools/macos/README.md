# macOS pointer profiles

Bring the exact trackpad acceleration of a Mac you like to Trackpad Plus on Omarchy. No
recording session is needed: macOS publishes its acceleration curves in the I/O Registry,
and the algorithm that applies them is open source. A 30-second check confirms the few
constants that live in Apple's closed multitouch driver.

## Workflow

On the Mac (system `python3` and Xcode's `swiftc`; no permissions are requested):

```sh
python3 tools/macos/export-profile.py          # writes <model>.json
swiftc -O tools/macos/probe.swift -o /tmp/trackpad-probe
/tmp/trackpad-probe probe.csv                  # move one finger slow → fast for 30 s
python3 tools/macos/export-profile.py --check probe.csv --profile <model>.json --write
python3 tools/macos/export-profile.py --preview <model>.json --hyprland-scale 2
```

The export records all of the trackpad's curves, the current **Tracking speed** (only the
starting position of the slider in Trackpad Plus), and the built-in display's size, so the
cursor can travel the same physical distance on Linux. `--check` measures the
pointer event rate, whether counts are whole numbers, Apple's per-event accelerator, and the
static curve against real strokes; `--write` stores those constants only when the check
passes. `--preview` prints the libinput curve and its error against macOS.

On Omarchy, copy the profile to `~/.config/trackpad-plus/profiles/` (mode 0600) and choose
it under **Pointer feel → macOS**. `profiles/` contains a checked profile for the MacBook
Pro 14" (M1 Pro, 2021), exported from macOS 15.7.9 on 2026-09-30.

## How macOS accelerates a trackpad

Sources: Apple's [IOHIDFamily-2115.140.4](https://github.com/apple-oss-distributions/IOHIDFamily/tree/IOHIDFamily-2115.140.4/IOHIDEventSystemPlugIns)
(macOS 15); `hidutil dump services` shows the built-in trackpad using exactly this path
(`IOHIDPointerAccelerator`, Rate 120, Resolution 400 → `IOHIDParametricAcceleration`).

1. The multitouch driver reports relative motion in whole counts at
   `HIDPointerResolution` = 400 per inch, about 123 events per second.
2. [`IOHIDPointerAccelerator::accelerate`](https://github.com/apple-oss-distributions/IOHIDFamily/blob/IOHIDFamily-2115.140.4/IOHIDEventSystemPlugIns/IOHIDAcceleration.cpp#L136-L171)
   takes one event at a time, with no smoothing or history beyond the last timestamp:
   `V = floor(|Δ|)` counts, scaled down only if the event is later than the 120 Hz
   `HIDPointerReportRate` period, and multiplies `Δ` by `multiplier(V) / V`.
3. [`IOHIDParametricAcceleration::multiplier`](https://github.com/apple-oss-distributions/IOHIDFamily/blob/IOHIDFamily-2115.140.4/IOHIDEventSystemPlugIns/IOHIDAccelerationAlgorithm.cpp#L211-L256)
   evaluates `x = V × 67 / 400` — the filter passes a fixed
   [`FRAME_RATE` of 67](https://github.com/apple-oss-distributions/IOHIDFamily/blob/IOHIDFamily-2115.140.4/IOHIDEventSystemPlugIns/IOHIDAcceleration.hpp#L30)
   ([call](https://github.com/apple-oss-distributions/IOHIDFamily/blob/IOHIDFamily-2115.140.4/IOHIDEventSystemPlugIns/IOHIDPointerScrollFilter.cpp#L647-L668)),
   not the report rate — and returns `f(x) × 96/67` points
   ([`kCursorScale`](https://github.com/apple-oss-distributions/IOHIDFamily/blob/IOHIDFamily-2115.140.4/IOHIDEventSystemPlugIns/IOHIDAccelerationAlgorithm.hpp#L23)).
4. `f` comes from the device's `HIDAccelCurves`
   ([setup](https://github.com/apple-oss-distributions/IOHIDFamily/blob/IOHIDFamily-2115.140.4/IOHIDEventSystemPlugIns/IOHIDAccelerationAlgorithm.cpp#L111-L205)):
   `lin·x + (par·x)² + (cub·x)³ + (quart·x)⁴` up to `TangentSpeedLinear`, the tangent line
   up to `TangentSpeedParabolicRoot`, then `sqrt(m₁·x + b₁)`. Gains are raised together with
   the speed — `(g·x)ⁿ`, not `g·xⁿ`. Tracking speeds between the stored notches interpolate
   every parameter linearly.

For ideal continuous counts with both resolution and assumed count scale at
400 per inch, let `q = min(r / report_rate, 1)`. The steady transfer is
`T(v) = (96·r/(67·q))·f(67·q·v/r)` points per second at event rate `r` and
finger speed `v` in inches/s. When `r` is at least the report rate, `q=1` and this
reduces to the original expression. Integer and fractional counts need the
per-event floor and carried-frame gaps as well. The converter models those
separately; regression tests compare all three rates (60, 120, 123.4 Hz) with
the event model. This verifies the mathematical model, not physical Mac parity.

## How Linux receives it

- libinput's custom profile takes **raw trackpad units per millisecond** at the x-axis
  resolution and returns logical pixels per millisecond: speed is one event's distance over
  the time since the previous one
  ([libinput 1.31.3 `filter-custom.c` L131](https://sources.debian.org/src/libinput/1.31.3-1/src/filter-custom.c/#L131)),
  points are interpolated linearly and extrapolated from the last two
  ([L152](https://sources.debian.org/src/libinput/1.31.3-1/src/filter-custom.c/#L152)), at most
  [64 points](https://sources.debian.org/src/libinput/1.31.3-1/src/libinput-private.h/#L341).
- Hyprland applies libinput's deltas unchanged, in logical pixels.
- On Asahi the MacBook Pro 14" trackpad is `apple-spi-trackpad`, with 12312 units over the
  same 124.80 mm sensor macOS reports: **98.65 units/mm**. It reports about 126 frames per
  second with steady spacing (7.7–8.1 ms) and no batching; because libinput's curve takes
  speed per millisecond, the frame rate does not change the curve.

The converter samples `T` as 64 points whose last two lie on Apple's tangent line, so
libinput's extrapolation follows the modeled tangent until the square-root knee.
Scaling targets comparable physical cursor travel on the same panel. The
contributor reported **1.8% conversion error over 6–600 mm/s** for the included
M1 Pro profile at tracking speed 0.875 and 98.65 units/mm, with larger errors
outside that band. Other profiles, event-rate combinations and delta models
have different approximation error; their conversion reports its own bands.

## What the check found on a MacBook Pro 14" (M1 Pro)

These are contributor-reported measurements from the original M1 Pro setup;
the Linux tests do not reproduce them on other hardware. Counts per physical
inch remain an assumption derived from exported resolution metadata. The
NSTouch comparison is a sanity check and does not calibrate that assumption;
PASS and `--write` update event rate and delta model, not physical count scale.

| Measurement | Result |
| --- | --- |
| Apple's accelerator, event by event | median error 0.03 % (1843 events) |
| Pointer event rate | 123.4 Hz (not the 120 Hz `HIDPointerReportRate`) |
| Counts | whole numbers; 400 per physical inch assumed |
| Static curve vs real strokes, 15–254 mm/s | within ±1.4 % |

**Why the probe avoids MultitouchSupport.** Reading raw frames through the private
MultitouchSupport framework (`MTDeviceStart`) changes macOS while it runs: in blind A/B runs
the pointer felt laggy and too fast. Measured, pointer events trailed the raw frames by
10–14 ms, and counts arrived in bursts that the convex curve accelerates 6–8 % more at slow
speeds, although Apple's accelerator still matched every event. The probe therefore uses
AppKit's public `NSTouch`, which leaves the pointer feeling native.

## Trackpad resolution

Trackpad Plus needs each trackpad's resolution in units per millimetre, because libinput's
custom curve counts raw device units. The MacBook Pro 14" (M1 Pro or M1 Max) under Asahi is
built in. For another trackpad, read the kernel's axis range once (read-only; replace
`eventN` with the trackpad's device from `/proc/bus/input/devices`):

```sh
sudo python3 -c 'import fcntl,os,struct,sys; v=struct.unpack("6i", fcntl.ioctl(os.open(sys.argv[1], os.O_RDONLY), 0x80184540, bytes(24))); print("min", v[1], "max", v[2], "resolution", v[5])' /dev/input/eventN
```

Units per millimetre are `(max − min) ÷ the sensor width in mm`. On a Mac,
`ioreg -l | grep 'Sensor Surface Width'` lists the width (124.80 mm on the MacBook Pro 14").
Without it, use the kernel's `resolution`, which is rounded to a whole number. Save the value for the
interface name that the error message gives, in the trackpad's settings group, for example:

```sh
python3 ~/.config/omarchy/plugins/davefano.trackpad-plus/trackpads.py \
  set apple units_per_mm '{"apple-inc.-magic-trackpad": 47.6}'   # your measured value
```

The value replaces the group's whole map, so list every interface you have measured. It is
stored as settings metadata and never sent to Hyprland. Trackpad Plus itself never
reads input devices or runs as root.

## What a curve cannot copy

- **Stroke starts.** macOS gives the first event after a pause near-minimum gain. libinput's
  custom profile times it from the previous stroke, or assumes 7 ms after a second of idle
  ([`custom_accelerator_restart`](https://sources.debian.org/src/libinput/1.31.3-1/src/filter-custom.c/#L267)
  is a no-op), so a landing finger can jump. This needs a libinput fix upstream.
- **Count quantisation.** Apple's 400-per-inch counts make the slowest gains slightly
  stepped; libinput sees about 6× finer motion, so the converter matches the average.
- **Latency and smoothing** before the accelerator differ between the two stacks.

## Credits

[iam4x/omarchy-macbookpro-m1-trackpad](https://github.com/iam4x/omarchy-macbookpro-m1-trackpad)
first carried Apple's curve to Hyprland on this laptop; it treats the curve's input as true
inches per second, which runs up to 65 % fast at everyday speeds.
[ReneXiong/macos-trackpad-libinput](https://github.com/ReneXiong/macos-trackpad-libinput)
applied the 67/120 rescaling. This implementation is independent and follows Apple's and
libinput's sources above.
