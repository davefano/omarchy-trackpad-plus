const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const qml = fs.readFileSync(path.join(__dirname, 'Panel.qml'), 'utf8');

// Execute the real QML functions; callback ordering is controlled by each test.
function context() {
  const settings = { enabled: true, natural_scroll: false, tap_to_click: true,
    disable_while_typing: true, clickfinger_behavior: true, accel_profile: 'adaptive',
    scroll_factor: 0.2, sensitivity: 0.1 };
  const ctx = {
    devices: [
      { id: 'apple', label: 'Apple', connected: true, names: ['apple'], settings: { ...settings } },
      { id: 'dell', label: 'Dell', connected: true, names: ['dell'], settings: { ...settings, sensitivity: 0.3 } }
    ],
    selectedDevice: 'apple', pendingActions: [], settingsError: '', actionError: '', deviceSettingsOpen: false,
    editingCurve: false, gestureEditor: {activeFocus: false},
    editGeneration: 0, stateGeneration: 0, refreshPending: false, pointerStatePending: false,
    actionProc: { running: false }, stateProc: { running: false }, backend: 'trackpads.py',
    palmProc: { running: false }, palmBackend: 'palm.py',
    palmEditor: { settings: {supported: false}, activeFocus: false, busy: false, dirty: false,
      error: '', resetDraft() { this.dirty = false }, acceptSettings(value) { this.settings = value },
      beginEditing() { this.activeFocus = true } },
    Model: require('./Model.js'),
    Curve: require('./Curve.js'), previousFeels: {}, previousScrollFeels: {},
    curveKind: 'pointer', scrollProgressive: false, profilesRequest: 0, profilesPending: false,
    profilesProc: {running: false}, pointerProfiles: {loading: false, error: '', directory: '', profiles: []},
    scrollFeel: { profile: 'mac', curve: require('./Curve.js').scrollDefaults() },
    curveEditor: {},
    keyCatcher: { forceActiveFocus() {} },
    scrollDebounce: { running: false, stop() { this.running = false; } },
    pointerDebounce: { running: false, stop() { this.running = false; } }
  };
  vm.createContext(ctx);
  const functions = qml.match(/^  function \w+\([^\n]*\) \{[^\n]*\}$|^  function \w+\([^\n]*\) \{\n[\s\S]*?^  \}/gm);
  for (const source of functions) vm.runInContext(source, ctx);
  ctx.loadSelection();
  return ctx;
}

// Complete queued callbacks and accept a current backend state before another editor action.
function reconcilePointerState(ctx) {
  while (ctx.pendingActions.length) {
    ctx.actionProc.running = false;
    ctx.finishAction(0);
  }
  ctx.actionProc.running = false;
  ctx.finishAction(0);
  ctx.receiveState(JSON.stringify({devices: ctx.devices}));
  ctx.stateProc.running = false;
  ctx.finishStateRead(0);
}

{
  const ctx = context();
  ctx.actionProc.running = true;
  ctx.devices[0].curve_calibration = { apple: 47 };
  ctx.enqueue('pointer_feel', {profile: 'custom', curve: ctx.Curve.defaults()});
  assert.equal(ctx.devices[0].previous_pointer_feel.calibration.apple, 47,
    'optimistic undo must retain the old curve calibration');
  ctx.loadSelection();
  reconcilePointerState(ctx);
  ctx.actionProc.running = true; // Keep the restore queued so its payload can be inspected.
  ctx.restorePointerFeel();
  assert.equal(ctx.pendingActions.at(-1).option, 'pointer_restore');
  assert.equal(ctx.pendingActions.at(-1).value.calibration.apple, 47,
    'undo sends saved calibration to the backend');
  assert.equal(Object.hasOwn(ctx.curveEditor.draft, 'calibration'), false,
    'editable Apply drafts must not carry undo-only calibration');
}

{
  const ctx = context();
  ctx.selectDevice('dell');
  ctx.actionProc.running = true;
  ctx.scrollDebounce.running = ctx.pointerDebounce.running = true;
  ctx.pendingScrollFactor = 0.6;
  ctx.pendingPointerSpeed = 0.8;
  ctx.selectDevice('apple');
  assert.equal(ctx.pendingActions.length, 2);
  assert.ok(ctx.pendingActions.every(x => x.device === 'dell'));
  assert.equal(ctx.pendingActions[0].value, 0.6);
  assert.equal(ctx.pendingActions[1].value, 0.8);
  assert.equal(ctx.pointerSpeed, 0.1);
  ctx.togglePointerAcceleration();
  assert.equal(ctx.pendingActions[2].device, 'apple');
  assert.equal(ctx.pendingActions[2].value, 'flat');
  ctx.selectDevice('dell');
  assert.equal(ctx.pointerAcceleration, true);
}

