#!/usr/bin/env python3
"""Per-device settings and native pointer curves for Trackpad Plus."""
from contextlib import contextmanager
import copy
import ctypes
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import secrets
import selectors
import stat
import time

import pointer_profiles

STATE_ROOT = Path(os.environ.get('XDG_STATE_HOME') or Path.home().resolve() / '.local/state')
DIRECTORY = STATE_ROOT / 'omarchy/local-touchpads'
STATE = DIRECTORY / 'settings.json'
GENERATED = STATE_ROOT / 'omarchy/toggles/hypr/zz-local-touchpads.lua'
PROFILES = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'trackpad-plus/profiles'
MACHINE_MODEL = Path('/sys/firmware/devicetree/base/model')
SCHEMA = 6
BOOLS = {'enabled', 'natural_scroll', 'tap_to_click', 'disable_while_typing', 'clickfinger_behavior'}
RANGES = {'sensitivity': (-1, 1), 'scroll_factor': (0.001, 10), 'scroll_scale': (0.1, 10)}
DEFAULT_CURVE = {'precision': 0.3, 'start': 0.8, 'end': 2.8, 'fast': 1.6}
# Slow two-finger motion stays near 1:1; faster flicks gain more distance, like macOS.
DEFAULT_SCROLL_CURVE = {'precision': 1.0, 'start': 0.5, 'end': 2.2, 'fast': 2.0}
IDENTITY_SCROLL = '1 0 1'
MAX_STATE_BYTES = 1024 * 1024
BUILTIN_APPLE = {'apple-mtp-multi-touch', 'apple-spi-trackpad', 'apple-spi-touchpad',
                 'bcm5974', 'apple-inc.-apple-internal-keyboard-/-trackpad-1'}
LEGACY_APPLE = BUILTIN_APPLE - {'apple-mtp-multi-touch'}
CURVE_RANGES = {'precision': (0.01, 10), 'start': (0, 3.8), 'end': (0.2, 4), 'fast': (0.01, 10)}
# Trackpad resolution in units/mm by compositor name and devicetree model. libinput's custom
# profile counts raw units, so a macOS profile needs it, and reading it from the kernel takes
# device access this plugin never has. MacBook Pro 14": 12312 units over 124.80 mm (Asahi).
KNOWN_RESOLUTIONS = {
    ('apple-spi-trackpad', 'Apple MacBook Pro (14-inch, M1 Pro, 2021)'): 12312 / 124.8,
    ('apple-spi-trackpad', 'Apple MacBook Pro (14-inch, M1 Max, 2021)'): 12312 / 124.8,
}
# Settings stored for the editor or conversion; they are never emitted as Hyprland options.
METADATA = {'curve', 'curve_preset', 'scroll_scale', 'scroll_progressive', 'scroll_curve', 'scroll_curve_preset', 'imported_curve', 'units_per_mm'}
IMPORTED_KEYS = {'name', 'file', 'sha256', 'tracking_speed', 'mm_per_point', 'px_per_point', 'devices'}
PROFILE_FILE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._ ,+-]{0,123}\.json')
MAX_PROFILES = 32


def validate_curve(value):
    if not isinstance(value, dict) or set(value) != set(CURVE_RANGES):
        raise ValueError('Expected precision, start, end, and fast curve controls')
    for key, (low, high) in CURVE_RANGES.items():
        n = value[key]
        if type(n) not in (int, float) or not math.isfinite(n) or not low <= n <= high:
            raise ValueError('Curve control is outside its allowed range')
    if value['end'] - value['start'] < 0.2 - 1e-9:
        raise ValueError('Acceleration end must be at least 0.2 above its start')
    if value['fast'] < value['precision']:
        raise ValueError('Fast movement must not be slower than precision movement')
    return value


def preset_for_scale(maximum, base=None):
    """Keep Curve.js presetForScale / scrollPresetForScale in sync with this."""
    curve = dict(base or DEFAULT_CURVE)
    factor = min(1.0, float(maximum) / curve['fast'])
    curve['precision'] = max(0.01, round(curve['precision'] * factor, 6))
    curve['fast'] = round(curve['fast'] * factor, 6)
    if curve['fast'] < curve['precision']:
        curve['fast'] = curve['precision']
    return validate_curve(curve)


def curve_profile(curve, resolution=None):
    """Sample output velocity, not gain; libinput linearly interpolates these points.

    Two samples beyond the visible end (4.0) keep extrapolation at constant gain.
    Keep this function in sync with Curve.js; the cross-language test compares both.

    The curve is defined in libinput's normalized units (1000 dpi). libinput's
    custom profile skips that normalization on touchpads and feeds raw device
    units, so a 96 units/mm sensor would move ~2.4x faster than intended. Scaling
    the sample spacing by resolution / (1000 / 25.4) and keeping the output
    samples unchanged makes the curve identical in normalized units.
    """
    validate_curve(curve)
    points = []
    for index in range(43):
        x = index * 0.1
        t = max(0, min(1, (x - curve['start']) / (curve['end'] - curve['start'])))
        gain = curve['precision'] + (curve['fast'] - curve['precision']) * t * t * (3 - 2 * t)
        points.append(f'{x * gain:.6f}')
    step = 0.1 if resolution is None else 0.1 * resolution / NORMALIZED_UNITS_PER_MM
    return f'custom {step:.6g} ' + ' '.join(points)


NORMALIZED_UNITS_PER_MM = 1000 / 25.4
SYSFS_INPUT = Path('/sys/class/input')
UDEV_DATA = Path('/run/udev/data')
# Kernel X resolutions (units/mm) for Apple USB/Bluetooth trackpads, keyed by
# product ID. hid-magicmouse sets them without a udev override:
# Magic Trackpad = (3167 + 2909) / 130, Magic Trackpad 2 = (3934 + 3678) / 160.
KERNEL_RESOLUTIONS = {(vendor, product): resolution
                      for vendor in ('05ac', '004c')
                      for product, resolution in (('030e', 46), ('0265', 47), ('0324', 47))}


