#!/usr/bin/env python3
"""macOS pointer profiles: validation, Apple's acceleration curve, and libinput points.

A profile records the acceleration curves and tracking speed a Mac exposes in `ioreg`, so
the same transfer function can be rebuilt for libinput. Shared by trackpads.py and
tools/macos/export-profile.py; tools/macos/README.md cites the source of every constant.
"""
import hashlib
import json
import math

FORMAT = 'trackpad-plus/pointer-profile'
MAX_PROFILE_BYTES = 64 * 1024
FIXED = 65536  # IOHIDFamily stores curve parameters as 16.16 fixed point.
FRAME_RATE = 67.0  # IOHIDAcceleration.hpp: speeds are standardised to 67 events per second.
CURSOR_SCALE = 96.0 / 67.0  # IOHIDAccelerationAlgorithm.hpp: kCursorScale.
MINIMUM_VELOCITY = 1 / FIXED  # IOHIDAcceleration.cpp: FIXED_TO_DOUBLE(0x1).
NPOINTS = 64  # libinput-private.h: LIBINPUT_ACCEL_NPOINTS_MAX.
LIBINPUT_LIMIT = 10000  # LIBINPUT_ACCEL_STEP_MAX and LIBINPUT_ACCEL_POINT_MAX_VALUE.
CURVE_KEYS = ('index', 'linear', 'parabolic', 'cubic', 'quartic', 'tangent_linear', 'tangent_root')
PROFILE_KEYS = {'format', 'version', 'kind', 'name', 'tracking_speed', 'curves', 'driver', 'display', 'source'}
DRIVER_KEYS = {'resolution_dpi', 'report_rate_hz', 'event_rate_hz', 'counts_per_inch', 'deltas', 'verified'}
DISPLAY_KEYS = {'points_wide', 'pixels_wide', 'width_mm'}
DELTA_MODELS = ('ideal', 'integer', 'fractional')
ERROR_BANDS = ((1.5, 3), (3, 6), (6, 600), (600, 800), (800, 1200))  # finger speed, mm/s
PLOT_SPEEDS = [1] + list(range(10, 410, 10))  # finger speed, mm/s