{
  const ctx = context();
  const oldRead = JSON.stringify({ devices: ctx.devices });
  ctx.refresh();
  ctx.toggleNaturalScroll();
  assert.equal(ctx.naturalScroll, true);
  ctx.actionProc.running = false;
  ctx.finishAction(0); // The old state process is still running.
  ctx.receiveState(oldRead);
  assert.equal(ctx.naturalScroll, true, 'late read must not revert the successful edit');
  ctx.stateProc.running = false;
  ctx.finishStateRead(0);
  assert.equal(ctx.stateProc.running, true, 'discarding a stale read must schedule a fresh read');
  ctx.receiveState(JSON.stringify({ devices: ctx.devices }));
  ctx.stateProc.running = false;
  ctx.finishStateRead(0);
  assert.equal(ctx.stateProc.running, false, 'a successful read must not poll in a tight loop');
  ctx.toggleNaturalScroll();
  assert.equal(JSON.parse(ctx.actionProc.command.at(-1)), false, 'next click must reverse the edit');
}

{
  const ctx = context();
  ctx.refresh();
  ctx.scrollDebounce.running = true;
  ctx.receiveState(JSON.stringify({ devices: ctx.devices }));
  ctx.stateProc.running = false;
  ctx.finishStateRead(0);
  assert.equal(ctx.stateProc.running, false, 'refresh must wait for pending slider edits');
  ctx.scrollDebounce.running = false;
  ctx.pendingScrollFactor = 0.7;
  ctx.commitScrollFactor();
  ctx.actionProc.running = false;
  ctx.finishAction(0);
  assert.equal(ctx.stateProc.running, true);
}

{
  const ctx = context();
  ctx.toggleNaturalScroll();
  ctx.togglePointerAcceleration();
  ctx.actionProc.running = false;
  ctx.finishAction(124);
  assert.match(ctx.settingsError, /Could not save/);
  assert.equal(ctx.actionProc.running, true, 'a timed-out write must release the next queued write');
  assert.equal(ctx.actionProc.command.at(-2), 'accel_profile');
  ctx.actionProc.running = false;
  ctx.finishAction(0);
  assert.equal(ctx.stateProc.running, true);
  ctx.stateProc.running = false;
  ctx.settingsError = '';
  ctx.finishStateRead(124);
  assert.match(ctx.settingsError, /Could not read/);
  assert.equal(ctx.stateProc.running, false, 'a failed read must not immediately retry forever');
  ctx.refresh();
  assert.equal(ctx.stateProc.running, true, 'a subsequent poll must recover after a read timeout');
}

{
  const ctx = context();
  const argv = ctx.bounded(0.05, ['python3', '-c', 'import time; time.sleep(60)']);
  const result = spawnSync(argv[0], Array.from(argv.slice(1)), { timeout: 4000 });
  assert.ifError(result.error);
  assert.equal(result.status, 124, 'the actual timeout wrapper must reap a stalled helper');
  assert.match(qml, /command: root\.bounded\(15, \["python3", root\.backend, "state"\]\)/);
  ctx.toggleNaturalScroll();
  assert.deepEqual(Array.from(ctx.actionProc.command.slice(0, 4)), ['timeout', '-k', '2', '10']);
}

{
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'manifest.json'), 'utf8'));
  assert.equal(manifest.id, 'davefano.trackpad-plus');
  assert.match(qml, /ipcTarget: "davefano\.trackpad-plus"/);
  assert.match(qml, /manageIpc: true/);
  assert.match(qml, /root\.receiveState\(String\(text\)\)/);
  assert.match(qml, /Qt\.callLater\(function\(\) \{ root\.finishStateRead\(code\) \}\)/);
  assert.match(qml, /Qt\.callLater\(function\(\) \{ root\.finishAction\(code\) \}\)/);
}
{
  const ctx = context();
  ctx.scrollDebounce.restart = function() { this.running = true; };
  ctx.setScrollFactor(0.05);
  ctx.focusSection = 'scroll';
  ctx.moveCursorH(-1);
  assert.equal(ctx.scrollFactor, 0.04);
  ctx.selectDevice('dell'); // Flush the precise value to the original device.
  assert.equal(JSON.parse(ctx.actionProc.command.at(-1)), 0.04);
  assert.equal(ctx.actionProc.command.at(-3), 'apple');
  assert.equal(ctx.scrollFactor, 0.2);
  ctx.setScrollFactor(0);
  assert.equal(ctx.scrollFactor, 0.01);
  ctx.setScrollFactor(0.056);
  assert.equal(ctx.scrollFactor, 0.06);
}

