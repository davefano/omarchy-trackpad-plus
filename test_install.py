"""Exercise a tracked-only installation against a fake compositor in temporary state."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plugin = self.root / 'plugin with spaces'
        self.plugin.mkdir()
        repo = Path(__file__).resolve().parent
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=repo).decode().split('\0')
        for name in filter(None, tracked):
            source = repo / name
            if source.is_file():
                destination = self.plugin / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        self.assertTrue((self.plugin / 'trackpads.py').is_file(), 'Backend must be tracked')
        self.env = dict(os.environ, XDG_STATE_HOME=str(self.root / 'state'),
                        PATH=str(self.root) + os.pathsep + os.environ['PATH'],
                        TRACKPAD_TEST_ROOT=str(self.root))
        self.devices = self.root / 'devices.json'
        self.devices.write_text(json.dumps({'mice': [
            {'name': 'ven_06cb:00-06cb:d01d-touchpad'},
            {'name': 'apple-inc.-magic-trackpad'},
            {'name': 'apple-inc.-magic-trackpad-1'}]}))
        fake = self.root / 'hyprctl'
        fake.write_text('#!' + sys.executable + '''
import json, os, sys
from pathlib import Path
root = Path(os.environ['TRACKPAD_TEST_ROOT'])
if sys.argv[1] == 'devices':
    print((root / 'devices.json').read_text())
elif sys.argv[1] == 'getoption':
    option = sys.argv[2].split(':')[-1]
    print(json.dumps({'float': 0.2} if option in ('sensitivity', 'scroll_factor')
                     else {'bool': option != 'natural_scroll'}))
elif sys.argv[1] == 'reload':
    (root / 'reload.log').write_text(' '.join(sys.argv[2:]))
    print('ok')
elif sys.argv[1] == 'eval':
    with (root / 'eval.log').open('a') as stream:
        stream.write(sys.argv[2])
    print('ok')
else:
    raise SystemExit(2)
''')
        fake.chmod(0o700)

    def call(self, *args):
        result = subprocess.run([sys.executable, str(self.plugin / 'trackpads.py'), *args],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_invalid_command_does_not_initialize_or_contact_compositor(self):
        for args in [('bad',), ('state', 'extra'), ('set', 'apple', 'scroll_factor', '0'),
                     ('set', 'apple', 'pointer_feel', '{}')]:
            result = subprocess.run([sys.executable, str(self.plugin / 'trackpads.py'), *args],
                                    env=self.env, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('error', json.loads(result.stdout))
            self.assertFalse((self.root / 'state').exists())

    def assert_lenovo_touchpad_round_trip(self, name):
        self.devices.write_text(json.dumps({'mice': [
            {'name': name, 'defaultSpeed': 0.0, 'scrollFactor': -1.0},
            {'name': 'tpps/2-elan-trackpoint', 'defaultSpeed': 0.0, 'scrollFactor': -1.0}
        ]}))
        discovered = self.call('state')['devices']
        self.assertEqual([d['id'] for d in discovered], [name])
        self.assertTrue(discovered[0]['connected'])
        self.assertFalse(discovered[0]['configured'])
        rules = self.root / 'state/omarchy/toggles/hypr/zz-local-touchpads.lua'
        self.assertNotIn('hl.device', rules.read_text())
        changed = self.call('set', name, 'scroll_factor', '0.1')
        self.assertEqual(changed, self.call('state'))
        self.assertEqual(changed['devices'][0]['settings']['scroll_factor'], 0.1)
        for payload in [rules.read_text(), (self.root / 'eval.log').read_text()]:
            self.assertIn(name, payload)
            self.assertNotIn('trackpoint', payload)

    def test_lenovo_touchpad_is_discovered_and_edits_leave_trackpoint_alone(self):
        self.assert_lenovo_touchpad_round_trip('synaptics-tm3512-010')

    def test_thinkpad_x280_touchpad_is_discovered_from_its_part_number(self):
        # The X280 reports the same Synaptics family as a different part number.
        self.assert_lenovo_touchpad_round_trip('synaptics-tm3381-002')

    def test_future_state_is_preserved_byte_for_byte(self):
        self.call('state')
        state = self.root / 'state/omarchy/local-touchpads/settings.json'
        rules = self.root / 'state/omarchy/toggles/hypr/zz-local-touchpads.lua'
        data = json.loads(state.read_text())
        data['version'] = 99
        state.write_text(json.dumps(data))
        before = (state.read_bytes(), rules.read_bytes())
        result = subprocess.run([sys.executable, str(self.plugin / 'trackpads.py'), 'state'],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsupported', result.stdout)
        self.assertEqual((state.read_bytes(), rules.read_bytes()), before)

    def test_direct_cli_lock_deadline(self):
        self.call('state')
        lock_path = self.root / 'state/omarchy/local-touchpads/settings.lock'
        with lock_path.open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = subprocess.run([sys.executable, str(self.plugin / 'trackpads.py'), 'state'],
                                    env=self.env, capture_output=True, text=True, timeout=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('busy', result.stdout)

    def test_compositor_output_limit_and_timeout(self):
        for body, expected in [("import sys; sys.stdout.write('x' * 2000000)", '1 MiB'),
                               ('import time; time.sleep(30)', 'timed out')]:
            (self.root / 'hyprctl').write_text('#!' + sys.executable + '\n' + body + '\n')
            result = subprocess.run([sys.executable, str(self.plugin / 'trackpads.py'), 'state'],
                                    env=self.env, capture_output=True, text=True, timeout=6)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stdout)
            self.assertFalse((self.root / 'state/omarchy/local-touchpads/settings.json').exists())

    def test_terminated_live_apply_is_recovered_on_next_read(self):
        self.call('state')
        state = self.root / 'state/omarchy/local-touchpads/settings.json'
        previous = json.loads(state.read_text())
        fake = self.root / 'hyprctl'
        original = fake.read_text()
        fake.write_text(original.replace("    print('ok')", "    import time; time.sleep(30)\n    print('ok')"))
        result = subprocess.run(['timeout', '-k', '1', '0.3', sys.executable,
                                 str(self.plugin / 'trackpads.py'), 'set', 'apple', 'sensitivity', '0.9'],
                                env=self.env, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 124)
        self.assertTrue(state.with_suffix('.pending.json').exists())
        self.assertEqual(json.loads(state.read_text()), previous)
        fake.write_text(original)
        self.call('state')
        self.assertEqual(json.loads(state.read_text()), previous)
        self.assertFalse(state.with_suffix('.pending.json').exists())
        self.assertEqual((self.root / 'reload.log').read_text(), 'config-only')

    def test_scroll_scale_survives_restart_and_applies_to_actual_factor(self):
        self.call('state')
        self.call('set', 'apple', 'scroll_factor', '1')
        after = self.call('set', 'apple', 'scroll_scale', '3')
        apple = next(row for row in after['devices'] if row['id'] == 'apple')
        self.assertEqual(apple['settings']['scroll_scale'], 3)
        self.assertEqual(apple['settings']['scroll_factor'], 3)
        self.assertEqual(self.call('state'), after)
        rules = self.root / 'state/omarchy/toggles/hypr/zz-local-touchpads.lua'
        self.assertIn('scroll_factor = 3', rules.read_text())
        self.assertNotIn('scroll_scale', rules.read_text())

    def test_fresh_install_initializes_and_persists_independent_settings(self):
        before = self.call('state')  # The same first command used by Panel.qml.
        self.assertEqual({d['id'] for d in before['devices']}, {'apple', 'dell'})
        generated = self.root / 'state/omarchy/toggles/hypr/zz-local-touchpads.lua'
        self.assertNotIn('hl.device', generated.read_text(), 'first read must not override user config')
        self.assertFalse((self.root / 'eval.log').exists())
        after = self.call('set', 'apple', 'accel_profile', '"flat"')
        self.assertEqual(next(d for d in before['devices'] if d['id'] == 'dell'),
                         next(d for d in after['devices'] if d['id'] == 'dell'))
        self.assertEqual(self.call('state'), after)
        lua = (self.root / 'eval.log').read_text()
        self.assertEqual(lua.count('accel_profile = "flat"'), 2)
        self.assertNotIn('ven_06cb', lua)
        generated = self.root / 'state/omarchy/toggles/hypr/zz-local-touchpads.lua'
        self.assertIn('accel_profile = "flat"', generated.read_text())
        self.assertNotIn('ven_06cb', generated.read_text(), 'unedited devices must remain untouched')

    def test_device_attached_after_empty_first_run_is_discovered(self):
        self.devices.write_text('{"mice": []}')
        self.assertEqual(self.call('state')['devices'], [])
        self.devices.write_text('{"mice": [{"name": "apple-inc.-magic-trackpad"}]}')
        self.assertEqual(self.call('state')['devices'][0]['id'], 'apple')
        self.call('set', 'apple', 'sensitivity', '0.4')
        self.assertEqual(self.call('state')['devices'][0]['settings']['sensitivity'], 0.4)

    def test_lock_stall_is_bounded_and_next_read_recovers(self):
        self.call('state')
        lock_path = self.root / 'state/omarchy/local-touchpads/settings.lock'
        with lock_path.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = subprocess.run(['timeout', '-k', '2', '0.1', sys.executable,
                                     str(self.plugin / 'trackpads.py'), 'state'],
                                    env=self.env, capture_output=True, timeout=4)
            self.assertEqual(result.returncode, 124)
        self.assertEqual(len(self.call('state')['devices']), 2)

    def test_builtin_apple_curve_survives_restart_and_can_return_to_adaptive(self):
        self.devices.write_text('{"mice": [{"name": "apple-mtp-multi-touch"}]}')
        before = self.call('state')['devices'][0]
        self.assertTrue(before['connected'])
        self.assertTrue((self.plugin / 'CurveEditor.qml').is_file())
        self.assertTrue((self.plugin / 'Curve.js').is_file())
        value = {'profile': 'custom', 'curve': {'precision': 0.3, 'start': 0.8, 'end': 4, 'fast': 2.0}}
        self.call('set', 'apple', 'pointer_feel', json.dumps(value))
        after = self.call('state')['devices'][0]
        self.assertEqual(after['settings']['curve'], value['curve'])
        self.assertEqual(after['settings']['accel_profile'], 'custom')
        self.assertEqual(after['previous_pointer_feel']['profile'], 'adaptive')
        self.assertEqual(after['settings']['scroll_factor'], before['settings']['scroll_factor'])
        value['profile'] = 'adaptive'
        self.call('set', 'apple', 'pointer_feel', json.dumps(value))
        self.assertEqual(self.call('state')['devices'][0]['settings']['accel_profile'], 'adaptive')

    def test_new_identity_preserves_legacy_state_and_repairs_missing_rules(self):
        manifest = json.loads((self.plugin / 'manifest.json').read_text())
        self.assertEqual(manifest['id'], 'davefano.trackpad-plus')
        self.assertEqual(manifest['name'], 'Trackpad Plus')
        self.call('state')
        self.call('set', 'apple', 'sensitivity', '-0.4')
        state_path = self.root / 'state/omarchy/local-touchpads/settings.json'
        saved = state_path.read_bytes()
        generated = self.root / 'state/omarchy/toggles/hypr/zz-local-touchpads.lua'
        for damage in ('missing', 'interrupted-save'):
            if damage == 'missing':
                generated.unlink()
            else:
                generated.write_text('do -- incomplete previous write\nend\n')
            self.call('state')
            self.assertEqual(state_path.read_bytes(), saved)
            self.assertIn('sensitivity = -0.4', generated.read_text())
            self.assertIn('davefano.trackpad-plus', generated.read_text())

    def test_slow_scrolling_persists_without_changing_pointer_curve(self):
        self.call('state')
        value = {'profile': 'custom', 'curve': {'precision': 0.05, 'start': 0.8, 'end': 2.8, 'fast': 0.85}}
        before = self.call('set', 'apple', 'pointer_feel', json.dumps(value))
        original = next(d for d in before['devices'] if d['id'] == 'apple')
        for factor in [0.05, 0.01]:
            after = self.call('set', 'apple', 'scroll_factor', str(factor))
            self.assertEqual(after, self.call('state'))
            apple = next(d for d in after['devices'] if d['id'] == 'apple')
            expected = dict(original['settings'], scroll_factor=factor)
            self.assertEqual(apple['settings'], expected)
            self.assertEqual(apple['previous_pointer_feel'], original['previous_pointer_feel'])
            self.assertEqual(next(d for d in after['devices'] if d['id'] == 'dell'),
                             next(d for d in before['devices'] if d['id'] == 'dell'))


if __name__ == '__main__':
    unittest.main()
