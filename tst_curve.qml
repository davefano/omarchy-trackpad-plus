import QtQuick
import QtTest
import "Curve.js" as Curve

Rectangle {
  width: 470
  height: 760
  color: "#171b22"
  CurveEditor {
    id: editor
    x: 20
    y: 20
    width: 430
    foreground: "#e7edf4"
    accent: "#82b8b0"
    fontFamily: "sans-serif"
    onApplyRequested: function(value) { saved = Curve.copy(value) }
  }
  SignalSpy { id: applied; target: editor; signalName: "applyRequested" }
  SignalSpy { id: restored; target: editor; signalName: "restoreRequested" }
  SignalSpy { id: back; target: editor; signalName: "backRequested" }
  TestCase {
    name: "CurveEditor"
    when: windowShown
    function init() {
      editor.kind = "pointer"
      editor.saved = {profile: "adaptive", curve: Curve.defaults()}
      editor.gainMaximum = 3.5
      editor.busy = false
      editor.settingsError = ""
      editor.canRestore = false
      editor.profiles = []
      editor.profilesStatus = ""
      editor.drift = false
      editor.missingInterfaces = []
      editor.begin()
      applied.clear(); restored.clear(); back.clear()
    }
    function test_device_scale_controls_chart_and_gain_inputs() {
      editor.saved = {profile: "custom", curve: {precision: 0.01, start: 0, end: 4, fast: 0.55}}
      editor.begin()
      var original = JSON.stringify(editor.draft)
      var plot = findChild(editor, "curvePlot")
      editor.gainMaximum = 1
      compare(plot.py(1), plot.topInset)
      compare(findChild(editor, "curveSpinner3").to, 10000)
      editor.gainMaximum = 3
      compare(plot.py(3), plot.topInset)
      compare(findChild(editor, "curveSpinner3").to, 30000)
      compare(JSON.stringify(editor.draft), original)
      compare(applied.count, 0)
      editor.adjust(3, 9)
      compare(editor.draft.curve.fast, 3)
      editor.gainMaximum = 1
      verify(editor.curveExceedsRange)
      compare(editor.draft.curve.fast, 3)
    }
    function test_scroll_preset_and_controls_ignore_pointer_scale() {
      editor.kind = "scroll"
      editor.saved = {profile: "mac", curve: Curve.scrollDefaults()}
      editor.begin()
      for (var i = 0; i < 3; i++) {
        editor.gainMaximum = [0.1, 1, 3][i]
        editor.choose("mac")
        compare(editor.draft.curve.precision, 1)
        compare(editor.draft.curve.fast, 2)
        compare(findChild(editor, "curveSpinner3").to, 100000)
        var plot = findChild(editor, "curvePlot")
        compare(plot.py(10), plot.topInset)
        editor.adjust(3, 12)
        compare(editor.draft.curve.fast, 10)
      }
      compare(applied.count, 0)
    }
    function test_mac_inspired_stays_optional_and_uses_pointer_scale() {
      compare(editor.draft.profile, "adaptive")
      compare(applied.count, 0)
      editor.gainMaximum = 1
      mouseClick(findChild(editor, "profileChoice-mac"))
      compare(editor.draft.profile, "mac")
      compare(editor.draft.curve.fast, 1)
      verify(findChild(editor, "curvePlot").visible)
      compare(applied.count, 0, "selecting a preset changes only the draft")
      mouseClick(findChild(editor, "applyCurve"))
      compare(applied.count, 1)
      compare(applied.signalArguments[0][0].profile, "mac")
      editor.profiles = profileRows()
      editor.choose("imported")
      compare(editor.draft.profile, "imported")
      verify(!findChild(editor, "curvePlot").visible)
    }
    function test_authoritative_read_pending_blocks_profile_actions() {
      editor.profiles = profileRows()
      editor.saved = {profile: "imported", curve: Curve.defaults(), imported: converted()}
      editor.begin()
      editor.setTrackingSpeed(1.5)
      editor.canRestore = true
      editor.drift = true
      editor.busy = true
      editor.settingsError = "Could not read trackpad settings"
      verify(!findChild(editor, "applyCurve").enabled)
      verify(!findChild(editor, "restoreCurve").enabled)
      verify(!findChild(editor, "reapplyProfile").enabled)
      verify(findChild(editor, "curveStatus").text.indexOf("Could not read") >= 0)
      compare(applied.count, 0)
      editor.busy = false
      editor.settingsError = ""
      verify(findChild(editor, "applyCurve").enabled)
      verify(findChild(editor, "restoreCurve").enabled)
    }
    function test_failed_save_is_not_labelled_applied() {
      editor.settingsError = "Compositor unavailable"
      var status = findChild(editor, "curveStatus")
      verify(status.text.indexOf("Compositor unavailable") >= 0)
      verify(status.text.indexOf("Applied") < 0)
    }
    function test_preview_apply_and_restore() {
      editor.choose("flat")
      verify(editor.dirty)
      compare(editor.saved.profile, "adaptive")
      compare(applied.count, 0)
      var apply = findChild(editor, "applyCurve")
      mouseClick(apply)
      compare(applied.count, 1)
      compare(editor.saved.profile, "flat")
      verify(!editor.dirty)
      editor.canRestore = true
      mouseClick(findChild(editor, "restoreCurve"))
      compare(restored.count, 1)
    }
    function test_keyboard_and_pointer_handles() {
      editor.choose("custom")
      wait(50)
      var handle = findChild(editor, "curveHandle0")
      handle.forceActiveFocus()
      keyClick(Qt.Key_Up)
      verify(editor.draft.curve.precision > 0.3)
      var start = findChild(editor, "curveHandle1")
      start.forceActiveFocus()
      keyClick(Qt.Key_Right)
      compare(editor.draft.curve.start, 0.85)
      compare(editor.draft.curve.end, 2.8)
      var end = findChild(editor, "curveHandle2")
      end.forceActiveFocus()
      keyClick(Qt.Key_Left)
      compare(editor.draft.curve.end, 2.75)
      compare(editor.draft.curve.start, 0.85)
      var fast = findChild(editor, "curveHandle3")
      var before = editor.draft.curve.fast
      mouseDrag(fast, fast.width / 2, fast.height / 2, 0, -30)
      verify(editor.draft.curve.fast > before)
      compare(applied.count, 0, "Dragging must not change live acceleration")
      keyClick(Qt.Key_Escape)
      compare(back.count, 1)
    }
    function test_practice_and_busy_state() {
      editor.choose("flat")
      editor.busy = true
      verify(!findChild(editor, "applyCurve").enabled)
      mouseClick(findChild(editor, "practiceTarget"))
      compare(editor.hits, 1)
    }
    function enterNumber(index, text) {
      var field = findChild(editor, "curveNumber" + index)
      mouseClick(field)
      keyClick(Qt.Key_A, Qt.ControlModifier)
      for (var i = 0; i < text.length; i++) keyClick(text.charAt(i))

    }
    function test_spinners_type_arrow_and_apply() {
      editor.choose("custom")
      wait(50)
      enterNumber(0, "0.0648")
      keyClick(Qt.Key_Return)
      compare(editor.draft.curve.precision, 0.0648)
      keyClick(Qt.Key_Up)
      compare(editor.draft.curve.precision, 0.0658)
      keyClick(Qt.Key_Down)
      compare(editor.draft.curve.precision, 0.0648)
      enterNumber(1, "21.00")
      keyClick(Qt.Key_Return)
      compare(editor.draft.curve.start, 0.84)
      keyClick(Qt.Key_Up)
      compare(editor.draft.curve.start, 0.88)
      enterNumber(2, "75.50")
      keyClick(Qt.Key_Return)
      compare(editor.draft.curve.end, 3.02)
      enterNumber(3, "0.5265")
      compare(applied.count, 0)
      mouseClick(findChild(editor, "applyCurve"))
      compare(applied.count, 1)
      compare(editor.saved.curve.fast, 0.5265)
      compare(editor.saved.curve.precision, 0.0648)
    }
    function test_spinners_shift_steps() {
      editor.choose("custom")
      wait(50)
      enterNumber(0, "0.0648")
      keyClick(Qt.Key_Up, Qt.ShiftModifier)
      compare(editor.draft.curve.precision, 0.0748)
      keyClick(Qt.Key_Down, Qt.ShiftModifier)
      compare(editor.draft.curve.precision, 0.0648)
      keyClick(Qt.Key_Up)
      compare(editor.draft.curve.precision, 0.0658)
      enterNumber(1, "21.00")
      keyClick(Qt.Key_Return)
      keyClick(Qt.Key_Up, Qt.ShiftModifier)
      compare(editor.draft.curve.start, 1.24)
      keyClick(Qt.Key_Down, Qt.ShiftModifier)
      compare(editor.draft.curve.start, 0.84)
      enterNumber(0, "0.0150")
      keyClick(Qt.Key_Down, Qt.ShiftModifier)
      compare(editor.draft.curve.precision, 0.01)
      enterNumber(1, "65.00")
      keyClick(Qt.Key_Up, Qt.ShiftModifier)
      compare(editor.draft.curve.start, 2.6)
      compare(applied.count, 0)
      mouseClick(findChild(editor, "applyCurve"))
      compare(editor.saved.curve.start, 2.6)
      compare(editor.saved.curve.precision, 0.01)
    }
    function test_spinners_follow_graph_and_enforce_bounds() {
      editor.choose("custom")
      editor.adjust(0, 0.12)
      compare(findChild(editor, "curveSpinner0").value, 1200)
      wait(50)
      enterNumber(0, "0.0100")
      keyClick(Qt.Key_Return)
      keyClick(Qt.Key_Down)
      compare(editor.draft.curve.precision, 0.01)
      enterNumber(1, "65.00")
      keyClick(Qt.Key_Return)
      keyClick(Qt.Key_Up)
      compare(editor.draft.curve.start, 2.6)
      verify(editor.draft.curve.end - editor.draft.curve.start >= 0.2 - 1e-9)
      editor.adjust(1, 0.812, true)
      editor.adjust(2, 0)
      verify(editor.draft.curve.end - editor.draft.curve.start >= 0.2 - 1e-9)
      mouseClick(findChild(editor, "curveSpinner0").up.indicator)
      compare(editor.draft.curve.precision, 0.011)
      editor.adjust(0, 0)
      compare(editor.draft.curve.precision, 0.01)
      enterNumber(3, "0.0100")
      keyClick(Qt.Key_Return)
      keyClick(Qt.Key_Down)
      compare(editor.draft.curve.fast, 0.01)
      mouseClick(findChild(editor, "applyCurve"))
      compare(editor.saved.curve.precision, 0.01)
      compare(editor.saved.curve.fast, 0.01)
    }
    function test_transition_end_reaches_100_percent() {
      editor.choose("custom")
      wait(50)
      enterNumber(2, "99.00")
      keyClick(Qt.Key_Return)
      compare(editor.draft.curve.end, 3.96)
      keyClick(Qt.Key_Up)
      compare(editor.draft.curve.end, 4)
      keyClick(Qt.Key_Up, Qt.ShiftModifier)
      compare(editor.draft.curve.end, 4)
      mouseClick(findChild(editor, "applyCurve"))
      compare(editor.saved.curve.end, 4)
      editor.begin()
      compare(findChild(editor, "curveSpinner2").value, 10000)
      var end = findChild(editor, "curveHandle2")
      var fast = findChild(editor, "curveHandle3")
      verify(Math.abs(end.y - fast.y) >= 30, "End and fast-swipe hit areas must remain separate")
      mouseDrag(end, end.width / 2, end.height / 2, -40, 0)
      verify(editor.draft.curve.end < 4)
      compare(editor.draft.curve.fast, 1.6)
      editor.adjust(2, 4)
      var before = editor.draft.curve.fast
      mouseDrag(fast, fast.width / 2, fast.height / 2, 0, -20)
      verify(editor.draft.curve.fast > before)
      compare(editor.draft.curve.end, 4)
    }
    function profileRows() {
      return [
        {file: "mac.json", name: "MacBook Pro (M1 Pro)", sha256: "a".repeat(64), tracking_speed: 0.875,
         speeds: [0, 0.125, 0.5, 0.6875, 0.875, 1, 1.5, 2, 2.5, 3]},
        {file: "broken.json", error: "Unsupported pointer profile format"}
      ]
    }
    function test_profile_names_render_markup_as_literal_text() {
      var rows = profileRows()
      var name = '<img src="http://127.0.0.1:1/profile.png">'
      rows[0].name = name
      editor.profiles = rows
      editor.choose("imported")
      wait(20)
      var row = findChild(editor, "profileRow0")
      verify(row !== null)
      compare(row.contentItem.textFormat, Text.PlainText)
      compare(row.contentItem.text, name)
      verify(row.contentItem.implicitWidth > 100, "the tag is rendered as text rather than an image")
    }
    function test_incomplete_import_warns_and_reapplies_existing_profile() {
      editor.profiles = profileRows()
      editor.saved = {profile: "imported", curve: Curve.defaults(), imported: converted()}
      editor.missingInterfaces = ["apple-inc.-magic-trackpad-2"]
      editor.begin()
      wait(20)
      var warning = findChild(editor, "importedIncomplete")
      verify(warning.visible)
      verify(warning.text.indexOf("apple-inc.-magic-trackpad-2") >= 0)
      verify(warning.text.indexOf("native tracking") >= 0)
      compare(warning.textFormat, Text.PlainText)
      var reapply = findChild(editor, "reapplyProfile")
      verify(reapply.visible)
      compare(reapply.text, "Re-apply for all interfaces")
      mouseClick(reapply)
      compare(applied.count, 1)
      verify(!applied.signalArguments[0][0].imported.devices)
      editor.profiles = []
      verify(!reapply.visible)
      verify(warning.text.indexOf("Restore the source profile file") >= 0)
    }
    function converted() {
      return {name: "MacBook Pro (M1 Pro)", file: "mac.json", sha256: "a".repeat(64), tracking_speed: 0.875,
        mm_per_point: 0.2, px_per_point: 1,
        devices: {"apple-spi-trackpad": {units_per_mm: 98.65, step: 1, points: [0, 1, 2, 3, 4, 5]}}}
    }
    function test_macos_profile_shows_only_the_model_and_tracking_speed() {
      editor.profiles = profileRows()
      editor.choose("imported")
      verify(editor.dirty)
      compare(editor.draft.imported.file, "mac.json")
      compare(editor.draft.imported.tracking_speed, 0.875, "starts at the Mac's own setting")
      verify(!findChild(editor, "curvePlot").visible, "an imported curve has no chart, handles or spinners")
      compare(findChild(editor, "profileRow0").text, "MacBook Pro (M1 Pro)")
      verify(findChild(editor, "profileRow0").selected)
      verify(!findChild(editor, "profileRow1").enabled, "invalid files are listed but cannot be chosen")
      var slider = findChild(editor, "trackingSpeed")
      verify(slider.visible)
      compare(slider.value, 4)
      mouseClick(findChild(editor, "applyCurve"))
      compare(applied.count, 1)
      compare(applied.signalArguments[0][0].imported.sha256, "a".repeat(64))
      compare(applied.signalArguments[0][0].imported.tracking_speed, 0.875)
    }
    function test_tracking_speed_moves_between_apples_notches() {
      editor.profiles = profileRows()
      editor.saved = {profile: "imported", curve: Curve.defaults(), imported: converted()}
      editor.begin()
      verify(!editor.dirty)
      wait(50)
      var slider = findChild(editor, "trackingSpeed")
      slider.forceActiveFocus()
      keyClick(Qt.Key_Right)
      compare(editor.draft.imported.tracking_speed, 1)
      verify(editor.dirty)
      keyClick(Qt.Key_Right)
      compare(editor.draft.imported.tracking_speed, 1.5)
      keyClick(Qt.Key_Left); keyClick(Qt.Key_Left)
      compare(editor.draft.imported.tracking_speed, 0.875)
      verify(!editor.dirty, "returning to the applied speed leaves nothing to apply")
      verify(!!editor.draft.imported.devices)
      keyClick(Qt.Key_Left)
      mouseClick(findChild(editor, "applyCurve"))
      compare(applied.signalArguments[0][0].imported.tracking_speed, 0.6875)
      verify(!applied.signalArguments[0][0].imported.devices)
    }
    function test_macos_profile_empty_state() {
      editor.choose("imported")
      verify(findChild(editor, "profilesEmpty").visible)
      verify(!findChild(editor, "trackingSpeed").visible)
      verify(!findChild(editor, "applyCurve").enabled, "nothing to apply without a profile")
      editor.profilesStatus = "Looking for macOS profiles…"
      verify(!findChild(editor, "profilesEmpty").visible)
    }
    function test_saved_macos_profile_is_clean_and_reapplies_after_drift() {
      editor.profiles = profileRows()
      var saved = converted()
      saved.tracking_speed = 1.5
      editor.saved = {profile: "imported", curve: Curve.defaults(), imported: saved}
      editor.begin()
      verify(!editor.dirty)
      compare(findChild(editor, "trackingSpeed").value, 6)
      mouseClick(findChild(editor, "profileRow0"))
      verify(!editor.dirty, "choosing the applied file keeps its saved curve")
      verify(!findChild(editor, "reapplyProfile").visible)
      editor.drift = true
      verify(findChild(editor, "importedDrift").visible)
      wait(50)
      mouseClick(findChild(editor, "reapplyProfile"))
      compare(applied.count, 1)
      verify(!applied.signalArguments[0][0].imported.devices, "re-apply converts the file again")
      compare(applied.signalArguments[0][0].imported.tracking_speed, 1.5, "at the applied speed")
      editor.saved = {profile: "imported", curve: Curve.defaults(), imported: converted()}
      editor.profiles = []
      editor.begin()
      verify(!findChild(editor, "reapplyProfile").visible, "a missing file cannot be re-applied")
      verify(findChild(editor, "importedDrift").text.indexOf("gone") >= 0)
    }
    function test_profile_choices_fit_with_custom_last() {
      var right = 0
      for (var id of ["adaptive", "mac", "flat", "imported", "custom"]) {
        var choice = findChild(editor, "profileChoice-" + id)
        verify(choice.width > 60)
        var left = choice.mapToItem(editor, 0, 0).x
        verify(left >= right, id + " follows the previous choice")
        right = choice.mapToItem(editor, choice.width, 0).x
      }
      verify(right <= editor.width + 0.5)
    }
    function test_visual_layout() {
      editor.choose("custom")
      wait(100)
      verify(editor.implicitHeight < 740)
      var picture = grabImage(editor)
      verify(picture.width > 0)
      editor.profiles = profileRows()
      editor.choose("imported")
      wait(100)
      verify(editor.implicitHeight < 740)
    }
  }
}
