pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Controls as Controls
import Quickshell.Io
import qs.Ui
import qs.Commons
import "Model.js" as Model
import "Curve.js" as Curve

Panel {
  id: root
  moduleName: "davefano.trackpad-plus"
  ipcTarget: "davefano.trackpad-plus"
  manageIpc: true

  property string releaseVersion: ""
  FileView {
    path: Qt.resolvedUrl("manifest.json")
    onLoaded: {
      try { root.releaseVersion = JSON.parse(text()).version || "" }
      catch (error) { root.releaseVersion = "" }
    }
  }

  // Each panel instance can select a device; the helper serializes writes across bars.
  property var devices: []
  property string selectedDevice: "apple"
  property string selectedLabel: "Apple"
  property bool deviceConnected: false
  property bool hasSavedSettings: false
  property string deviceName: ""
  property bool touchpadEnabled: true
  property bool naturalScroll: false
  property bool tapToClick: true
  property bool disableWhileTyping: true
  property bool clickfingerBehavior: true
  property bool pointerAcceleration: true
  property var pointerFeel: ({ profile: "adaptive", curve: Curve.defaults() })
  property var previousFeels: ({})
  property bool pointerDrift: false
  property var pointerMissingInterfaces: []
  // macOS profiles found for the selected trackpad, read when the editor opens.
  property var pointerProfiles: ({ loading: false, error: "", directory: "", profiles: [] })
  property int profilesRequest: 0
  property bool profilesPending: false
  property bool scrollProgressive: false
  property var scrollFeel: ({ profile: "mac", curve: Curve.scrollDefaults() })
  property var previousScrollFeels: ({})
  property string curveKind: "pointer"
  property bool editingCurve: false
  property bool deviceSettingsOpen: false
  property bool gestureCanEdit: false
  property bool gestureCanRestore: false
  property var gestureCompanion: ({installed: false})
  property string overviewPreviewText: ""
  property bool overviewReceived: false
  property int overviewRequest: 0
  property var gestureHymission: ({available: false, message: "Checking HyMission…"})
  property string gestureStatus: "Loading gesture settings…"
  property string gestureError: ""
  property bool gestureReceived: false
  readonly property string gestureBackend: decodeURIComponent(String(Qt.resolvedUrl("gestures.py")).replace(/^file:\/\//, ""))
  property real scrollScale: 1
  property real scrollFactor: 0.2
  property real pointerSpeed: 0.0
  property real pendingPointerSpeed: 0.0
  property string settingsError: ""
  property string actionError: ""
  property var pendingActions: []
  property int editGeneration: 0
  property int stateGeneration: 0
  property bool refreshPending: false
  // Profile writes can materialize imported curves; wait for authoritative undo data.
  property bool pointerStatePending: false
  readonly property string backend: decodeURIComponent(String(Qt.resolvedUrl("trackpads.py")).replace(/^file:\/\//, ""))
  readonly property string palmBackend: decodeURIComponent(String(Qt.resolvedUrl("palm.py")).replace(/^file:\/\//, ""))
  property bool palmReceived: false
  property string palmRequestDevice: ""

  function refreshPalm() {
    if (palmProc.running) return
    if (selectedDevice !== "apple") {
      palmEditor.acceptSettings({supported: false})
      return
    }
    palmRequestDevice = selectedDevice
    palmReceived = false
    palmProc.command = bounded(8, ["python3", "-B", palmBackend, "state", selectedDevice])
    palmProc.running = true
  }

  function applyPalm(threshold) {
    if (palmProc.running || selectedDevice !== "apple") return
    palmRequestDevice = selectedDevice
    palmReceived = false
    palmEditor.error = ""
    palmEditor.busy = true
    palmProc.command = bounded(120, ["python3", "-B", palmBackend, "set", selectedDevice, threshold])
    palmProc.running = true
  }

  function receivePalm(raw) {
    palmReceived = true
    if (palmRequestDevice !== selectedDevice) return
    try {
      var data = JSON.parse(raw)
      if (data.error) { palmEditor.error = data.error; return }
      if (palmEditor.busy) palmEditor.dirty = false
      palmEditor.error = ""
      palmEditor.acceptSettings(data)
    } catch (error) { palmEditor.error = "Could not read palm settings" }
  }

  function updateState(raw) {
    var data
    try { data = JSON.parse(raw) } catch (e) { settingsError = "Could not read trackpad settings"; return }
    if (data.error) { settingsError = data.error; return }
    if (!Array.isArray(data.devices)) { settingsError = "Could not read trackpad settings"; return false }
    devices = data.devices
    loadSelection()
    refreshPalm()
    return true
  }

  function loadSelection() {
    var row = null
    for (var i = 0; i < devices.length; i++) {
      if (devices[i].id === selectedDevice) row = devices[i]
    }
    if (!row && devices.length) { row = devices[0]; selectedDevice = row.id }
    if (!row) { deviceName = ""; deviceConnected = false; return }
    selectedLabel = row.label
    deviceConnected = row.connected
    hasSavedSettings = row.configured !== false
    deviceName = row.names[0] || ""
    var v = row.settings
    touchpadEnabled = v.enabled
    naturalScroll = v.natural_scroll
    tapToClick = v.tap_to_click
    disableWhileTyping = v.disable_while_typing
    clickfingerBehavior = v.clickfinger_behavior
    pointerAcceleration = v.accel_profile !== "flat"
    pointerFeel = Curve.fromSettings(v)
    pointerDrift = !!row.imported_drift
    pointerMissingInterfaces = row.imported_missing_interfaces || []
    var previous = Curve.copy(previousFeels)
    if (row.previous_pointer_feel) previous[selectedDevice] = row.previous_pointer_feel
    else delete previous[selectedDevice]
    previousFeels = previous
    scrollProgressive = v.scroll_progressive === true
    scrollFeel = Curve.fromScrollSettings(v)
    var previousScroll = Curve.copy(previousScrollFeels)
    if (row.previous_scroll_feel) previousScroll[selectedDevice] = row.previous_scroll_feel
    else delete previousScroll[selectedDevice]
    previousScrollFeels = previousScroll
    scrollScale = v.scroll_scale || Math.max(1, v.scroll_factor)
    scrollFactor = v.scroll_factor / scrollScale
    pendingScrollFactor = scrollFactor
    pointerSpeed = v.sensitivity
    pendingPointerSpeed = pointerSpeed
  }

  function selectDevice(key) {
    // Flush pending slider edits against the OLD device before changing selection.
    if (scrollDebounce.running) { scrollDebounce.stop(); commitScrollFactor() }
    if (pointerDebounce.running) { pointerDebounce.stop(); commitPointerSpeed() }
    profilesRequest++ // Discard replies for a previous device or editor tab.
    profilesPending = false
    selectedDevice = key
    settingsError = ""
    actionError = ""
    loadSelection()
    palmEditor.resetDraft()
    palmEditor.error = ""
    palmEditor.acceptSettings({supported: false})
    refreshPalm()
  }

  function enqueue(option, value) {
    var queue = pendingActions.slice()
    // Replace only consecutive writes of the same scalar; preserve profile/undo ordering.
    var last = queue.length ? queue[queue.length - 1] : null
    if (last && last.device === selectedDevice && last.option === option && option !== "pointer_feel" && option !== "scroll_feel" && option !== "pointer_restore") {
      queue.pop()
    }
    if (queue.length >= 128) {
      settingsError = "Too many pending changes; wait for them to finish"
      loadSelection()
      return
    }
    editGeneration++
    if (["pointer_feel", "pointer_restore", "scroll_feel", "scroll_progressive", "accel_profile"].indexOf(option) >= 0)
      pointerStatePending = true
    settingsError = ""
    actionError = ""
    queue.push({ device: selectedDevice, option: option, value: option === "pointer_feel" ? Curve.request(value) : value })
    pendingActions = queue
    // Keep the local snapshot consistent while queued writes finish.
    for (var i = 0; i < devices.length; i++) {
      if (devices[i].id === selectedDevice) {
        var settings = devices[i].settings
        if (option === "pointer_feel" || option === "pointer_restore") {
          devices[i].previous_pointer_feel = Curve.fromSettings(settings)
          devices[i].previous_pointer_feel.calibration = Curve.copy(devices[i].curve_calibration || {})
          devices[i].imported_drift = false
          settings.accel_profile = Curve.usesCurve(value.profile) ? "custom" : value.profile
          settings.curve = Curve.copy(value.curve)
          settings.curve_preset = value.profile === "mac" || value.profile === "imported" ? value.profile : "custom"
          if (value.profile === "imported") settings.imported_curve = Curve.copy(value.imported)
          else delete settings.imported_curve
          if (value.calibration) devices[i].curve_calibration = Curve.copy(value.calibration)
          if (value.profile === "adaptive" || value.profile === "flat") settings.scroll_progressive = false
        } else if (option === "scroll_feel") {
          devices[i].previous_scroll_feel = Curve.fromScrollSettings(settings)
          settings.scroll_progressive = true
          settings.scroll_curve = Curve.copy(value.curve)
          settings.scroll_curve_preset = value.profile === "mac" ? "mac" : "custom"
          if (settings.accel_profile !== "custom") {
            settings.accel_profile = "custom"
            settings.curve = Curve.presetForScale(settings.scroll_scale || Math.max(1, settings.scroll_factor))
            settings.curve_preset = "mac"
            delete settings.imported_curve
          }
        } else if (option === "scroll_progressive") {
          settings.scroll_progressive = value
          if (value && settings.accel_profile !== "custom") {
            settings.accel_profile = "custom"
            settings.curve = Curve.presetForScale(settings.scroll_scale || Math.max(1, settings.scroll_factor))
            settings.curve_preset = "mac"
            delete settings.imported_curve
          }
        } else if (option === "scroll_scale") {
          var oldScale = settings.scroll_scale || Math.max(1, settings.scroll_factor)
          settings.scroll_factor = Math.round(settings.scroll_factor * value / oldScale * 1000000) / 1000000
          settings.scroll_scale = value
        } else settings[option] = value
      }
    }
    runNextAction()
  }

  function runNextAction() {
    if (actionProc.running || pendingActions.length === 0) return
    var queue = pendingActions.slice()
    var next = queue.shift()
    pendingActions = queue
    actionProc.command = bounded(10, ["python3", backend, "set", next.device, next.option, JSON.stringify(next.value)])
    actionProc.running = true
  }

  // Pending scroll factor while dragging the slider.
  property real pendingScrollFactor: 0.4
  property bool scrollSetQueued: false

  // Carry sub-notch touchpad deltas between wheel events.
  property real wheelAccumulator: 0

  function runGestureAction(action, value) {
    if (gestureProc.running) return
    gestureError = ""
    gestureReceived = false
    var args = ["python3", "-B", gestureBackend, action]
    if (action === "set") args.push(JSON.stringify(value))
    gestureProc.command = bounded(30, args)
    gestureProc.running = true
  }

  function refreshGestures() { runGestureAction("state") }

  function receiveGestures(raw) {
    gestureReceived = true
    try {
      var data = JSON.parse(raw)
      if (data.error) { gestureError = data.error; return }
      if (!data.settings) { gestureError = "Could not read gesture settings"; return }
      gestureCanEdit = data.can_edit === true
      gestureCanRestore = data.can_restore === true
      gestureCompanion = data.companion || {installed: false}
      gestureHymission = data.hymission || {available: false, message: "Reload settings to check HyMission"}
      gestureStatus = data.message || ""
      gestureEditor.load(data.settings)
    } catch (error) { gestureError = "Could not read gesture settings" }
  }

  function finishGestureAction(code) {
    if ((code !== 0 || !gestureReceived) && !gestureError)
      gestureError = "Gesture update did not complete. Reload settings to recover and try again."
  }

  // Preview/status have their own process and never reload the settings draft.
  function requestOverview(preview) {
    if (overviewProc.running || gestureProc.running) return
    overviewRequest += 1
    overviewProc.requestId = overviewRequest
    overviewReceived = false
    overviewPreviewText = preview ? "Opening overview…" : "Checking overview…"
    overviewProc.command = bounded(12, ["python3", "-B", gestureBackend, preview ? "preview" : "overview-status"])
    overviewProc.running = true
  }

  function receiveOverview(raw, requestId) {
    if (requestId !== overviewRequest) return
    overviewReceived = true
    try {
      var data = JSON.parse(raw)
      gestureCompanion = data
      if (data.error) overviewPreviewText = data.message || data.error
      else if (!data.installed) overviewPreviewText = "Update or reinstall Trackpad Plus to include overview."
      else if (!data.reachable) overviewPreviewText = "Installed · not running. Test overview to open it."
      else if (!data.protocolCompatible) overviewPreviewText = "Overview version mismatch. Stop it and reinstall matching files."
      else if (data.lockState !== "unlocked") overviewPreviewText = "Overview is closed until the session is unlocked and lock status is available."
      else if (data.rendered) overviewPreviewText = "Overview rendered. Close with Escape, then apply to connect your gestures."
      else if (data.opened) overviewPreviewText = "Overview opened; rendering is not confirmed yet. Check status while it is visible."
      else overviewPreviewText = "Overview ready · closed. Test it to check window previews."
    } catch (error) { overviewPreviewText = "Could not read overview status. Try Test overview again." }
  }

  function finishOverview(code, requestId) {
    if (requestId !== overviewRequest) return
    if (!overviewReceived)
      overviewPreviewText = "Overview did not respond in time. Pointer controls and workspace swipes are unchanged."
  }

  // ---- Cursor navigation ----
  property string activeTab: "pointer"
  property string focusSection: "device"
  property int selectedIndex: 0
  property bool cursorActive: false
  readonly property var allSections: navigationSections()

  function navigationSections() {
    var sections = ["device", "device-settings"]
    if (deviceSettingsOpen) sections.push("enable")
    sections.push("tabs")
    if (activeTab === "scrolling") {
      var extra = ["scroll", "progressive"]
      if (scrollProgressive) extra.push("scroll-accel")
      return sections.concat(extra.concat(["natural"]))
    }
    if (activeTab === "gestures") return sections
    if (!Curve.usesCurve(pointerFeel.profile)) sections.push("pointer")
    sections = sections.concat(["acceleration", "tap", "typing", "clickfinger"])
    if (palmEditor.settings.supported) sections.push("palm")
    return sections
  }

  function changeTab(tab) {
    if (["pointer", "scrolling", "gestures"].indexOf(tab) < 0) return
    selectDevice(selectedDevice) // Commit pending slider edits before hiding them.
    editingCurve = false
    activeTab = tab
    focusSection = "tabs"
    keyCatcher.forceActiveFocus()
    if (tab === "gestures") refreshGestures()
  }

  readonly property string icon: {
    if (!deviceName) return ""
    return touchpadEnabled ? "󰟸" : "󰤳"
  }

  readonly property string heroStatusText: deviceConnected
    ? (touchpadEnabled ? (hasSavedSettings ? "Settings saved separately" : "Ready to customize") : "Trackpad disabled")
    : "Disconnected · settings remembered"

  readonly property color hoverFill: bar
    ? Style.hoverFillFor(bar.foreground, Color.accent)
    : "transparent"
  readonly property color selectedFill: bar
    ? Style.selectedFillFor(bar.foreground, Color.accent)
    : "transparent"

  function keyboardNavigationBlocked() {
    return editingCurve || gestureEditor.activeFocus || palmEditor.activeFocus
  }

  function moveCursor(delta) {
    if (delta > 0 && focusSection === "tabs" && activeTab === "gestures") {
      gestureEditor.beginEditing()
      return
    }
    var sections = allSections
    var sIdx = sections.indexOf(focusSection)
    if (sIdx < 0) { focusSection = sections[0]; return }

    if (delta > 0) {
      if (sIdx < sections.length - 1) focusSection = sections[sIdx + 1]
    } else {
      if (sIdx > 0) focusSection = sections[sIdx - 1]
    }
  }

  function moveCursorH(delta) {
    if (focusSection === "device") {
      var index = devices.findIndex(function(d) { return d.id === selectedDevice })
      var next = Math.max(0, Math.min(devices.length - 1, index + delta))
      if (devices[next]) selectDevice(devices[next].id)
    } else if (focusSection === "tabs") {
      var tabs = ["pointer", "scrolling", "gestures"]
      changeTab(tabs[Math.max(0, Math.min(2, tabs.indexOf(activeTab) + delta))])
    } else if (focusSection === "scroll") {
      adjustScrollFactor(delta > 0 ? 0.01 : -0.01)
    } else if (focusSection === "pointer") {
      adjustPointerSpeed(delta > 0 ? 0.1 : -0.1)
    }
  }

  function activateCursor() {
    if (focusSection === "tabs" && activeTab === "gestures") { gestureEditor.beginEditing(); return }
    if (focusSection === "device-settings") { toggleDeviceSettings(selectedDevice); return }
    if (focusSection === "acceleration") { openCurveEditor(); return }
    if (focusSection === "scroll-accel") { openScrollEditor(); return }
    if (focusSection === "progressive") { toggleProgressiveScroll(); return }
    if (focusSection === "enable") { toggleTouchpad(); return }
    if (focusSection === "natural") { toggleNaturalScroll(); return }
    if (focusSection === "tap") { toggleTapToClick(); return }
    if (focusSection === "typing") { toggleDisableWhileTyping(); return }
    if (focusSection === "palm") { palmEditor.beginEditing(); return }
    if (focusSection === "clickfinger") { toggleClickfingerBehavior(); return }
  }

  // ---- Process discipline ----
  //
  // Nothing this widget launches may outlive its usefulness. Every spawn goes
  // through here, so a wedged hyprctl, a stuck omarchy-* tool or a helper
  // blocked on something unforeseen is reaped rather than accumulating one
  // orphan per click.
  //
  // timeout(1) without --foreground runs the command in its own process group
  // and signals that group, so a shell's children die with it instead of being
  // left behind; -k follows SIGTERM with SIGKILL for anything that ignores it.
  function bounded(seconds, argv) {
    return ["timeout", "-k", "2", String(seconds)].concat(argv)
  }

  // ---- Actions: every change is scoped to the selected trackpad. ----
  function toggleTouchpad() {
    if (!deviceName) return
    touchpadEnabled = !touchpadEnabled
    enqueue("enabled", touchpadEnabled)
  }

  function toggleNaturalScroll() {
    var next = !naturalScroll
    naturalScroll = next
    setHyprOption("natural_scroll", next)
  }

  function toggleTapToClick() {
    var next = !tapToClick
    tapToClick = next
    setHyprOption("tap_to_click", next)
  }

  function toggleDisableWhileTyping() {
    var next = !disableWhileTyping
    disableWhileTyping = next
    setHyprOption("disable_while_typing", next)
  }

  function toggleClickfingerBehavior() {
    var next = !clickfingerBehavior
    clickfingerBehavior = next
    setHyprOption("clickfinger_behavior", next)
  }

  function setHyprOption(option, value) { enqueue(option, value) }

  function togglePointerAcceleration() {
    if (!touchpadEnabled) return
    pointerAcceleration = !pointerAcceleration
    enqueue("accel_profile", pointerAcceleration ? "adaptive" : "flat")
  }

  function openCurveEditor() {
    if (!touchpadEnabled) return
    selectDevice(selectedDevice) // Flush any pending speed edits first.
    curveKind = "pointer"
    editingCurve = true
    curveEditor.begin()
    refreshProfiles()
  }

  // Profiles are listed for the device being edited; a reply for another device is dropped.
  function refreshProfiles() {
    if (profilesProc.running) { profilesPending = true; return }
    profilesPending = false
    profilesRequest++
    pointerProfiles = { loading: true, error: "", directory: pointerProfiles.directory,
      profiles: pointerProfiles.device === selectedDevice ? pointerProfiles.profiles : [], device: selectedDevice }
    profilesProc.requestId = profilesRequest
    profilesProc.command = bounded(15, ["python3", backend, "profiles", selectedDevice])
    profilesProc.running = true
  }

  function receiveProfiles(raw, request) {
    if (request !== profilesRequest || pointerProfiles.device !== selectedDevice || curveKind !== "pointer" || !editingCurve) return
    var data
    try { data = JSON.parse(raw) } catch (e) { data = { error: "Could not read pointer profiles" } }
    if (data.error || pointerProfiles.device !== selectedDevice) {
      pointerProfiles = { loading: false, error: data.error || "", directory: "", profiles: [], device: pointerProfiles.device }
      return
    }
    var monitor = data.context && data.context.monitor || {}
    pointerProfiles = { loading: false, error: monitor.error ? "Display size unavailable: " + monitor.error : "",
      directory: data.directory || "", profiles: data.profiles || [], device: pointerProfiles.device }
  }

  function finishProfiles(code, request) {
    if (request === profilesRequest && pointerProfiles.loading)
      pointerProfiles = { loading: false, error: "Could not read pointer profiles", directory: "", profiles: [], device: pointerProfiles.device }
    if (profilesPending && editingCurve && curveKind === "pointer") refreshProfiles()
  }

  function openScrollEditor() {
    if (!touchpadEnabled) return
    selectDevice(selectedDevice)
    curveKind = "scroll"
    editingCurve = true
    curveEditor.begin()
  }

  function toggleProgressiveScroll() {
    var next = !scrollProgressive
    scrollProgressive = next
    if (next && !Curve.usesCurve(pointerFeel.profile)) {
      pointerFeel = { profile: "mac", curve: Curve.presetForScale(scrollScale) }
    }
    enqueue("scroll_progressive", next)
  }

  function applyPointerFeel(value, restoring) {
    if (pointerStatePending) return
    if (curveKind === "scroll") {
      var previousScroll = Curve.copy(previousScrollFeels)
      previousScroll[selectedDevice] = Curve.copy(scrollFeel)
      previousScrollFeels = previousScroll
      enqueue("scroll_feel", value)
      loadSelection()
      return
    }
    var previous = Curve.copy(previousFeels)
    previous[selectedDevice] = Curve.copy(pointerFeel)
    previousFeels = previous
    enqueue(restoring ? "pointer_restore" : "pointer_feel", value)
    loadSelection()
  }

  function restorePointerFeel() {
    if (pointerStatePending) return
    if (curveKind === "scroll") {
      if (!previousScrollFeels[selectedDevice]) return
      var scrollValue = Curve.copy(previousScrollFeels[selectedDevice])
      applyPointerFeel(scrollValue)
      curveEditor.draft = Curve.copy(scrollValue)
      return
    }
    if (!previousFeels[selectedDevice]) return
    var value = Curve.copy(previousFeels[selectedDevice])
    applyPointerFeel(value, true)
    curveEditor.draft = Curve.fromSettings(devices.filter(function(row) { return row.id === selectedDevice })[0].settings)
  }

  function toggleDeviceSettings(key) {
    var sameDevice = key === selectedDevice
    selectDevice(key)
    deviceSettingsOpen = sameDevice ? !deviceSettingsOpen : true
  }

  function setScrollScale(value) {
    var next = Math.max(0.1, Math.min(10, Math.round(value * 100) / 100))
    if (Math.abs(next - scrollScale) < 0.000001) return
    // A pending speed edit belongs to the old scale; keep that ordering.
    if (scrollDebounce.running) { scrollDebounce.stop(); commitScrollFactor() }
    enqueue("scroll_scale", next)
    scrollScale = next
  }

  function adjustScrollFactor(delta) {
    var next = Model.clampScrollFactor(scrollFactor + delta)
    scrollFactor = next
    pendingScrollFactor = next
    scrollDebounce.restart()
  }

  function setScrollFactor(value) {
    var clamped = Model.clampScrollFactor(value)
    scrollFactor = clamped
    pendingScrollFactor = clamped
    scrollDebounce.restart()
  }

  function commitScrollFactor() {
    setHyprOption("scroll_factor", Math.round(pendingScrollFactor * scrollScale * 1000000) / 1000000)
  }

  function adjustPointerSpeed(delta) {
    if (Curve.usesCurve(pointerFeel.profile)) return
    var next = Model.clampSensitivity(pointerSpeed + delta)
    pointerSpeed = next
    pendingPointerSpeed = next
    pointerDebounce.restart()
  }

  function setPointerSpeed(value) {
    if (Curve.usesCurve(pointerFeel.profile)) return
    var clamped = Model.clampSensitivity(value)
    pointerSpeed = clamped
    pendingPointerSpeed = clamped
    pointerDebounce.restart()
  }

  function commitPointerSpeed() {
    enqueue("sensitivity", Model.clampSensitivity(pendingPointerSpeed))
  }

  function refresh() {
    refreshPending = true
    if (!stateProc.running && !actionProc.running && pendingActions.length === 0
        && !scrollDebounce.running && !pointerDebounce.running) {
      refreshPending = false
      stateGeneration = editGeneration
      stateProc.running = true
    }
  }

  function receiveState(raw) {
    // A read remains stale even after the newer write has finished.
    if (stateGeneration !== editGeneration || actionProc.running || pendingActions.length
        || scrollDebounce.running || pointerDebounce.running) {
      refreshPending = true
      return
    }
    if (updateState(raw)) {
      pointerStatePending = false
      settingsError = actionError || ""
    }
  }

  function finishStateRead(code) {
    if (code !== 0 && !settingsError) settingsError = "Could not read trackpad settings"
    if (refreshPending) refresh()
  }

  function finishAction(code) {
    if (code !== 0 && !actionError) actionError = settingsError || "Could not save trackpad settings"
    if (actionError) settingsError = actionError
    if (pendingActions.length) runNextAction()
    else refresh()
  }

  // ---- Lifecycle ----
  visible: deviceName !== ""
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Component.onCompleted: refresh()

  onOpenedChanged: {
    if (opened) {
      editingCurve = false
      refresh()
      if (activeTab === "gestures") refreshGestures()
      focusSection = "device"
      cursorActive = false
    }
  }

  // Poll while open so external changes are reflected.
  Timer {
    interval: 3000
    running: root.opened || root.devices.length === 0
    repeat: true
    onTriggered: root.refresh()
  }

  // Give omarchy-toggle-input-device and the reload time to land, then
  // reconcile the panel against real state.
  Timer {
    id: enableSettle
    interval: 600
    repeat: false
    onTriggered: root.refresh()
  }

  Timer {
    id: scrollDebounce
    interval: 200
    repeat: false
    onTriggered: root.commitScrollFactor()
  }

  Timer {
    id: pointerDebounce
    interval: 200
    repeat: false
    onTriggered: root.commitPointerSpeed()
  }

  Process {
    id: stateProc
    command: root.bounded(15, ["python3", root.backend, "state"])
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.receiveState(String(text))
      }
    }
    onExited: function(code, status) {
      Qt.callLater(function() { root.finishStateRead(code) })
    }
  }

  Process {
    id: actionProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(String(text))
          if (data.error) { root.actionError = data.error; root.settingsError = data.error }
        } catch (e) { root.actionError = "Could not save trackpad settings"; root.settingsError = root.actionError }
      }
    }
    onExited: function(code, status) {
      Qt.callLater(function() { root.finishAction(code) })
    }
  }

  Process {
    id: profilesProc
    property int requestId: 0
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.receiveProfiles(String(text), profilesProc.requestId)
    }
    onExited: function(code, status) {
      var request = requestId
      Qt.callLater(function() { root.finishProfiles(code, request) })
    }
  }

  Process {
    id: palmProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.receivePalm(String(text))
    }
    onExited: function(code, status) {
      if (root.palmRequestDevice === root.selectedDevice && code !== 0 && !root.palmReceived)
        palmEditor.error = "Palm settings did not respond in time"
      palmEditor.busy = false
      if (root.palmRequestDevice !== root.selectedDevice) Qt.callLater(root.refreshPalm)
    }
  }

  Process {
    id: gestureProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.receiveGestures(String(text))
    }
    onExited: function(code, status) {
      Qt.callLater(function() { root.finishGestureAction(code) })
    }
  }

  Process {
    id: overviewProc
    property int requestId: 0
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.receiveOverview(String(text), overviewProc.requestId)
    }
    onExited: function(code, status) {
      var request = requestId
      Qt.callLater(function() { root.finishOverview(code, request) })
    }
  }

  // ---- Bar icon ----
  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.icon
    onPressed: function(b) {
      if (b === Qt.RightButton) root.toggleTouchpad()
      else root.toggle()
    }
  }

  // ---- Popup panel ----
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(root.editingCurve ? 430 : 340))
    contentHeight: panel.fittedContentHeight(root.editingCurve ? curveColumn.implicitHeight : column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.keyboardNavigationBlocked()
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        if (dy !== 0) root.moveCursor(dy)
        else if (dx !== 0) root.moveCursorH(dx)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      ScrollView {
        anchors.fill: parent
        visible: root.editingCurve
        clip: true
        contentWidth: availableWidth
        Column {
          id: curveColumn
          width: parent.width
          spacing: Style.space(10)
          Text {
            visible: root.settingsError !== ""
            width: parent.width
            text: root.settingsError
            color: Color.urgent
            wrapMode: Text.Wrap
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
          CurveEditor {
            id: curveEditor
            width: parent.width
            foreground: root.bar.foreground
            accent: Color.accent
            fontFamily: root.bar.fontFamily
            uiScale: Style.space(100) / 100
            saved: root.curveKind === "scroll" ? root.scrollFeel : root.pointerFeel
            kind: root.curveKind
            gainMaximum: root.scrollScale
            deviceLabel: root.selectedLabel + " Trackpad"
            busy: actionProc.running || root.pendingActions.length > 0 || root.pointerStatePending
            settingsError: root.settingsError
            canRestore: root.curveKind === "scroll" ? !!root.previousScrollFeels[root.selectedDevice] : !!root.previousFeels[root.selectedDevice]
            drift: root.curveKind === "pointer" && root.pointerDrift
            missingInterfaces: root.curveKind === "pointer" ? root.pointerMissingInterfaces : []
            profiles: root.pointerProfiles.device === root.selectedDevice ? root.pointerProfiles.profiles : []
            profilesDirectory: root.pointerProfiles.directory
            profilesStatus: root.pointerProfiles.loading ? "Looking for macOS profiles…" : root.pointerProfiles.error
            onApplyRequested: function(value) { root.applyPointerFeel(value) }
            onRestoreRequested: root.restorePointerFeel()
            onBackRequested: { root.editingCurve = false; keyCatcher.forceActiveFocus() }
          }
        }
      }

      ScrollView {
        anchors.fill: parent
        visible: !root.editingCurve
        clip: true
        contentWidth: availableWidth
      Column {
        id: column
        width: parent.width
        spacing: Style.space(14)

        Row {
          width: parent.width
          spacing: Style.space(8)
          Repeater {
            model: root.devices
            CursorSurface {
              id: deviceButton
              required property var modelData
              width: (column.width - Style.space(8) * (root.devices.length - 1)) / Math.max(1, root.devices.length)
              height: Style.space(38)
              foreground: root.bar.foreground
              fill: root.selectedDevice === modelData.id ? root.selectedFill : root.hoverFill
              current: root.selectedDevice === modelData.id
              hasCursor: root.cursorActive && root.focusSection === "device" && root.selectedDevice === modelData.id
              Text {
                anchors.centerIn: parent
                width: parent.width - Style.space(76)
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                text: deviceButton.modelData.label
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.body
                font.bold: root.selectedDevice === deviceButton.modelData.id
              }
              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: { root.selectDevice(parent.modelData.id); root.focusSection = "device" }
              }
              CursorSurface {
                id: deviceGear
                anchors.right: parent.right
                anchors.rightMargin: Style.space(4)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(30)
                height: Style.space(30)
                foreground: root.bar.foreground
                hasCursor: root.cursorActive && root.focusSection === "device-settings" && root.selectedDevice === deviceButton.modelData.id
                Accessible.role: Accessible.Button
                Accessible.name: "Settings for " + deviceButton.modelData.label
                Text {
                  anchors.centerIn: parent
                  text: "⚙"
                  color: root.bar.foreground
                  font.pixelSize: Style.font.heading
                }
                MouseArea {
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onContainsMouseChanged: if (containsMouse) {
                    root.cursorActive = true
                    root.focusSection = "device-settings"
                  }
                  onClicked: root.toggleDeviceSettings(deviceButton.modelData.id)
                }
                PanelToolTip {
                  visible: deviceGear.hasCursor
                  text: "Trackpad settings"
                  fontFamily: root.bar.fontFamily
                }
              }
            }
          }
        }

        Text {
          width: parent.width
          visible: root.settingsError !== ""
          text: root.settingsError
          wrapMode: Text.Wrap
          color: Color.urgent
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
        }

        Column {
          width: parent.width
          visible: root.deviceSettingsOpen
          spacing: Style.space(8)
          ToggleRow {
            width: parent.width
            label: "Enable trackpad"
            description: "Turn the selected device on or off"
            checked: root.touchpadEnabled
            sectionName: "enable"
            opacity: 1
            onToggled: root.toggleTouchpad()
          }
          Item {
            width: parent.width
            implicitHeight: scaleSpinner.implicitHeight
            Text {
              anchors.left: parent.left
              anchors.leftMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              text: "Device scale"
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.body
            }
            SpinBox {
              id: scaleSpinner
              objectName: "scrollScaleSpinner"
              anchors.right: parent.right
              anchors.rightMargin: Style.space(10)
              width: Style.space(110)
              from: 10
              to: 1000
              stepSize: 10
              value: Math.round(root.scrollScale * 100)
              editable: true
              live: false
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.body
              textFromValue: function(value, locale) { return (value / 100).toLocaleString(locale, 'f', 2) }
              valueFromText: function(text, locale) { return Math.round(Number.fromLocaleString(locale, text) * 100) }
              validator: DoubleValidator { bottom: 0.1; top: 10; decimals: 2; notation: DoubleValidator.StandardNotation; locale: scaleSpinner.locale.name }
              onValueModified: root.setScrollScale(value / 100)
              Accessible.name: "Device scale for " + root.selectedLabel
              wheelEnabled: false
              implicitHeight: Style.space(34)
              leftPadding: Style.space(8)
              rightPadding: Style.space(24)
              function handleLargeStep(event) {
                if (!(event.modifiers & Qt.ShiftModifier) || (event.key !== Qt.Key_Up && event.key !== Qt.Key_Down)) return
                var current = scaleInput.acceptableInput ? valueFromText(scaleInput.text, locale) : value
                root.setScrollScale(Math.max(from, Math.min(to, current + (event.key === Qt.Key_Up ? 1 : -1) * stepSize * 10)) / 100)
                event.accepted = true
              }
              Keys.onPressed: function(event) { handleLargeStep(event) }
              contentItem: TextInput {
                id: scaleInput
                objectName: "scrollScaleInput"
                text: scaleSpinner.textFromValue(scaleSpinner.value, scaleSpinner.locale)
                font: scaleSpinner.font
                color: root.bar.foreground
                selectionColor: Color.accent
                selectedTextColor: Color.background
                verticalAlignment: TextInput.AlignVCenter
                selectByMouse: true
                clip: true
                validator: scaleSpinner.validator
                inputMethodHints: Qt.ImhFormattedNumbersOnly
                Keys.onPressed: function(event) { scaleSpinner.handleLargeStep(event) }
              }
              background: Rectangle {
                color: Qt.alpha(root.bar.foreground, 0.04)
                border.width: 1
                border.color: scaleSpinner.activeFocus ? Color.accent : Qt.alpha(root.bar.foreground, 0.2)
              }
              up.indicator: Text {
                x: scaleSpinner.width - width
                y: 0
                width: Style.space(22)
                height: scaleSpinner.height / 2
                text: "▴"
                color: root.bar.foreground
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                opacity: scaleSpinner.up.pressed ? 1 : 0.65
              }
              down.indicator: Text {
                x: scaleSpinner.width - width
                y: scaleSpinner.height / 2
                width: Style.space(22)
                height: scaleSpinner.height / 2
                text: "▾"
                color: root.bar.foreground
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                opacity: scaleSpinner.down.pressed ? 1 : 0.65
              }
            }
          }
          Text {
            width: parent.width - Style.space(20)
            x: Style.space(10)
            text: "Sets the scroll-speed range and pointer acceleration chart maximum. The scroll acceleration chart has its own 10× range."
            wrapMode: Text.WordWrap
            color: Qt.darker(root.bar.foreground, 1.4)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        Text {
          width: parent.width
          text: root.heroStatusText
          color: Qt.alpha(root.bar.foreground, 0.65)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }

        Row {
          width: parent.width
          spacing: Style.space(4)
          Repeater {
            model: ["Pointer", "Scrolling", "Gestures"]
            Controls.Button {
              id: tabButton
              required property string modelData
              required property int index
              width: (column.width - Style.space(8)) / 3
              height: Style.space(34)
              text: modelData
              Accessible.name: modelData + " tab"
              property bool selected: root.activeTab === modelData.toLowerCase()
              onClicked: root.changeTab(modelData.toLowerCase())
              contentItem: Text {
                text: tabButton.text
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.body
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
              }
              background: Rectangle {
                color: tabButton.selected ? root.selectedFill : root.hoverFill
                border.width: 1
                border.color: tabButton.selected || tabButton.activeFocus ? Color.accent : Qt.alpha(root.bar.foreground, 0.2)
              }
            }
          }
        }

        Column {
          id: settingsList
          visible: root.activeTab !== "gestures"
          width: parent.width
          spacing: -1 // Adjacent row borders share exactly the same pixel.
          // ========== Scroll speed slider ==========
          SettingRow {
            sectionName: "scroll"
            visible: root.activeTab === "scrolling"
            width: parent.width
            implicitHeight: scrollContent.implicitHeight + Style.space(28)
            Column {
              id: scrollContent
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.leftMargin: Style.space(10)
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(8)
              opacity: root.touchpadEnabled ? 1.0 : 0.4

              Item {
                width: parent.width
                implicitHeight: scrollLabel.implicitHeight

                Text {
                  id: scrollLabel
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  text: "Scroll Speed"
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                }

                Text {
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: {
                    var v = scrollSlider.dragging ? scrollSlider.liveValue : root.scrollFactor
                    return Model.scrollSpeedLabel(v) + "  " + v.toFixed(2) + (root.scrollScale === 1 ? "×" : " × " + root.scrollScale.toFixed(2))
                  }
                  color: Qt.darker(root.bar.foreground, 1.4)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              Item {
                width: parent.width
                implicitHeight: Math.max(minusBtn.implicitHeight, scrollRow.implicitHeight, plusBtn.implicitHeight)

                // Minus button
                CursorSurface {
                  id: minusSurface
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(32)
                  height: Style.space(32)
                  hasCursor: false
                  foreground: root.bar.foreground
                  fill: root.hoverFill

                  Text {
                    id: minusBtn
                    anchors.centerIn: parent
                    text: "−"
                    color: root.bar.foreground
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.heading
                    opacity: root.scrollFactor <= 0.01 ? 0.3 : 1.0
                  }

                  MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.adjustScrollFactor(-0.01)
                    onContainsMouseChanged: if (containsMouse) {
                      root.cursorActive = true
                      root.focusSection = "scroll"
                    }
                  }
                }

                // Slider track
                CursorSurface {
                  id: scrollRow
                  anchors.left: minusSurface.right
                  anchors.right: plusSurface.left
                  anchors.leftMargin: Style.space(4)
                  anchors.rightMargin: Style.space(4)
                  anchors.verticalCenter: parent.verticalCenter
                  height: scrollSlider.implicitHeight + Style.spacing.controlGap
                  hasCursor: false
                  foreground: root.bar.foreground
                  outline: true

                  PanelSlider {
                    id: scrollSlider
                    bar: root.bar
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(6)
                    anchors.rightMargin: Style.space(6)
                    minimum: 0.01
                    maximum: 1.0
                    step: 0.01
                    value: root.scrollFactor
                    onMoved: function(v) { root.setScrollFactor(v) }
                    onReleased: function(v) {
                      root.setScrollFactor(v)
                      scrollDebounce.stop()
                      root.commitScrollFactor()
                    }
                  }

                  HoverHandler {
                    onHoveredChanged: if (hovered) {
                      root.cursorActive = true
                      root.focusSection = "scroll"
                    }
                  }
                }

                // Plus button
                CursorSurface {
                  id: plusSurface
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(32)
                  height: Style.space(32)
                  hasCursor: false
                  foreground: root.bar.foreground
                  fill: root.hoverFill

                  Text {
                    id: plusBtn
                    anchors.centerIn: parent
                    text: "+"
                    color: root.bar.foreground
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.heading
                    opacity: root.scrollFactor >= 1.0 ? 0.3 : 1.0
                  }

                  MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.adjustScrollFactor(0.01)
                    onContainsMouseChanged: if (containsMouse) {
                      root.cursorActive = true
                      root.focusSection = "scroll"
                    }
                  }
                }
              }
            }
          }

          // ========== Pointer speed slider ==========
          // Range is Hyprland's [-1.0, 1.0], centered on 0.0 rather than running
          // low-to-high like the scroll slider above it.
          SettingRow {
            sectionName: "pointer"
            width: parent.width
            visible: root.activeTab === "pointer" && !Curve.usesCurve(root.pointerFeel.profile)
            implicitHeight: pointerContent.implicitHeight + Style.space(28)
            Column {
              id: pointerContent
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.leftMargin: Style.space(10)
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(8)
              opacity: root.touchpadEnabled ? 1.0 : 0.4

              Item {
                width: parent.width
                implicitHeight: pointerLabel.implicitHeight

                Text {
                  id: pointerLabel
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  text: "Pointer Speed"
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                }

                Text {
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  text: {
                    var v = pointerSlider.dragging ? pointerSlider.liveValue : root.pointerSpeed
                    return Model.pointerSpeedLabel(v) + "  " + v.toFixed(1)
                  }
                  color: Qt.darker(root.bar.foreground, 1.4)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              Item {
                width: parent.width
                implicitHeight: Math.max(pMinusBtn.implicitHeight, pointerRow.implicitHeight, pPlusBtn.implicitHeight)

                CursorSurface {
                  id: pMinusSurface
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(32)
                  height: Style.space(32)
                  hasCursor: false
                  foreground: root.bar.foreground
                  fill: root.hoverFill

                  Text {
                    id: pMinusBtn
                    anchors.centerIn: parent
                    text: "−"
                    color: root.bar.foreground
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.heading
                    opacity: root.pointerSpeed <= -1.0 ? 0.3 : 1.0
                  }

                  MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.adjustPointerSpeed(-0.1)
                    onContainsMouseChanged: if (containsMouse) {
                      root.cursorActive = true
                      root.focusSection = "pointer"
                    }
                  }
                }

                CursorSurface {
                  id: pointerRow
                  anchors.left: pMinusSurface.right
                  anchors.right: pPlusSurface.left
                  anchors.leftMargin: Style.space(4)
                  anchors.rightMargin: Style.space(4)
                  anchors.verticalCenter: parent.verticalCenter
                  height: pointerSlider.implicitHeight + Style.spacing.controlGap
                  hasCursor: false
                  foreground: root.bar.foreground
                  outline: true

                  PanelSlider {
                    id: pointerSlider
                    bar: root.bar
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(6)
                    anchors.rightMargin: Style.space(6)
                    minimum: -1.0
                    maximum: 1.0
                    step: 0.1
                    value: root.pointerSpeed
                    onMoved: function(v) { root.setPointerSpeed(v) }
                    onReleased: function(v) {
                      root.setPointerSpeed(v)
                      pointerDebounce.stop()
                      root.commitPointerSpeed()
                    }
                  }

                  HoverHandler {
                    onHoveredChanged: if (hovered) {
                      root.cursorActive = true
                      root.focusSection = "pointer"
                    }
                  }
                }

                CursorSurface {
                  id: pPlusSurface
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(32)
                  height: Style.space(32)
                  hasCursor: false
                  foreground: root.bar.foreground
                  fill: root.hoverFill

                  Text {
                    id: pPlusBtn
                    anchors.centerIn: parent
                    text: "+"
                    color: root.bar.foreground
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.heading
                    opacity: root.pointerSpeed >= 1.0 ? 0.3 : 1.0
                  }

                  MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.adjustPointerSpeed(0.1)
                    onContainsMouseChanged: if (containsMouse) {
                      root.cursorActive = true
                      root.focusSection = "pointer"
                    }
                  }
                }
              }
            }
          }

          // ========== Toggle rows ==========
          SettingRow {
            sectionName: "acceleration"
            visible: root.activeTab === "pointer"
            width: parent.width
            height: Style.space(58)
            foreground: root.bar.foreground
            fill: root.hoverFill
            opacity: root.touchpadEnabled ? 1.0 : 0.4
            enabled: root.touchpadEnabled
            Column {
              anchors.left: parent.left
              anchors.leftMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(3)
              Text {
                text: "Pointer feel  ›"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.body
              }
              Text {
                width: parent.parent.width - Style.space(20)
                elide: Text.ElideRight
                objectName: "pointerFeelSummary"
                textFormat: Text.PlainText
                text: Curve.label(root.pointerFeel) + (root.pointerFeel.profile !== "imported" ? " · Presets and acceleration curve"
                  : root.pointerMissingInterfaces.length ? " · Incomplete import, re-apply"
                  : root.pointerDrift ? " · Display scale changed, re-apply" : "")
                color: Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onContainsMouseChanged: if (containsMouse) { root.cursorActive = true; root.focusSection = "acceleration" }
              onClicked: root.openCurveEditor()
            }
          }

          ToggleRow {
            width: parent.width
            label: "Progressive Scrolling"
            description: "Slow swipes stay precise; faster flicks cover more distance, like macOS"
            checked: root.scrollProgressive
            sectionName: "progressive"
            visible: root.activeTab === "scrolling"
            enabled: root.touchpadEnabled
            onToggled: root.toggleProgressiveScroll()
          }

          SettingRow {
            sectionName: "scroll-accel"
            visible: root.activeTab === "scrolling" && root.scrollProgressive
            width: parent.width
            height: Style.space(58)
            foreground: root.bar.foreground
            fill: root.hoverFill
            opacity: root.touchpadEnabled ? 1.0 : 0.4
            enabled: root.touchpadEnabled
            Column {
              anchors.left: parent.left
              anchors.leftMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(3)
              Text {
                text: "Scroll acceleration  ›"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.body
              }
              Text {
                text: ({ mac: "Mac-inspired", custom: "Custom" })[root.scrollFeel.profile] + " · Slow vs flick response"
                color: Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onContainsMouseChanged: if (containsMouse) { root.cursorActive = true; root.focusSection = "scroll-accel" }
              onClicked: root.openScrollEditor()
            }
          }

          ToggleRow {
            width: parent.width
            label: "Natural Scrolling"
            description: "Scroll content in the direction of finger movement"
            checked: root.naturalScroll
            sectionName: "natural"
            visible: root.activeTab === "scrolling"
            enabled: root.touchpadEnabled
            onToggled: root.toggleNaturalScroll()
          }

          ToggleRow {
            width: parent.width
            label: "Tap to Click"
            description: "Tap the touchpad to click"
            checked: root.tapToClick
            sectionName: "tap"
            visible: root.activeTab === "pointer"
            enabled: root.touchpadEnabled
            onToggled: root.toggleTapToClick()
          }

          ToggleRow {
            width: parent.width
            label: "Disable While Typing"
            description: "Ignore touchpad input while typing"
            checked: root.disableWhileTyping
            sectionName: "typing"
            visible: root.activeTab === "pointer"
            enabled: root.touchpadEnabled
            onToggled: root.toggleDisableWhileTyping()
          }

          ToggleRow {
            width: parent.width
            label: "Two-Finger Right Click"
            description: "Press with two fingers to right-click"
            checked: root.clickfingerBehavior
            sectionName: "clickfinger"
            visible: root.activeTab === "pointer"
            enabled: root.touchpadEnabled
            onToggled: root.toggleClickfingerBehavior()
          }

          PalmSettings {
            id: palmEditor
            loading: palmProc.running && !busy
            width: parent.width - Style.space(20)
            x: Style.space(10)
            visible: root.activeTab === "pointer" && root.selectedDevice === "apple" && settings.supported === true
            foreground: root.bar.foreground
            accent: Color.accent
            fontFamily: root.bar.fontFamily
            bodySize: Style.font.body
            captionSize: Style.font.caption
            unit: Style.space(1)
            onApplyRequested: function(threshold) { root.applyPalm(threshold) }
            onEditingFinished: keyCatcher.forceActiveFocus()
          }
        }

        GestureEditor {
          id: gestureEditor
          visible: root.activeTab === "gestures"
          width: parent.width
          foreground: root.bar.foreground
          accent: Color.accent
          fontFamily: root.bar.fontFamily
          uiScale: Style.space(100) / 100
          busy: gestureProc.running
          canEdit: root.gestureCanEdit
          canRestore: root.gestureCanRestore
          hymission: root.gestureHymission
          companion: root.gestureCompanion
          previewBusy: overviewProc.running
          previewText: root.overviewPreviewText
          onPreviewRequested: root.requestOverview(true)
          onOverviewStatusRequested: root.requestOverview(false)
          onHymissionInfoRequested: Qt.openUrlExternally("https://github.com/gfhdhytghd/hymission#installation")
          statusText: root.gestureStatus
          errorText: root.gestureError
          onApplyRequested: function(settings) { root.runGestureAction("set", settings) }
          onRestoreRequested: root.runGestureAction("restore")
          onRefreshRequested: root.refreshGestures()
          onBackRequested: { root.changeTab("pointer"); keyCatcher.forceActiveFocus() }
        }

        Text {
          width: parent.width
          visible: root.releaseVersion !== ""
          text: "Version " + root.releaseVersion
          horizontalAlignment: Text.AlignHCenter
          color: Qt.alpha(root.bar.foreground, 0.6)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
      }
    }
  }

  component SettingRow: CursorSurface {
    id: settingRow
    required property string sectionName
    foreground: root.bar.foreground
    fill: root.hoverFill
    radius: 0
    hasCursor: root.cursorActive && root.focusSection === sectionName
    z: hasCursor ? 1 : 0

    Rectangle {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      height: 1
      color: Qt.alpha(settingRow.foreground, 0.12)
      visible: !settingRow.hasCursor
    }
    Rectangle {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      height: 1
      color: Qt.alpha(settingRow.foreground, 0.12)
      visible: !settingRow.hasCursor
    }
    HoverHandler {
      onHoveredChanged: if (hovered) {
        root.cursorActive = true
        root.focusSection = settingRow.sectionName
      }
    }
  }

  // ========== Reusable toggle row component ==========
  component ToggleRow: SettingRow {
    id: toggleRow
    required property string label
    required property string description
    required property bool checked
    signal toggled()

    hasCursor: root.cursorActive && root.focusSection === sectionName
    foreground: root.bar.foreground
    fill: root.hoverFill

    implicitHeight: Math.max(Style.space(58), rowContent.implicitHeight + Style.space(24))
    opacity: root.touchpadEnabled ? 1.0 : 0.4

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) {
        root.cursorActive = true
        root.focusSection = toggleRow.sectionName
      }
      onClicked: if (toggleRow.enabled) toggleRow.toggled()
    }

    Item {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      implicitHeight: Math.max(rowLabels.implicitHeight, rowSwitch.implicitHeight)

      Column {
        id: rowLabels
        anchors.left: parent.left
        anchors.right: rowSwitch.left
        anchors.rightMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(1)

        Text {
          text: toggleRow.label
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
          width: parent.width
        }

        Text {
          visible: toggleRow.description !== ""
          text: toggleRow.description
          color: Qt.darker(root.bar.foreground, 1.5)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
          width: parent.width
          wrapMode: Text.WordWrap
        }
      }

      ToggleSwitch {
        id: rowSwitch
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        checked: toggleRow.checked
        foreground: root.bar.foreground
        onToggled: if (toggleRow.enabled) toggleRow.toggled()
      }
    }
  }
}