def device_resolution(name):
    """Resolve only a unique exact evdev name; never strip compositor suffixes.

    A native name may itself end in digits. Hyprland's added duplicate -N
    suffix does not identify a sysfs node and must not match the base name.
    """
    matches = []
    for event in SYSFS_INPUT.glob('event*'):
        try:
            if (event / 'device/name').read_text().strip().lower().replace(' ', '-') == name:
                matches.append(event)
        except OSError:
            continue
    if len(matches) != 1:
        return None
    event = matches[0]
    try:
        udev = UDEV_DATA / ('c' + (event / 'dev').read_text().strip())
        if udev.exists():
            for line in udev.read_text().splitlines():
                match = re.fullmatch(r'E:EVDEV_ABS_00=[^:]*:[^:]*:([0-9]+)(:.*)?', line)
                if match and 0 < int(match.group(1)) <= 65535:
                    return int(match.group(1))
        ids = tuple((event / 'device/id' / key).read_text().strip().lower()
                    for key in ('vendor', 'product'))
        return KERNEL_RESOLUTIONS.get(ids)
    except OSError:
        return None


def validate_calibration(value, names=None):
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError('Invalid curve calibration')
    for name, resolution in value.items():
        validate_name(name)
        if names is not None and name not in names:
            raise ValueError('Curve calibration must belong to a saved interface')
        if type(resolution) not in (int, float) or not math.isfinite(resolution) or not 0 < resolution <= 65535:
            raise ValueError('Invalid curve sensor resolution')
    return value


def scroll_profile(curve):
    """Hyprland scroll_points is '<step> <points...>' without a custom prefix."""
    return curve_profile(curve).removeprefix('custom ')


def hypr(*args):
    """Bound both runtime and output; kill and reap failed compositor requests."""
    command = ['hyprctl', *args]
    deadline = time.monotonic() + 4
    output = [bytearray(), bytearray()]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            with selectors.DefaultSelector() as streams:
                streams.register(process.stdout, selectors.EVENT_READ, 0)
                streams.register(process.stderr, selectors.EVENT_READ, 1)
                while streams.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, 4)
                    for event, _ in streams.select(remaining):
                        chunk = os.read(event.fd, 65536)
                        if not chunk:
                            streams.unregister(event.fileobj)
                        else:
                            output[event.data].extend(chunk)
                            if sum(map(len, output)) > MAX_STATE_BYTES:
                                raise ValueError('Hyprland response exceeds the 1 MiB limit')
                code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            stdout, stderr = (part.decode('utf-8', errors='replace') for part in output)
            if code != 0 or (args[0] in ('eval', 'reload') and stdout.strip() != 'ok'):
                raise RuntimeError((stderr.strip() or stdout.strip() or 'Hyprland rejected settings')[:2048])
            return stdout
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def validate_native_profile(profile, scroll_points=None):
    """Check libinput itself: Hyprland 0.56 ignores set_points' error status.

    This creates a configuration object only; it does not open input devices or
    require elevated permissions. In particular, libinput rejects >64 points.
    """
    lib = ctypes.CDLL('libinput.so.10')
    lib.libinput_config_accel_create.argtypes = [ctypes.c_int]
    lib.libinput_config_accel_create.restype = ctypes.c_void_p
    lib.libinput_config_accel_set_points.argtypes = [ctypes.c_void_p, ctypes.c_int,
        ctypes.c_double, ctypes.c_size_t, ctypes.POINTER(ctypes.c_double)]
    lib.libinput_config_accel_set_points.restype = ctypes.c_int
    lib.libinput_config_accel_destroy.argtypes = [ctypes.c_void_p]
    lib.libinput_config_accel_destroy.restype = None
    config = lib.libinput_config_accel_create(4)  # LIBINPUT_CONFIG_ACCEL_PROFILE_CUSTOM
    if not config:
        raise RuntimeError('libinput could not create a custom acceleration profile')
    try:
        fields = profile.split()
        step = float(fields[1])
        values = list(map(float, fields[2:]))
        if scroll_points:
            scroll_fields = scroll_points.split()
            scroll_step = float(scroll_fields[0])
            scroll_values = list(map(float, scroll_fields[1:]))
        else:
            scroll_step, scroll_values = 1.0, [0.0, 1.0]
        for motion_type, spacing, points in [(1, step, values), (2, scroll_step, scroll_values)]:
            native_points = (ctypes.c_double * len(points))(*points)
            status = lib.libinput_config_accel_set_points(config, motion_type, spacing, len(points), native_points)
            if status != 0:
                raise ValueError(f'libinput rejected the acceleration curve (status {status}, {len(points)} points)')
    finally:
        lib.libinput_config_accel_destroy(config)


def validate_native_curve(curve, scroll_curve=None):
    validate_native_profile(curve_profile(curve),
                            scroll_profile(scroll_curve) if scroll_curve is not None else None)


def imported_profile(device):
    """The native string for one interface of a materialized macOS profile."""
    return f"custom {device['step']:.4f} " + ' '.join(f'{point:.6f}' for point in device['points'])


