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

STATE_ROOT = Path(os.environ.get('XDG_STATE_HOME') or Path.home().resolve() / '.local/state')
DIRECTORY = STATE_ROOT / 'omarchy/local-touchpads'
STATE = DIRECTORY / 'settings.json'
GENERATED = STATE_ROOT / 'omarchy/toggles/hypr/zz-local-touchpads.lua'
BOOLS = {'enabled', 'natural_scroll', 'tap_to_click', 'disable_while_typing', 'clickfinger_behavior'}
RANGES = {'sensitivity': (-1, 1), 'scroll_factor': (0.001, 10), 'scroll_scale': (0.1, 10)}
DEFAULT_CURVE = {'precision': 0.3, 'start': 0.8, 'end': 2.8, 'fast': 1.6}
MAX_STATE_BYTES = 1024 * 1024
BUILTIN_APPLE = {'apple-mtp-multi-touch', 'apple-spi-trackpad', 'apple-spi-touchpad',
                 'bcm5974', 'apple-inc.-apple-internal-keyboard-/-trackpad-1'}
LEGACY_APPLE = BUILTIN_APPLE - {'apple-mtp-multi-touch'}
CURVE_RANGES = {'precision': (0.01, 10), 'start': (0, 3.8), 'end': (0.2, 4), 'fast': (0.01, 10)}


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
KERNEL_RESOLUTIONS = {('05ac', '030e'): 46, ('05ac', '0265'): 47, ('05ac', '0324'): 47}


def device_resolution(name):
    """X resolution (units/mm) of the Hyprland device `name`, or None if unknown.

    Hyprland names are the evdev name lowercased with spaces as dashes, plus a
    -N suffix for duplicates. The evdev node needs input permissions, so read
    the udev database (hwdb EVDEV_ABS overrides) and sysfs IDs instead. Unknown
    devices return None and keep the unscaled curve.
    """
    wanted = {name, re.sub(r'-[0-9]+$', '', name)}
    for event in sorted(SYSFS_INPUT.glob('event*')):
        try:
            evdev_name = (event / 'device/name').read_text().strip()
            if evdev_name.lower().replace(' ', '-') not in wanted:
                continue
            udev = UDEV_DATA / ('c' + (event / 'dev').read_text().strip())
            if udev.exists():
                for line in udev.read_text().splitlines():
                    match = re.fullmatch(r'E:EVDEV_ABS_00=[^:]*:[^:]*:([0-9]+)(:.*)?', line)
                    if match and int(match.group(1)) > 0:
                        return int(match.group(1))
            ids = tuple((event / 'device/id' / key).read_text().strip().lower()
                        for key in ('vendor', 'product'))
            if ids in KERNEL_RESOLUTIONS:
                return KERNEL_RESOLUTIONS[ids]
        except OSError:
            continue
    return None


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


def validate_native_profile(profile):
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
        for motion_type, spacing, points in [(1, step, values), (2, 1.0, [0.0, 1.0])]:
            native_points = (ctypes.c_double * len(points))(*points)
            status = lib.libinput_config_accel_set_points(config, motion_type, spacing, len(points), native_points)
            if status != 0:
                raise ValueError(f'libinput rejected the acceleration curve (status {status}, {len(points)} points)')
    finally:
        lib.libinput_config_accel_destroy(config)


