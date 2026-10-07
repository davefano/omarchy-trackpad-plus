#!/usr/bin/env python3
"""Export this Mac's trackpad acceleration as a Trackpad Plus pointer profile.

Run on macOS with the system python3; no permissions or extra packages are needed:

    python3 tools/macos/export-profile.py            # writes <model>.json
    python3 tools/macos/export-profile.py --list     # shows the trackpads that can be exported
    python3 tools/macos/export-profile.py --preview PROFILE --units-per-mm 98.65 --hyprland-scale 2

The profile copies Apple's own curves from `ioreg` (HIDAccelCurves) and the current tracking
speed. See tools/macos/README.md for the math and the 30-second check (probe.swift).
"""
import argparse
import datetime
import json
import math
from pathlib import Path
import plistlib
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pointer_profiles as pp  # noqa: E402

CURVE_KEYS = {'index': 'HIDAccelIndex', 'linear': 'HIDAccelGainLinear', 'parabolic': 'HIDAccelGainParabolic',
              'cubic': 'HIDAccelGainCubic', 'quartic': 'HIDAccelGainQuartic',
              'tangent_linear': 'HIDAccelTangentSpeedLinear',
              'tangent_root': 'HIDAccelTangentSpeedParabolicRoot'}
DISPLAY_SCRIPT = '''
ObjC.import("AppKit"); ObjC.import("CoreGraphics");
var screens = $.NSScreen.screens, out = [];
for (var i = 0; i < screens.count; i++) {
  var screen = screens.objectAtIndex(i);
  var id = screen.deviceDescription.objectForKey("NSScreenNumber").unsignedIntValue;
  out.push({builtin: !!$.CGDisplayIsBuiltin(id), points: screen.frame.size.width,
            mm: $.CGDisplayScreenSize(id).width});
}
JSON.stringify(out);
'''
PREVIEW_SPEEDS = [2, 5, 10, 20, 50, 100, 200, 300, 500, 800]
CHECK_BANDS = [(8, 15), (15, 30), (30, 64), (64, 127), (127, 254), (254, 508)]  # finger mm/s
MIN_WINDOWS = 50  # steady 7-event windows a band needs before it counts toward the gate


def run(*command):
    return subprocess.run(command, check=True, capture_output=True, timeout=30).stdout


def ioreg(*arguments):
    raw = run('ioreg', '-a', '-r', *arguments, '-d', '1')
    return plistlib.loads(raw) if raw.strip() else []


def trackpads():
    """Multitouch devices that publish parametric curves, built-in first."""
    devices = [d for d in ioreg('-c', 'AppleMultitouchDevice') if d.get('HIDAccelCurves')]
    return sorted(devices, key=lambda d: not d.get('MT Built-In'))


def tracking_speed():
    """The live HIDTrackpadAcceleration (16.16) that the HID event system uses."""
    for service in ioreg('-k', 'HIDEventServiceProperties'):
        value = (service.get('HIDEventServiceProperties') or {}).get('HIDTrackpadAcceleration')
        if isinstance(value, int):
            return value / pp.FIXED
    scaling = float(run('defaults', 'read', '-g', 'com.apple.trackpad.scaling'))
    return round(scaling * pp.FIXED) / pp.FIXED


def builtin_display():
    screens = json.loads(run('osascript', '-l', 'JavaScript', '-e', DISPLAY_SCRIPT))
    screen = next((s for s in screens if s['builtin']), None)
    if screen is None:
        raise SystemExit('No built-in display found; open the lid and try again.')
    panels = json.loads(run('system_profiler', 'SPDisplaysDataType', '-json'))['SPDisplaysDataType']
    pixels = next(int(d['_spdisplays_pixels'].split(' x ')[0]) for gpu in panels
                  for d in gpu.get('spdisplays_ndrvs', [])
                  if d.get('spdisplays_connection_type') == 'spdisplays_internal')
    return {'points_wide': round(screen['points'], 3), 'pixels_wide': pixels,
            'width_mm': round(screen['mm'], 2)}