{
  const ctx = context();
  ctx.actionProc.running = true;
  const original = JSON.stringify(ctx.pointerFeel);
  ctx.applyPointerFeel({profile: 'custom', curve: ctx.Curve.defaults()});
  assert.equal(ctx.devices[0].settings.accel_profile, 'custom');
  assert.equal(ctx.pointerFeel.profile, 'custom');
  ctx.selectDevice('dell');
  assert.equal(ctx.pointerFeel.profile, 'adaptive');
  assert.equal(ctx.previousFeels.dell, undefined);
  assert.equal(ctx.pendingActions[0].device, 'apple');
  ctx.selectDevice('apple');
  reconcilePointerState(ctx);
  ctx.actionProc.running = true;
  ctx.restorePointerFeel();
  assert.equal(JSON.stringify(ctx.pointerFeel), original);
  assert.equal(ctx.pendingActions.at(-1).value.profile, 'adaptive');
  assert.equal(ctx.devices[1].settings.accel_profile, 'adaptive');
}

{
  const ctx = context();
  ctx.previousFeels.apple = {profile: 'flat', curve: ctx.Curve.defaults()};
  ctx.loadSelection();
  assert.equal(ctx.previousFeels.apple, undefined, 'authoritative state clears stale undo');
  ctx.devices = [];
  ctx.loadSelection();
  assert.equal(ctx.deviceName, '', 'removed devices must not remain actionable');
  assert.equal(ctx.deviceConnected, false);
  const backendExpression = qml.match(/readonly property string backend: (.*)/)[1];
  ctx.Qt = {resolvedUrl: () => 'file:///tmp/plugin%20with%20spaces/trackpads.py'};
  assert.equal(vm.runInContext(backendExpression, ctx), '/tmp/plugin with spaces/trackpads.py');
}

// Mac-inspired remains available alongside optional imported profiles.
{
  const Curve = require('./Curve.js');
  const curve = {precision: 0.2, start: 0.8, end: 2.8, fast: 1};
  const feel = Curve.fromSettings({accel_profile: 'custom', curve_preset: 'mac', curve});
  assert.equal(feel.profile, 'mac');
  assert.deepEqual(feel.curve, curve);
  assert.equal(Curve.label(feel), 'Mac-inspired');
  assert.ok(Curve.usesCurve('mac'), 'an earlier undo record still restores as a curve');
  const ctx = context();
  ctx.actionProc.running = true;
  ctx.previousFeels.apple = {profile: 'mac', curve};
  ctx.restorePointerFeel();
  assert.equal(ctx.devices[0].settings.accel_profile, 'custom');
  assert.equal(ctx.pointerFeel.profile, 'mac');
}

// macOS profiles: a previewed file is applied by reference and undone by its converted curve.
{
  const ctx = context();
  ctx.actionProc.running = true;
  const reference = {file: 'mac.json', sha256: 'a'.repeat(64), name: 'MacBook Pro (M1 Pro)', tracking_speed: 1.5};
  ctx.applyPointerFeel({profile: 'imported', curve: ctx.Curve.defaults(), imported: reference});
  assert.deepEqual(ctx.pendingActions[0].value.imported, {file: 'mac.json', sha256: 'a'.repeat(64), tracking_speed: 1.5},
    'the backend receives the file reference and tracking speed, not the display name');
  const settings = ctx.devices[0].settings;
  assert.equal(settings.accel_profile, 'custom');
  assert.equal(settings.curve_preset, 'imported');
  assert.equal(ctx.pointerFeel.profile, 'imported');
  assert.equal(ctx.Curve.label(ctx.pointerFeel), 'macOS · MacBook Pro (M1 Pro)');
  ctx.deviceSettingsOpen = false;
  ctx.activeTab = 'pointer';
  assert.ok(!ctx.navigationSections().includes('pointer'), 'Pointer Speed is hidden for an imported curve');
  ctx.pointerSpeed = 0.1;
  ctx.adjustPointerSpeed(0.2);
  assert.equal(ctx.pointerSpeed, 0.1);
  reconcilePointerState(ctx);
  ctx.applyPointerFeel({profile: 'flat', curve: ctx.Curve.defaults()});
  assert.equal(ctx.devices[0].settings.imported_curve, undefined, 'leaving the profile drops its local curve');
  assert.equal(ctx.devices[0].settings.curve_preset, 'custom');
}