def validate_native_curve(curve):
    validate_native_profile(curve_profile(curve))


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
        if value not in ('mac', 'custom'):
            raise ValueError('Unknown curve preset')
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
                if key in ('curve', 'curve_preset', 'scroll_scale'):
                    continue  # Editor metadata is never emitted as a Hyprland option.
                if key == 'accel_profile' and value == 'custom':
                    # Per device: one group can mix sensors of different resolutions.
                    value = curve_profile(group['settings'].get('curve', DEFAULT_CURVE),
                                          device_resolution(name))
                fields.append(f'{key} = {json.dumps(value)}')
            if group['settings'].get('accel_profile') == 'custom':
                # Explicit identity scrolling, independent of the pointer curve.
                fields.append('scroll_points = "1 0 1"')
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
    if type(state['version']) is not int or state['version'] not in (1, 2, 3, 4):
        raise ValueError('Unsupported trackpad state version; saved settings were not changed')
    devices = state['devices']
    if not isinstance(devices, dict) or len(devices) > 128:
        raise ValueError('Invalid trackpad device list')
    all_names = set()
    for key, group in devices.items():
        validate_name(key)
        if not isinstance(group, dict) or set(group) - {'id', 'label', 'names', 'settings', 'previous_pointer_feel', 'configured'}:
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
        scale = settings.get('scroll_scale', max(1, settings['scroll_factor']))
        normalized = settings['scroll_factor'] / scale
        if not 0.01 - 1e-9 <= normalized <= 1 + 1e-9:
            raise ValueError('Scroll speed must be between 0.01 and 1.00 of the device scale')
        if 'previous_pointer_feel' in group:
            validate_change('pointer_feel', group['previous_pointer_feel'])
    return state


def validate_change(option, value):
    if option != 'pointer_feel':
        validate_setting(option, value)
        return
    if not isinstance(value, dict) or set(value) != {'profile', 'curve'}:
        raise ValueError('Expected a pointer profile and curve')
    if value['profile'] not in ('adaptive', 'flat', 'mac', 'custom'):
        raise ValueError('Unknown pointer profile')
    validate_curve(value['curve'])


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
        if group['settings'].get('accel_profile') == 'custom':
            validate_native_curve(group['settings'].get('curve', DEFAULT_CURVE))


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
        if group['settings'].get('accel_profile') == 'custom':
            validate_native_curve(group['settings'].get('curve', DEFAULT_CURVE))
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


def snapshot(state, live):
    rows = []
    for key in sorted(state['devices'], key=lambda k: (k != 'apple', k != 'dell', k)):
        group = copy.deepcopy(state['devices'][key])
        group['connected'] = key in live
        rows.append(group)
    return {'devices': rows}


def migrate(state):
    """The previous panel inherited the driver's default adaptive profile."""
    if not isinstance(state, dict) or type(state.get('version')) is not int or state['version'] not in (1, 2, 3, 4):
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
        if previous and 'transition' in previous.get('curve', {}):
            old = previous['curve']
            previous['curve'] = validate_curve({'precision': old['precision'], 'start': 0,
                                               'end': 2 * old['transition'], 'fast': old['fast']})
            if previous['profile'] == 'mac':
                previous['profile'] = 'custom'
    updated['version'] = 4
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
    settings = updated['devices'][key]['settings']
    if option == 'pointer_feel':
        if not isinstance(value, dict) or set(value) != {'profile', 'curve'}:
            raise ValueError('Expected a pointer profile and curve')
        profile = value['profile']
        if profile not in ('adaptive', 'flat', 'mac', 'custom'):
            raise ValueError('Unknown pointer profile')
        curve = validate_curve(value['curve'])
        old_profile = settings.get('accel_profile', 'adaptive')
        updated['devices'][key]['previous_pointer_feel'] = {
            'profile': settings.get('curve_preset', 'custom') if old_profile == 'custom' else old_profile,
            'curve': copy.deepcopy(settings.get('curve', DEFAULT_CURVE)),
        }
        settings['accel_profile'] = 'custom' if profile in ('mac', 'custom') else profile
        settings['curve'] = dict(curve)  # The editor sizes new presets to the device range.
        settings['curve_preset'] = 'mac' if profile == 'mac' else 'custom'
    else:
        validate_setting(option, value)
        if option == 'scroll_scale':
            old_scale = settings.get('scroll_scale', max(1, settings['scroll_factor']))
            settings['scroll_factor'] = round(settings['scroll_factor'] * value / old_scale, 6)
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
    elif command not in ('state', 'init') or len(sys.argv) > 2:
        raise ValueError('Usage: trackpads.py [state|init|set DEVICE OPTION JSON_VALUE]')
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
        if command == 'set':
            state = change(state, sys.argv[2], sys.argv[3], value)
        print(json.dumps(snapshot(state, live)))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