def export(device):
    speed = tracking_speed()
    if speed < 0:
        raise SystemExit('Trackpad acceleration is turned off on this Mac; there is no curve to export.')
    hardware = json.loads(run('system_profiler', 'SPHardwareDataType', '-json'))['SPHardwareDataType'][0]
    chip = hardware.get('chip_type', '').removeprefix('Apple ')
    # The tracking speed is only the default; Trackpad Plus offers the same slider.
    name = f"{hardware.get('machine_name', 'Mac')} ({chip})" if chip else hardware.get('machine_name', 'Mac')
    resolution = device['HIDPointerResolution'] / pp.FIXED
    rate = device['HIDPointerReportRate']
    version = run('sw_vers', '-productVersion').decode().strip()
    build = run('sw_vers', '-buildVersion').decode().strip()
    profile = {
        'format': pp.FORMAT, 'version': 1, 'kind': 'apple-parametric', 'name': name[:80],
        'tracking_speed': speed,
        # Keep the device's order: IOHIDFamily walks the array as stored.
        'curves': [{key: int(curve.get(field, 0)) for key, field in CURVE_KEYS.items()}
                   for curve in device['HIDAccelCurves']],
        'driver': {'resolution_dpi': resolution, 'report_rate_hz': rate, 'event_rate_hz': rate,
                   'counts_per_inch': resolution, 'deltas': 'ideal', 'verified': ''},
        'display': builtin_display(),
        'source': {'os': f'macOS {version} ({build})', 'model': hardware.get('machine_model', ''),
                   'device': device.get('Product', 'Trackpad'), 'transport': device.get('Transport', ''),
                   'exported': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'exporter': 1},
    }
    return pp.validate_profile(profile)


def preview(profile, units_per_mm, monitor):
    f = pp.apple_function(pp.apple_parameters(profile))
    result = pp.convert(profile, units_per_mm, pp.px_per_point(pp.mm_per_point(profile), monitor))
    mm_per_point = pp.mm_per_point(profile)
    print(f"{profile['name']}\n")
    print(' finger mm/s   macOS pt/mm   cursor mm per finger mm')
    for speed in PREVIEW_SPEEDS:
        points = pp.cursor_speed(f, speed / 25.4, profile['driver']) / speed
        print(f'{speed:12g}   {points:11.2f}   {points * mm_per_point:23.3f}')
    print(f"\nlibinput: {units_per_mm:g} units/mm, {result['px_per_point']:g} px per macOS point")
    print('Largest difference from macOS by finger speed (mm/s): '
          + ', '.join(f'{band} {error:.1f} %' for band, error in result['error_bands'].items()))
    print(f"\naccel_profile = \"{result['native']}\"")


def read_probe(path):
    """Pointer rows from probe.swift: (timestamp, x, y, unaccelerated x, y, fractional?)."""
    bounds, rows, touches = None, [], []
    for line in path.read_text().splitlines():
        fields = line.split(',')
        if line.startswith('# global_bounds,'):
            bounds = [float(v) for v in fields[1:5]]
        elif fields[0] == 'P':
            t, _, x, y, _, _, ux, uy, uxd, uyd = map(float, fields[1:11])
            rows.append((t, x, y, uxd, uyd, uxd != round(uxd) or uyd != round(uyd)))
        elif fields[0] == 'N':
            touches.append((float(fields[1]), int(fields[4]), float(fields[6]), float(fields[7]),
                            float(fields[8]), float(fields[9])))
    if bounds is None or len(rows) < 200:
        raise SystemExit(f'{path} has too few pointer events; record again for the full 30 seconds.')
    return bounds, rows, touches


