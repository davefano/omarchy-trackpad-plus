from contextlib import nullcontext
import copy
import ctypes
import importlib.util
import io
from itertools import product
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('trackpads',Path(__file__).with_name('trackpads.py'))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
MAC_PROFILE = Path(__file__).with_name('tools') / 'macos' / 'profiles' / 'MacBookPro18-3.json'
PANEL = [{'name': 'eDP-1', 'width': 3024, 'height': 1890, 'scale': 2.0, 'physicalWidth': 302, 'focused': True}]
try:
    ctypes.CDLL('libinput.so.10')
    NATIVE = nullcontext  # Linux: the real libinput validates every imported curve.
except OSError:
    NATIVE = lambda: patch.object(m, 'validate_native_profile')  # noqa: E731 (macOS development)

class TrackpadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        # Empty input/udev trees keep host trackpads out of generated curves.
        self.sysfs, self.udev = root / 'sys-input', root / 'udev-data'
        for name, value in [('DIRECTORY', root), ('STATE', root / 'settings.json'),
                            ('GENERATED', root / 'settings.lua'),
                            ('SYSFS_INPUT', self.sysfs), ('UDEV_DATA', self.udev)]:
            replacement = patch.object(m, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.groups=m.group_devices([{'name':n} for n in ['ven_06cb:00-06cb:d01d-touchpad','apple-inc.-magic-trackpad','apple-inc.-magic-trackpad-1','usb-mouse']])
        for g in self.groups.values():
            g['settings']={'enabled':True,'sensitivity':0.3 if g['id']=='dell' else 0.1,'scroll_factor':0.2,'natural_scroll':False,'tap_to_click':True,'clickfinger_behavior':True,'disable_while_typing':True}
        self.state={'version':1,'devices':self.groups}

    def legacy_apple_state(self, name, with_apple=False):
        state = m.migrate(self.state)
        raw = copy.deepcopy(state['devices']['apple'])
        raw.update(id=name, label=name, names=[name], configured=True)
        raw['previous_pointer_feel'] = {'profile': 'flat', 'curve': dict(m.DEFAULT_CURVE), 'calibration': {}}
        raw['settings']['scroll_factor'] = 0.34
        if not with_apple:
            del state['devices']['apple']
        state['devices'][name] = raw
        return state

    def add_input_device(self, event, name, dev, vendor='0000', product='0000', udev=None):
        device = self.sysfs / event / 'device'
        (device / 'id').mkdir(parents=True)
        (device / 'name').write_text(name + '\n')
        (device / 'id' / 'vendor').write_text(vendor + '\n')
        (device / 'id' / 'product').write_text(product + '\n')
        (self.sysfs / event / 'dev').write_text(dev + '\n')
        if udev is not None:
            self.udev.mkdir(exist_ok=True)
            (self.udev / ('c' + dev)).write_text(udev)

    def run_main(self, names, *args):
        def compositor(*command):
            if command == ('devices', '-j'):
                return json.dumps({'mice': [{'name': n} for n in names]})
            return 'ok'
        with patch.object(m, 'hypr', side_effect=compositor) as run, \
                patch.object(m.sys, 'argv', ['trackpads.py', *args]), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            m.main()
        return json.loads(output.getvalue()), run

    def test_all_builtin_apple_names_are_grouped_without_mouse(self):
        names = sorted(m.BUILTIN_APPLE)
        groups = m.group_devices([{'name': n} for n in names + ['usb-mouse', 'bcm5974-mouse']])
        self.assertEqual(set(groups), {'apple'})
        self.assertEqual(groups['apple']['names'], names)

    def test_lone_legacy_builtin_rekeys_without_changing_settings_or_undo(self):
        for name in sorted(m.LEGACY_APPLE):
            state = self.legacy_apple_state(name)
            before = copy.deepcopy(state)
            migrated = m.migrate(state)
            self.assertNotIn(name, migrated['devices'])
            expected = dict(state['devices'][name], id='apple', label='Apple')
            self.assertEqual(migrated['devices']['apple'], expected)
            self.assertEqual(m.lua_for(migrated['devices']), m.lua_for(state['devices']))
            self.assertEqual(m.migrate(migrated), migrated)
            self.assertEqual(state, before)
            with patch.object(m, 'hypr'):
                changed = m.change(migrated, name, 'scroll_factor', 0.1)
            self.assertEqual(changed['devices']['apple']['settings']['scroll_factor'], 0.1)
            self.assertEqual(changed['devices']['dell'], migrated['devices']['dell'])

    def test_apple_collision_preserves_both_preferences_and_refresh_ownership(self):
        for name in sorted(m.LEGACY_APPLE):
            for configured, legacy_configured in product((False, True), repeat=2):
                state = self.legacy_apple_state(name, with_apple=True)
                state['devices'][name]['configured'] = legacy_configured
                state['devices']['apple']['configured'] = configured
                state['devices']['apple']['settings']['scroll_factor'] = 0.7
                before_rules = m.lua_for(state['devices'])
                m.save(state)
                for names in ([name, 'apple-inc.-magic-trackpad'], [], [name]):
                    view, run = self.run_main(names, 'state')
                    saved = json.loads(m.STATE.read_text())
                    self.assertEqual(set(saved['devices']), set(state['devices']))
                    for key in state['devices']:
                        self.assertEqual(saved['devices'][key]['settings'], state['devices'][key]['settings'])
                        self.assertEqual(saved['devices'][key].get('previous_pointer_feel'),
                                         state['devices'][key].get('previous_pointer_feel'))
                    self.assertEqual(m.lua_for(saved['devices']), before_rules)
                    raw = next(row for row in view['devices'] if row['id'] == name)
                    self.assertEqual(raw['label'], 'Apple (' + name + ')')
                    self.assertEqual(raw['connected'], name in names)
                    self.assertFalse(any(call.args[0] == 'eval' for call in run.call_args_list))
                with patch.object(m, 'hypr') as run:
                    changed = m.change(saved, name, 'sensitivity', -0.2)
                self.assertEqual(changed['devices']['apple'], saved['devices']['apple'])
                self.assertNotIn('magic-trackpad', run.call_args.args[1])

    def test_configured_group_new_interface_applies_before_rules_are_marked_current(self):
        state = m.migrate(self.state)
        m.save(state)
        name = 'apple-mtp-multi-touch'
        _, run = self.run_main([name], 'state')
        self.assertTrue(any(call.args[0] == 'eval' and name in call.args[1]
                            for call in run.call_args_list))
        _, run = self.run_main([name], 'state')
        self.assertFalse(any(call.args[0] == 'eval' for call in run.call_args_list))

    def test_new_interface_refresh_failure_retries_from_authoritative_json(self):
        for fail_target in ('eval', 'generated'):
            state = m.migrate(self.state)
            m.save(state)
            original = m.atomic_write
            def write(path, content):
                if path == m.GENERATED and fail_target == 'generated':
                    raise OSError('rules unavailable')
                return original(path, content)
            def compositor(*args):
                if args[0] == 'devices':
                    return '{"mice": [{"name": "apple-mtp-multi-touch"}]}'
                if fail_target == 'eval':
                    raise RuntimeError('compositor unavailable')
                return 'ok'
            with patch.object(m, 'hypr', side_effect=compositor), \
                    patch.object(m, 'atomic_write', side_effect=write), \
                    patch.object(m.sys, 'argv', ['trackpads.py', 'state']):
                with self.assertRaises((OSError, RuntimeError)):
                    m.main()
            self.assertEqual(m.GENERATED.read_text(), m.lua_for(state['devices']))
            self.assertIn('apple-mtp-multi-touch', json.loads(m.STATE.read_text())['devices']['apple']['names'])
            _, run = self.run_main(['apple-mtp-multi-touch'], 'state')
            self.assertTrue(any(call.args[0] == 'eval' for call in run.call_args_list))
            self.assertEqual(m.GENERATED.read_text(), m.lua_for(json.loads(m.STATE.read_text())['devices']))

    def test_new_interface_refresh_leaves_unrelated_groups_out_of_live_apply(self):
        m.save(m.migrate(self.state))
        _, run = self.run_main(['apple-mtp-multi-touch'], 'state')
        applied = [call.args[1] for call in run.call_args_list if call.args[0] == 'eval']
        self.assertEqual(len(applied), 1)
        self.assertNotIn('ven_06cb', applied[0])
        self.assertIn('apple-mtp-multi-touch', applied[0])

    def test_legacy_rekey_main_accepts_stale_edit_and_preserves_other_groups(self):
        for name in sorted(m.LEGACY_APPLE):
            state = self.legacy_apple_state(name)
            m.save(state)
            view, run = self.run_main([name], 'set', name, 'scroll_factor', '0.15')
            rows = {row['id']: row for row in view['devices']}
            self.assertEqual(rows['apple']['settings']['scroll_factor'], 0.15)
            self.assertTrue(rows['apple']['connected'])
            self.assertEqual(rows['dell']['settings'], state['devices']['dell']['settings'])
            self.assertEqual(sum(call.args[0] == 'eval' for call in run.call_args_list), 1)

    def test_two_legacy_builtin_groups_keep_independent_owners_without_apple(self):
        state = self.legacy_apple_state('bcm5974')
        spi = self.legacy_apple_state('apple-spi-trackpad')['devices']['apple-spi-trackpad']
        spi['settings']['scroll_factor'] = 0.1
        state['devices']['apple-spi-trackpad'] = spi
        m.save(state)
        view, _ = self.run_main(['bcm5974', 'apple-spi-trackpad'], 'state')
        rows = {row['id']: row for row in view['devices']}
        self.assertEqual(set(rows), set(state['devices']))
        self.assertEqual(rows['bcm5974']['settings']['scroll_factor'], 0.34)
        self.assertEqual(rows['apple-spi-trackpad']['settings']['scroll_factor'], 0.1)
        self.assertTrue(rows['bcm5974']['connected'])
        self.assertTrue(rows['apple-spi-trackpad']['connected'])

    def test_interface_limit_rejects_refresh_without_writing_or_applying(self):
        state = m.migrate(self.state)
        state['devices']['apple']['names'] = ['apple-inc.-magic-trackpad-' + str(i) for i in range(32)]
        m.save(state)
        before = (m.STATE.read_bytes(), m.GENERATED.read_bytes())
        with self.assertRaisesRegex(ValueError, 'Invalid trackpad names'):
            self.run_main(['apple-mtp-multi-touch'], 'state')
        self.assertEqual((m.STATE.read_bytes(), m.GENERATED.read_bytes()), before)

    def test_legacy_curve_refresh_succeeds_first_time(self):
        for generated in (None, '-- rules from an older version\n'):
            state = copy.deepcopy(self.state)
            state['devices']['apple']['settings'].update(
                accel_profile='custom', curve={'precision': 0.4, 'transition': 0.9, 'fast': 1.6})
            m.STATE.write_text(json.dumps(state))
            if generated is None:
                m.GENERATED.unlink(missing_ok=True)
            else:
                m.GENERATED.write_text(generated)
            _, run = self.run_main(['apple-inc.-magic-trackpad'], 'state')
            migrated = m.migrate(state)
            self.assertEqual(json.loads(m.STATE.read_text()), migrated)
            self.assertEqual(m.GENERATED.read_text(), m.lua_for(migrated['devices']))
            self.assertEqual(sum(call.args[0] == 'eval' for call in run.call_args_list), 1)
            _, run = self.run_main(['apple-inc.-magic-trackpad'], 'state')
            self.assertFalse(any(call.args[0] == 'eval' for call in run.call_args_list))

    def test_damaged_rules_and_new_interface_reapply_all_groups_once(self):
        m.save(m.migrate(self.state))
        m.GENERATED.write_text('-- damaged rules\n')
        _, run = self.run_main(['apple-mtp-multi-touch'], 'state')
        applied = [call.args[1] for call in run.call_args_list if call.args[0] == 'eval']
        self.assertEqual(len(applied), 1)
        self.assertIn('apple-mtp-multi-touch', applied[0])
        self.assertIn('ven_06cb', applied[0])
        self.assertEqual(m.GENERATED.read_text(), m.lua_for(json.loads(m.STATE.read_text())['devices']))
        _, run = self.run_main(['apple-mtp-multi-touch'], 'state')
        self.assertFalse(any(call.args[0] == 'eval' for call in run.call_args_list))

    def test_refresh_json_write_failure_leaves_rules_and_runtime_untouched(self):
        m.save(m.migrate(self.state))
        before = (m.STATE.read_bytes(), m.GENERATED.read_bytes())
        write = m.atomic_write
        def fail_json(path, content):
            if path == m.STATE:
                raise OSError('state unavailable')
            return write(path, content)
        with patch.object(m, 'atomic_write', side_effect=fail_json), \
                patch.object(m, 'hypr', return_value='{"mice":[{"name":"apple-mtp-multi-touch"}]}') as run, \
                patch.object(m.sys, 'argv', ['trackpads.py', 'state']):
            with self.assertRaisesRegex(OSError, 'state unavailable'):
                m.main()
        self.assertEqual((m.STATE.read_bytes(), m.GENERATED.read_bytes()), before)
        self.assertFalse(any(call.args[0] == 'eval' for call in run.call_args_list))
        _, run = self.run_main(['apple-mtp-multi-touch'], 'state')
        self.assertTrue(any(call.args[0] == 'eval' for call in run.call_args_list))
        self.assertIn('apple-mtp-multi-touch', json.loads(m.STATE.read_text())['devices']['apple']['names'])

    def test_legacy_sensitivity_import_accepts_ps2_slash(self):
        name = 'synps/2-synaptics-touchpad'
        legacy = 'hl.device({ name = "' + name + '", sensitivity = -0.4 })'
        with patch.object(m, 'defaults', return_value=self.groups['dell']['settings']), \
                patch.object(m, 'read_state_file', return_value=legacy):
            state = m.initialize(m.group_devices([{'name': name}]))
        self.assertEqual(state['devices'][name]['settings']['sensitivity'], -0.4)
        self.assertFalse(state['devices'][name]['configured'])

    def test_state_files_reject_links_special_files_and_large_input(self):
        target = m.STATE.parent / 'target'
        target.write_text('keep me')
        m.STATE.symlink_to(target)
        for operation in [lambda: m.read_state_file(m.STATE), lambda: m.atomic_write(m.STATE, 'replace')]:
            with self.assertRaises((ValueError, OSError)):
                operation()
        self.assertEqual(target.read_text(), 'keep me')
        m.STATE.unlink()
        os.mkfifo(m.STATE)
        with self.assertRaises(ValueError):
            m.read_state_file(m.STATE)
        with self.assertRaises(ValueError):
            m.atomic_write(m.STATE, 'replace')
        m.STATE.unlink()
        os.link(target, m.STATE)
        with self.assertRaises(ValueError):
            m.read_state_file(m.STATE)
        m.STATE.unlink()
        m.STATE.write_bytes(b'x' * (m.MAX_STATE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, '1 MiB'):
            m.read_state_file(m.STATE)

    def test_parent_symlink_is_not_followed_and_writes_are_private(self):
        link = m.STATE.parent / 'link'
        link.symlink_to(m.STATE.parent, target_is_directory=True)
        with self.assertRaises(OSError):
            m.atomic_write(link / 'escaped', 'no')
        self.assertFalse((m.STATE.parent / 'escaped').exists())
        m.atomic_write(m.STATE, 'safe')
        self.assertEqual(m.STATE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(m.read_state_file(m.STATE), 'safe')

    def test_future_and_malformed_state_is_rejected(self):
        for state in [dict(self.state, version=m.SCHEMA + 1), dict(self.state, version=True),
                      dict(self.state, extra='unsupported')]:
            with self.assertRaises(ValueError):
                m.migrate(state)
        for mutate in [lambda s: s['devices']['apple']['names'].append('bad"lua'),
                       lambda s: s['devices']['apple']['settings'].update(scroll_factor=0),
                       lambda s: s['devices']['apple'].update(id='other'),
                       lambda s: s['devices']['apple'].update(previous_pointer_feel={'profile': 'bad', 'curve': m.DEFAULT_CURVE})]:
            state = copy.deepcopy(self.state)
            mutate(state)
            with self.assertRaises(ValueError):
                m.migrate(state)

    def test_apply_failure_attempts_live_rollback_and_preserves_original_error(self):
        with patch.object(m, 'hypr', side_effect=[RuntimeError('partial apply'), 'ok']) as run:
            with self.assertRaisesRegex(RuntimeError, 'partial apply'):
                m.change(self.state, 'apple', 'sensitivity', 0.9)
        self.assertEqual(run.call_count, 2)
        self.assertIn('sensitivity = 0.1', run.call_args.args[1])
        self.assertFalse(m.STATE.with_suffix('.pending.json').exists())

    def test_interrupted_edit_recovers_even_when_rule_file_matches_json(self):
        m.save(self.state)
        m.atomic_write(m.STATE.with_suffix('.pending.json'), json.dumps({'state': self.state, 'device': 'apple'}))
        with patch.object(m, 'hypr') as run:
            m.recover_pending()
        self.assertIn('sensitivity = 0.1', run.call_args.args[1])
        self.assertNotIn('ven_06cb', run.call_args.args[1])
        self.assertEqual(json.loads(m.STATE.read_text()), self.state)
        self.assertFalse(m.STATE.with_suffix('.pending.json').exists())

    def test_failed_rollback_keeps_journal_for_next_process(self):
        with patch.object(m, 'hypr', side_effect=RuntimeError('compositor unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'recovery is pending'):
                m.change(self.state, 'apple', 'sensitivity', 0.9)
        self.assertTrue(m.STATE.with_suffix('.pending.json').exists())
        with patch.object(m, 'hypr'):
            m.recover_pending()
        self.assertEqual(json.loads(m.STATE.read_text()), self.state)

    def test_failure_at_either_persistence_file_restores_previous_state(self):
        original_write = m.atomic_write
        for target in [m.STATE, m.GENERATED]:
            m.save(self.state)
            failed = False
            def fail_once(path, content):
                nonlocal failed
                if path == target and not failed:
                    failed = True
                    raise OSError('simulated disk failure')
                return original_write(path, content)
            with patch.object(m, 'atomic_write', side_effect=fail_once), patch.object(m, 'hypr') as run:
                with self.assertRaisesRegex(OSError, 'simulated disk failure'):
                    m.change(self.state, 'apple', 'sensitivity', 0.9)
            self.assertEqual(json.loads(m.STATE.read_text()), self.state)
            self.assertEqual(m.GENERATED.read_text(), m.lua_for(self.state['devices']))
            self.assertIn('sensitivity = 0.1', run.call_args.args[1])
            self.assertFalse(m.STATE.with_suffix('.pending.json').exists())

    def test_crash_after_committing_both_files_restores_journal_snapshot(self):
        with patch.object(m, 'hypr'), patch.object(m, 'clear_pending', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                m.change(self.state, 'apple', 'sensitivity', 0.9)
        self.assertEqual(json.loads(m.STATE.read_text())['devices']['apple']['settings']['sensitivity'], 0.9)
        with patch.object(m, 'hypr'):
            m.recover_pending()
        self.assertEqual(json.loads(m.STATE.read_text()), self.state)
        self.assertEqual(m.GENERATED.read_text(), m.lua_for(self.state['devices']))

    def test_file_and_native_validation_releases_descriptors(self):
        m.atomic_write(m.STATE, 'data')
        before = len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(100):
            self.assertEqual(m.read_state_file(m.STATE), 'data')
            m.atomic_write(m.STATE, 'data')
            m.validate_native_curve(m.DEFAULT_CURVE)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)

    def test_persistent_disk_failure_still_attempts_live_rollback(self):
        with patch.object(m, 'save', side_effect=OSError('disk full')), patch.object(m, 'hypr') as run:
            with self.assertRaisesRegex(RuntimeError, 'disk full; recovery is pending'):
                m.change(self.state, 'apple', 'sensitivity', 0.9)
        self.assertIn('sensitivity = 0.1', run.call_args.args[1])
        self.assertTrue(m.STATE.with_suffix('.pending.json').exists())

    def test_scroll_scale_changes_only_selected_group_and_preserves_pointer(self):
        state = m.migrate(self.state)
        state['devices']['apple']['settings']['scroll_factor'] = 1
        state['devices']['apple']['settings'].update(accel_profile='custom', curve=m.DEFAULT_CURVE)
        with patch.object(m, 'hypr') as run:
            updated = m.change(state, 'apple', 'scroll_scale', 3)
        settings = updated['devices']['apple']['settings']
        self.assertEqual(settings['scroll_factor'], 3)
        self.assertEqual(settings['scroll_scale'], 3)
        self.assertEqual(settings['curve'], m.DEFAULT_CURVE)
        self.assertEqual(settings['sensitivity'], state['devices']['apple']['settings']['sensitivity'])
        self.assertEqual(updated['devices']['dell'], state['devices']['dell'])
        self.assertIn('scroll_factor = 3', run.call_args.args[1])
        self.assertNotIn('scroll_scale', run.call_args.args[1])
        self.assertEqual(json.loads(m.STATE.read_text()), updated)
        with patch.object(m, 'hypr'):
            restored = m.change(updated, 'apple', 'scroll_scale', 1)
        self.assertEqual(restored, state)

    def test_scroll_scale_migration_preserves_effective_values_and_lua(self):
        for factor in [0.01, 0.05, 0.5, 1, 1.5, 2]:
            state = m.migrate(self.state)
            state['version'] = 3
            for group in state['devices'].values():
                group['settings'].pop('scroll_scale')
                group['settings']['scroll_factor'] = factor
            expected = m.lua_for(state['devices'])
            updated = m.migrate(state)
            self.assertEqual(m.lua_for(updated['devices']), expected)
            for group in updated['devices'].values():
                self.assertEqual(group['settings']['scroll_factor'], factor)
                self.assertEqual(group['settings']['scroll_scale'], max(1, factor))
            self.assertEqual(m.migrate(updated), updated)

    def test_scroll_scale_bounds_and_save_failure(self):
        state = m.migrate(self.state)
        for value in [0, 0.09, 10.01, True, '3', float('nan')]:
            with patch.object(m, 'hypr') as run, self.assertRaises(ValueError):
                m.change(state, 'apple', 'scroll_scale', value)
            run.assert_not_called()
        with patch.object(m, 'hypr'), patch.object(m, 'save', side_effect=[OSError('disk full'), None]):
            with self.assertRaises(OSError):
                m.change(state, 'apple', 'scroll_scale', 3)
        self.assertEqual(state['devices']['apple']['settings']['scroll_scale'], 1)
        with patch.object(m, 'hypr'):
            slow = m.change(state, 'apple', 'scroll_scale', 0.1)
            slow = m.change(slow, 'apple', 'scroll_factor', 0.001)
        self.assertAlmostEqual(slow['devices']['apple']['settings']['scroll_factor'] / 0.1, 0.01)

    def test_scaled_mac_preset_and_wider_curve_are_applied_as_displayed(self):
        curve = dict(m.DEFAULT_CURVE, precision=0.1875, fast=1)
        with patch.object(m, 'hypr') as run:
            updated = m.change(m.migrate(self.state), 'apple', 'pointer_feel', {'profile': 'mac', 'curve': curve})
        self.assertEqual(updated['devices']['apple']['settings']['curve'], curve)
        self.assertIn(m.curve_profile(curve), run.call_args.args[1])
        m.validate_native_curve(dict(m.DEFAULT_CURVE, precision=3, fast=10))
        with self.assertRaises(ValueError):
            m.validate_curve(dict(m.DEFAULT_CURVE, fast=10.01))

    def test_groups_two_apple_interfaces_without_mouse(self):
        self.assertEqual(set(self.groups),{'apple','dell'})
        self.assertEqual(len(self.groups['apple']['names']),2)

    def test_lenovo_synaptics_without_touchpad_suffix_excludes_trackpoint(self):
        # TM3512-010 and TM3381-002 (ThinkPad X280) are one Synaptics family.
        # The suffixed name proves the match is anchored to the whole name.
        names = ['synaptics-tm3512-010', 'synaptics-tm3381-002', 'synaptics-tm2768-001']
        others = ['tpps/2-elan-trackpoint', 'usb-mouse', 'synaptics-usb-mouse',
                  'synaptics-tm3381-002-trackpoint']
        groups = m.group_devices([{'name': n} for n in names + others])
        self.assertEqual(set(groups), set(names))
        for name in names:
            self.assertEqual(groups[name]['names'], [name])

    def test_ps2_synaptics_touchpad_slash_in_name_is_accepted(self):
        # ThinkPads (e.g. the T470) report the classic PS/2 Synaptics driver as
        # "synps/2-synaptics-touchpad" -- a literal '/' in the Hyprland device
        # name. It must be both detected and pass name validation.
        name = 'synps/2-synaptics-touchpad'
        self.assertEqual(m.validate_name(name), name)
        groups = m.group_devices([{'name': n} for n in [
            name, 'tpps/2-ibm-trackpoint', 'usb-mouse']])
        self.assertEqual(set(groups), {name})
        self.assertEqual(groups[name]['names'], [name])

    def test_acceleration_migration_preserves_existing_settings(self):
        migrated = m.migrate(self.state)
        self.assertEqual(migrated['version'], m.SCHEMA)
        for key in self.groups:
            settings = dict(migrated['devices'][key]['settings'])
            self.assertEqual(settings.pop('accel_profile'), 'adaptive')
            self.assertEqual(settings.pop('scroll_scale'), 1)
            self.assertEqual(settings, self.groups[key]['settings'])
        migrated['devices']['apple']['settings']['accel_profile'] = 'flat'
        self.assertEqual(m.migrate(migrated), migrated)

    def test_acceleration_change_targets_only_selected_trackpad(self):
        state = m.migrate(self.state)
        with patch.object(m, 'hypr') as run, patch.object(m, 'save'):
            updated = m.change(state, 'apple', 'accel_profile', 'flat')
        self.assertEqual(updated['devices']['dell'], state['devices']['dell'])
        self.assertEqual(run.call_args.args[1].count('accel_profile = "flat"'), 2)
        self.assertNotIn('ven_06cb', run.call_args.args[1])
        for value in [True, None, 0, 'custom 1 0 1', 'bad"lua', []]:
            with self.assertRaises(ValueError):
                m.validate_setting('accel_profile', value)

    def test_change_apple_only_and_persist_both(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m,'STATE',Path(d)/'settings.json'), patch.object(m,'GENERATED',Path(d)/'settings.lua'), patch.object(m,'hypr') as run:
            updated=m.change(self.state,'apple','scroll_factor',0.7)
            self.assertEqual(updated['devices']['dell'],self.state['devices']['dell'])
            self.assertEqual(updated['devices']['apple']['settings']['scroll_factor'],0.7)
            lua=run.call_args.args[1]
            self.assertFalse(lua.lstrip().startswith('-'), 'Lua must not be parsed as a hyprctl flag')
            self.assertNotIn('ven_06cb',lua)
            self.assertEqual(lua.count('hl.device('),2)
            self.assertNotIn('hl.config(',m.GENERATED.read_text())
            self.assertEqual(json.loads(m.STATE.read_text()),updated)

    def test_invalid_settings_and_names_rejected_before_apply(self):
        for key,value in [('sensitivity',9),('scroll_factor',float('nan')),('scroll_factor',0.009),('scroll_factor',10.01),('enabled','false'),('unknown',True)]:
            with patch.object(m,'hypr') as run, self.assertRaises(ValueError):m.change(self.state,'apple',key,value)
            run.assert_not_called()
        with self.assertRaises(ValueError):m.validate_name('bad" }); os.execute("x")')

    def test_disabled_or_unplugged_device_remains_selectable(self):
        state=copy.deepcopy(self.state)
        state['devices']['apple']['settings']['enabled']=False
        view=m.snapshot(state,{'dell':self.groups['dell']})
        apple=next(g for g in view['devices'] if g['id']=='apple')
        self.assertFalse(apple['connected'])
        self.assertFalse(apple['settings']['enabled'])
        self.assertEqual(len(apple['names']),2)

    def test_apply_error_does_not_persist(self):
        with patch.object(m,'hypr',side_effect=RuntimeError('rejected')),patch.object(m,'save') as save:
            with self.assertRaises(RuntimeError):m.change(self.state,'dell','sensitivity',0.9)
            save.assert_not_called()

    def test_save_failure_rolls_back(self):
        with patch.object(m,'hypr') as run,patch.object(m,'save',side_effect=[OSError('disk full'),None]):
            with self.assertRaises(OSError):m.change(self.state,'apple','sensitivity',0.5)
            self.assertIn('sensitivity = 0.1',run.call_args.args[1])
            self.assertNotIn('ven_06cb',run.call_args.args[1])

    def test_builtin_apple_is_discovered_and_existing_id_is_preserved(self):
        groups = m.group_devices([{'name': 'apple-mtp-multi-touch'}, {'name': 'usb-mouse'}])
        self.assertEqual(list(groups), ['apple'])
        self.assertEqual(groups['apple']['names'], ['apple-mtp-multi-touch'])

    def test_curve_apply_is_atomic_and_only_emits_native_settings(self):
        state = m.migrate(self.state)
        with patch.object(m, 'hypr') as run, patch.object(m, 'save'):
            updated = m.change(state, 'apple', 'pointer_feel', {'profile': 'mac', 'curve': m.DEFAULT_CURVE})
        self.assertEqual(updated['devices']['dell'], state['devices']['dell'])
        settings = updated['devices']['apple']['settings']
        self.assertEqual(settings['accel_profile'], 'custom')
        self.assertEqual(settings['sensitivity'], 0.1)
        lua = run.call_args.args[1]
        self.assertIn('accel_profile = "custom 0.1 0.000000', lua)
        self.assertIn('scroll_points = "1 0 1"', lua)
        self.assertNotIn('curve =', lua)
        self.assertNotIn('curve_preset', lua)
        with patch.object(m, 'hypr') as run, patch.object(m, 'save', side_effect=[OSError('disk full'), None]):
            with self.assertRaises(OSError):
                m.change(updated, 'apple', 'pointer_feel', {'profile': 'flat', 'curve': m.DEFAULT_CURVE})
        self.assertIn('accel_profile = "custom 0.1', run.call_args.args[1])

    def test_invalid_curve_is_rejected_without_touching_compositor(self):
        for curve in [None, {}, dict(m.DEFAULT_CURVE, precision=True),
                      dict(m.DEFAULT_CURVE, precision=0.009),
                      dict(m.DEFAULT_CURVE, precision=0.01, fast=0.009),
                      dict(m.DEFAULT_CURVE, fast=float('inf')),
                      dict(m.DEFAULT_CURVE, fast=0.2),
                      dict(m.DEFAULT_CURVE, start=2.7),
                      dict(m.DEFAULT_CURVE, end=0.5),
                      dict(m.DEFAULT_CURVE, start=float("nan")),
                      dict(m.DEFAULT_CURVE, extra=1)]:
            with patch.object(m, 'hypr') as run, self.assertRaises(ValueError):
                m.change(self.state, 'apple', 'pointer_feel', {'profile': 'custom', 'curve': curve})
            run.assert_not_called()

    def test_precision_range_stays_flat_and_ramp_end_is_independent(self):
        curve = dict(m.DEFAULT_CURVE)
        points = list(map(float, m.curve_profile(curve).split()[2:]))
        for index in range(1, 9):
            self.assertAlmostEqual(points[index] / (index * 0.1), curve['precision'])
        self.assertGreater(points[16] / 1.6, curve['precision'])
        self.assertAlmostEqual(points[28] / 2.8, curve['fast'])
        later_end = list(map(float, m.curve_profile(dict(curve, end=3.6)).split()[2:]))
        self.assertEqual(later_end[:9], points[:9])
        self.assertLess(later_end[20], points[20])

    def test_legacy_curve_migration_preserves_response(self):
        state = m.migrate(self.state)
        state['devices']['apple']['settings'].update(accel_profile='custom', curve_preset='mac',
            curve={'precision': 0.4, 'transition': 0.9, 'fast': 1.6})
        updated = m.migrate(state)
        settings = updated['devices']['apple']['settings']
        self.assertEqual(settings['curve'], {'precision': 0.4, 'start': 0, 'end': 1.8, 'fast': 1.6})
        self.assertEqual(settings['curve_preset'], 'custom')
        values = list(map(float, m.curve_profile(settings['curve']).split()[2:]))
        for index, value in enumerate(values):
            speed = index * 0.1
            t = min(1, speed / 1.8)
            self.assertAlmostEqual(value, speed * (0.4 + 1.2 * t * t * (3 - 2 * t)), places=5)
        self.assertEqual(m.migrate(updated), updated)

    def test_graph_matches_backend_and_has_constant_gain_tail(self):
        curves = [m.DEFAULT_CURVE, {'precision': 0.08, 'start': 2.0, 'end': 3.6, 'fast': 0.65}, {'precision': 0.2, 'start': 0, 'end': 0.4, 'fast': 3.5},
                  {'precision': 1.5, 'start': 3.4, 'end': 3.6, 'fast': 1.5},
                  dict(m.DEFAULT_CURVE, end=3.96), dict(m.DEFAULT_CURVE, end=4),
                  dict(m.DEFAULT_CURVE, start=3.8, end=4)]
        script = "const c=require('./Curve.js'); console.log(JSON.stringify(JSON.parse(process.argv[1]).map(x=>c.points(x))))"
        plotted = json.loads(subprocess.check_output(['node', '-e', script, json.dumps(curves)], cwd=Path(__file__).parent))
        for curve, graph in zip(curves, plotted):
            points = list(map(float, m.curve_profile(curve).split()[2:]))
            self.assertEqual(graph, points)
            self.assertEqual(points[0], 0)
            self.assertEqual(points, sorted(points))
            self.assertAlmostEqual((points[-1] - points[-2]) / 0.1, curve['fast'])

    def test_legacy_custom_and_undo_remain_unscaled_on_migration(self):
        self.add_input_device('event5', 'Apple Inc. Magic Trackpad', '13:69', '05ac', '0265')
        state = m.migrate(self.state)
        state['version'] = 4
        group = state['devices']['apple']
        group['settings'].update(accel_profile='custom', curve=dict(m.DEFAULT_CURVE), curve_preset='custom')
        group['previous_pointer_feel'] = {'profile': 'custom', 'curve': dict(m.DEFAULT_CURVE, fast=2)}
        migrated = m.migrate(state)
        self.assertIn('accel_profile = "' + m.curve_profile(m.DEFAULT_CURVE) + '"', m.lua_for(migrated['devices']))
        with patch.object(m, 'hypr', return_value='ok'):
            changed = m.change(migrated, 'apple', 'pointer_feel', {'profile': 'mac', 'curve': m.DEFAULT_CURVE})
            restored = m.change(changed, 'apple', 'pointer_feel', changed['devices']['apple']['previous_pointer_feel'])
            self.assertEqual(m.lua_for(restored['devices']), m.lua_for(migrated['devices']))
            old_undo = m.change(migrated, 'apple', 'pointer_feel', migrated['devices']['apple']['previous_pointer_feel'])
            self.assertIn('accel_profile = "' + m.curve_profile(dict(m.DEFAULT_CURVE, fast=2)) + '"', m.lua_for(old_undo['devices']))

    def test_explicit_curve_calibration_persists_when_sensor_disconnects_and_undo_restores_it(self):
        self.add_input_device('event5', 'Apple Inc. Magic Trackpad', '13:69', '05ac', '0265')
        with patch.object(m, 'hypr', return_value='ok'):
            state = m.change(m.migrate(self.state), 'apple', 'pointer_feel', {'profile': 'mac', 'curve': m.DEFAULT_CURVE})
            group = state['devices']['apple']
            self.assertEqual(group['curve_calibration'], {'apple-inc.-magic-trackpad': 47})
            connected = m.lua_for(state['devices'])
            (self.sysfs / 'event5').rename(self.sysfs / 'disconnected')
            self.assertEqual(m.lua_for(state['devices']), connected)
            m.save(state)
            self.assertEqual(m.GENERATED.read_text(), connected)
            changed = m.change(state, 'apple', 'pointer_feel', {'profile': 'flat', 'curve': m.DEFAULT_CURVE})
            restored = m.change(changed, 'apple', 'pointer_feel', changed['devices']['apple']['previous_pointer_feel'])
            self.assertEqual(m.lua_for(restored['devices']), connected)

    def test_saved_calibration_rendering_never_depends_on_live_discovery(self):
        self.add_input_device('event5', 'Apple Inc. Magic Trackpad', '13:69', '05ac', '0265')
        state = m.migrate(self.state)
        group = state['devices']['apple']
        group['settings'].update(accel_profile='custom', curve=dict(m.DEFAULT_CURVE), curve_preset='mac')
        group['curve_calibration'] = {'apple-inc.-magic-trackpad': 47}
        connected = m.lua_for(state['devices'])
        (self.sysfs / 'event5').rename(self.sysfs / 'disconnected')
        self.assertEqual(m.lua_for(state['devices']), connected)
        with patch.object(m, 'device_resolution', side_effect=AssertionError('Rendering must use saved calibration')):
            self.assertEqual(m.lua_for(state['devices']), connected)

    def test_explicit_undo_without_calibration_preserves_saved_spacing(self):
        self.add_input_device('event5', 'Apple Inc. Magic Trackpad', '13:69', '05ac', '0265')
        with patch.object(m, 'hypr', return_value='ok'):
            legacy = m.migrate(self.state)
            legacy['devices']['apple']['settings'].update(accel_profile='custom', curve=dict(m.DEFAULT_CURVE), curve_preset='custom')
            changed = m.change(legacy, 'apple', 'pointer_feel', {'profile': 'mac', 'curve': dict(m.DEFAULT_CURVE, fast=2)})
            previous = changed['devices']['apple']['previous_pointer_feel']
            restored = m.change(changed, 'apple', 'pointer_restore', {k: previous[k] for k in ('profile', 'curve')})
            self.assertEqual(m.lua_for(restored['devices']), m.lua_for(legacy['devices']))

    def test_invalid_or_unowned_calibration_is_rejected_before_writes(self):
        for calibration in [{'other-trackpad': 47}, {'apple-inc.-magic-trackpad': True},
                            {'apple-inc.-magic-trackpad': 0}, {'apple-inc.-magic-trackpad': float('inf')},
                            {'apple-inc.-magic-trackpad': 65536}, []]:
            state = m.migrate(self.state)
            state['devices']['apple']['curve_calibration'] = calibration
            with patch.object(m, 'atomic_write') as write, self.assertRaises(ValueError):
                m.save(state)
            write.assert_not_called()
        with patch.object(m, 'atomic_write') as write, self.assertRaises(ValueError):
            m.change(m.migrate(self.state), 'apple', 'pointer_feel',
                     {'profile': 'mac', 'curve': m.DEFAULT_CURVE, 'calibration': {'apple-inc.-magic-trackpad': 47}})
        write.assert_not_called()

    def test_unique_native_name_ending_digits_can_be_calibrated(self):
        self.add_input_device('event5', 'Synaptics TM3512-010', '13:69',
                              udev='E:EVDEV_ABS_00=::42\n')
        self.assertEqual(m.device_resolution('synaptics-tm3512-010'), 42)
        self.assertIsNone(m.device_resolution('synaptics-tm3512-010-1'))
        self.add_input_device('event7', 'Synaptics TM3512-010', '13:71',
                              udev='E:EVDEV_ABS_00=::80\n')
        self.assertIsNone(m.device_resolution('synaptics-tm3512-010'))
        self.assertIsNone(m.device_resolution('synaptics-tm3512-010-1'))

    def test_duplicate_sensor_names_never_guess_calibration(self):
        self.add_input_device('event5', 'Apple Inc. Magic Trackpad', '13:69', '05ac', '0265')
        self.add_input_device('event7', 'Apple Inc. Magic Trackpad', '13:71', '05ac', '0324', udev='E:EVDEV_ABS_00=::80\n')
        self.assertIsNone(m.device_resolution('apple-inc.-magic-trackpad'))
        self.assertIsNone(m.device_resolution('apple-inc.-magic-trackpad-1'))
        self.assertIsNone(m.device_resolution('apple-inc.-magic-trackpad-9'))

    def test_bluetooth_apple_kernel_resolution(self):
        self.add_input_device('event5', 'Apple Inc. Magic Trackpad', '13:69', '004c', '0265')
        self.assertEqual(m.device_resolution('apple-inc.-magic-trackpad'), 47)

    def test_custom_curve_spacing_follows_device_resolution(self):
        curve = dict(m.DEFAULT_CURVE, precision=0.1875, fast=1)
        unscaled = m.curve_profile(curve).split()
        self.assertEqual(unscaled[1], '0.1')
        for resolution in [96, 47, 46, 12]:
            scaled = m.curve_profile(curve, resolution).split()
            # libinput feeds touchpad custom curves raw device units: scaling the
            # spacing alone keeps the response identical per millimetre of travel.
            self.assertAlmostEqual(float(scaled[1]) / resolution, 0.1 / m.NORMALIZED_UNITS_PER_MM)
            self.assertEqual(scaled[2:], unscaled[2:])
            m.validate_native_profile(' '.join(scaled))

    def test_device_resolution_reads_udev_overrides_and_known_kernel_values(self):
        self.add_input_device('event5', 'Apple SPI Touchpad', '13:69',
                              udev='E:ID_INPUT=1\nE:EVDEV_ABS_00=::96\nE:EVDEV_ABS_01=::95\n')
        self.add_input_device('event7', 'Apple Inc. Magic Trackpad', '13:71', '05ac', '0265')
        self.add_input_device('event8', 'Apple Inc. Magic Trackpad', '13:72', '05ac', '0324',
                              udev='E:EVDEV_ABS_00=1:7000:80:0:0\n')
        self.add_input_device('event9', 'Unknown Touchpad', '13:73', '06cb', 'd01d', udev='E:ID_INPUT=1\n')
        self.assertEqual(m.device_resolution('apple-spi-touchpad'), 96)
        self.assertIsNone(m.device_resolution('apple-inc.-magic-trackpad'))
        self.assertIsNone(m.device_resolution('apple-inc.-magic-trackpad-1'))
        self.assertIsNone(m.device_resolution('unknown-touchpad'))
        self.assertIsNone(m.device_resolution('missing-touchpad'))
        (self.sysfs / 'event7').rename(self.sysfs / 'disconnected')
        self.assertEqual(m.device_resolution('apple-inc.-magic-trackpad'), 80)

    def test_mixed_resolution_group_emits_per_device_curves(self):
        self.add_input_device('event5', 'Apple SPI Touchpad', '13:69', udev='E:EVDEV_ABS_00=::96\n')
        self.add_input_device('event7', 'Apple Inc. Magic Trackpad', '13:71', '05ac', '0265')
        groups = m.group_devices([{'name': 'apple-spi-touchpad'}, {'name': 'apple-inc.-magic-trackpad'},
                                  {'name': 'apple-inc.-magic-trackpad-9'}, {'name': 'unlisted-trackpad'}])
        curve = dict(m.DEFAULT_CURVE, precision=0.1875, fast=1)
        for group in groups.values():
            group['settings'] = {'accel_profile': 'custom', 'curve': curve, 'curve_preset': 'mac'}
            group['curve_calibration'] = {name: resolution for name in group['names']
                                          if (resolution := m.device_resolution(name)) is not None}
        lua = m.lua_for(groups)
        for name, resolution in [('apple-spi-touchpad', 96), ('apple-inc.-magic-trackpad', 47),
                                 ('apple-inc.-magic-trackpad-9', None), ('unlisted-trackpad', None)]:
            self.assertIn('name = "%s", accel_profile = "%s"' % (name, m.curve_profile(curve, resolution)), lua)

    def test_native_libinput_accepts_curve_and_rejects_old_81_point_payload(self):
        m.validate_native_curve(m.DEFAULT_CURVE)
        slow = dict(m.DEFAULT_CURVE, precision=0.01, fast=0.01)
        m.validate_native_curve(slow)
        old_payload = 'custom 0.05 ' + ' '.join(str(i * 0.001) for i in range(81))
        with self.assertRaisesRegex(ValueError, 'libinput rejected.*81 points'):
            m.validate_native_profile(old_payload)

    def test_native_rejection_never_applies_or_saves_settings(self):
        with patch.object(m, 'validate_native_curve', side_effect=ValueError('libinput rejected curve')):
            with patch.object(m, 'hypr') as run, patch.object(m, 'save') as save:
                with self.assertRaisesRegex(ValueError, 'libinput rejected'):
                    m.change(self.state, 'apple', 'pointer_feel', {'profile': 'custom', 'curve': m.DEFAULT_CURVE})
                run.assert_not_called()
                save.assert_not_called()

    def test_full_width_transition_has_constant_native_tail(self):
        for end in [3.96, 4.0]:
            curve = dict(m.DEFAULT_CURVE, end=end)
            m.validate_native_curve(curve)
            points = list(map(float, m.curve_profile(curve).split()[2:]))
            self.assertEqual(len(points), 43)
            for index in [40, 41, 42]:
                self.assertAlmostEqual(points[index] / (index * 0.1), curve['fast'])
            self.assertAlmostEqual((points[-1] - points[-2]) / 0.1, curve['fast'])
            self.assertLess(points[35] / 3.5, curve['fast'])
        m.validate_native_curve(dict(m.DEFAULT_CURVE, start=3.8, end=4))
        with self.assertRaises(ValueError):
            m.validate_curve(dict(m.DEFAULT_CURVE, end=4.01))

    def test_legacy_undo_curve_is_migrated_and_restorable(self):
        state = m.migrate(self.state)
        state['devices']['apple']['previous_pointer_feel'] = {
            'profile': 'mac', 'curve': {'precision': 0.3, 'transition': 0.9, 'fast': 1.6}}
        updated = m.migrate(state)
        previous = updated['devices']['apple']['previous_pointer_feel']
        self.assertEqual(previous['profile'], 'custom')
        self.assertEqual(previous['curve']['end'], 1.8)
        with patch.object(m, 'hypr'), patch.object(m, 'save'):
            restored = m.change(updated, 'apple', 'pointer_feel', previous)
        self.assertEqual(restored['devices']['apple']['settings']['curve'], previous['curve'])
        self.assertEqual(m.migrate(updated), updated)

    def test_save_validates_custom_profile_before_writing_either_file(self):
        state = m.migrate(self.state)
        state['devices']['apple']['settings']['accel_profile'] = 'custom'
        with patch.object(m, 'validate_native_curve', side_effect=ValueError('unsupported libinput')):
            with patch.object(m, 'atomic_write') as write, self.assertRaises(ValueError):
                m.save(state)
            write.assert_not_called()

    def profiles_dir(self):
        directory = Path(tempfile.mkdtemp(dir=m.DIRECTORY))
        directory.chmod(0o700)
        replacement = patch.object(m, 'PROFILES', directory)
        replacement.start()
        self.addCleanup(replacement.stop)
        (directory / MAC_PROFILE.name).write_bytes(MAC_PROFILE.read_bytes())
        return directory

    def compositor(self, *command, monitors=PANEL):
        if command == ('monitors', '-j'):
            return json.dumps(monitors)
        return 'ok'

    def reference(self, name=MAC_PROFILE.name):
        raw = (m.PROFILES / name).read_bytes()
        return {'file': name, 'sha256': m.pointer_profiles.digest(raw)}

    def imported_state(self):
        """Apple group with resolutions for both Magic Trackpad interfaces and a macOS profile."""
        self.profiles_dir()
        units = {'apple-inc.-magic-trackpad': 47.6, 'apple-inc.-magic-trackpad-1': 47.6}
        with patch.object(m, 'hypr', side_effect=self.compositor), patch.object(m, 'save'), NATIVE():
            state = m.change(m.migrate(self.state), 'apple', 'units_per_mm', units)
            state = m.change(state, 'apple', 'pointer_feel',
                             {'profile': 'imported', 'curve': m.DEFAULT_CURVE, 'imported': self.reference()})
        return state

    def test_macos_profile_is_converted_per_interface_and_emitted(self):
        state = self.imported_state()
        settings = state['devices']['apple']['settings']
        imported = settings['imported_curve']
        self.assertEqual(settings['accel_profile'], 'custom')
        self.assertEqual(settings['curve_preset'], 'imported')
        self.assertEqual(settings['curve'], m.DEFAULT_CURVE)  # the custom curve is kept for later
        self.assertEqual(imported['name'], 'MacBook Pro (M1 Pro)')
        self.assertEqual(imported['tracking_speed'], 0.875)  # the Mac's own setting by default
        self.assertEqual(imported['px_per_point'], round(301.21 / 1512 * 3024 / 2 / 302, 6))
        self.assertEqual(set(imported['devices']), {'apple-inc.-magic-trackpad', 'apple-inc.-magic-trackpad-1'})
        device = imported['devices']['apple-inc.-magic-trackpad']
        self.assertEqual(len(device['points']), 64)
        self.assertEqual(device['units_per_mm'], 47.6)
        lua = m.lua_for({'apple': state['devices']['apple']})
        for name in imported['devices']:
            line = next(line for line in lua.splitlines() if json.dumps(name) in line)
            self.assertIn(f'accel_profile = "custom {device["step"]:.4f} 0.000000 ', line)
            self.assertIn('scroll_points = "1 0 1"', line)
            self.assertEqual(line.count('accel_profile'), 1)
        self.assertNotIn('imported_curve', lua)
        self.assertNotIn('units_per_mm', lua)
        self.assertEqual(state['devices']['apple']['previous_pointer_feel']['profile'], 'adaptive')
        self.assertEqual(m.migrate(state), state)

    def test_tracking_speed_selects_apples_curve_for_that_slider_position(self):
        self.profiles_dir()
        group = {'id': 'apple', 'label': 'Apple', 'names': ['apple-spi-trackpad'], 'settings': {}}
        profile = m.pointer_profiles.load_profile(MAC_PROFILE.read_bytes())
        with patch.object(m, 'machine_model', return_value='Apple MacBook Pro (14-inch, M1 Pro, 2021)'):
            default = m.import_profile(group, self.reference(), PANEL[0])
            same = m.import_profile(group, dict(self.reference(), tracking_speed=0.875), PANEL[0])
            faster = m.import_profile(group, dict(self.reference(), tracking_speed=1.5), PANEL[0])
        self.assertEqual(same, default)
        self.assertEqual(faster['tracking_speed'], 1.5)
        scale = m.profile_scale(m.pointer_profiles.mm_per_point(profile), PANEL[0])
        expected = m.pointer_profiles.convert(dict(profile, tracking_speed=1.5), 12312 / 124.8, scale)
        self.assertEqual(faster['devices']['apple-spi-trackpad']['points'], expected['points'])
        self.assertGreater(faster['devices']['apple-spi-trackpad']['points'][10],
                           default['devices']['apple-spi-trackpad']['points'][10])
        m.validate_change('pointer_feel', {'profile': 'imported', 'curve': m.DEFAULT_CURVE,
                                           'imported': dict(self.reference(), tracking_speed=3)})
        for bad in (3.5, -0.1, True, '1'):
            with self.assertRaises(ValueError):
                m.validate_change('pointer_feel', {'profile': 'imported', 'curve': m.DEFAULT_CURVE,
                                                   'imported': dict(self.reference(), tracking_speed=bad)})

    def test_builtin_resolution_is_used_for_known_macbooks(self):
        self.profiles_dir()
        group = {'id': 'apple', 'label': 'Apple', 'names': ['apple-spi-trackpad'], 'settings': {}}
        with patch.object(m, 'machine_model', return_value='Apple MacBook Pro (14-inch, M1 Pro, 2021)'):
            self.assertEqual(m.interface_units(group, 'apple-spi-trackpad', m.machine_model()),
                             (12312 / 124.8, 'built-in'))
            imported = m.import_profile(group, self.reference(), PANEL[0])
        self.assertAlmostEqual(imported['devices']['apple-spi-trackpad']['step'], 0.5211)
        group['settings']['units_per_mm'] = {'apple-spi-trackpad': 98}
        self.assertEqual(m.interface_units(group, 'apple-spi-trackpad', 'Apple MacBook Pro (14-inch, M1 Pro, 2021)'),
                         (98, 'setting'))
        with patch.object(m, 'machine_model', return_value='Apple MacBook Air (M1, 2020)'):
            self.assertEqual(m.interface_units({'settings': {}}, 'apple-spi-trackpad', m.machine_model()), (None, None))

    def test_macos_profile_needs_resolution_and_the_previewed_file(self):
        self.profiles_dir()
        state = m.migrate(self.state)
        apply = lambda reference: m.change(state, 'apple', 'pointer_feel',
                                           {'profile': 'imported', 'curve': m.DEFAULT_CURVE, 'imported': reference})
        with patch.object(m, 'hypr', side_effect=self.compositor) as run, patch.object(m, 'save') as save, NATIVE():
            with self.assertRaisesRegex(ValueError, 'resolution is unknown for apple-inc.-magic-trackpad'):
                apply(self.reference())
            state = m.change(state, 'apple', 'units_per_mm',
                             {'apple-inc.-magic-trackpad': 47.6, 'apple-inc.-magic-trackpad-1': 47.6})
            run.reset_mock(); save.reset_mock()
            with self.assertRaisesRegex(ValueError, 'changed after it was previewed'):
                apply(dict(self.reference(), sha256='0' * 64))
            with self.assertRaises((ValueError, OSError)):
                apply({'file': 'missing.json', 'sha256': '0' * 64})
            for reference in [{'file': '../escape.json', 'sha256': '0' * 64}, {'file': 'x.json'}]:
                with self.assertRaises(ValueError):
                    m.validate_change('pointer_feel', {'profile': 'imported', 'curve': m.DEFAULT_CURVE, 'imported': reference})
            self.assertFalse([call for call in run.call_args_list if call.args[0] == 'eval'])
            save.assert_not_called()

    def test_restore_previous_round_trips_the_converted_curve_without_the_file(self):
        state = self.imported_state()
        original = copy.deepcopy(state['devices']['apple']['settings']['imported_curve'])
        with patch.object(m, 'hypr', side_effect=self.compositor), patch.object(m, 'save'), NATIVE():
            state = m.change(state, 'apple', 'pointer_feel', {'profile': 'custom', 'curve': m.DEFAULT_CURVE})
            settings = state['devices']['apple']['settings']
            self.assertNotIn('imported_curve', settings)
            self.assertEqual(settings['curve_preset'], 'custom')
            previous = state['devices']['apple']['previous_pointer_feel']
            self.assertEqual(previous['profile'], 'imported')
            self.assertEqual(previous['imported'], original)
            (m.PROFILES / MAC_PROFILE.name).unlink()
            state = m.change(state, 'apple', 'pointer_feel', previous)
        settings = state['devices']['apple']['settings']
        self.assertEqual(settings['imported_curve'], original)
        self.assertEqual(settings['curve_preset'], 'imported')
        self.assertEqual(state['devices']['apple']['previous_pointer_feel']['profile'], 'custom')

    def test_profiles_command_lists_valid_files_and_reports_bad_ones(self):
        directory = self.profiles_dir()
        (directory / 'broken.json').write_text('{"format": "nope"}')
        (directory / 'notes.txt').write_text('ignored')
        state = m.migrate(self.state)
        state['devices']['apple']['settings']['units_per_mm'] = {'apple-inc.-magic-trackpad': 47.6}
        with patch.object(m, 'hypr', side_effect=self.compositor):
            result = m.list_profiles(state['devices']['apple'])
        self.assertEqual(result['directory'], str(directory))
        rows = {row['file']: row for row in result['profiles']}
        self.assertEqual(set(rows), {'broken.json', MAC_PROFILE.name})
        self.assertIn('error', rows['broken.json'])
        good = rows[MAC_PROFILE.name]
        self.assertEqual(good['sha256'], self.reference()['sha256'])
        self.assertEqual(good['name'], 'MacBook Pro (M1 Pro)')
        self.assertEqual(good['tracking_speed'], 0.875)
        self.assertEqual(good['speeds'], [0, 0.125, 0.5, 0.6875, 0.875, 1, 1.5, 2, 2.5, 3])
        interfaces = result['context']['interfaces']
        self.assertEqual(interfaces['apple-inc.-magic-trackpad'], {'units_per_mm': 47.6, 'source': 'setting'})
        self.assertEqual(interfaces['apple-inc.-magic-trackpad-1'], {'units_per_mm': None, 'source': None})
        self.assertEqual(result['context']['monitor']['name'], 'eDP-1')

    def test_profile_list_keeps_valid_rows_when_an_integer_overflows_float(self):
        directory = self.profiles_dir()
        profile = json.loads(MAC_PROFILE.read_bytes())
        profile['tracking_speed'] = 10 ** 1000
        (directory / 'huge.json').write_text(json.dumps(profile))
        with patch.object(m, 'hypr', side_effect=self.compositor):
            rows = {row['file']: row for row in m.list_profiles(m.migrate(self.state)['devices']['apple'])['profiles']}
        self.assertIn('error', rows['huge.json'])
        self.assertNotIn('error', rows[MAC_PROFILE.name])

    def test_import_resolution_uses_each_measured_or_remembered_sensor(self):
        self.profiles_dir()
        group = m.migrate(self.state)['devices']['apple']
        group['curve_calibration'] = {'apple-inc.-magic-trackpad-1': 47}
        with patch.object(m, 'device_resolution', side_effect=lambda name: 96 if name == 'apple-inc.-magic-trackpad' else None):
            converted = m.import_profile(group, self.reference(), PANEL[0])
        self.assertEqual(converted['devices']['apple-inc.-magic-trackpad']['units_per_mm'], 96)
        self.assertEqual(converted['devices']['apple-inc.-magic-trackpad-1']['units_per_mm'], 47)
        self.assertGreater(converted['devices']['apple-inc.-magic-trackpad']['step'],
                           converted['devices']['apple-inc.-magic-trackpad-1']['step'])
        group['curve_calibration'] = {}
        with patch.object(m, 'device_resolution', return_value=96):
            # Both measured interfaces are known; group-wide guessing is unnecessary.
            self.assertEqual(len(m.import_profile(group, self.reference(), PANEL[0])['devices']), 2)
        with patch.object(m, 'device_resolution', side_effect=[96, None]):
            with self.assertRaisesRegex(ValueError, 'unknown for apple-inc.-magic-trackpad-1'):
                m.import_profile(group, self.reference(), PANEL[0])

    def test_import_scroll_undo_and_calibration_remain_independent(self):
        state = self.imported_state()
        group = state['devices']['apple']
        group['curve_calibration'] = {'apple-inc.-magic-trackpad': 96, 'apple-inc.-magic-trackpad-1': 47}
        pointer = copy.deepcopy(group['settings']['imported_curve'])
        with patch.object(m, 'hypr', side_effect=self.compositor), NATIVE():
            state = m.change(state, 'apple', 'scroll_progressive', True)
            self.assertEqual(state['devices']['apple']['settings']['imported_curve'], pointer)
            lua = m.lua_for({'apple': state['devices']['apple']})
            for name, device in pointer['devices'].items():
                line = next(line for line in lua.splitlines() if json.dumps(name) in line)
                self.assertIn(json.dumps(m.imported_profile(device)), line)
                self.assertIn(json.dumps(m.scroll_profile(m.DEFAULT_SCROLL_CURVE)), line)
            scroll = dict(m.DEFAULT_SCROLL_CURVE, fast=3)
            state = m.change(state, 'apple', 'scroll_feel', {'profile': 'custom', 'curve': scroll})
            previous_scroll = state['devices']['apple']['previous_scroll_feel']
            state = m.change(state, 'apple', 'scroll_feel', previous_scroll)
            self.assertEqual(state['devices']['apple']['settings']['imported_curve'], pointer)
            for profile in ('adaptive', 'custom'):
                state = m.change(state, 'apple', 'pointer_feel', {'profile': profile, 'curve': m.DEFAULT_CURVE})
                previous = copy.deepcopy(state['devices']['apple']['previous_pointer_feel'])
                with patch.object(m, 'read_profile', side_effect=AssertionError('Undo must not read profile')), \
                        patch.object(m, 'device_resolution', side_effect=AssertionError('Undo must preserve resolution')):
                    state = m.change(state, 'apple', 'pointer_restore', previous)
                self.assertEqual(state['devices']['apple']['settings']['imported_curve'], pointer)
                self.assertEqual(state['devices']['apple']['curve_calibration'],
                                 {'apple-inc.-magic-trackpad': 96, 'apple-inc.-magic-trackpad-1': 47})

    def test_schema_five_migration_keeps_calibration_and_generated_motion(self):
        state = m.migrate(self.state)
        state['version'] = 5
        group = state['devices']['apple']
        group['curve_calibration'] = {'apple-inc.-magic-trackpad': 96, 'apple-inc.-magic-trackpad-1': 47}
        group['previous_pointer_feel'] = {'profile': 'custom', 'curve': dict(m.DEFAULT_CURVE),
                                          'calibration': copy.deepcopy(group['curve_calibration'])}
        group['settings'].update(accel_profile='custom', curve=dict(m.DEFAULT_CURVE), curve_preset='custom',
                                 scroll_progressive=True, scroll_curve=dict(m.DEFAULT_SCROLL_CURVE))
        before = m.lua_for(state['devices'])
        updated = m.migrate(state)
        self.assertEqual(updated['version'], 6)
        self.assertEqual(updated['devices'], state['devices'])
        self.assertEqual(m.lua_for(updated['devices']), before)

    def test_profiles_directory_may_be_a_stow_link_but_files_stay_private(self):
        directory = self.profiles_dir()
        link = directory.parent / 'linked-profiles'
        link.symlink_to(directory, target_is_directory=True)
        with patch.object(m, 'PROFILES', link):
            self.assertEqual(m.profile_files(), [MAC_PROFILE.name])
            self.assertEqual(m.read_profile(MAC_PROFILE.name), MAC_PROFILE.read_bytes())
            (directory / MAC_PROFILE.name).chmod(0o666)
            with self.assertRaisesRegex(ValueError, 'privately writable'):
                m.read_profile(MAC_PROFILE.name)
        with patch.object(m, 'PROFILES', directory / 'absent'):
            self.assertEqual(m.profile_files(), [])

    def test_interfaces_added_later_keep_their_own_acceleration(self):
        state = self.imported_state()
        group = copy.deepcopy(state['devices']['apple'])
        group['names'].append('apple-inc.-magic-trackpad-2')
        line = next(line for line in m.lua_for({'apple': group}).splitlines()
                    if '"apple-inc.-magic-trackpad-2"' in line)
        self.assertNotIn('accel_profile', line)
        self.assertIn('tap_to_click = true', line)

    def test_new_imported_interface_exposes_native_fallback_and_reapply_recovery(self):
        state = self.imported_state()
        group = state['devices']['apple']
        group['settings'].update(scroll_progressive=True, scroll_curve=dict(m.DEFAULT_SCROLL_CURVE))
        original = copy.deepcopy(group['settings']['imported_curve']['devices'])
        m.save(state)
        added = 'apple-inc.-magic-trackpad-2'
        view, _ = self.run_main(group['names'] + [added], 'state')
        saved = json.loads(m.STATE.read_text())
        lines = m.lua_for(saved['devices']).splitlines()
        fallback = next(line for line in lines if '"' + added + '"' in line)
        self.assertNotIn('accel_profile', fallback)
        self.assertNotIn('scroll_points', fallback, 'custom scrolling must not be attached to a native fallback')
        row = next(row for row in view['devices'] if row['id'] == 'apple')
        self.assertEqual(row['imported_missing_interfaces'], [added])
        for name in original:
            line = next(line for line in lines if '"' + name + '"' in line)
            self.assertIn('accel_profile = "custom ', line)
            self.assertIn('scroll_points = "' + m.scroll_profile(m.DEFAULT_SCROLL_CURVE), line)
        with patch.object(m, 'hypr', side_effect=self.compositor):
            request = {'profile': 'imported', 'curve': m.DEFAULT_CURVE, 'imported': self.reference()}
            with self.assertRaisesRegex(ValueError, added):
                m.change(saved, 'apple', 'pointer_feel', request)
            units = {name: 47.6 for name in saved['devices']['apple']['names']}
            measured = m.change(saved, 'apple', 'units_per_mm', units)
            recovered = m.change(measured, 'apple', 'pointer_feel', request)
        final = recovered['devices']['apple']
        self.assertIn(added, final['settings']['imported_curve']['devices'])
        for name, curve in original.items():
            self.assertEqual(final['settings']['imported_curve']['devices'][name], curve)
        self.assertEqual(m.snapshot(recovered, {'apple': {}})['devices'][0]['imported_missing_interfaces'], [])
        recovered_line = next(line for line in m.lua_for(recovered['devices']).splitlines() if '"' + added + '"' in line)
        self.assertIn('accel_profile = "custom ', recovered_line)
        self.assertIn('scroll_points = "' + m.scroll_profile(m.DEFAULT_SCROLL_CURVE), recovered_line)

    def test_disconnected_refresh_keeps_materialized_import_and_undo_history(self):
        state = self.imported_state()
        group = state['devices']['apple']
        group['curve_calibration'] = {name: 47 for name in group['names']}
        m.save(state)
        (m.PROFILES / MAC_PROFILE.name).unlink()
        with patch.object(m, 'device_resolution', side_effect=AssertionError('Refresh cannot recalibrate')), \
                patch.object(m, 'read_profile', side_effect=AssertionError('Refresh cannot reimport')):
            view, _ = self.run_main([], 'state')
        self.assertEqual(json.loads(m.STATE.read_text()), state)
        row = next(row for row in view['devices'] if row['id'] == 'apple')
        self.assertFalse(row['connected'])
        self.assertEqual(row['settings']['imported_curve'], group['settings']['imported_curve'])
        self.assertEqual(row['previous_pointer_feel'], group['previous_pointer_feel'])
        self.assertEqual(row['curve_calibration'], group['curve_calibration'])

    def test_imported_settings_are_validated_like_any_other(self):
        state = self.imported_state()
        for mutate in [lambda s: s['imported_curve']['devices']['apple-inc.-magic-trackpad']['points'].reverse(),
                       lambda s: s['imported_curve']['devices']['apple-inc.-magic-trackpad']['points'].extend([1e3]),
                       lambda s: s['imported_curve']['devices']['apple-inc.-magic-trackpad'].update(step=0),
                       lambda s: s['imported_curve'].update(extra=1),
                       lambda s: s['imported_curve'].update(file='../x.json'),
                       lambda s: s.pop('imported_curve'),
                       lambda s: s.update(curve_preset='custom'),
                       lambda s: s.update(units_per_mm={'bad"name': 1}),
                       lambda s: s['imported_curve']['devices'].update({'other-trackpad': copy.deepcopy(s['imported_curve']['devices']['apple-inc.-magic-trackpad'])})]:
            broken = copy.deepcopy(state)
            mutate(broken['devices']['apple']['settings'])
            with self.assertRaises(ValueError):
                m.validate_state(broken)

    def test_flat_or_adaptive_override_still_wins_over_an_imported_curve(self):
        state = self.imported_state()
        with patch.object(m, 'hypr', side_effect=self.compositor), patch.object(m, 'save'), NATIVE():
            state = m.change(state, 'apple', 'accel_profile', 'flat')
        lua = m.lua_for({'apple': state['devices']['apple']})
        self.assertIn('accel_profile = "flat"', lua)
        self.assertNotIn('custom', lua)

    def test_progressive_after_direct_native_override_creates_a_clean_mac_preset(self):
        state = self.imported_state()
        calibration = {name: 47 for name in state['devices']['apple']['names']}
        state['devices']['apple']['curve_calibration'] = dict(calibration)
        for native, option in product(('flat', 'adaptive'), ('scroll_progressive', 'scroll_feel')):
            with self.subTest(native=native, option=option), \
                    patch.object(m, 'hypr', side_effect=self.compositor), NATIVE():
                overridden = m.change(state, 'apple', 'accel_profile', native)
                group = overridden['devices']['apple']
                previous_curve = copy.deepcopy(group['settings']['curve'])
                # Direct native overrides may retain dormant imported metadata,
                # but replacing that pointer with a Mac preset must discard it.
                value = True if option == 'scroll_progressive' else {'profile': 'mac', 'curve': m.DEFAULT_SCROLL_CURVE}
                updated = m.change(overridden, 'apple', option, value)
                group = updated['devices']['apple']
                settings = group['settings']
                self.assertEqual(settings['accel_profile'], 'custom')
                self.assertEqual(settings['curve_preset'], 'mac')
                self.assertNotIn('imported_curve', settings)
                self.assertTrue(settings['scroll_progressive'])
                self.assertEqual(group['previous_pointer_feel'],
                                 {'profile': native, 'curve': previous_curve, 'calibration': calibration})
                restored = m.change(updated, 'apple', 'pointer_restore', group['previous_pointer_feel'])
                self.assertEqual(restored['devices']['apple']['settings']['accel_profile'], native)
                self.assertEqual(restored['devices']['apple']['curve_calibration'], calibration)

    def test_display_scale_drift_is_reported_without_rewriting(self):
        state = self.imported_state()
        same = m.snapshot(state, {}, PANEL[0])['devices'][0]
        moved = m.snapshot(state, {}, dict(PANEL[0], scale=1.6))['devices'][0]
        self.assertFalse(same['imported_drift'])
        self.assertTrue(moved['imported_drift'])
        self.assertNotIn('imported_drift', m.snapshot(state, {})['devices'][0])
    def test_progressive_scroll_default_is_independent_of_device_scale(self):
        for scale, factor in [(0.1, 0.02), (1, 0.2), (3, 0.6), (10, 1)]:
            with self.subTest(scale=scale):
                state = m.migrate(self.state)
                settings = state['devices']['apple']['settings']
                settings.update(scroll_scale=scale, scroll_factor=factor)
                with patch.object(m, 'hypr'), patch.object(m, 'save'):
                    enabled = m.change(state, 'apple', 'scroll_progressive', True)
                    changed = m.change(enabled, 'apple', 'scroll_scale', 2)
                actual = enabled['devices']['apple']['settings']
                self.assertEqual(actual['scroll_curve'], m.DEFAULT_SCROLL_CURVE)
                self.assertEqual(actual['scroll_factor'], factor)
                self.assertEqual(changed['devices']['apple']['settings']['scroll_curve'], m.DEFAULT_SCROLL_CURVE)
                lua = m.lua_for({'apple': enabled['devices']['apple']})
                self.assertIn('scroll_points = "' + m.scroll_profile(m.DEFAULT_SCROLL_CURVE), lua)

    def test_progressive_scroll_keeps_custom_pointer_and_linear_default(self):
        state = m.migrate(self.state)
        self.assertFalse(state['devices']['apple']['settings'].get('scroll_progressive', False))
        pointer = {'precision': 0.2, 'start': 0.6, 'end': 3, 'fast': 1.4}
        with patch.object(m, 'hypr'), patch.object(m, 'save'):
            custom = m.change(state, 'apple', 'pointer_feel', {'profile': 'custom', 'curve': pointer})
            enabled = m.change(custom, 'apple', 'scroll_progressive', True)
            disabled = m.change(enabled, 'apple', 'scroll_progressive', False)
        for result in (enabled, disabled):
            self.assertEqual(result['devices']['apple']['settings']['curve'], pointer)
        self.assertEqual(disabled['devices']['apple']['settings']['scroll_curve'], m.DEFAULT_SCROLL_CURVE)
        self.assertIn('scroll_points = "' + m.IDENTITY_SCROLL,
                      m.lua_for({'apple': disabled['devices']['apple']}))

    def test_progressive_scroll_emits_curve_not_identity(self):
        state = m.migrate(self.state)
        curve = dict(m.DEFAULT_SCROLL_CURVE)
        with patch.object(m, 'hypr') as run, patch.object(m, 'save'):
            updated = m.change(state, 'apple', 'scroll_feel', {'profile': 'mac', 'curve': curve})
        settings = updated['devices']['apple']['settings']
        self.assertTrue(settings['scroll_progressive'])
        self.assertEqual(settings['accel_profile'], 'custom')
        self.assertEqual(settings['scroll_curve_preset'], 'mac')
        lua = run.call_args.args[1]
        self.assertIn('scroll_points = "' + m.scroll_profile(curve), lua)
        self.assertNotIn(f'scroll_points = "{m.IDENTITY_SCROLL}"', lua)
        self.assertNotIn('scroll_progressive', lua)
        self.assertNotIn('scroll_curve =', lua)
        self.assertEqual(updated['devices']['dell'], state['devices']['dell'])

    def test_progressive_toggle_switches_adaptive_pointer_to_mac(self):
        state = m.migrate(self.state)
        with patch.object(m, 'hypr') as run, patch.object(m, 'save'):
            updated = m.change(state, 'apple', 'scroll_progressive', True)
        settings = updated['devices']['apple']['settings']
        self.assertTrue(settings['scroll_progressive'])
        self.assertEqual(settings['accel_profile'], 'custom')
        self.assertEqual(settings['curve_preset'], 'mac')
        self.assertIn('scroll_points = "' + m.scroll_profile(settings['scroll_curve']), run.call_args.args[1])

    def test_system_pointer_turns_off_progressive_scroll(self):
        state = m.migrate(self.state)
        with patch.object(m, 'hypr'), patch.object(m, 'save'):
            enabled = m.change(state, 'apple', 'scroll_progressive', True)
            updated = m.change(enabled, 'apple', 'pointer_feel',
                               {'profile': 'adaptive', 'curve': m.DEFAULT_CURVE})
        self.assertFalse(updated['devices']['apple']['settings']['scroll_progressive'])
        lua = m.lua_for({'apple': updated['devices']['apple']})
        self.assertIn('accel_profile = "adaptive"', lua)
        self.assertNotIn('scroll_points', lua)

    def test_scroll_defaults_match_curve_js(self):
        script = "const c=require('./Curve.js'); console.log(JSON.stringify([c.scrollDefaults(), c.points(c.scrollDefaults())]))"
        defaults, graph = json.loads(subprocess.check_output(
            ['node', '-e', script], cwd=Path(__file__).parent))
        self.assertEqual(defaults, m.DEFAULT_SCROLL_CURVE)
        self.assertEqual(graph, list(map(float, m.scroll_profile(m.DEFAULT_SCROLL_CURVE).split()[1:])))

    def test_calibrated_pointer_keeps_independent_progressive_scroll(self):
        state = m.migrate(self.state)
        group = state['devices']['apple']
        group['curve_calibration'] = {name: 47 for name in group['names']}
        group['settings'].update(accel_profile='custom', curve=dict(m.DEFAULT_CURVE),
                                 scroll_progressive=True, scroll_curve=dict(m.DEFAULT_SCROLL_CURVE))
        m.validate_persisted(state)
        lua = m.lua_for({'apple': group})
        self.assertIn('accel_profile = "' + m.curve_profile(m.DEFAULT_CURVE, 47) + '"', lua)
        self.assertIn('scroll_points = "' + m.scroll_profile(m.DEFAULT_SCROLL_CURVE) + '"', lua)

    def test_progressive_scroll_captures_calibration_for_new_custom_pointer(self):
        state = m.migrate(self.state)
        with patch.object(m, 'device_resolution', return_value=47):
            m.enable_progressive_scroll(state['devices']['apple'])
        group = state['devices']['apple']
        self.assertEqual(group['curve_calibration'], {name: 47 for name in group['names']})
        self.assertEqual(group['previous_pointer_feel']['calibration'], {})

    def test_reapplying_previous_curve_is_apply_not_undo(self):
        state = m.migrate(self.state)
        group = state['devices']['apple']
        group['settings'].update(accel_profile='custom', curve_preset='mac', curve=dict(m.DEFAULT_CURVE))
        with patch.object(m, 'hypr', return_value='ok'), patch.object(m, 'device_resolution', return_value=47):
            system = m.change(state, 'apple', 'pointer_feel', {'profile':'adaptive', 'curve':m.DEFAULT_CURVE})
            applied = m.change(system, 'apple', 'pointer_feel', {'profile':'mac', 'curve':m.DEFAULT_CURVE})
        self.assertEqual(applied['devices']['apple']['curve_calibration'],
                         {name:47 for name in group['names']})

    def test_edit_while_sensor_disconnected_retains_calibration(self):
        state = m.migrate(self.state)
        group = state['devices']['apple']
        group['settings'].update(accel_profile='custom', curve_preset='custom', curve=dict(m.DEFAULT_CURVE))
        group['curve_calibration'] = {name:47 for name in group['names']}
        with patch.object(m, 'hypr', return_value='ok'), patch.object(m, 'device_resolution', return_value=None):
            applied = m.change(state, 'apple', 'pointer_feel',
                               {'profile':'custom', 'curve':dict(m.DEFAULT_CURVE, fast=2)})
        self.assertEqual(applied['devices']['apple']['curve_calibration'],group['curve_calibration'])
        with patch.object(m, 'hypr', return_value='ok'), patch.object(m, 'device_resolution', return_value=None):
            system = m.change(state, 'apple', 'pointer_feel', {'profile':'adaptive', 'curve':m.DEFAULT_CURVE})
            reapplied = m.change(system, 'apple', 'pointer_feel',
                                 {'profile':'custom', 'curve':dict(m.DEFAULT_CURVE, fast=2)})
        self.assertEqual(reapplied['devices']['apple']['curve_calibration'], group['curve_calibration'])

if __name__=='__main__':unittest.main()
