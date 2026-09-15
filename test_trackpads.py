import copy
import importlib.util
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

class TrackpadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, value in [('DIRECTORY', root), ('STATE', root / 'settings.json'),
                            ('GENERATED', root / 'settings.lua')]:
            replacement = patch.object(m, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.groups=m.group_devices([{'name':n} for n in ['ven_06cb:00-06cb:d01d-touchpad','apple-inc.-magic-trackpad','apple-inc.-magic-trackpad-1','usb-mouse']])
        for g in self.groups.values():
            g['settings']={'enabled':True,'sensitivity':0.3 if g['id']=='dell' else 0.1,'scroll_factor':0.2,'natural_scroll':False,'tap_to_click':True,'clickfinger_behavior':True,'disable_while_typing':True}
        self.state={'version':1,'devices':self.groups}

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
        for state in [dict(self.state, version=5), dict(self.state, version=True),
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
        names = ['synaptics-tm3512-010', 'synaptics-tm3381-002']
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
        self.assertEqual(migrated['version'], 4)
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

if __name__=='__main__':unittest.main()
