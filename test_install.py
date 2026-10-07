"""Exercise a tracked-only installation against a fake compositor in temporary state."""
import fcntl
import json
import os
import platform
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
elif sys.argv[1] == 'plugin':
    print((root / 'plugins.json').read_text() if (root / 'plugins.json').exists() else '[]')
elif sys.argv[1] == 'getoption':
    option = sys.argv[2].split(':')[-1]
    if option in ('workspace_swipe_distance', 'workspace_swipe_invert'):
        settings = dict(distance=300, invert=False)
        config = Path(os.environ.get('XDG_CONFIG_HOME', root / 'config')) / 'hypr/input.lua'
        if config.exists():
            for line in config.read_text().splitlines():
                if line.startswith('-- {'):
                    data = json.loads(line[3:])
                    if 'settings' in data: settings = data['settings']
        print(json.dumps({'int': settings['distance']} if option.endswith('distance')
                         else {'bool': settings['invert']})); sys.exit(0)
    print(json.dumps({'float': 0.2} if option in ('sensitivity', 'scroll_factor')
                     else {'bool': option != 'natural_scroll'}))
elif sys.argv[1] == 'monitors':
    print(json.dumps([{'name': 'eDP-1', 'width': 3024, 'height': 1890, 'scale': 2.0,
                       'physicalWidth': 302, 'focused': True}]))