{
  const ctx = context();
  ctx.actionProc.running = true;
  const converted = {name: 'Mac', file: 'mac.json', sha256: 'b'.repeat(64), tracking_speed: 0.875,
    mm_per_point: 0.2, px_per_point: 1, devices: {apple: {units_per_mm: 98.65, step: 0.1, points: [0, 1]}}};
  ctx.devices[0].settings = {...ctx.devices[0].settings, accel_profile: 'custom', curve_preset: 'imported',
    curve: ctx.Curve.defaults(), imported_curve: converted};
  ctx.devices[0].imported_drift = true;
  ctx.loadSelection();
  assert.equal(ctx.pointerDrift, true);
  ctx.applyPointerFeel({profile: 'adaptive', curve: ctx.Curve.defaults()});
  assert.equal(ctx.pointerDrift, false, 'a new feel clears the drift notice locally');
  reconcilePointerState(ctx);
  ctx.actionProc.running = true;
  ctx.restorePointerFeel();
  assert.deepEqual(ctx.pendingActions.at(-1).value.imported, converted, 'undo needs no profile file');
  assert.equal(ctx.devices[0].settings.curve_preset, 'imported');
  assert.ok(ctx.Curve.same(ctx.pointerFeel, {profile: 'imported', curve: ctx.Curve.defaults(),
    imported: {file: 'mac.json', sha256: 'b'.repeat(64), tracking_speed: 0.875}}), 'a reference equals its converted curve');
  assert.ok(!ctx.Curve.same(ctx.pointerFeel, {profile: 'imported', curve: ctx.Curve.defaults(),
    imported: {file: 'mac.json', sha256: 'b'.repeat(64), tracking_speed: 1}}), 'another tracking speed is a change');
}

{
  const ctx = context();
  ctx.profilesProc = {running: false};
  ctx.editingCurve = true;
  ctx.pointerProfiles = {loading: false, error: '', directory: '', profiles: []};
  ctx.profilesRequest = 0;
  ctx.refreshProfiles();
  assert.deepEqual(Array.from(ctx.profilesProc.command), ['timeout', '-k', '2', '15', 'python3', 'trackpads.py', 'profiles', 'apple']);
  const first = ctx.profilesProc.requestId;
  ctx.refreshProfiles();
  assert.equal(ctx.profilesPending, true, 'an overlapping open waits for the running read');
  ctx.profilesProc.running = false;
  ctx.finishProfiles(0, first);
  assert.equal(ctx.profilesProc.running, true);
  ctx.receiveProfiles(JSON.stringify({profiles: [{file: 'old.json'}]}), first);
  assert.equal(ctx.pointerProfiles.loading, true, 'a superseded reply is dropped');
  ctx.receiveProfiles(JSON.stringify({directory: '/p', profiles: [{file: 'new.json'}],
    context: {interfaces: {apple: {units_per_mm: 98.65}}, monitor: {name: 'eDP-1'}}}), ctx.profilesProc.requestId);
  assert.equal(ctx.pointerProfiles.profiles[0].file, 'new.json');
  assert.equal(ctx.pointerProfiles.device, 'apple');
  ctx.profilesProc.running = false;
  ctx.refreshProfiles();
  ctx.profilesProc.running = false;
  ctx.finishProfiles(124, ctx.profilesProc.requestId);
  assert.match(ctx.pointerProfiles.error, /Could not read pointer profiles/, 'a timed-out read reports an error');
}

{
  const Curve = require('./Curve.js');
  const curve = Curve.defaults();
  const samples = Curve.points(curve);
  for (let index = 0; index <= 160; index++) {
    assert.equal(Curve.sampledGain(curve, index / 40, samples), Curve.sampledGain(curve, index / 40));
  }
}

{
  const ctx = context();
  ctx.actionProc.running = true;
  for (let i = 0; i < 1000; i++) ctx.enqueue('scroll_factor', 0.01 + (i % 100) / 100);
  assert.equal(ctx.pendingActions.length, 1, 'repeated scalar updates should coalesce');
  assert.equal(ctx.pendingActions[0].value, 1);
  for (let i = 0; i < 200; i++) ctx.enqueue(i % 2 ? 'natural_scroll' : 'tap_to_click', true);
  assert.equal(ctx.pendingActions.length, 128, 'pending actions must have a fixed memory bound');
  assert.match(ctx.settingsError, /Too many/);
}

{
  const ctx = context();
  ctx.actionProc.running = true;
  ctx.scrollDebounce.running = true;
  ctx.pendingScrollFactor = 0.4;
  ctx.setScrollScale(3);
  assert.equal(ctx.pendingActions[0].option, 'scroll_factor');
  assert.equal(ctx.pendingActions[0].value, 0.4, 'pending edit must use its original scale');
  assert.equal(ctx.pendingActions[1].option, 'scroll_scale');
  assert.equal(ctx.pendingActions[1].value, 3);
  assert.equal(ctx.devices[0].settings.scroll_factor, 1.2);
  ctx.loadSelection();
  assert.ok(Math.abs(ctx.scrollFactor - 0.4) < 1e-9);
  ctx.pendingScrollFactor = 1;
  ctx.commitScrollFactor();
  assert.equal(ctx.pendingActions[2].value, 3, 'full slider reaches the configured scale');
  ctx.selectDevice('dell');
  assert.equal(ctx.scrollScale, 1);
  assert.equal(ctx.scrollFactor, 0.2);
  assert.equal(ctx.Model.clampScrollFactor(3), 1);
}