def number(value, low, high, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not low <= value <= high \
            or not math.isfinite(value):
        raise ValueError('Pointer profile value is outside its allowed range')
    return value


def text(value, limit, empty=False):
    if not isinstance(value, str) or len(value) > limit or not (empty or value) or not value.isprintable():
        raise ValueError('Pointer profile text is missing, too long, or not printable')
    return value


def validate_profile(value):
    if not isinstance(value, dict) or set(value) != PROFILE_KEYS:
        raise ValueError('Not a Trackpad Plus pointer profile')
    if value['format'] != FORMAT or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Unsupported pointer profile format or version')
    if value['kind'] != 'apple-parametric':
        raise ValueError('Unsupported pointer profile kind')
    text(value['name'], 80)
    number(value['tracking_speed'], 0, 3)
    curves = value['curves']
    if not isinstance(curves, list) or not 1 <= len(curves) <= 16:
        raise ValueError('Expected 1 to 16 acceleration curves')
    previous = -1
    for curve in curves:
        if not isinstance(curve, dict) or set(curve) != set(CURVE_KEYS):
            raise ValueError('Invalid acceleration curve')
        for key in CURVE_KEYS:
            number(curve[key], 0, 1000 * FIXED, integer=True)
        if curve['index'] <= previous:
            raise ValueError('Acceleration curves must be sorted by unique index')
        previous = curve['index']
        # IOHIDFamily skips gainless curves, which would shift its index lookup; reject them.
        if not any(curve[key] for key in ('linear', 'parabolic', 'cubic', 'quartic')):
            raise ValueError('Acceleration curve has no gain')
        if curve['tangent_linear'] and curve['tangent_root'] and curve['tangent_root'] <= curve['tangent_linear']:
            raise ValueError('Acceleration curve tangents are out of order')
    driver = value['driver']
    if not isinstance(driver, dict) or set(driver) != DRIVER_KEYS:
        raise ValueError('Invalid pointer profile driver constants')
    for key, low, high in [('resolution_dpi', 50, 5000), ('report_rate_hz', 30, 1000),
                           ('event_rate_hz', 30, 1000), ('counts_per_inch', 50, 5000)]:
        number(driver[key], low, high)
    if driver['deltas'] not in DELTA_MODELS:
        raise ValueError('Unknown pointer profile delta model')
    text(driver['verified'], 160, empty=True)
    display = value['display']
    if not isinstance(display, dict) or set(display) != DISPLAY_KEYS:
        raise ValueError('Invalid pointer profile display')
    number(display['points_wide'], 100, 20000)
    number(display['pixels_wide'], 100, 20000, integer=True)
    number(display['width_mm'], 20, 2000)
    source = value['source']
    if not isinstance(source, dict) or len(source) > 16:
        raise ValueError('Invalid pointer profile source')
    for key, item in source.items():
        text(key, 40)
        if isinstance(item, str):
            text(item, 160, empty=True)
        else:
            number(item, -1e9, 1e9)
    return value


def reject_constant(name):
    raise ValueError(f'Pointer profiles must not contain {name}')


def load_profile(raw):
    """Parse untrusted bytes; NaN and Infinity are rejected before validation."""
    if len(raw) > MAX_PROFILE_BYTES:
        raise ValueError('Pointer profile exceeds the 64 KiB limit')
    return validate_profile(json.loads(raw.decode('utf-8'), parse_constant=reject_constant))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def apple_parameters(profile):
    """Mirror IOHIDParametricAcceleration::CreateWithParameters: linear in every parameter."""
    rows = [{key: curve[key] / FIXED for key in CURVE_KEYS} for curve in profile['curves']]
    speed = profile['tracking_speed']
    current = 0
    for position, row in enumerate(rows):
        if speed >= row['index']:
            current = position
    low = rows[current]
    if low['index'] < speed and current + 1 < len(rows):
        high = rows[current + 1]
        ratio = (speed - low['index']) / (high['index'] - low['index'])
        return {key: low[key] + ratio * (high[key] - low[key]) for key in CURVE_KEYS}
    return dict(low)


def apple_function(parameters):
    """Apple's three-segment curve, without kCursorScale (IOHIDParametricAcceleration::multiplier)."""
    p = parameters
    linear, root = p['tangent_linear'], p['tangent_root']

    def poly(x):  # Apple raises gain and speed together: (g·x)ⁿ, not g·xⁿ.
        return p['linear'] * x + (p['parabolic'] * x) ** 2 + (p['cubic'] * x) ** 3 + (p['quartic'] * x) ** 4

    def slope(x):
        return (p['linear'] + 2 * x * p['parabolic'] ** 2 + 3 * x ** 2 * p['cubic'] ** 3
                + 4 * x ** 3 * p['quartic'] ** 4)

    tangent, m, b = [math.inf, math.inf], [0.0, 0.0], [0.0, 0.0]
    if linear:
        m[0] = slope(linear)
        b[0] = poly(linear) - m[0] * linear
        tangent[0] = linear
        if root:
            y1 = m[0] * root + b[0]
            m[1] = 2 * y1 * m[0]
            b[1] = y1 ** 2 - m[1] * root
            tangent[1] = root
    elif root:
        m[1] = slope(root)
        b[1] = poly(root) ** 2 - m[1] * root
        tangent[0] = root

    def f(x):
        if x <= tangent[0]:
            return poly(x)
        if x <= tangent[1] and tangent[0] == linear:
            return m[0] * x + b[0]
        return math.sqrt(m[1] * x + b[1])
    return f


def apple_multiplier(f, velocity, driver):
    """Points for one event of `velocity` counts (IOHIDParametricAcceleration::multiplier)."""
    return f(velocity * FRAME_RATE / driver['resolution_dpi']) * CURSOR_SCALE


def apple_event(f, dx, dy, dt_ms, driver):
    """Accelerate one event of raw counts, as IOHIDPointerAccelerator::accelerate does."""
    rate_multiplier = 1.0
    if dt_ms:
        period = 1000 / driver['report_rate_hz']
        rate_multiplier = period / max(dt_ms, period)
    velocity = max(math.floor(math.hypot(dx, dy)) * rate_multiplier, MINIMUM_VELOCITY)
    scale = apple_multiplier(f, velocity, driver) / velocity
    return dx * scale, dy * scale


def floor_gain(f, whole, driver, rate_multiplier=1.0):
    """Points per count for an event whose |Δ| floors to `whole` counts."""
    velocity = max(whole * rate_multiplier, MINIMUM_VELOCITY)
    return apple_multiplier(f, velocity, driver) / velocity


def cursor_speed(f, finger_in_s, driver):
    """Cursor points/s for a steady finger speed in inches/s.

    `deltas` describes the closed multitouch driver: ideal (continuous counts), integer
    (whole counts with carried remainders), or fractional, where Apple's per-event floor makes
    a sawtooth; a stroke's per-event counts vary, so that case averages over one count.
    All three models retain Apple's normalization for events arriving later than the
    report period; skipped zero-delta integer frames also contribute to that delay.
    """
    rate = driver['event_rate_hz']
    counts = driver['counts_per_inch'] * finger_in_s / rate
    rate_multiplier = min(rate / driver['report_rate_hz'], 1.0)
    if driver['deltas'] == 'fractional':
        low, high, total = counts - 0.5, counts + 0.5, 0.0
        whole = math.floor(low)
        while whole < high:
            a, b = max(low, whole), min(high, whole + 1)
            total += floor_gain(f, max(whole, 0), driver, rate_multiplier) * (b * b - a * a) / 2
            whole += 1
        return rate * total
    if driver['deltas'] == 'integer':
        if counts >= 1:
            whole = math.floor(counts)
            part = counts - whole
            return rate * ((1 - part) * whole * floor_gain(f, whole, driver, rate_multiplier)
                           + part * (whole + 1) * floor_gain(f, whole + 1, driver, rate_multiplier))
        if not counts:
            return 0.0
        # Carried integer remainders yield single-count events separated by either
        # floor(1/counts) or ceil(1/counts) report periods. Apple normalizes each
        # event by that actual gap, not the nominal period of zero-count frames.
        gap = 1 / counts
        whole = math.floor(gap)
        part = gap - whole
        gain = sum(weight * floor_gain(f, 1, driver, min(rate / driver['report_rate_hz'] / periods, 1.0))
                   for weight, periods in ((1 - part, whole), (part, whole + 1)))
        return rate * counts * gain
    # Ideal deltas remove Apple's integer floor, while retaining its timing clamp.
    return rate * counts * floor_gain(f, counts, driver, rate_multiplier)


def mm_per_point(profile):
    display = profile['display']
    return display['width_mm'] / display['points_wide']


def px_per_point(millimetres, monitor):
    """Logical px per macOS point that keeps the cursor's physical travel on a Hyprland monitor."""
    for key in ('width', 'scale', 'physicalWidth'):
        number(monitor.get(key), 1e-6, 1e6)
    return millimetres * monitor['width'] / monitor['scale'] / monitor['physicalWidth']


def interpolate(step, points, speed):
    """libinput's custom profile: linear between points, extrapolated from the last two."""
    index = min(int(speed / step), len(points) - 2)
    return points[index] + (points[index + 1] - points[index]) * (speed / step - index)


def convert(profile, units_per_mm, scale):
    """Sample a profile as libinput custom points for one trackpad interface.

    libinput's input is raw trackpad units per ms at the x resolution and its output is
    logical px per ms; `scale` is logical px per macOS point (see px_per_point). The last two
    points sit on Apple's tangent line, so libinput's linear extrapolation stays exact up to
    the square-root knee.
    """
    number(units_per_mm, 1, 10000)
    number(scale, 0.01, 100)
    parameters = apple_parameters(profile)
    f = apple_function(parameters)
    driver = profile['driver']
    inches = 1000 / (units_per_mm * 25.4)  # finger inches/s per libinput unit/ms

    def output(speed):
        return cursor_speed(f, speed * inches, driver) * scale / 1000

    if parameters['tangent_linear']:
        counts = parameters['tangent_linear'] * driver['resolution_dpi'] / FRAME_RATE
        counts /= min(driver['event_rate_hz'] / driver['report_rate_hz'], 1.0)
        if driver['deltas'] != 'ideal':
            # Every floored count contributing to either final sample must
            # clear the tangent, including the fractional model's jitter edge.
            counts = math.ceil(counts) + (0.5 if driver['deltas'] == 'fractional' else 0)
        linear_from = counts * driver['event_rate_hz'] / driver['counts_per_inch'] / inches
        step = math.ceil(linear_from / (NPOINTS - 2) * 10000) / 10000
    else:
        step = math.ceil(600 / 25.4 / inches / (NPOINTS - 1) * 10000) / 10000
    points = [round(output(index * step), 6) for index in range(NPOINTS)]
    if not 0 < step <= LIBINPUT_LIMIT or points[-1] > LIBINPUT_LIMIT:
        raise ValueError('This profile does not fit libinput’s custom acceleration range')
    px_per_mm = scale / mm_per_point(profile)
    bands = {}
    for low, high in ERROR_BANDS:
        worst = 0.0
        speed = low
        while speed <= high:
            actual = interpolate(step, points, speed * units_per_mm / 1000)
            expected = output(speed * units_per_mm / 1000)
            worst = max(worst, abs(actual / expected - 1) * 100)
            speed *= 1.02
        bands[f'{low:g}-{high:g}'] = round(worst, 2)
    plot = [[speed, round(interpolate(step, points, speed * units_per_mm / 1000) / px_per_mm * 1000 / speed, 4)]
            for speed in PLOT_SPEEDS]
    return {'step': step, 'points': points, 'px_per_point': round(scale, 6), 'plot': plot,
            'error_bands': bands,
            'native': f'custom {step:.4f} ' + ' '.join(f'{point:.6f}' for point in points)}
