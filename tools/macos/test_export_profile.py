import contextlib
import importlib.util
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('export_profile', HERE / 'export-profile.py')
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)
pp = e.pp
PROFILE = HERE / 'profiles' / 'MacBookPro18-3.json'


def recording(path, burst=0.0, rate=123.4, seed=7):
    """Synthesise a probe.swift CSV: steady strokes of whole counts, accelerated exactly as macOS does.

    `burst` > 0 makes counts alternate above and below the mean, like the attached driver.
    """
    profile = pp.load_profile(PROFILE.read_bytes())
    f = pp.apple_function(pp.apple_parameters(profile))
    driver = profile['driver']
    lines = ['# trackpad-plus probe 2', '# global_bounds,0.0,0.0,100000.0,100000.0']
    t, x, y = 1000.0, 50000.0, 50000.0
    period = 1 / rate
    for stroke, finger in enumerate([0.4, 0.7, 1.0, 1.5, 2.2, 3.0, 4.5, 6.5, 9.0] * 2):
        per_event, carried = 400 * finger / rate, 0.0
        angle = (stroke + seed) % 4 * math.pi / 2  # axis-aligned, so every |Δ| is a whole count
        for index in range(160):
            wobble = burst * per_event * (1 if index % 2 else -1)
            carried += per_event + wobble
            counts = math.floor(carried)
            carried -= counts
            t += period
            if not counts:
                continue
            ux, uy = round(counts * math.cos(angle)), round(counts * math.sin(angle))
            if not (ux or uy):
                continue
            dx, dy = pp.apple_event(f, ux, uy, period * 1000, driver)
            x, y = x + dx, y + dy
            lines.append(f'P,{t},{t},{x},{y},{round(dx)},{round(dy)},{ux},{uy},{float(ux)},{float(uy)},{round(dx)},{round(dy)}')
        t += 0.2
    path.write_text('\n'.join(lines) + '\n')


def fractional_recording(path, profile, gain_bias=1):
    """Record double raw counts with truncated integer columns, like the native probe."""
    f = pp.apple_function(pp.apple_parameters(profile))
    rate = 123.4
    period = 1 / rate
    timestamp, x = 1000.0, 1000000.0
    lines = ['# global_bounds,0,0,10000000,10000000']
    for speed in (12, 22, 47, 92, 180, 360):
        for index in range(800):
            raw = 400 * (speed / 25.4) / rate + ((index % 20 + 0.5) / 20 - 0.5)
            delta, _ = pp.apple_event(f, raw, 0, period * 1000, profile['driver'])
            timestamp += period
            x += delta * gain_bias
            lines.append(f'P,{timestamp},{timestamp},{x},1000000,{delta},0,{int(raw)},0,{raw},0,{delta},0')
        timestamp += 0.2
    path.write_text('\n'.join(lines) + '\n')


class CheckTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.profile = pp.load_profile(PROFILE.read_bytes())

    def check(self, **options):
        path = self.root / 'probe.csv'
        recording(path, **options)
        result = e.check_probe(self.profile, *e.read_probe(path))
        with contextlib.redirect_stdout(io.StringIO()):
            passed, worst, trusted = e.report_check(result)
        return result, passed, worst

    def test_native_strokes_pass_and_measure_the_event_rate(self):
        result, passed, worst = self.check()
        self.assertTrue(passed)
        self.assertAlmostEqual(result['driver']['event_rate_hz'], 123.4, places=1)
        self.assertFalse(result['fractional'])
        self.assertLess(result['median_error'], 0.01)
        self.assertLess(worst, 0.02)

    def test_fractional_recordings_verify_and_write_only_after_a_pass(self):
        probe, target = self.root / 'fractional.csv', self.root / 'profile.json'
        fractional_recording(probe, self.profile)
        bounds, rows, touches = e.read_probe(probe)
        result = e.check_probe(self.profile, bounds, rows, touches)
        self.assertTrue(result['fractional'])
        self.assertLess(result['median_error'], 0.01)
        target.write_bytes(PROFILE.read_bytes())
        args = ['export-profile.py', '--check', str(probe), '--profile', str(target), '--write']
        with mock.patch('sys.argv', args), contextlib.redirect_stdout(io.StringIO()):
            e.main()
        driver = json.loads(target.read_text())['driver']
        self.assertEqual(driver['deltas'], 'fractional')
        self.assertEqual(driver['event_rate_hz'], 123.4)
        self.assertEqual(driver['counts_per_inch'], self.profile['driver']['counts_per_inch'])
        before = target.read_bytes()
        fractional_recording(probe, self.profile, gain_bias=1.3)
        with mock.patch('sys.argv', args), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            e.main()
        self.assertEqual(target.read_bytes(), before)

    def test_bursty_counts_like_the_attached_driver_fail(self):
        result, passed, worst = self.check(burst=0.6)
        self.assertLess(result['median_error'], 0.01)  # the accelerator itself still matches
        self.assertFalse(passed)
        self.assertGreater(worst, 0.03)

    def test_write_stores_measured_constants_only_after_a_pass(self):
        probe, target = self.root / 'probe.csv', self.root / 'profile.json'
        recording(probe, rate=125.0)
        target.write_bytes(PROFILE.read_bytes())
        with mock.patch('sys.argv', ['export-profile.py', '--check', str(probe), '--profile', str(target), '--write']), \
                contextlib.redirect_stdout(io.StringIO()):
            e.main()
        driver = json.loads(target.read_text())['driver']
        self.assertEqual(driver['event_rate_hz'], 125.0)
        self.assertIn('accelerator median error', driver['verified'])
        recording(probe, burst=0.6)
        before = target.read_text()
        with mock.patch('sys.argv', ['export-profile.py', '--check', str(probe), '--profile', str(target), '--write']), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            e.main()
        self.assertEqual(target.read_text(), before)

    def test_short_recordings_are_rejected(self):
        path = self.root / 'short.csv'
        path.write_text('# global_bounds,0,0,10,10\nP,1,1,1,1,0,0,1,0,1.0,0.0,0,0\n')
        with self.assertRaises(SystemExit):
            e.read_probe(path)

    def test_mismatched_display_bounds_are_rejected_with_a_clear_error(self):
        path = self.root / 'probe.csv'
        recording(path)
        _, rows, touches = e.read_probe(path)
        with self.assertRaisesRegex(ValueError, 'selected display'):
            e.check_probe(self.profile, (0, 0, 10, 10), rows, touches)

    def test_nonpositive_event_gaps_are_rejected_with_a_clear_error(self):
        path = self.root / 'probe.csv'
        recording(path)
        bounds, rows, touches = e.read_probe(path)
        rows = [(rows[0][0], *row[1:]) for row in rows]
        with self.assertRaisesRegex(ValueError, 'event timing'):
            e.check_probe(self.profile, bounds, rows, touches)

    def test_shipped_profile_is_valid_and_verified(self):
        self.assertEqual(self.profile['tracking_speed'], 0.875)
        self.assertEqual(len(self.profile['curves']), 10)
        self.assertTrue(self.profile['driver']['verified'].startswith('probe '))


if __name__ == '__main__':
    unittest.main()