{
  const ctx = context();
  ctx.activeTab = "pointer";
  ctx.deviceSettingsOpen = false;
  assert.ok(ctx.navigationSections().includes("acceleration"));
  assert.ok(!ctx.navigationSections().includes("scroll"));
  ctx.activeTab = "scrolling";
  assert.ok(ctx.navigationSections().includes("scroll"));
  assert.ok(!ctx.navigationSections().includes("tap"));
  ctx.deviceSettingsOpen = true;
  assert.ok(ctx.navigationSections().includes("enable"));
  ctx.activeTab = "gestures";
  assert.ok(!ctx.navigationSections().includes("scroll"));
  ctx.scrollDebounce.running = true;
  ctx.pendingScrollFactor = 0.15;
  ctx.changeTab("pointer");
  assert.equal(ctx.activeTab, "pointer");
  assert.equal(ctx.scrollDebounce.running, false);
  assert.equal(JSON.parse(ctx.actionProc.command.at(-1)), 0.15);
}

{
  const ctx = context();
  ctx.gestureProc = {running: false};
  ctx.gestureBackend = 'gestures.py';
  ctx.gestureEditor = {load(value) { this.saved = value; }};
  ctx.runGestureAction('set', {enabled: true, fingers: 3, distance: 300, invert: false});
  assert.equal(ctx.gestureProc.running, true);
  assert.equal(ctx.gestureProc.command.at(-2), 'set');
  const before = JSON.stringify(ctx.gestureProc.command);
  ctx.runGestureAction('restore');
  assert.equal(JSON.stringify(ctx.gestureProc.command), before, 'busy gesture writes cannot overlap');
  ctx.finishGestureAction(124);
  assert.match(ctx.gestureError, /did not complete/);
  ctx.gestureProc.running = false;
  ctx.refreshGestures();
  assert.equal(ctx.gestureProc.command.at(-1), 'state');
  ctx.receiveGestures(JSON.stringify({can_edit: true, can_restore: true,
    settings: {enabled: true, fingers: 4, distance: 400, invert: false}, message: 'Managed'}));
  assert.equal(ctx.gestureEditor.saved.fingers, 4);
  assert.equal(ctx.gestureCanRestore, true);
  ctx.finishGestureAction(0);
  assert.equal(ctx.gestureError, '');
  ctx.receiveGestures('{"error":"reload rejected"}');
  assert.equal(ctx.gestureEditor.saved.fingers, 4, 'failed save must retain previous acknowledged settings');
  assert.match(ctx.gestureError, /reload rejected/);
}

// Explicit overview checks cannot discard unsaved gesture edits or accept old responses.
{
  const ctx = context();
  ctx.gestureProc = {running: false};
  ctx.overviewProc = {running: false};
  ctx.overviewRequest = 0;
  ctx.gestureBackend = 'gestures.py';
  ctx.gestureEditor = {draft: {fingers: 4, overview_provider: 'trackpad-plus'},
    load() { throw new Error('preview must not reload the draft'); }};
  ctx.requestOverview(true);
  const request = ctx.overviewRequest;
  assert.equal(ctx.overviewProc.command.at(-1), 'preview');
  ctx.requestOverview(false);
  assert.equal(ctx.overviewRequest, request, 'preview operations cannot overlap');
  ctx.receiveOverview(JSON.stringify({installed: true, reachable: true, protocolCompatible: true,
    rendered: false, opened: true, lockState: 'unlocked'}), request);
  assert.match(ctx.overviewPreviewText, /rendering is not confirmed/);
  assert.equal(ctx.gestureEditor.draft.fingers, 4);
  const status = ctx.overviewPreviewText;
  ctx.receiveOverview('{"error":"stale error"}', request - 1);
  ctx.finishOverview(124, request - 1);
  assert.equal(ctx.overviewPreviewText, status);
  ctx.overviewProc.running = false;
  ctx.requestOverview(false);
  assert.equal(ctx.overviewProc.command.at(-1), 'overview-status');
  ctx.finishOverview(124, ctx.overviewRequest);
  assert.match(ctx.overviewPreviewText, /did not respond/);
  ctx.receiveOverview(JSON.stringify({installed: true, reachable: true, protocolCompatible: true,
    rendered: true, opened: true, lockState: 'unlocked'}), ctx.overviewRequest);
  assert.match(ctx.overviewPreviewText, /rendered/);
  assert.equal(ctx.gestureEditor.draft.overview_provider, 'trackpad-plus');
}