def check_probe(profile, bounds, rows, touches):
    """Compare a recording with Apple's accelerator and the static curve libinput receives."""
    f = pp.apple_function(pp.apple_parameters(profile))
    driver = dict(profile['driver'])
    inside = lambda row: bounds[0] + 0.5 < row[1] < bounds[2] - 1 and bounds[1] + 0.5 < row[2] < bounds[3] - 1
    gaps = sorted(b[0] - a[0] for a, b in zip(rows, rows[1:]) if 0 < b[0] - a[0] < 0.03)
    if not gaps:
        raise ValueError('The probe contains no usable positive event timing; record again.')
    driver['event_rate_hz'] = round(1 / gaps[len(gaps) // 2], 1)
    fractional = any(r[5] for r in rows)
    # 1. Every event against IOHIDPointerAccelerator: the curve and its constants.
    errors = []
    for a, b in zip(rows, rows[1:]):
        if (b[3] or b[4]) and inside(a) and inside(b) and b[0] - a[0] < 0.1:
            px, py = pp.apple_event(f, b[3], b[4], (b[0] - a[0]) * 1000, driver)
            errors.append(abs(math.hypot(b[1] - a[1], b[2] - a[2]) / math.hypot(px, py) - 1) * 100)
    errors.sort()
    if not errors:
        raise ValueError('No usable movement events were recorded inside the selected display; record again there.')
    # 2. Seven-event windows of steady motion against the static curve libinput will get. The
    #    delta model that best reproduces them is kept; whole counts allow ideal or integer.
    windows = []
    for k in range(6, len(rows)):
        segment = rows[k - 6:k + 1]
        if all(0 < b[0] - a[0] < 0.012 for a, b in zip(segment, segment[1:])) and all(map(inside, segment)):
            seconds = segment[-1][0] - segment[0][0]
            counts = sum(math.hypot(r[3], r[4]) for r in segment[1:])
            moved = sum(math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(segment, segment[1:]))
            windows.append((counts / driver['counts_per_inch'] / seconds, moved / seconds))
    fits = {}
    for model in ['fractional'] if fractional else ['ideal', 'integer']:
        candidate = dict(driver, deltas=model)
        bands = []
        for low, high in CHECK_BANDS:
            ratios = sorted(measured / pp.cursor_speed(f, finger, candidate)
                            for finger, measured in windows if low <= finger * 25.4 < high)
            if ratios:
                bands.append((low, high, len(ratios), ratios[len(ratios) // 2]))
        trusted = [band for band in bands if band[2] >= MIN_WINDOWS]
        fits[model] = (sum((band[3] - 1) ** 2 for band in trusted), bands)
    driver['deltas'] = min(fits, key=lambda model: fits[model][0])
    # 3. NSTouch scale: Apple reports positions on the same count grid, in its own millimetres.
    touch_cpi = None
    if touches:
        moved = sum(math.hypot((b[2] - a[2]) * b[4], (b[3] - a[3]) * b[5]) / 72
                    for a, b in zip(touches, touches[1:]) if a[1] == b[1] == 1 and b[0] - a[0] < 0.03)
        counts = sum(math.hypot(r[3], r[4]) for r in rows)
        touch_cpi = counts / moved if moved else None
    return {'driver': driver, 'fractional': fractional, 'events': len(errors),
            'median_error': errors[len(errors) // 2],
            'within_half_percent': sum(e < 0.5 for e in errors) / len(errors),
            'bands': fits[driver['deltas']][1], 'fits': {model: fit[1] for model, fit in fits.items()},
            'touch_counts_per_inch': touch_cpi}


def report_check(result):
    driver = result['driver']
    print(f"Pointer events: {driver['event_rate_hz']} Hz; counts are "
          f"{'fractional' if result['fractional'] else 'whole numbers'}.")
    print(f"Apple's accelerator, event by event: median error {result['median_error']:.2f} % over "
          f"{result['events']} events ({result['within_half_percent']:.0%} within 0.5 %).")
    if result['touch_counts_per_inch']:
        print(f"Touch positions: {result['touch_counts_per_inch']:.1f} counts per inch on Apple's own scale.")
    print('Physical counts per inch remain an input assumption; this check does not calibrate them.')
    models = list(result['fits'])
    print('Static curve vs measured strokes, measured ÷ model (finger mm/s, windows):')
    print('              windows  ' + '  '.join(f'{model:>10}' for model in models))
    for index, (low, high, count, _) in enumerate(result['bands']):
        ratios = '  '.join(f"{result['fits'][model][index][3]:10.3f}" for model in models)
        print(f"  {low:4g}–{high:<4g} {count:7d}  {ratios}{'' if count >= MIN_WINDOWS else '   (too few to judge)'}")
    trusted = [band for band in result['bands'] if band[2] >= MIN_WINDOWS]
    worst = max((abs(ratio - 1) for *_, ratio in trusted), default=1)
    passed = result['median_error'] <= 1 and worst <= 0.03 and len(trusted) >= 3
    print(f"\n{'PASS' if passed else 'FAIL'}: accelerator ≤1 % and the {driver['deltas']} curve within ±3 % "
          f"(worst band {worst * 100:.1f} % across {len(trusted)} bands with ≥{MIN_WINDOWS} windows).")
    return passed, worst, trusted


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--list', action='store_true', help='list trackpads with parametric curves')
    parser.add_argument('--device', type=int, default=0, help='trackpad number from --list (default: built-in)')
    parser.add_argument('--output', type=Path, help='profile path (default: <model>.json)')
    parser.add_argument('--preview', type=Path, metavar='PROFILE', help='print the libinput curve for a profile')
    parser.add_argument('--units-per-mm', type=float, default=98.65,
                        help='Linux trackpad resolution (default: 98.65, MacBook Pro 14/16 M1 Pro/Max)')
    parser.add_argument('--hyprland-scale', type=float, help='Hyprland monitor scale (default: macOS scale)')
    parser.add_argument('--linux-width', type=int, help='Linux panel width in px (default: profile panel)')
    parser.add_argument('--linux-width-mm', type=float, help='Linux panel width in mm (default: profile panel)')
    parser.add_argument('--check', type=Path, metavar='PROBE_CSV', help='compare a probe.swift recording with --profile')
    parser.add_argument('--profile', type=Path, help='profile to check (and update with --write)')
    parser.add_argument('--write', action='store_true', help='store the measured driver constants in --profile')
    args = parser.parse_args()

    if args.check:
        if not args.profile:
            raise SystemExit('--check needs --profile PROFILE.json')
        profile = pp.load_profile(args.profile.read_bytes())
        try:
            result = check_probe(profile, *read_probe(args.check))
        except ValueError as error:
            raise SystemExit(str(error)) from error
        passed, worst, trusted = report_check(result)
        if args.write and passed:
            date = datetime.date.today().isoformat()
            profile['driver'] = dict(result['driver'], verified=(
                f"probe {date}: accelerator median error {result['median_error']:.2f} %, "
                f"curve within ±{worst * 100:.1f} % at {trusted[0][0]:g}–{trusted[-1][1]:g} mm/s"))
            args.profile.write_text(json.dumps(pp.validate_profile(profile), indent=2, ensure_ascii=False) + '\n')
            print(f'Updated {args.profile}.')
        elif args.write:
            raise SystemExit('Not updating the profile because the check failed.')
        return

    if args.preview:
        profile = pp.load_profile(args.preview.read_bytes())
        display = profile['display']
        monitor = {'width': args.linux_width or display['pixels_wide'],
                   'scale': args.hyprland_scale or display['pixels_wide'] / display['points_wide'],
                   'physicalWidth': args.linux_width_mm or display['width_mm']}
        preview(profile, args.units_per_mm, monitor)
        return

    devices = trackpads()
    if not devices:
        raise SystemExit('No trackpad with parametric acceleration curves (HIDAccelCurves) was found.')
    if args.list:
        for number, device in enumerate(devices):
            kind = 'built-in' if device.get('MT Built-In') else device.get('Transport', 'external')
            print(f"{number}: {device.get('Product', 'Trackpad')} ({kind}), "
                  f"{len(device['HIDAccelCurves'])} curves")
        return
    if not 0 <= args.device < len(devices):
        raise SystemExit(f'Choose --device 0 to {len(devices) - 1}; see --list.')
    profile = export(devices[args.device])
    stem = re.sub(r'[^A-Za-z0-9.]+', '-', profile['source']['model'] or 'mac')
    output = args.output or Path(f'{stem}.json')
    output.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + '\n')
    print(f"Wrote {output} — {profile['name']}, {len(profile['curves'])} curves.")
    print('Copy it to ~/.config/trackpad-plus/profiles/ on Omarchy and choose it under Pointer feel → macOS.')


if __name__ == '__main__':
    main()