elif sys.argv[1] == 'configerrors':
    print('')
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

    def macos_profile(self):
        profiles = self.root / 'config/trackpad-plus/profiles'
        profiles.mkdir(parents=True)
        source = self.plugin / 'tools/macos/profiles/MacBookPro18-3.json'
        shutil.copy2(source, profiles / source.name)
        self.env['XDG_CONFIG_HOME'] = str(self.root / 'config')
        self.call('state')
        units = {'apple-inc.-magic-trackpad': 47.6, 'apple-inc.-magic-trackpad-1': 47.6}
        self.call('set', 'apple', 'units_per_mm', json.dumps(units))
        return source.name

    def test_installed_copy_lists_macos_profiles(self):
        name = self.macos_profile()
        listed = self.call('profiles', 'apple')
        self.assertEqual([row['file'] for row in listed['profiles']], [name])
        row = listed['profiles'][0]
        self.assertNotIn('error', row)
        self.assertEqual(len(row['speeds']), 10)
        self.assertEqual(listed['context']['monitor']['name'], 'eDP-1')
        self.assertFalse((self.root / 'eval.log').exists() and 'custom' in (self.root / 'eval.log').read_text())

    def test_installed_macos_profile_survives_restart_and_undo(self):
        name = self.macos_profile()
        row = self.call('profiles', 'apple')['profiles'][0]
        curve = {'precision': 0.3, 'start': 0.8, 'end': 2.8, 'fast': 1.6}
        applied = self.call('set', 'apple', 'pointer_feel', json.dumps(
            {'profile': 'imported', 'curve': curve, 'imported': {'file': name, 'sha256': row['sha256']}}))
        apple = next(device for device in applied['devices'] if device['id'] == 'apple')
        self.assertEqual(apple['settings']['curve_preset'], 'imported')
        self.assertFalse(apple['imported_drift'])
        generated = (self.root / 'state/omarchy/toggles/hypr/zz-local-touchpads.lua').read_text()
        self.assertEqual(generated.count('accel_profile = "custom 0.'), 2)
        restarted = next(device for device in self.call('state')['devices'] if device['id'] == 'apple')
        self.assertEqual(restarted['settings']['imported_curve'], apple['settings']['imported_curve'])
        undone = self.call('set', 'apple', 'pointer_restore', json.dumps(apple['previous_pointer_feel']))
        self.assertEqual(next(d for d in undone['devices'] if d['id'] == 'apple')['settings']['accel_profile'], 'adaptive')

    def test_optional_typing_guard_and_login_service_are_packaged(self):
        import configparser
        self.assertTrue((self.plugin / 'trackpad-typing-guard.py').is_file())
        service = configparser.ConfigParser(interpolation=None)
        service.read(self.plugin / 'trackpad-typing-guard.service')
        self.assertEqual(service['Install']['WantedBy'], 'graphical-session.target')
        self.assertIn(' restore', service['Service']['ExecStopPost'])

    def test_stow_gesture_cli_preserves_links_across_process_restarts(self):
        for layout in ('file', 'hypr-directory', 'config-directory'):
            with self.subTest(layout=layout):
                home = self.root / layout
                target = home / '.dotfiles/hypr/.config/hypr/input.lua'
                target.parent.mkdir(parents=True)
                source = '-- Stowed input\nhl.gesture({ fingers = 3, direction = "horizontal", action = "workspace" })\n'
                target.write_text(source)
                config = home / '.config'
                if layout == 'config-directory':
                    link = config
                    link.symlink_to('.dotfiles/hypr/.config', target_is_directory=True)
                elif layout == 'hypr-directory':
                    config.mkdir()
                    link = config / 'hypr'
                    link.symlink_to('../.dotfiles/hypr/.config/hypr', target_is_directory=True)
                else:
                    (config / 'hypr').mkdir(parents=True)
                    link = config / 'hypr/input.lua'
                    link.symlink_to(os.path.relpath(target, link.parent))
                link_text = os.readlink(link)
                (config / 'hypr/wallpapers').symlink_to(home / 'unmounted/wallpapers', target_is_directory=True)
                env = dict(self.env, XDG_CONFIG_HOME=str(config), XDG_STATE_HOME=str(home / 'state'))
                def gesture(*args):
                    result = subprocess.run([sys.executable, '-B', str(self.plugin / 'gestures.py'), *args],
                                            env=env, capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    return json.loads(result.stdout)
                self.assertTrue(gesture('state')['can_edit'])
                settings = dict(enabled=True, fingers=4, distance=500, invert=True, overview=False)
                self.assertTrue(gesture('set', json.dumps(settings))['managed'])
                saved = gesture('state')
                self.assertEqual(saved['settings']['fingers'], 4)
                self.assertEqual(saved['settings']['distance'], 500)
                self.assertIn('BEGIN Trackpad Plus gestures', target.read_text())
                self.assertFalse(gesture('restore')['managed'])
                self.assertEqual(target.read_text(), source)
                self.assertEqual(os.readlink(link), link_text)
                self.assertFalse((home / 'state/omarchy/local-touchpads/gestures.pending.json').exists())

    def test_overview_runtime_is_packaged_and_inspection_never_starts_it(self):
        for name in ('manifest.json', 'trackpads.py', 'overview-control.py',
                     'overview/shell.qml', 'overview/Session.qml', 'overview/lock-watch.py',
                     'overview/Model.js', 'overview/Overview.qml', 'overview/WindowCard.qml',
                     'overview/Preview.qml', 'overview/PreparedWallpaper.qml'):
            self.assertTrue((self.plugin / name).is_file(), f'{name} must be tracked for installation')
        runtime = self.root / 'runtime'
        runtime.mkdir(mode=0o700)
        marker = self.root / 'qs-started'
        fake_qs = self.root / 'qs'
        fake_qs.write_text('#!' + sys.executable + '\n'
                           'import os\nfrom pathlib import Path\n'
                           'Path(os.environ["TRACKPAD_TEST_ROOT"], "qs-started").touch()\n'
                           'raise SystemExit(87)\n')
        fake_qs.chmod(0o700)
        env = dict(self.env, XDG_RUNTIME_DIR=str(runtime),
                   HYPRLAND_INSTANCE_SIGNATURE='trackpad-install-test', WAYLAND_DISPLAY='wayland-test')
        for operation in ('status', 'close', 'stop'):
            result = subprocess.run([sys.executable, '-B', str(self.plugin / 'overview-control.py'), operation],
                                    env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            response = json.loads(result.stdout)
            self.assertTrue(response['installed'])
            self.assertFalse(response['reachable'])
            self.assertFalse(response['opened'])
            self.assertFalse(response['rendered'])
        self.assertFalse(marker.exists(), 'Inspection/cleanup must not execute Quickshell')
        self.assertEqual(list(runtime.iterdir()), [])
        self.assertFalse((self.root / 'state').exists(), 'Overview commands must not initialize pointer settings')
        self.assertFalse((self.root / 'eval.log').exists())

    def test_gesture_cli_adopts_applies_and_restores_original_input(self):
        config = self.root / 'config'
        (config / 'hypr').mkdir(parents=True)
        source = '-- Keep this input setting\nhl.gesture({ fingers = 3, direction = "horizontal", action = "workspace" })\n'
        input_file = config / 'hypr/input.lua'
        input_file.write_text(source)
        env = dict(self.env, XDG_CONFIG_HOME=str(config))
        def gesture(*args):
            result = subprocess.run([sys.executable, '-B', str(self.plugin / 'gestures.py'), *args],
                                    env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        initial = gesture('state')
        self.assertTrue(initial['can_edit'])
        self.assertFalse(initial['managed'])
        self.assertFalse(initial['hymission']['available'])
        denied = subprocess.run([sys.executable, '-B', str(self.plugin/'gestures.py'), 'set',
                                 json.dumps(dict(enabled=True, fingers=3, distance=300, invert=False, overview=True))],
                                env=env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn('HyMission', denied.stdout)
        self.assertEqual(input_file.read_text(), source)
        saved = gesture('set', json.dumps(dict(enabled=True, fingers=4, distance=500, invert=True)))
        self.assertTrue(saved['managed'])
        self.assertEqual(saved, gesture('state'))
        self.assertEqual(saved['settings']['fingers'], 4)
        (self.root/'plugins.json').write_text('[{"name":"hymission","version":"0.7.0"}]')
        if platform.machine().lower() not in ('x86_64', 'amd64'):
            # Exercise the installed CLI on ARM too, even with a loaded provider.
            before = input_file.read_bytes()
            denied = subprocess.run([sys.executable, '-B', str(self.plugin/'gestures.py'), 'set',
                                     json.dumps(dict(saved['settings'], overview=True))],
                                    env=env, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn('function hooks', denied.stdout)
            self.assertEqual(input_file.read_bytes(), before)
            self.assertFalse(gesture('state')['hymission']['supported'])
            self.assertFalse(gesture('restore')['managed'])
            self.assertEqual(input_file.read_text(), source)
            return
        overview = gesture('set', json.dumps(dict(saved['settings'], overview=True)))
        self.assertTrue(overview['hymission']['available'])
        self.assertTrue(overview['settings']['overview'])
        self.assertIn('hl.plugin.hymission.gesture', input_file.read_text())
        self.assertEqual(overview, gesture('state'))
        self.assertFalse(gesture('restore')['managed'])
        self.assertEqual(input_file.read_text(), source)

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

    def test_spi_and_intel_apple_discovery_persist_without_touching_other_devices(self):
        for name in ('apple-spi-trackpad', 'bcm5974', 'apple-spi-touchpad',
                     'apple-inc.-apple-internal-keyboard-/-trackpad-1'):
            self.devices.write_text(json.dumps({'mice': [{'name': name}, {'name': 'usb-mouse'}]}))
            initial = self.call('state')['devices']
            self.assertEqual([row['id'] for row in initial], ['apple'])
            self.assertTrue(initial[0]['connected'])
            self.call('set', 'apple', 'scroll_factor', '0.15')
            updated = self.call('state')['devices'][0]
            self.assertIn(name, updated['names'])
            self.assertEqual(updated['settings']['scroll_factor'], 0.15)
            self.assertNotIn('usb-mouse', (self.root / 'eval.log').read_text())

    def test_legacy_spi_upgrade_preserves_preferences_across_process_restarts(self):
        self.devices.write_text('{"mice": [{"name": "apple-spi-trackpad"}]}')
        self.call('state')
        self.call('set', 'apple', 'scroll_factor', '0.34')
        state_file = self.root / 'state/omarchy/local-touchpads/settings.json'
        state = json.loads(state_file.read_text())
        group = state['devices'].pop('apple')
        group.update(id='apple-spi-trackpad', label='apple-spi-trackpad')
        state['devices']['apple-spi-trackpad'] = group
        state_file.write_text(json.dumps(state))
        before = (self.root / 'eval.log').read_bytes()
        after = self.call('state')
        self.assertEqual(after['devices'][0]['id'], 'apple')
        self.assertEqual(after['devices'][0]['settings']['scroll_factor'], 0.34)
        self.assertEqual(after, self.call('state'))
        self.assertEqual((self.root / 'eval.log').read_bytes(), before)

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