// Gesture tab focus must not disable the panel's keyboard dispatcher until
// a control inside the editor actually owns focus.
{
  const ctx = context();
  let entered = 0, panelFocused = 0;
  ctx.gestureEditor = {activeFocus: false, beginEditing() { entered++; this.activeFocus = true; }};
  ctx.keyCatcher = {forceActiveFocus() { panelFocused++; ctx.gestureEditor.activeFocus = false; }};
  ctx.gestureProc = {running: false};
  ctx.gestureBackend = 'gestures.py';
  ctx.editingCurve = false;
  ctx.deviceSettingsOpen = false;
  ctx.activeTab = 'gestures';
  ctx.focusSection = 'device';
  assert.equal(ctx.keyboardNavigationBlocked(), false, 'reopened gesture tab must accept Escape and arrows');
  ctx.allSections = ctx.navigationSections();
  ctx.moveCursor(1);
  assert.equal(ctx.focusSection, 'device-settings');
  ctx.moveCursor(1);
  assert.equal(ctx.focusSection, 'tabs');
  ctx.moveCursor(1);
  assert.equal(entered, 1, 'Down from tabs enters the gesture controls');
  assert.equal(ctx.keyboardNavigationBlocked(), true, 'editor owns its keyboard input');
  ctx.changeTab('gestures');
  assert.equal(panelFocused, 1, 'mouse or keyboard tab selection restores panel navigation');
  assert.equal(ctx.keyboardNavigationBlocked(), false);
  ctx.activateCursor();
  assert.equal(entered, 2, 'Enter from tabs also enters the gesture controls');
  ctx.changeTab('pointer');
  assert.equal(ctx.keyboardNavigationBlocked(), false);
  ctx.activateCursor();
  assert.equal(entered, 2, 'other tabs retain existing navigation');
  ctx.editingCurve = true;
  assert.equal(ctx.keyboardNavigationBlocked(), true, 'curve editor keeps its existing focus behavior');
}

// Keep the inherited device parser usable for Intel Mac installations.
{
  const model = require('./Model.js');
  for (const name of ['bcm5974', 'apple-spi-trackpad', 'apple-mtp-multi-touch'])
    assert.equal(model.parseTouchpadDevice(JSON.stringify({mice: [{name}]})), name);
  assert.equal(model.parseTouchpadDevice(JSON.stringify({mice: [{name: 'bcm5974-mouse'}]})), '');
}
console.log('Passed: device selection, fine scroll steps, stale-read rejection, debounce ordering, timeout recovery, and IPC configuration.');

{
  const ctx = context();
  ctx.activeTab = 'pointer';
  ctx.palmEditor.settings = {supported: true};
  assert.ok(ctx.navigationSections().includes('palm'));
  ctx.focusSection = 'palm';
  ctx.activateCursor();
  assert.equal(ctx.keyboardNavigationBlocked(), true);
  ctx.palmRequestDevice = 'apple';
  ctx.selectedDevice = 'dell';
  ctx.receivePalm(JSON.stringify({supported: true, threshold: 700}));
  assert.equal(ctx.palmEditor.settings.threshold, undefined, 'stale Apple response must not update Dell');
  ctx.palmProc.running = false;
  ctx.applyPalm('700');
  assert.equal(ctx.palmProc.running, false, 'Dell must never start an Apple palm write');
}
{
  const ctx = context();
  ctx.applyPalm('700');
  assert.equal(ctx.palmEditor.busy, true);
  assert.equal(ctx.palmProc.command[3], '120', 'administrator prompt gets a bounded, interactive deadline');
  assert.deepEqual(Array.from(ctx.palmProc.command).slice(-3), ['set', 'apple', '700']);
  ctx.palmEditor.dirty = true;
  ctx.receivePalm(JSON.stringify({device: 'apple', supported: true, threshold: 700, pending: true}));
  assert.equal(ctx.palmEditor.dirty, false);
  assert.equal(ctx.palmEditor.settings.pending, true);
}
console.log('Palm panel action scoping and stale-response checks passed.');

{
  const ctx = context();
  ctx.actionProc.running = true;
  ctx.curveKind = 'scroll';
  const first = {profile: 'custom', curve: {precision: 0.8, start: 0.5, end: 2.2, fast: 1.7}};
  const second = {profile: 'custom', curve: {precision: 1.2, start: 0.5, end: 2.2, fast: 2.8}};
  ctx.enqueue("scroll_feel", first);
  ctx.loadSelection();
  ctx.enqueue("scroll_feel", second);
  ctx.loadSelection();
  assert.equal(ctx.pendingActions.length, 2, 'scroll Apply operations must preserve undo ordering');
  assert.equal(JSON.stringify(ctx.previousScrollFeels.apple), JSON.stringify(first));
  ctx.enqueue("scroll_feel", ctx.previousScrollFeels.apple);
  ctx.loadSelection();
  assert.equal(ctx.pendingActions.length, 3);
  assert.equal(JSON.stringify(ctx.pendingActions[2].value), JSON.stringify(first));
  assert.equal(JSON.stringify(ctx.previousScrollFeels.apple), JSON.stringify(second));
}

