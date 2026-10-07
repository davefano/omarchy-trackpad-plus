import copy
import importlib.util
from itertools import product
import json
import math
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('pointer_profiles', Path(__file__).with_name('pointer_profiles.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

# HIDAccelCurves read from a MacBookPro18,3 (macOS 15.7.9): index, linear, parabolic, cubic,
# tangent_linear, tangent_root, all 16.16 fixed point.
MACBOOK_CURVES = [(0, 65536, 0, 0, 484966, 1376256), (8192, 64881, 32768, 5243, 478413, 1310720),
                  (32768, 64225, 43254, 6554, 471859, 1245184), (45056, 62915, 54395, 7864, 465306, 1179648),
                  (57344, 61604, 65536, 9830, 458752, 1114112), (65536, 60293, 75366, 11796, 458752, 1048576),
                  (98304, 58327, 85197, 13763, 458752, 983040), (131072, 56361, 95027, 15729, 458752, 917504),
                  (163840, 54395, 108790, 18350, 458752, 851968), (196608, 65536, 123208, 23593, 458752, 786432)]
PANEL = {'width': 3024, 'scale': 2, 'physicalWidth': 301.2}


def profile(**changes):
    value = {
        'format': p.FORMAT, 'version': 1, 'kind': 'apple-parametric',
        'name': 'MacBook Pro (M1 Pro)', 'tracking_speed': 0.875,
        'curves': [dict(zip(('index', 'linear', 'parabolic', 'cubic', 'tangent_linear', 'tangent_root'), row),
                        quartic=0) for row in MACBOOK_CURVES],
        'driver': {'resolution_dpi': 400, 'report_rate_hz': 120, 'event_rate_hz': 120,
                   'counts_per_inch': 400, 'deltas': 'ideal', 'verified': ''},
        'display': {'points_wide': 1512, 'pixels_wide': 3024, 'width_mm': 301.2},
        'source': {'model': 'MacBookPro18,3', 'exporter': 1},
    }
    value.update(changes)
    return value


class AppleCurveTests(unittest.TestCase):
    def setUp(self):
        self.profile = p.validate_profile(profile())
        self.f = p.apple_function(p.apple_parameters(self.profile))

    def test_matching_notch_is_used_without_interpolation(self):
        parameters = p.apple_parameters(self.profile)
        self.assertEqual(parameters['linear'], 61604 / 65536)
        self.assertEqual(parameters['cubic'], 9830 / 65536)
        self.assertEqual(parameters['tangent_root'], 17)

    def test_between_notches_every_parameter_is_interpolated(self):
        # 0.8 lies 60 % of the way from the 0.6875 notch to the 0.875 notch.
        parameters = p.apple_parameters(profile(tracking_speed=0.8))
        self.assertAlmostEqual(parameters['index'], 0.8)
        self.assertAlmostEqual(parameters['linear'], (62915 + 0.6 * (61604 - 62915)) / 65536)
        self.assertAlmostEqual(parameters['parabolic'], (54395 + 0.6 * (65536 - 54395)) / 65536)
        self.assertAlmostEqual(parameters['tangent_root'], 18 + 0.6 * (17 - 18))
        top = p.apple_parameters(profile(tracking_speed=3))
        self.assertEqual(top['linear'], 1.0)

    def test_three_segments_use_apples_power_quirk(self):
        lin, par, cub = 61604 / 65536, 1.0, 9830 / 65536
        self.assertAlmostEqual(self.f(1), lin + par ** 2 + cub ** 3)
        m0 = lin + 2 * 7 * par ** 2 + 3 * 49 * cub ** 3
        b0 = 7 * lin + 49 * par ** 2 + 343 * cub ** 3 - 7 * m0
        self.assertAlmostEqual(m0, 15.436, places=3)
        self.assertAlmostEqual(b0, -51.315, places=3)
        self.assertAlmostEqual(self.f(10), 10 * m0 + b0)
        y1 = 17 * m0 + b0
        self.assertAlmostEqual(self.f(20), math.sqrt(y1 ** 2 + 2 * y1 * m0 * 3))
        for x in [7, 17]:  # continuous where the segments meet
            self.assertAlmostEqual(self.f(x - 1e-9), self.f(x + 1e-9), places=5)

    def test_tangentless_curves_follow_apples_branches(self):
        polynomial = p.apple_function({'linear': 1, 'parabolic': 1, 'cubic': 0, 'quartic': 0,
                                       'tangent_linear': 0, 'tangent_root': 0})
        self.assertAlmostEqual(polynomial(100), 100 + 10000)
        rooted = p.apple_function({'linear': 1, 'parabolic': 1, 'cubic': 0, 'quartic': 0,
                                   'tangent_linear': 0, 'tangent_root': 4})
        self.assertAlmostEqual(rooted(4), 20)
        self.assertAlmostEqual(rooted(5), math.sqrt(9 * 5 + 400 - 9 * 4))

    def test_event_follows_ioh_pointer_accelerator(self):
        driver = self.profile['driver']
        dx, dy = p.apple_event(self.f, 3, 4, 1000 / 120, driver)
        scale = self.f(5 * 67 / 400) * 96 / 67 / 5
        self.assertAlmostEqual(dx, 3 * scale)
        self.assertAlmostEqual(dy, 4 * scale)
        # A late event counts as slower; an early one is clamped to the report period.
        late = p.apple_event(self.f, 6, 0, 2000 / 120, driver)[0]
        self.assertAlmostEqual(late, 6 * self.f(3 * 67 / 400) * 96 / 67 / 3)
        self.assertEqual(p.apple_event(self.f, 6, 0, 1, driver), p.apple_event(self.f, 6, 0, 0, driver))
        # Below one whole count the floor clamps to the minimum velocity: the slowest gain.
        tiny = p.apple_event(self.f, 0.6, 0, 1000 / 120, driver)[0]
        self.assertAlmostEqual(tiny / 0.6, 61604 / 65536 * 96 / 400, places=5)

    def test_slow_gain_is_independent_of_event_rate(self):
        for rate in [67, 120, 125]:
            driver = dict(self.profile['driver'], event_rate_hz=rate)
            self.assertAlmostEqual(p.cursor_speed(self.f, 1e-6, driver) / 1e-6, 96 * 61604 / 65536, places=3)
        fast = [p.cursor_speed(self.f, 5, dict(self.profile['driver'], event_rate_hz=rate)) for rate in [120, 125]]
        self.assertLess(fast[1], fast[0])

    def test_late_regular_events_match_apples_rate_clamp(self):
        driver = dict(self.profile['driver'], event_rate_hz=60, report_rate_hz=120, deltas='integer')
        # Ten counts each 60 Hz event, despite the driver's nominal 120 Hz report period.
        expected = p.apple_event(self.f, 10, 0, 1000 / 60, driver)[0] * 60
        self.assertAlmostEqual(expected, 256.3011933899454)
        self.assertAlmostEqual(p.cursor_speed(self.f, 1.5, driver), expected)

    def test_static_models_match_simulated_steady_strokes(self):
        # Fractional events jitter evenly across one count; integer events carry remainders.
        for model, finger, rate in product(['fractional', 'integer'], [0.01, 0.04, 0.4, 1.3, 2.75, 9.0], [60, 120, 123.4]):
            driver = dict(self.profile['driver'], deltas=model, event_rate_hz=rate)
            period = 1000 / driver['event_rate_hz']
            per_event = driver['counts_per_inch'] * finger / driver['event_rate_hz']
            travelled, carried, waited, frames = 0.0, 0.0, 0.0, 12000
            for frame in range(frames):
                waited += period
                if model == 'fractional':
                    counts = per_event + (frame % 100 + 0.5) / 100 - 0.5
                else:
                    carried += per_event
                    counts = math.floor(carried)
                    carried -= counts
                if counts:
                    travelled += p.apple_event(self.f, counts, 0, waited, driver)[0]
                    waited = 0.0
            simulated = travelled / (frames * period / 1000)
            self.assertAlmostEqual(simulated / p.cursor_speed(self.f, finger, driver), 1, delta=0.01,
                                   msg=f'{model} at {finger} in/s, {rate} Hz')


class ConversionTests(unittest.TestCase):
    def test_this_mac_converts_to_64_increasing_points(self):
        result = p.convert(profile(), 98.65, 1.0)
        self.assertEqual(len(result['points']), 64)
        self.assertEqual(result['points'][0], 0)
        self.assertEqual(result['points'], sorted(result['points']))
        self.assertEqual(result['step'], 0.5067)
        self.assertEqual(result['px_per_point'], 1.0)
        self.assertTrue(result['native'].startswith('custom 0.5067 0.000000 '))
        self.assertEqual(len(result['native'].split()), 66)

    def test_error_bands_match_the_planned_fidelity(self):
        bands = p.convert(profile(), 98.65, 1.0)['error_bands']
        self.assertLessEqual(bands['6-600'], 1.8)
        self.assertLessEqual(bands['600-800'], 0.1)
        self.assertLessEqual(bands['3-6'], 5)
        self.assertLessEqual(bands['1.5-3'], 8.5)
        self.assertLessEqual(bands['800-1200'], 10)

    def test_tail_extrapolation_is_exact_on_the_tangent_line(self):
        result = p.convert(profile(), 98.65, 1.0)
        f = p.apple_function(p.apple_parameters(profile()))
        driver = profile()['driver']
        for speed in [400, 600, 770]:  # past the last point, before the 773 mm/s knee
            units = speed * 98.65 / 1000
            expected = p.cursor_speed(f, speed / 25.4, driver) / 1000
            self.assertAlmostEqual(p.interpolate(result['step'], result['points'], units) / expected, 1, places=4)

    def test_late_event_sampling_places_tail_on_the_correct_tangent(self):
        driver = dict(profile()['driver'], event_rate_hz=60, deltas='ideal')
        sample = profile(driver=driver)
        result = p.convert(sample, 98.65, 1.0)
        self.assertAlmostEqual(result['step'], 0.5067)
        f = p.apple_function(p.apple_parameters(sample))
        for speed in [400, 600, 770]:
            counts = speed / 25.4 * driver['counts_per_inch'] / driver['event_rate_hz']
            velocity = counts * 0.5  # 60 Hz reports are late against the nominal 120 Hz period.
            expected = counts / velocity * p.apple_multiplier(f, velocity, driver) * 60 / 1000
            actual = p.interpolate(result['step'], result['points'], speed * 98.65 / 1000)
            self.assertAlmostEqual(actual / expected, 1, places=4)

    def test_discrete_tail_samples_clear_the_tangent_after_rate_normalization(self):
        for model, rate in product(['integer', 'fractional'], [60, 120, 123.4]):
            driver = dict(profile()['driver'], deltas=model, event_rate_hz=rate)
            value = profile(driver=driver)
            result = p.convert(value, 98.65, 1)
            # Both final samples must use the linear branch, including the lowest
            # count in the fractional model's one-count jitter interval.
            counts = 62 * result['step'] * 1000 / 98.65 / 25.4 * driver['counts_per_inch'] / rate
            lowest_whole = math.floor(counts - (0.5 if model == 'fractional' else 0))
            argument = lowest_whole * min(rate / driver['report_rate_hz'], 1) * p.FRAME_RATE / driver['resolution_dpi']
            self.assertGreaterEqual(argument, p.apple_parameters(value)['tangent_linear'])

    def test_hyprland_scale_keeps_physical_travel(self):
        base = p.convert(profile(), 98.65, 1.0)
        scaled = p.convert(profile(), 98.65, p.px_per_point(p.mm_per_point(profile()), dict(PANEL, scale=4 / 3)))
        self.assertAlmostEqual(scaled['px_per_point'], 1.5)
        self.assertEqual(scaled['step'], base['step'])
        for low, high in zip(base['points'][1:], scaled['points'][1:]):
            self.assertAlmostEqual(high / low, 1.5, places=3)
        for (speed, low), (_, high) in zip(base['plot'], scaled['plot']):  # same gain on screen
            self.assertAlmostEqual(high / low, 1, places=3, msg=f'{speed} mm/s')

    def test_plot_is_physical_gain_in_cursor_mm_per_finger_mm(self):
        result = p.convert(profile(), 98.65, 1.0)
        speeds = [speed for speed, _ in result['plot']]
        self.assertEqual(speeds[:3], [1, 10, 20])
        self.assertEqual(speeds[-1], 400)
        gains = [gain for _, gain in result['plot']]
        self.assertEqual(gains, sorted(gains))
        # Slowest gain: 96 pt/in × 0.94 at 301.2 mm / 1512 pt ≈ 0.71 cursor mm per finger mm,
        # raised slightly by libinput's first chord.
        self.assertAlmostEqual(gains[0], 0.79, places=2)

    def test_resolution_and_delta_models_change_only_what_they_should(self):
        coarse = p.convert(profile(), 47.6, 1.0)
        fine = p.convert(profile(), 98.65, 1.0)
        self.assertAlmostEqual(coarse['step'] / fine['step'], 47.6 / 98.65, places=3)
        # Whole counts and skipped zero-delta frames put kinks into Apple's slow
        # end that 64 points can only approximate (3.82 % for this integer profile).
        for model, limit in [('integer', 4), ('fractional', 2)]:
            result = p.convert(profile(driver=dict(profile()['driver'], deltas=model)), 98.65, 1.0)
            self.assertEqual(result['points'], sorted(result['points']))
            self.assertLessEqual(result['error_bands']['6-600'], limit)

    def test_out_of_range_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'range'):
            p.convert(profile(), 0.5, 1.0)
        with self.assertRaisesRegex(ValueError, 'range'):
            p.convert(profile(), 98.65, 0)
        with self.assertRaisesRegex(ValueError, 'range'):
            p.px_per_point(p.mm_per_point(profile()), dict(PANEL, physicalWidth=0))
        with self.assertRaisesRegex(ValueError, 'range'):
            p.px_per_point(p.mm_per_point(profile()), {'width': 3024, 'scale': 2})


class ValidationTests(unittest.TestCase):
    def rejects(self, value, message=''):
        with self.assertRaisesRegex(ValueError, message):
            p.validate_profile(value)

    def test_round_trip_and_digest(self):
        raw = json.dumps(profile()).encode()
        self.assertEqual(p.load_profile(raw), profile())
        self.assertEqual(len(p.digest(raw)), 64)

    def test_structure_is_exact(self):
        self.rejects([], 'Not a')
        self.rejects(dict(profile(), extra=1), 'Not a')
        self.rejects(profile(format='other'), 'format')
        self.rejects(profile(version=True), 'format')
        self.rejects(profile(version=2), 'format')
        self.rejects(profile(kind='sampled'), 'kind')

    def test_curves_are_sorted_have_gain_and_ordered_tangents(self):
        curves = profile()['curves']
        self.rejects(profile(curves=[]), '1 to 16')
        self.rejects(profile(curves=curves * 2), '1 to 16')
        self.rejects(profile(curves=list(reversed(curves))), 'sorted')
        gainless = copy.deepcopy(curves)
        gainless[2].update(linear=0, parabolic=0, cubic=0)
        self.rejects(profile(curves=gainless), 'no gain')
        swapped = copy.deepcopy(curves)
        swapped[0]['tangent_root'] = swapped[0]['tangent_linear']
        self.rejects(profile(curves=swapped), 'order')
        floating = copy.deepcopy(curves)
        floating[0]['linear'] = 1.0
        self.rejects(profile(curves=floating), 'range')
        self.rejects(profile(curves=[dict(curves[0], extra=1)]), 'Invalid')

    def test_numbers_text_and_models_are_bounded(self):
        self.rejects(profile(tracking_speed=3.5), 'range')
        self.rejects(profile(tracking_speed=-1), 'range')
        self.rejects(profile(name='bad\nname'), 'printable')
        self.rejects(profile(name='x' * 81), 'text')
        self.rejects(profile(driver=dict(profile()['driver'], deltas='other')), 'delta')
        self.rejects(profile(driver=dict(profile()['driver'], event_rate_hz=0)), 'range')
        self.rejects(profile(display=dict(profile()['display'], pixels_wide=3024.0)), 'range')
        self.rejects(profile(source={'nested': {}}), 'range')
        self.rejects(profile(source={str(i): i for i in range(17)}), 'source')

    def test_huge_json_integer_is_rejected_as_a_validation_error(self):
        # JSON permits integers too large to convert to a float.
        with self.assertRaisesRegex(ValueError, 'range'):
            p.load_profile(json.dumps(profile(tracking_speed=10 ** 1000)).encode())

    def test_untrusted_bytes_are_bounded_and_finite(self):
        with self.assertRaisesRegex(ValueError, 'NaN'):
            p.load_profile(json.dumps(profile(tracking_speed=float('nan'))).encode())
        with self.assertRaisesRegex(ValueError, '64 KiB'):
            p.load_profile(b' ' * (p.MAX_PROFILE_BYTES + 1))
        with self.assertRaises(UnicodeDecodeError):
            p.load_profile(b'\xff')


if __name__ == '__main__':
    unittest.main()