def validate_imported(value):
    """A macOS profile converted for this group's interfaces: bounded literals only."""
    if not isinstance(value, dict) or set(value) != IMPORTED_KEYS:
        raise ValueError('Invalid imported pointer profile')
    pointer_profiles.text(value['name'], 80)
    if not isinstance(value['file'], str) or not PROFILE_FILE.fullmatch(value['file']):
        raise ValueError('Invalid pointer profile file name')
    if not isinstance(value['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['sha256']):
        raise ValueError('Invalid pointer profile digest')
    pointer_profiles.number(value['tracking_speed'], 0, 3)
    pointer_profiles.number(value['mm_per_point'], 0.01, 10)
    pointer_profiles.number(value['px_per_point'], 0.01, 100)
    devices = value['devices']
    if not isinstance(devices, dict) or not 1 <= len(devices) <= 32:
        raise ValueError('Invalid imported pointer profile devices')
    for name, device in devices.items():
        validate_name(name)
        if not isinstance(device, dict) or set(device) != {'units_per_mm', 'step', 'points'}:
            raise ValueError('Invalid imported pointer curve')
        pointer_profiles.number(device['units_per_mm'], 1, 10000)
        pointer_profiles.number(device['step'], 0.0001, 10000)
        points = device['points']
        if not isinstance(points, list) or not 2 <= len(points) <= 64:
            raise ValueError('Imported pointer curves need 2 to 64 points')
        for point in points:
            pointer_profiles.number(point, 0, 10000)
        if points != sorted(points):
            raise ValueError('Imported pointer curves must not slow down as speed rises')
    return value


def validate_units(value):
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError('Expected trackpad resolutions by device name')
    for name, units in value.items():
        validate_name(name)
        pointer_profiles.number(units, 1, 10000)
    return value


def validate_native_settings(settings, calibration=None):
    """Validate pointer and scroll points in the combinations emitted to libinput."""
    if settings.get('accel_profile') != 'custom':
        return
    scroll = scroll_profile(settings.get('scroll_curve', DEFAULT_SCROLL_CURVE)) \
        if settings.get('scroll_progressive') else None
    if imported_active(settings):
        for device in settings['imported_curve']['devices'].values():
            validate_native_profile(imported_profile(device), scroll)
    else:
        validate_native_curve(settings.get('curve', DEFAULT_CURVE),
                              settings.get('scroll_curve', DEFAULT_SCROLL_CURVE) if scroll else None)
        for resolution in set((calibration or {}).values()):
            validate_native_profile(curve_profile(settings.get('curve', DEFAULT_CURVE), resolution), scroll)


def validate_name(name):
    # PS/2 Synaptics touchpads (common on ThinkPads, e.g. "synps/2-synaptics-touchpad")
    # include a literal '/' in their Hyprland device name; allow it alongside the
    # original safe alphabet.
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.:/+-]{1,128}', name):
        raise ValueError('Unsupported trackpad device name')
    return name


def validate_setting(key, value):
    if key == 'accel_profile':
        if value not in ('adaptive', 'flat', 'custom'):
            raise ValueError('Expected adaptive, flat, or custom acceleration')
    elif key == 'curve':
        validate_curve(value)
    elif key == 'curve_preset':
        if value not in ('mac', 'custom', 'imported'):
            raise ValueError('Unknown curve preset')
    elif key == 'imported_curve':
        validate_imported(value)
    elif key == 'units_per_mm':
        validate_units(value)
    elif key == 'scroll_progressive':
        if type(value) is not bool:
            raise ValueError('Expected a boolean')
    elif key == 'scroll_curve':
        validate_curve(value)
    elif key == 'scroll_curve_preset':
        if value not in ('mac', 'custom'):
            raise ValueError('Unknown scroll curve preset')
    elif key in BOOLS:
        if type(value) is not bool:
            raise ValueError('Expected a boolean')
    elif key in RANGES:
        low, high = RANGES[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('Setting is outside its allowed range')
    else:
        raise ValueError('Unknown setting')
    return value


def group_devices(mice):
    groups = {}
    for mouse in mice:
        name = mouse['name']
        is_builtin_apple = name in BUILTIN_APPLE
        # Lenovo Synaptics touchpads report a part number instead of a device
        # type, e.g. 'synaptics-tm3381-002' on the X280 or 'synaptics-tm3512-010'
        # elsewhere. Matching the whole name keeps a suffixed TrackPoint name out.
        is_known_touchpad = is_builtin_apple or re.fullmatch(r'synaptics-tm[0-9]{4}-[0-9]{3}', name) is not None
        if not is_known_touchpad and not re.search('touchpad|trackpad', name, re.I):
            continue
        validate_name(name)
        if is_builtin_apple or name.startswith('apple-inc.-magic-trackpad'):
            key, label = 'apple', 'Apple'
        elif name == 'ven_06cb:00-06cb:d01d-touchpad':
            key, label = 'dell', 'Dell'
        else:
            key, label = name, name
        groups.setdefault(key, {'id': key, 'label': label, 'names': []})['names'].append(name)
    return groups


def lua_for(groups):
    # hyprctl interprets an argument starting with '--' as a CLI flag.
    lines = ['do -- Managed by davefano.trackpad-plus. Change settings in Trackpad Plus.']
    for group in groups.values():
        if not group.get('configured', True):
            continue
        for name in group['names']:
            validate_name(name)
            fields = []
            for key, value in sorted(group['settings'].items()):
                validate_setting(key, value)
                if key in METADATA:
                    continue  # Editor metadata is never emitted as a Hyprland option.
                if key == 'accel_profile' and value == 'custom':
                    # Per device: one group can mix sensors of different resolutions.
                    settings = group['settings']
                    if imported_active(settings):
                        device = settings['imported_curve']['devices'].get(name)
                        if device is None:
                            continue  # New interfaces retain their native pointer profile.
                        value = imported_profile(device)
                    else:
                        value = curve_profile(settings.get('curve', DEFAULT_CURVE),
                                              group.get('curve_calibration', {}).get(name))
                fields.append(f'{key} = {json.dumps(value)}')
            if group['settings'].get('accel_profile') == 'custom' and (
                    not imported_active(group['settings'])
                    or name in group['settings']['imported_curve']['devices']):
                scroll = group['settings']
                points = scroll_profile(scroll.get('scroll_curve', DEFAULT_SCROLL_CURVE)) if scroll.get('scroll_progressive') else IDENTITY_SCROLL
                fields.append('scroll_points = ' + json.dumps(points))
            lines.append('hl.device({ name = ' + json.dumps(name) + ', ' + ', '.join(fields) + ' })')
    return '\n'.join(lines + ['end']) + '\n'


@contextmanager
def state_directory(path, create=False):
    """Pin each directory with a descriptor; never follow state-path symlinks."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('State paths must be absolute and contain no parent traversal')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            shared_sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
            if info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not shared_sticky):
                raise ValueError('State directories must not be writable by other users')
        if os.fstat(fd).st_mode & 0o022:
            raise ValueError('State directory must not be shared')
        yield fd
    finally:
        os.close(fd)


def check_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o022):
        raise ValueError('State must be a regular, privately writable file owned by this user')


def read_state_file(path):
    try:
        with state_directory(path.parent) as directory:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as stream:
                check_file(os.fstat(stream.fileno()))
                raw = stream.read(MAX_STATE_BYTES + 1)
                if len(raw) > MAX_STATE_BYTES:
                    raise ValueError('State file exceeds the 1 MiB limit')
                return raw.decode('utf-8')
    except FileNotFoundError:
        return None


def config_target(path, label='Hyprland configuration'):
    """Resolve config links through trusted directories; state stays no-follow.

    Stow may link a file or an entire directory. Resolve each link explicitly
    under the same directory ownership checks used for state, then let the
    no-follow reader/writer validate and access the final target.
    """
    path = Path(path)
    if not path.is_absolute():
        raise ValueError(f'{label} paths must be absolute')
    pending = list(path.parts[1:])
    resolved, links = Path('/'), 0
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        while pending:
            part = pending.pop(0)
            if part == '..':
                child = os.open('..', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
                resolved = resolved.parent
                continue
            info = os.stat(part, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                links += 1
                if links > 40:
                    raise ValueError(f'{label} has a symlink loop or too many links')
                if info.st_uid not in (0, os.getuid()):
                    raise ValueError(f'{label} link is owned by another user')
                target = Path(os.readlink(part, dir_fd=directory))
                if target.is_absolute():
                    child = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
                    os.close(directory)
                    directory = child
                    resolved = Path('/')
                    pending = list(target.parts[1:]) + pending
                else:
                    pending = list(target.parts) + pending
                continue
            if pending or stat.S_ISDIR(info.st_mode):
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
                info = os.fstat(directory)
                shared_sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
                if info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not shared_sticky):
                    raise ValueError(f'{label} directories must not be writable by other users')
            resolved /= part
    finally:
        os.close(directory)
    return resolved


def profile_files():
    """Profile names in the user's profiles directory, which may be a Stow link."""
    try:
        directory = config_target(PROFILES, 'Pointer profile')
        with state_directory(directory) as descriptor:
            names = os.listdir(descriptor)
    except FileNotFoundError:
        return []
    return sorted(name for name in names if PROFILE_FILE.fullmatch(name))[:MAX_PROFILES]


def read_profile(name):
    """Raw bytes of one profile; the file itself must pass the private-file checks."""
    if not isinstance(name, str) or not PROFILE_FILE.fullmatch(name):
        raise ValueError('Invalid pointer profile file name')
    try:
        raw = read_state_file(config_target(PROFILES / name, 'Pointer profile'))
    except FileNotFoundError:
        raw = None
    if raw is None:
        raise ValueError(f'Pointer profile {name} was not found')
    return raw.encode('utf-8')


def machine_model():
    try:
        with open(MACHINE_MODEL, 'rb') as stream:
            return stream.read(256).decode('utf-8', 'replace').strip('\0').strip()
    except OSError:
        return ''


def interface_units(group, name, model):
    """Use an explicit value, verified model, exact measured sensor, or saved calibration."""
    override = group['settings'].get('units_per_mm', {}).get(name)
    if override is not None:
        return override, 'setting'
    known = KNOWN_RESOLUTIONS.get((name, model))
    if known:
        return known, 'built-in'
    measured = device_resolution(name)
    if measured is not None:
        return measured, 'measured'
    saved = group.get('curve_calibration', {}).get(name)
    return (saved, 'saved') if saved is not None else (None, None)


def panel_monitor():
    """The built-in panel (eDP), else the focused monitor, from Hyprland."""
    monitors = json.loads(hypr('monitors', '-j'))
    if not isinstance(monitors, list) or not monitors:
        raise ValueError('Hyprland reported no monitors')
    monitor = next((m for m in monitors if str(m.get('name', '')).startswith('eDP')), None) \
        or next((m for m in monitors if m.get('focused')), monitors[0])
    return {key: monitor.get(key) for key in ('name', 'width', 'scale', 'physicalWidth')}


def profile_scale(millimetres, monitor):
    """Logical px per macOS point on this panel; 1 when Hyprland cannot report its width."""
    if not monitor.get('physicalWidth'):
        return 1.0
    return pointer_profiles.px_per_point(millimetres, monitor)


def import_profile(group, reference, monitor=None):
    """Convert a previewed profile file for every interface in the group."""
    raw = read_profile(reference['file'])
    if pointer_profiles.digest(raw) != reference['sha256']:
        raise ValueError('The pointer profile changed after it was previewed; choose it again')
    profile = pointer_profiles.load_profile(raw)
    if 'tracking_speed' in reference:
        # The macOS Tracking speed slider: Apple interpolates its own curves for any value.
        profile = dict(profile, tracking_speed=reference['tracking_speed'])
    millimetres = pointer_profiles.mm_per_point(profile)
    scale = profile_scale(millimetres, monitor or panel_monitor())
    model = machine_model()
    devices, missing = {}, []
    for name in group['names']:
        units, _ = interface_units(group, name, model)
        if units is None:
            missing.append(name)
            continue
        result = pointer_profiles.convert(profile, units, scale)
        devices[name] = {'units_per_mm': units, 'step': result['step'], 'points': result['points']}
    if missing:
        raise ValueError('Trackpad resolution is unknown for ' + ', '.join(missing)
                         + '; set its units_per_mm (see tools/macos/README.md)')
    return validate_imported({
        'name': profile['name'], 'file': reference['file'], 'sha256': reference['sha256'],
        'tracking_speed': profile['tracking_speed'], 'mm_per_point': round(millimetres, 6),
        'px_per_point': round(scale, 6), 'devices': devices})


def list_profiles(group):
    """Profiles in the profiles directory; each must convert for this group's first known interface."""
    model = machine_model()
    interfaces = {}
    for name in group['names']:
        units, source = interface_units(group, name, model)
        interfaces[name] = {'units_per_mm': units, 'source': source}
    primary = next((name for name in group['names'] if interfaces[name]['units_per_mm']), None)
    try:
        monitor = panel_monitor()
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        monitor = {'error': str(exc)[:200]}
    rows = []
    for name in profile_files():
        row = {'file': name}
        try:
            raw = read_profile(name)
            profile = pointer_profiles.load_profile(raw)
            if primary and 'error' not in monitor:
                # Refuse a profile here, not at Apply, if it cannot fit libinput's custom range.
                pointer_profiles.convert(profile, interfaces[primary]['units_per_mm'],
                                         profile_scale(pointer_profiles.mm_per_point(profile), monitor))
            # The tracking speeds are Apple's notches: the macOS slider's stops.
            row.update(name=profile['name'], sha256=pointer_profiles.digest(raw),
                       tracking_speed=profile['tracking_speed'],
                       speeds=[curve['index'] / pointer_profiles.FIXED for curve in profile['curves']])
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            row['error'] = str(exc)[:200]
        rows.append(row)
    return {'directory': str(PROFILES), 'profiles': rows,
            'context': {'interfaces': interfaces, 'monitor': monitor}}


def atomic_write(path, content):
    encoded = content.encode('utf-8')
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError('State file exceeds the 1 MiB limit')
    with state_directory(path.parent, create=True) as directory:
        try:
            check_file(os.stat(path.name, dir_fd=directory, follow_symlinks=False))
        except FileNotFoundError:
            pass
        temp = '.' + path.name + '.' + secrets.token_hex(12)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temp, dir_fd=directory)
            except FileNotFoundError:
                pass


def clear_pending():
    with state_directory(STATE.parent) as directory:
        os.unlink(STATE.with_suffix('.pending.json').name, dir_fd=directory)
        os.fsync(directory)


@contextmanager
def state_lock():
    with state_directory(DIRECTORY, create=True) as directory:
        fd = os.open('settings.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
    with os.fdopen(fd, 'r+') as lock:
        check_file(os.fstat(lock.fileno()))
        deadline = time.monotonic() + 2
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Trackpad settings are busy; try again')
                time.sleep(0.025)
        yield


def validate_state(state):
    if not isinstance(state, dict) or set(state) != {'version', 'devices'}:
        raise ValueError('Invalid trackpad state structure')
    if type(state['version']) is not int or not 1 <= state['version'] <= SCHEMA:
        raise ValueError('Unsupported trackpad state version; saved settings were not changed')
    devices = state['devices']
    if not isinstance(devices, dict) or len(devices) > 128:
        raise ValueError('Invalid trackpad device list')
    all_names = set()
    for key, group in devices.items():
        validate_name(key)
        if not isinstance(group, dict) or set(group) - {'id', 'label', 'names', 'settings', 'previous_pointer_feel', 'previous_scroll_feel', 'configured', 'curve_calibration'}:
            raise ValueError('Invalid trackpad group')
        if group.get('id') != key or not isinstance(group.get('label'), str) or not 1 <= len(group['label']) <= 128:
            raise ValueError('Invalid trackpad identity')
        if 'configured' in group and type(group['configured']) is not bool:
            raise ValueError('Invalid trackpad configuration status')
        names = group.get('names')
        if not isinstance(names, list) or not 1 <= len(names) <= 32:
            raise ValueError('Invalid trackpad names')
        for name in names:
            validate_name(name)
            if name in all_names:
                raise ValueError('Duplicate trackpad name')
            all_names.add(name)
        settings = group.get('settings')
        if not isinstance(settings, dict) or not (BOOLS | {'sensitivity', 'scroll_factor'}) <= set(settings):
            raise ValueError('Missing trackpad settings')
        for option, value in settings.items():
            validate_setting(option, value)
        if (settings.get('curve_preset') == 'imported') != ('imported_curve' in settings):
            raise ValueError('An imported pointer profile needs its converted curve')
        if 'imported_curve' in settings and not set(settings['imported_curve']['devices']) <= set(names):
            raise ValueError('Imported pointer curves must belong to saved interfaces')
        if 'units_per_mm' in settings and not set(settings['units_per_mm']) <= set(names):
            raise ValueError('Trackpad resolutions must belong to saved interfaces')
        scale = settings.get('scroll_scale', max(1, settings['scroll_factor']))
        normalized = settings['scroll_factor'] / scale
        if not 0.01 - 1e-9 <= normalized <= 1 + 1e-9:
            raise ValueError('Scroll speed must be between 0.01 and 1.00 of the device scale')
        validate_calibration(group.get('curve_calibration', {}), names)
        if 'previous_pointer_feel' in group:
            validate_change('pointer_feel', group['previous_pointer_feel'])
            previous = group['previous_pointer_feel']
            validate_calibration(previous.get('calibration', {}), names)
            if previous['profile'] == 'imported':
                validate_imported(previous['imported'])
                if not set(previous['imported']['devices']) <= set(names):
                    raise ValueError('Previous imported pointer curves must belong to saved interfaces')
        if 'previous_scroll_feel' in group:
            validate_change('scroll_feel', group['previous_scroll_feel'])
    return state


def validate_change(option, value):
    if option == 'scroll_feel':
        if not isinstance(value, dict) or set(value) != {'profile', 'curve'}:
            raise ValueError('Expected a scroll profile and curve')
        if value['profile'] not in ('mac', 'custom'):
            raise ValueError('Unknown scroll profile')
        validate_curve(value['curve'])
        return
    if option not in ('pointer_feel', 'pointer_restore'):
        validate_setting(option, value)
        return
    if not isinstance(value, dict) or not {'profile', 'curve'} <= set(value) or set(value) - {'profile', 'curve', 'calibration', 'imported'}:
        raise ValueError('Expected a pointer profile and curve')
    if value['profile'] not in ('adaptive', 'flat', 'mac', 'custom', 'imported'):
        raise ValueError('Unknown pointer profile')
    validate_curve(value['curve'])
    if value['profile'] == 'imported':
        reference = value.get('imported')
        if isinstance(reference, dict) and set(reference) in ({'file', 'sha256'}, {'file', 'sha256', 'tracking_speed'}):
            if not isinstance(reference['file'], str) or not PROFILE_FILE.fullmatch(reference['file']) \
                    or not isinstance(reference['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', reference['sha256']):
                raise ValueError('Invalid pointer profile reference')
            if 'tracking_speed' in reference:
                pointer_profiles.number(reference['tracking_speed'], 0, 3)
        else:
            validate_imported(reference)
    elif 'imported' in value:
        raise ValueError('Only imported pointer profiles can contain imported curves')
    if 'calibration' in value:
        validate_calibration(value['calibration'])


def recover_pending():
    """An uncleared journal means the last edit did not finish successfully."""
    raw = read_state_file(STATE.with_suffix('.pending.json'))
    if raw is None:
        return
    pending = json.loads(raw)
    if not isinstance(pending, dict) or set(pending) != {'state', 'device'}:
        raise ValueError('Invalid trackpad recovery journal')
    previous = validate_state(pending['state'])
    key = pending['device']
    if not isinstance(key, str) or key not in previous['devices']:
        raise ValueError('Invalid trackpad recovery device')
    restore_previous(previous, key)


def restore_previous(state, key, persist=True):
    failures = []
    # Try disk and live rollback independently: a full disk must not prevent
    # restoring pointer control, and an offline compositor must not prevent saving.
    if persist:
        try:
            save(state)
        except Exception as exc:
            failures.append(str(exc))
    try:
        if state['devices'][key].get('configured', True):
            hypr('eval', lua_for({key: state['devices'][key]}))
        elif not failures:
            # No previous plugin rule exists. Only a config reload can remove
            # the first runtime override and restore the user's original rules.
            hypr('reload', 'config-only')
    except Exception as exc:
        failures.append(str(exc))
    if failures:
        raise RuntimeError('; '.join(failures))
    clear_pending()


def validate_persisted(state):
    validate_state(state)
    for group in state['devices'].values():
        validate_native_settings(group['settings'], group.get('curve_calibration', {}))


def save(state):
    validate_persisted(state)
    atomic_write(GENERATED, lua_for(state['devices']))
    atomic_write(STATE, json.dumps(state, indent=2) + '\n')


def reconcile_generated(state, previous=None):
    """Recover a removed rule file or an interrupted two-file save from JSON."""
    expected = lua_for(state['devices'])
    stored = read_state_file(GENERATED)
    if stored == expected:
        return
    for group in state['devices'].values():
        validate_native_settings(group['settings'], group.get('curve_calibration', {}))
    groups = state['devices']
    if previous is not None:
        # Old curve formats cannot be rendered by the current Lua serializer.
        previous = migrate(previous)
    if previous is not None and stored == lua_for(previous['devices']):
        # Normal discovery updates only the affected groups. After interruption,
        # the rule file no longer matches previous JSON, so reapply all saved rules.
        groups = {key: group for key, group in groups.items()
                  if key not in previous['devices']
                  or lua_for({key: group}) != lua_for({key: previous['devices'][key]})}
    hypr('eval', lua_for(groups))
    atomic_write(GENERATED, expected)


def defaults():
    values = {}
    for key in sorted(BOOLS - {'enabled'} | {'scroll_factor'}):
        option = json.loads(hypr('getoption', 'input:touchpad:' + key, '-j'))
        values[key] = option.get('bool', option.get('float'))
        validate_setting(key, values[key])
    values['sensitivity'] = json.loads(hypr('getoption', 'input:sensitivity', '-j'))['float']
    values['enabled'] = True
    values['accel_profile'] = 'adaptive'
    return values


def initialize(live):
    if not live:
        return {'version': 1, 'devices': {}}
    base = defaults()
    devices = copy.deepcopy(live)
    # Import the old panel's Dell-only pointer setting without executing its Lua.
    legacy = STATE_ROOT / 'omarchy/toggles/hypr/touchpad-settings.lua'
    text = read_state_file(legacy) or ''
    overrides = dict(re.findall(r'hl\.device\(\{ name = "([A-Za-z0-9_.:/+-]+)", sensitivity = (-?[0-9.]+) \}\)', text))
    for group in devices.values():
        group['configured'] = False
        group['settings'] = dict(base)
        for name in group['names']:
            if name in overrides:
                group['settings']['sensitivity'] = validate_setting('sensitivity', float(overrides[name]))
    return {'version': 1, 'devices': devices}


def saved_device_owners(live, state):
    """Route known interfaces to their saved group before grouping new devices.

    Older SPI/Intel workarounds may coexist with separately configured Apple
    devices. Their preferences and undo history must remain independent.
    """
    owners = {name: key for key, group in state['devices'].items() for name in group['names']}
    routed = {}
    for key, group in live.items():
        for name in group['names']:
            owner = owners.get(name, key)
            identity = state['devices'].get(owner, group)
            row = routed.setdefault(owner, {'id': owner, 'label': identity['label'], 'names': []})
            if name not in row['names']:
                row['names'].append(name)
    return routed


def imported_active(settings):
    return settings.get('curve_preset') == 'imported' and settings.get('accel_profile') == 'custom'


def snapshot(state, live, monitor=None):
    rows = []
    for key in sorted(state['devices'], key=lambda k: (k != 'apple', k != 'dell', k)):
        group = copy.deepcopy(state['devices'][key])
        group['connected'] = key in live
        settings = group['settings']
        if imported_active(settings):
            group['imported_missing_interfaces'] = [
                name for name in group['names'] if name not in settings['imported_curve']['devices']]
        if monitor and imported_active(settings):
            # The curve was converted for one Hyprland scale; a new scale needs a fresh Apply.
            imported = settings['imported_curve']
            try:
                current = profile_scale(imported['mm_per_point'], monitor)
                group['imported_drift'] = abs(current / imported['px_per_point'] - 1) > 0.01
            except ValueError:
                pass  # An incomplete monitor report says nothing about drift.
        rows.append(group)
    return {'devices': rows}


def migrate(state):
    """The previous panel inherited the driver's default adaptive profile."""
    if not isinstance(state, dict) or type(state.get('version')) is not int or not 1 <= state['version'] <= SCHEMA:
        raise ValueError('Unsupported trackpad state version; saved settings were not changed')
    updated = copy.deepcopy(state)
    for group in updated['devices'].values():
        settings = group['settings']
        settings.setdefault('accel_profile', 'adaptive')
        # Store the effective Hyprland value unchanged; scale is UI metadata.
        validate_setting('scroll_factor', settings['scroll_factor'])
        settings.setdefault('scroll_scale', max(1, settings['scroll_factor']))
        curve = settings.get('curve')
        if isinstance(curve, dict) and set(curve) == {'precision', 'transition', 'fast'}:
            transition = curve['transition']
            if type(transition) not in (int, float) or not math.isfinite(transition) or not 0.2 <= transition <= 1.8:
                raise ValueError('Invalid legacy curve transition')
            settings['curve'] = validate_curve({'precision': curve['precision'], 'start': 0,
                                                'end': 2 * transition, 'fast': curve['fast']})
            # The old Mac preset has a different shape from the new starting preset.
            settings['curve_preset'] = 'custom'
        previous = group.get('previous_pointer_feel')
        if previous is not None:
            previous.setdefault('calibration', {})
        if previous and 'transition' in previous.get('curve', {}):
            old = previous['curve']
            previous['curve'] = validate_curve({'precision': old['precision'], 'start': 0,
                                               'end': 2 * old['transition'], 'fast': old['fast']})
            if previous['profile'] == 'mac':
                previous['profile'] = 'custom'
    updated['version'] = SCHEMA
    validate_state(updated)
    devices = updated['devices']
    legacy = [key for key in LEGACY_APPLE if key in devices
              and devices[key]['names'] == [key]]
    if 'apple' not in devices and len(legacy) == 1:
        key = legacy[0]
        # Preserve dictionary order, and therefore byte-identical generated rules.
        updated['devices'] = {('apple' if old == key else old):
                              (dict(group, id='apple', label='Apple') if old == key else group)
                              for old, group in devices.items()}
    else:
        for key in legacy:
            devices[key]['label'] = 'Apple (' + key + ')'
    return validate_state(updated)


def capture_calibration(names, saved):
    return {name: resolution for name in names
            if (resolution := device_resolution(name) or saved.get(name)) is not None}


def ensure_custom_pointer(group):
    """Progressive scroll_points only apply when accel_profile is custom."""
    settings = group['settings']
    if settings.get('accel_profile') == 'custom':
        return
    old_profile = settings.get('accel_profile', 'adaptive')
    group['previous_pointer_feel'] = {
        'profile': old_profile,
        'curve': copy.deepcopy(settings.get('curve', DEFAULT_CURVE)),
        'calibration': copy.deepcopy(group.get('curve_calibration', {})),
    }
    scale = settings.get('scroll_scale', max(1, settings.get('scroll_factor', 1)))
    settings['accel_profile'] = 'custom'
    settings['curve'] = preset_for_scale(scale)
    settings['curve_preset'] = 'mac'
    settings.pop('imported_curve', None)
    group['curve_calibration'] = capture_calibration(group['names'], group.get('curve_calibration', {}))


def enable_progressive_scroll(group):
    settings = group['settings']
    ensure_custom_pointer(group)
    settings.setdefault('scroll_curve', dict(DEFAULT_SCROLL_CURVE))
    settings.setdefault('scroll_curve_preset', 'mac')
    settings['scroll_progressive'] = True


def change(state, key, option, value):
    validate_state(state)
    if key not in state['devices'] and key in LEGACY_APPLE:
        # A queued UI edit can still carry the pre-migration ID.
        key = next((owner for owner, group in state['devices'].items()
                    if key in group['names']), key)
    if key not in state['devices']:
        raise ValueError('Unknown trackpad')
    updated = copy.deepcopy(state)
    if updated['devices'][key].get('configured') is False:
        updated['devices'][key]['configured'] = True
    group = updated['devices'][key]
    settings = group['settings']
    if option in ('pointer_feel', 'pointer_restore'):
        validate_change(option, value)
        previous = group.get('previous_pointer_feel')
        # Undo is explicit; a fresh Apply may equal the old curve numerically.
        restoring = option == 'pointer_restore' or 'calibration' in value
        if restoring and (previous is None or value not in (previous, {k: previous[k] for k in ('profile', 'curve')})):
            raise ValueError('Restore must match the saved previous pointer feel')
        if 'calibration' in value and not restoring:
            raise ValueError('Calibration can only restore the saved previous pointer feel')
        profile = value['profile']
        curve = value['curve']
        old_profile = settings.get('accel_profile', 'adaptive')
        group['previous_pointer_feel'] = {
            'profile': settings.get('curve_preset', 'custom') if old_profile == 'custom' else old_profile,
            'curve': copy.deepcopy(settings.get('curve', DEFAULT_CURVE)),
            'calibration': copy.deepcopy(group.get('curve_calibration', {})),
        }
        if restoring:
            calibration = previous.get('calibration', {})
        elif profile in ('mac', 'custom'):
            calibration = capture_calibration(group['names'], group.get('curve_calibration', {}))
        else:
            # Native profiles ignore calibration, but keep remembered sensors
            # available for a later explicit custom Apply while disconnected.
            calibration = group.get('curve_calibration', {})
        group['curve_calibration'] = copy.deepcopy(calibration)
        if group['previous_pointer_feel']['profile'] == 'imported':
            group['previous_pointer_feel']['imported'] = copy.deepcopy(settings['imported_curve'])
        settings.pop('imported_curve', None)
        if profile == 'imported':
            imported = value['imported']
            settings['imported_curve'] = copy.deepcopy(imported) if 'devices' in imported else import_profile(group, imported)
        settings['accel_profile'] = 'custom' if profile in ('mac', 'custom', 'imported') else profile
        settings['curve'] = dict(curve)  # The editor sizes new presets to the device range.
        settings['curve_preset'] = profile if profile in ('mac', 'imported') else 'custom'
        if profile in ('adaptive', 'flat'):
            settings['scroll_progressive'] = False
    elif option == 'scroll_feel':
        validate_change(option, value)
        group['previous_scroll_feel'] = {
            'profile': settings.get('scroll_curve_preset', 'mac'),
            'curve': copy.deepcopy(settings.get('scroll_curve', DEFAULT_SCROLL_CURVE)),
        }
        enable_progressive_scroll(group)
        settings['scroll_curve'] = dict(value['curve'])
        settings['scroll_curve_preset'] = 'mac' if value['profile'] == 'mac' else 'custom'
    else:
        validate_setting(option, value)
        if option == 'scroll_scale':
            old_scale = settings.get('scroll_scale', max(1, settings['scroll_factor']))
            settings['scroll_factor'] = round(settings['scroll_factor'] * value / old_scale, 6)
        if option == 'scroll_progressive' and value:
            enable_progressive_scroll(group)
        settings[option] = value
    # Validate every persisted curve before touching the compositor or disk.
    validate_persisted(updated)
    atomic_write(STATE.with_suffix('.pending.json'), json.dumps({'state': state, 'device': key}))
    saving = False
    try:
        # Only the selected trackpad receives a live update.
        hypr('eval', lua_for({key: updated['devices'][key]}))
        saving = True
        save(updated)
        clear_pending()
    except Exception as original:
        try:
            restore_previous(state, key, persist=saving)
        except Exception as rollback:
            raise RuntimeError(f'{original}; recovery is pending: {rollback}') from original
        raise
    return updated


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'state'
    if command == 'set' and len(sys.argv) == 5:
        validate_name(sys.argv[2])
        value = json.loads(sys.argv[4])
        validate_change(sys.argv[3], value)
    elif command == 'profiles' and len(sys.argv) == 3:
        validate_name(sys.argv[2])
    elif command not in ('state', 'init') or len(sys.argv) > 2:
        raise ValueError('Usage: trackpads.py [state|init|profiles DEVICE|set DEVICE OPTION JSON_VALUE]')
    with state_lock():
        # Refuse future/corrupt state before processing even an older journal.
        raw = read_state_file(STATE)
        if raw is not None:
            migrate(json.loads(raw))
        recover_pending()
        live = group_devices(json.loads(hypr('devices', '-j'))['mice'])
        raw = read_state_file(STATE)
        if raw is not None:
            state = json.loads(raw)
        elif command in ('state', 'init'):
            state = initialize(live)
            save(state)
        else:
            raise ValueError('Trackpads have not been initialized')
        previous = copy.deepcopy(state)
        state = migrate(state)
        live = saved_device_owners(live, state)
        # Keep saved settings while discovering trackpads attached after first run.
        new_devices = {key: group for key, group in live.items() if key not in state['devices']}
        if new_devices:
            state['devices'].update(initialize(new_devices)['devices'])
            state = migrate(state)
        for key, group in live.items():
            if key in state['devices']:
                state['devices'][key]['names'] = sorted(set(state['devices'][key]['names'] + group['names']))
        if state != previous:
            validate_persisted(state)
            # JSON is authoritative. Keep the previous rule file until live
            # reconciliation succeeds, so a failed/interrupted refresh retries.
            atomic_write(STATE, json.dumps(state, indent=2) + '\n')
        reconcile_generated(state, previous)
        if command == 'profiles':
            if sys.argv[2] not in state['devices']:
                raise ValueError('Unknown trackpad')
            print(json.dumps(list_profiles(state['devices'][sys.argv[2]])))
            return
        if command == 'set':
            state = change(state, sys.argv[2], sys.argv[3], value)
        monitor = None
        if any(imported_active(group['settings']) for group in state['devices'].values()):
            try:
                monitor = panel_monitor()
            except (ValueError, RuntimeError, subprocess.TimeoutExpired):
                pass  # Drift is advisory; the saved curve stays in effect.
        print(json.dumps(snapshot(state, live, monitor)))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