// Imported undo retains the materialized curve and calibration; the editable draft stays clean.
{
  const ctx = context();
  ctx.actionProc.running = true;
  const imported = {file: 'saved.json', sha256: 'c'.repeat(64), tracking_speed: 1,
    name: 'Mac', devices: {apple: {step: 0.1, points: [0, 1]}}};
  ctx.devices[0].settings = {...ctx.devices[0].settings, accel_profile: 'custom',
    curve_preset: 'imported', imported_curve: imported, curve: ctx.Curve.defaults()};
  ctx.devices[0].curve_calibration = {apple: 47};
  ctx.loadSelection();
  ctx.applyPointerFeel({profile: 'custom', curve: ctx.Curve.defaults()});
  reconcilePointerState(ctx);
  ctx.actionProc.running = true;
  ctx.restorePointerFeel();
  const restore = ctx.pendingActions.at(-1);
  assert.equal(restore.option, 'pointer_restore');
  assert.deepEqual(restore.value.imported, imported);
  assert.equal(restore.value.calibration.apple, 47);
  assert.equal(ctx.curveEditor.draft.profile, 'imported');
  assert.equal(ctx.Curve.same(ctx.curveEditor.draft, ctx.pointerFeel), true);
  assert.equal(Object.hasOwn(ctx.curveEditor.draft, 'calibration'), false);
  reconcilePointerState(ctx);
  ctx.actionProc.running = true;
  ctx.applyPointerFeel(ctx.curveEditor.draft);
  assert.deepEqual(ctx.pendingActions.at(-1).value.imported,
    {file: 'saved.json', sha256: 'c'.repeat(64), tracking_speed: 1},
    'a fresh Apply resolves the source again rather than reusing undo data');
}

// Progressive scrolling stays independent of the imported pointer curve.
{
  const ctx = context();
  ctx.actionProc.running = true;
  const imported = {file: 'saved.json', sha256: 'd'.repeat(64), tracking_speed: 1};
  ctx.applyPointerFeel({profile: 'imported', curve: ctx.Curve.defaults(), imported});
  ctx.toggleProgressiveScroll();
  assert.equal(ctx.pointerFeel.profile, 'imported');
  assert.equal(ctx.devices[0].settings.scroll_progressive, true);
  assert.equal(ctx.devices[0].settings.curve_preset, 'imported');
  reconcilePointerState(ctx);
  ctx.curveKind = 'scroll';
  ctx.applyPointerFeel({profile: 'mac', curve: ctx.Curve.scrollDefaults()});
  assert.equal(ctx.pointerFeel.profile, 'imported');
  assert.equal(ctx.scrollFeel.curve.precision, 1);
  assert.equal(ctx.scrollFeel.curve.fast, 2);
}

// Profile discovery never publishes a reply after device or tab changes.
{
  const ctx = context();
  ctx.editingCurve = true;
  ctx.refreshProfiles();
  const request = ctx.profilesProc.requestId;
  ctx.selectDevice('dell');
  ctx.receiveProfiles(JSON.stringify({profiles: [{file: 'old.json'}]}), request);
  assert.equal(ctx.pointerProfiles.profiles.length, 0);
  ctx.profilesProc.running = false;
  ctx.refreshProfiles();
  const next = ctx.profilesProc.requestId;
  ctx.changeTab('scrolling');
  ctx.receiveProfiles(JSON.stringify({profiles: [{file: 'wrong-tab.json'}]}), next);
  assert.equal(ctx.pointerProfiles.profiles.length, 0);
  assert.equal(ctx.editingCurve, false);
}
console.log('Imported Apply/undo, progressive scrolling, retained Mac-inspired, and stale profile reads passed.');

// An imported Apply finishes before the authoritative converted record is read.
// Keep editor actions blocked across this gap, stale reads, and failed reads.
{
  const ctx = context();
  ctx.editingCurve = true;
  const imported = {file: 'mac.json', sha256: 'e'.repeat(64), tracking_speed: 1};
  ctx.applyPointerFeel({profile: 'imported', curve: ctx.Curve.defaults(), imported});
  ctx.actionProc.running = false;
  ctx.finishAction(0);
  assert.equal(ctx.stateProc.running, true);
  assert.equal(ctx.pointerStatePending, true, 'finished write still awaits converted backend state');
  const generation = ctx.editGeneration;
  ctx.applyPointerFeel({profile: 'mac', curve: ctx.Curve.defaults()});
  ctx.restorePointerFeel();
  assert.equal(ctx.editGeneration, generation, 'Apply/Restore cannot capture a reference-only undo');
  ctx.stateGeneration = generation - 1;
  ctx.receiveState(JSON.stringify({devices: ctx.devices}));
  assert.equal(ctx.pointerStatePending, true, 'a stale read cannot release editor actions');
  ctx.stateGeneration = generation;
  ctx.receiveState('{invalid json');
  assert.equal(ctx.pointerStatePending, true);
  ctx.receiveState('{}');
  assert.equal(ctx.pointerStatePending, true, 'an incomplete response cannot release editor actions');
  ctx.receiveState(JSON.stringify({error: 'display unavailable'}));
  assert.equal(ctx.pointerStatePending, true, 'a failed read cannot release editor actions');
  ctx.stateProc.running = false;
  ctx.finishStateRead(124);
  assert.match(ctx.settingsError, /display unavailable/);
  ctx.refresh();
  assert.equal(ctx.stateProc.running, true, 'normal refresh retries the authoritative read');
  const rows = JSON.parse(JSON.stringify(ctx.devices));
  rows[0].settings.imported_curve = {...imported, name: 'Mac', devices: {apple: {step: 0.1, points: [0, 1]}}};
  rows[0].previous_pointer_feel = {profile: 'adaptive', curve: ctx.Curve.defaults(), calibration: {apple: 47}};
  ctx.receiveState(JSON.stringify({devices: rows}));
  assert.equal(ctx.pointerStatePending, false, 'current successful state releases the editor');
  assert.equal(ctx.settingsError, '');
  ctx.stateProc.running = false;
  ctx.applyPointerFeel({profile: 'mac', curve: ctx.Curve.defaults()});
  assert.equal(ctx.previousFeels.apple.imported.devices.apple.points[1], 1,
    'the next Apply captures converted imported data for a valid restore');
}


// Switching progressive scrolling on creates the native preset only for non-custom pointers.
{
  for (const option of ['scroll_progressive', 'scroll_feel']) {
    const ctx = context();
    ctx.actionProc.running = true;
    ctx.devices[0].settings.accel_profile = 'flat';
    ctx.devices[0].settings.imported_curve = {file: 'stale.json'};
    ctx.enqueue(option, option === 'scroll_progressive' ? true
      : {profile: 'mac', curve: ctx.Curve.scrollDefaults()});
    assert.equal(ctx.devices[0].settings.curve_preset, 'mac');
    assert.equal(ctx.devices[0].settings.imported_curve, undefined,
      'a native pointer preset must discard stale imported data');
  }
  assert.match(qml, /busy: actionProc\.running \|\| root\.pendingActions\.length > 0 \|\| root\.pointerStatePending/,
    'the editor remains visibly blocked through authoritative reconciliation');
}
{
  const ctx = context();
  const saved = JSON.stringify({devices: ctx.devices});
  ctx.applyPointerFeel({profile: 'mac', curve: ctx.Curve.defaults()});
  ctx.actionProc.running = false;
  ctx.finishAction(1);
  ctx.receiveState(saved);
  assert.equal(ctx.pointerStatePending, false, 'successful rollback state permits a retry');
  assert.match(ctx.settingsError, /Could not save/, 'reconciliation must retain the failed-save message');
  ctx.stateProc.running = false;
  ctx.applyPointerFeel({profile: 'mac', curve: ctx.Curve.defaults()});
  assert.equal(ctx.settingsError, '', 'an explicit retry clears the previous save error');
}
console.log('Pointer state reconciliation race and progressive-scroll imported-data cleanup passed.');

{
  const ctx = context();
  ctx.devices[0].imported_missing_interfaces = ['apple-inc.-magic-trackpad-2'];
  ctx.loadSelection();
  assert.equal(ctx.pointerMissingInterfaces[0], 'apple-inc.-magic-trackpad-2');
  ctx.selectDevice('dell');
  assert.equal(ctx.pointerMissingInterfaces.length, 0, 'missing-interface notices belong to the selected device');
  const summary = qml.match(/objectName: "pointerFeelSummary"[\s\S]*?font\.pixelSize: Style\.font\.caption/)[0];
  assert.match(summary, /textFormat: Text\.PlainText/, 'profile subtitle must keep imported markup literal');
  assert.match(qml, /missingInterfaces: root\.curveKind === "pointer" \? root\.pointerMissingInterfaces : \[\]/,
    'the warning belongs to the pointer editor, not the independent scroll editor');
}
