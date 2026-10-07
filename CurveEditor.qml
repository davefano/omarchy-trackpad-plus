pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "Curve.js" as Curve

// A self-contained editor: only Apply changes the live pointer response.
FocusScope {
  id: editor
  required property color foreground
  required property color accent
  required property string fontFamily
  property real uiScale: 1
  property real gainMaximum: 1
  readonly property real curveMaximum: scrollMode ? 10 : gainMaximum
  readonly property bool curveExceedsRange: draft.curve.fast > curveMaximum
  property var saved: ({ profile: "adaptive", curve: Curve.defaults() })
  property var draft: Curve.copy(saved)
  property string deviceLabel: "Trackpad"
  property string kind: "pointer"
  readonly property bool scrollMode: kind === "scroll"
  property bool busy: false
  property string settingsError: ""
  property bool canRestore: false
  property bool numberPending: false
  property int hits: 0
  property int targetIndex: 0
  // macOS profiles: rows from `trackpads.py profiles` for this trackpad.
  property var profiles: []
  property string profilesDirectory: ""
  property string profilesStatus: ""
  property bool drift: false
  property var missingInterfaces: []
  readonly property bool custom: draft.profile === "custom"
  readonly property bool imported: draft.profile === "imported"
  readonly property var importedRow: imported && draft.imported ? rowFor(draft.imported) : null
  // Apple's tracking speed notches, as stored in the profile (the macOS slider's stops).
  readonly property var speeds: importedRow && importedRow.speeds ? importedRow.speeds : []
  readonly property bool canApply: (dirty || numberPending) && !busy && (!imported || !!draft.imported)
  readonly property bool dirty: !Curve.same(draft, saved)
  signal applyRequested(var value)
  signal restoreRequested()
  signal backRequested()
  implicitHeight: contents.implicitHeight

  function begin() {
    forceActiveFocus()
    backButton.forceActiveFocus()
    draft = Curve.copy(saved)
    numberPending = false
    hits = 0
  }
  function choose(profile) {
    if (profile === "imported") {
      if (saved.profile === "imported") { draft = Curve.copy(saved); return }
      var first = profiles.filter(function(row) { return !row.error })[0]
      draft = { profile: profile, curve: Curve.copy(draft.curve), imported: first ? reference(first) : null }
      return
    }
    var preset = editor.scrollMode ? Curve.scrollDefaults() : Curve.presetForScale(gainMaximum)
    draft = { profile: profile, curve: profile === "mac" ? preset : Curve.copy(draft.curve) }
  }
  function reference(row, speed) {
    return { file: row.file, sha256: row.sha256, name: row.name, tracking_speed: speed === undefined ? row.tracking_speed : speed }
  }
  function rowFor(imported) {
    for (var i = 0; i < profiles.length; i++)
      if (profiles[i].file === imported.file && profiles[i].sha256 === imported.sha256) return profiles[i]
    return null
  }
  // Choosing the applied file keeps its saved curve; Re-apply converts it for a new display.
  function chooseFile(row) {
    if (row.error) return
    var current = saved.profile === "imported" && saved.imported && saved.imported.file === row.file
      && saved.imported.sha256 === row.sha256
    draft = current ? Curve.copy(saved) : { profile: "imported", curve: Curve.copy(draft.curve), imported: reference(row) }
  }
  // Returning to the applied speed restores the saved curve, so nothing is left to apply.
  function setTrackingSpeed(speed) {
    if (!importedRow) return
    var applied = saved.profile === "imported" && Curve.same(saved, { profile: "imported", curve: saved.curve,
      imported: reference(importedRow, speed) })
    draft = applied ? Curve.copy(saved) : { profile: "imported", curve: Curve.copy(draft.curve), imported: reference(importedRow, speed) }
  }
  function reapply() {
    var row = saved.imported ? rowFor(saved.imported) : null
    if (!row || busy) return
    draft = { profile: "imported", curve: Curve.copy(saved.curve), imported: reference(row, saved.imported.tracking_speed) }
    applyRequested(Curve.copy(draft))
  }
  function adjust(handle, value, precise) {
    draft = { profile: "custom", curve: Curve.adjust(draft.curve, handle, value, precise, curveMaximum) }
  }
  onDraftChanged: graph.requestPaint()
  onCurveMaximumChanged: graph.requestPaint()
  onForegroundChanged: graph.requestPaint()
  onAccentChanged: graph.requestPaint()
  Keys.onEscapePressed: backRequested()

  component Label: Text {
    textFormat: Text.PlainText
    color: editor.foreground
    font.family: editor.fontFamily
    font.pixelSize: 13 * editor.uiScale
    wrapMode: Text.WordWrap
  }
  component Action: Button {
    id: control
    property bool selected: false
    implicitHeight: 34 * editor.uiScale
    font.family: editor.fontFamily
    font.pixelSize: 12 * editor.uiScale
    padding: 8 * editor.uiScale
    contentItem: Text {
      text: control.text
      textFormat: Text.PlainText
      font: control.font
      color: editor.foreground
      horizontalAlignment: Text.AlignHCenter
      verticalAlignment: Text.AlignVCenter
      elide: Text.ElideRight
      opacity: control.enabled ? 1 : 0.4
    }
    background: Rectangle {
      radius: 5 * editor.uiScale
      color: control.selected || control.hovered ? Qt.alpha(editor.accent, 0.18) : Qt.alpha(editor.foreground, 0.04)
      border.width: control.activeFocus || control.selected ? 2 : 1
      border.color: control.activeFocus || control.selected ? editor.accent : Qt.alpha(editor.foreground, 0.2)
      opacity: control.enabled ? 1 : 0.4
    }
  }

  component NumberSpinner: SpinBox {
    id: numberControl
    required property int controlIndex
    readonly property bool percentage: controlIndex === 1 || controlIndex === 2
    readonly property int decimals: percentage ? 2 : 4
    readonly property int factor: percentage ? 100 : 10000
    readonly property real units: percentage ? 25 : 1
    readonly property real curveValue: controlIndex === 0 ? editor.draft.curve.precision
      : controlIndex === 1 ? editor.draft.curve.start
      : controlIndex === 2 ? editor.draft.curve.end : editor.draft.curve.fast
    readonly property real minimum: controlIndex === 0 ? 0.01 : controlIndex === 1 ? 0
      : controlIndex === 2 ? editor.draft.curve.start + 0.2 : editor.draft.curve.precision
    readonly property real maximum: controlIndex === 0 ? Math.max(editor.curveMaximum, editor.draft.curve.precision)
      : controlIndex === 1 ? editor.draft.curve.end - 0.2 : controlIndex === 2 ? 4 : Math.max(editor.curveMaximum, editor.draft.curve.fast)
    objectName: "curveSpinner" + controlIndex
    from: Math.ceil(minimum * units * factor - 0.000001)
    to: Math.floor(maximum * units * factor + 0.000001)
    value: Math.round(curveValue * units * factor)
    stepSize: percentage ? 100 : 10 // 1 percentage point or 0.001×.
    editable: true
    live: false
    wheelEnabled: false
    implicitHeight: 34 * editor.uiScale
    leftPadding: 6 * editor.uiScale
    rightPadding: 22 * editor.uiScale
    font.family: editor.fontFamily
    font.pixelSize: 12 * editor.uiScale
    Accessible.name: ["Precision multiplier", "Acceleration start percent", "Acceleration end percent", "Fast swipe multiplier"][controlIndex]
    textFromValue: function(value, locale) { return Number(value / factor).toLocaleString(locale, "f", decimals) }
    valueFromText: function(text, locale) { return Math.round(Number.fromLocaleString(locale, text) * factor) }
    validator: DoubleValidator {
      bottom: numberControl.from / numberControl.factor
      top: numberControl.to / numberControl.factor
      decimals: numberControl.decimals
      notation: DoubleValidator.StandardNotation
      locale: numberControl.locale.name
    }
    onValueModified: editor.adjust(controlIndex, value / (factor * units), true)
    function handleLargeStep(event) {
      if (!(event.modifiers & Qt.ShiftModifier) || (event.key !== Qt.Key_Up && event.key !== Qt.Key_Down)) return
      var current = numberInput.acceptableInput ? valueFromText(numberInput.text, locale) : value
      var next = Math.max(from, Math.min(to, current + (event.key === Qt.Key_Up ? 1 : -1) * stepSize * 10))
      editor.adjust(controlIndex, next / (factor * units), true)
      editor.numberPending = false
      event.accepted = true
    }
    Keys.onPressed: function(event) { handleLargeStep(event) }
    contentItem: TextInput {
      id: numberInput
      objectName: "curveNumber" + numberControl.controlIndex
      text: numberControl.textFromValue(numberControl.value, numberControl.locale)
      font: numberControl.font
      color: editor.foreground
      selectionColor: editor.accent
      selectedTextColor: "#161a20"
      verticalAlignment: TextInput.AlignVCenter
      selectByMouse: true
      clip: true
      readOnly: !numberControl.editable
      validator: numberControl.validator
      inputMethodHints: Qt.ImhFormattedNumbersOnly
      Keys.onPressed: function(event) { numberControl.handleLargeStep(event) }
      onTextEdited: editor.numberPending = true
      onEditingFinished: editor.numberPending = false
    }
    background: Rectangle {
      radius: 4 * editor.uiScale
      color: Qt.alpha(editor.foreground, 0.04)
      border.width: numberControl.activeFocus || numberControl.contentItem.activeFocus ? 2 : 1
      border.color: numberControl.activeFocus || numberControl.contentItem.activeFocus ? editor.accent : Qt.alpha(editor.foreground, 0.25)
    }
    up.indicator: Rectangle {
      x: numberControl.width - width
      width: 20 * editor.uiScale
      height: numberControl.height / 2
      color: numberControl.up.pressed || numberControl.up.hovered ? Qt.alpha(editor.accent, 0.25) : "transparent"
      Text { anchors.centerIn: parent; text: "▴"; color: editor.foreground; font.pixelSize: 11 * editor.uiScale; opacity: numberControl.up.indicator.enabled ? 1 : 0.3 }
    }
    down.indicator: Rectangle {
      x: numberControl.width - width
      y: numberControl.height / 2
      width: 20 * editor.uiScale
      height: numberControl.height / 2
      color: numberControl.down.pressed || numberControl.down.hovered ? Qt.alpha(editor.accent, 0.25) : "transparent"
      Text { anchors.centerIn: parent; text: "▾"; color: editor.foreground; font.pixelSize: 11 * editor.uiScale; opacity: numberControl.down.indicator.enabled ? 1 : 0.3 }
    }
  }

  Column {
    id: contents
    width: parent.width
    spacing: 12 * editor.uiScale

    Row {
      width: parent.width
      spacing: 12 * editor.uiScale
      Action { id: backButton; text: "‹ Back"; width: 70 * editor.uiScale; onClicked: editor.backRequested() }
      Column {
        width: parent.width - backButton.width - parent.spacing
        Label { text: editor.scrollMode ? "Scroll acceleration" : "Pointer feel"; font.pixelSize: 18 * editor.uiScale; font.bold: true }
        Label { text: editor.deviceLabel; opacity: 0.65; width: parent.width; elide: Text.ElideRight; wrapMode: Text.NoWrap }
      }
    }

    Row {
      width: parent.width
      spacing: 5 * editor.uiScale
      Repeater {
        model: editor.scrollMode
          ? [{ id: "mac", name: "Mac-inspired" }, { id: "custom", name: "Custom" }]
          : [{ id: "adaptive", name: "System" }, { id: "mac", name: "Mac-inspired" }, { id: "flat", name: "Flat" }, { id: "imported", name: "macOS" }, { id: "custom", name: "Custom" }]
        Action {
          required property var modelData
          objectName: "profileChoice-" + modelData.id
          width: editor.scrollMode
            ? (contents.width - 5 * editor.uiScale) * (modelData.id === "mac" ? 1.4 : 1) / 2.4
            : (contents.width - 20 * editor.uiScale) * (modelData.id === "mac" ? 1.4 : 1) / 5.4
          text: modelData.name
          selected: editor.draft.profile === modelData.id
          onClicked: editor.choose(modelData.id)
        }
      }
    }

    Label {
      visible: !editor.imported
      width: parent.width
      text: editor.scrollMode ? "Slow two-finger swipes stay precise; faster flicks cover more distance, like macOS."
        : editor.draft.profile === "mac" ? "An experimental starting curve inspired by Mac tracking; tune it to your hand."
        : editor.custom ? "A steady precision range for small corrections, then a smooth rise for faster swipes."
        : editor.draft.profile === "flat" ? "Constant response at every finger speed. Use Pointer Speed in the main panel to adjust it."
        : "Use libinput’s adaptive response and your existing Pointer Speed setting."
    }

    Column {
      width: parent.width
      spacing: 6 * editor.uiScale
      visible: editor.custom || editor.draft.profile === "mac"
      Label { text: editor.scrollMode ? "Scroll travel (×)" : "Cursor travel (×)"; opacity: 0.7; font.pixelSize: 11 * editor.uiScale }
      Item {
        id: plot
        objectName: "curvePlot"
        width: parent.width
        height: 190 * editor.uiScale
        readonly property real leftInset: 40 * editor.uiScale
        readonly property real rightInset: 12 * editor.uiScale
        readonly property real topInset: 12 * editor.uiScale
        readonly property real bottomInset: 22 * editor.uiScale
        readonly property real plotWidth: width - leftInset - rightInset
        readonly property real plotHeight: height - topInset - bottomInset
        function px(speed) { return leftInset + speed / 4 * plotWidth }
        function py(gain) { return topInset + (1 - Math.max(0, Math.min(1, gain / editor.curveMaximum))) * plotHeight }
        Canvas {
          id: graph
          anchors.fill: parent
          onWidthChanged: requestPaint()
          onHeightChanged: requestPaint()
          onPaint: {
            var samples = Curve.points(editor.draft.curve)
            var ctx = getContext("2d")
            ctx.reset()
            ctx.fillStyle = Qt.alpha(editor.accent, 0.09)
            ctx.fillRect(plot.px(0), plot.topInset, plot.px(editor.draft.curve.start) - plot.px(0), plot.plotHeight)
            ctx.lineWidth = 1
            ctx.font = (10 * editor.uiScale) + "px sans-serif"
            ctx.textAlign = "right"
            var divisions = editor.curveMaximum === 3 ? 3 : 4
            for (var tick = 0; tick <= divisions; tick++) {
              var n = editor.curveMaximum * tick / divisions
              ctx.strokeStyle = Qt.alpha(editor.foreground, n === 1 ? 0.3 : 0.12)
              ctx.beginPath(); ctx.moveTo(plot.px(0), plot.py(n)); ctx.lineTo(plot.px(4), plot.py(n)); ctx.stroke()
              ctx.fillStyle = Qt.alpha(editor.foreground, 0.65)
              ctx.fillText(Number(n.toFixed(2)) + "×", plot.leftInset - 7 * editor.uiScale, plot.py(n) + 4 * editor.uiScale)
            }
            ctx.strokeStyle = editor.accent
            ctx.lineWidth = 2.5 * editor.uiScale
            ctx.beginPath()
            for (var i = 0; i <= 160; i++) {
              var speed = i / 40
              var x = plot.px(speed), y = plot.py(Curve.sampledGain(editor.draft.curve, speed, samples))
              if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
            }
            ctx.stroke()
            ctx.fillStyle = Qt.alpha(editor.foreground, 0.65)
            ctx.textAlign = "left"; ctx.fillText("Slow", plot.px(0), height - 3 * editor.uiScale)
            ctx.textAlign = "right"; ctx.fillText("Fast", plot.px(4), height - 3 * editor.uiScale)
          }
        }
        Repeater {
          model: 4
          Rectangle {
            id: handle
            objectName: "curveHandle" + index
            required property int index
            readonly property bool horizontal: index === 1 || index === 2
            readonly property real speed: index === 0 ? 0 : index === 1 ? editor.draft.curve.start : index === 2 ? editor.draft.curve.end : 4
            readonly property real gainValue: index < 2 ? editor.draft.curve.precision : editor.draft.curve.fast
            // Keep the end and fast-swipe handles separately clickable at 100%.
            readonly property real endOffset: index === 2 && plot.px(4) - plot.px(speed) < 32 * editor.uiScale
              ? (plot.py(gainValue) < plot.topInset + 32 * editor.uiScale ? 32 : -32) * editor.uiScale : 0
            x: plot.px(speed) - width / 2
            y: plot.py(gainValue) + endOffset - height / 2
            width: 16 * editor.uiScale
            height: width
            radius: horizontal ? 3 * editor.uiScale : width / 2
            color: editor.accent
            border.width: activeFocus ? 3 : 1
            border.color: editor.foreground
            activeFocusOnTab: true
            Accessible.role: Accessible.Slider
            Accessible.name: ["Precision speed", "Acceleration start", "Acceleration end", "Fast swipe travel"][index]
            Accessible.description: "Use arrow keys to adjust"
            Rectangle {
              visible: handle.endOffset !== 0
              x: (parent.width - width) / 2
              y: parent.height / 2 - Math.max(0, handle.endOffset)
              width: editor.uiScale
              height: Math.abs(handle.endOffset)
              color: Qt.alpha(editor.accent, 0.5)
              z: -1
            }
            Keys.onPressed: function(event) {
              var direction = event.key === Qt.Key_Right || event.key === Qt.Key_Up ? 1 : event.key === Qt.Key_Left || event.key === Qt.Key_Down ? -1 : 0
              if (!direction) return
              editor.adjust(index, (horizontal ? speed : gainValue) + direction * 0.05)
              event.accepted = true
            }
            MouseArea {
              anchors.fill: parent
              anchors.margins: -7 * editor.uiScale
              cursorShape: handle.horizontal ? Qt.SizeHorCursor : Qt.SizeVerCursor
              preventStealing: true
              onPressed: handle.forceActiveFocus()
              onPositionChanged: function(mouse) {
                if (!pressed) return
                var p = mapToItem(plot, mouse.x, mouse.y)
                editor.adjust(handle.index, handle.horizontal ? (p.x - plot.leftInset) / plot.plotWidth * 4 : (1 - (p.y - plot.topInset) / plot.plotHeight) * editor.curveMaximum)
              }
            }
          }
        }
      }
      Label { text: "Finger speed →"; anchors.horizontalCenter: parent.horizontalCenter; opacity: 0.65; font.pixelSize: 11 * editor.uiScale }
      Label {
        visible: editor.curveExceedsRange
        width: parent.width
        text: editor.scrollMode ? "This saved curve exceeds the 10× chart range; its values have been preserved."
          : "This saved curve exceeds the chart range. Increase Device scale to see it fully; its values have been preserved."
        font.pixelSize: 11 * editor.uiScale
        opacity: 0.7
      }
      Row {
        width: parent.width
        spacing: 6 * editor.uiScale
        Repeater {
          model: ["Precision ×", "Start %", "End %", "Fast swipes ×"]
          Column {
            required property string modelData
            required property int index
            width: (contents.width - 18 * editor.uiScale) / 4
            spacing: 5 * editor.uiScale
            Label { text: parent.modelData; width: parent.width; opacity: 0.65; font.pixelSize: 11 * editor.uiScale }
            NumberSpinner {
              width: parent.width
              controlIndex: parent.index
            }
          }
        }
      }
    }

    Column {
      objectName: "importedSection"
      width: parent.width
      spacing: 8 * editor.uiScale
      visible: editor.imported
      Label {
        objectName: "importedIncomplete"
        visible: editor.saved.profile === "imported" && editor.missingInterfaces.length > 0
        width: parent.width
        text: "No converted curve for: " + editor.missingInterfaces.join(", ")
          + ". These interfaces keep native tracking and scrolling; progressive scrolling waits for Re-apply. "
          + "Provide their sensor resolution (see the profile setup instructions), then reapply the profile."
          + (!editor.saved.imported || !editor.rowFor(editor.saved.imported) ? " Restore the source profile file first." : "")
        font.pixelSize: 11 * editor.uiScale
      }
      Label {
        visible: editor.drift && editor.saved.profile === "imported"
        width: parent.width
        objectName: "importedDrift"
        text: editor.saved.imported && editor.rowFor(editor.saved.imported)
          ? "Display scale changed since this was applied."
          : "Display scale changed since this was applied, and its profile file has changed or is gone."
        font.pixelSize: 11 * editor.uiScale
      }
      Action {
        objectName: "reapplyProfile"
        visible: (editor.drift || editor.missingInterfaces.length > 0) && editor.saved.profile === "imported" && !!editor.saved.imported && !!editor.rowFor(editor.saved.imported)
        width: parent.width
        text: editor.missingInterfaces.length ? "Re-apply for all interfaces" : "Re-apply for this display"
        enabled: !editor.busy
        onClicked: editor.reapply()
      }
      Label {
        visible: editor.profilesStatus !== ""
        width: parent.width
        text: editor.profilesStatus
        opacity: 0.7
        font.pixelSize: 11 * editor.uiScale
      }
      Label {
        objectName: "profilesEmpty"
        visible: editor.profiles.length === 0 && editor.profilesStatus === ""
        width: parent.width
        text: "No macOS profiles yet. On your Mac, run python3 tools/macos/export-profile.py, then copy the .json file to "
          + (editor.profilesDirectory || "~/.config/trackpad-plus/profiles") + "."
        font.pixelSize: 11 * editor.uiScale
        opacity: 0.8
      }
      Repeater {
        model: editor.profiles
        Action {
          required property var modelData
          required property int index
          objectName: "profileRow" + index
          width: parent.width
          enabled: !modelData.error
          selected: !!editor.draft.imported && editor.draft.imported.file === modelData.file
            && editor.draft.imported.sha256 === modelData.sha256
          text: modelData.error ? modelData.file + " · " + modelData.error : modelData.name
          onClicked: editor.chooseFile(modelData)
        }
      }
      Column {
        visible: editor.speeds.length > 1
        width: parent.width
        spacing: 2 * editor.uiScale
        Label { text: "Tracking speed"; opacity: 0.7; font.pixelSize: 11 * editor.uiScale }
        Slider {
          objectName: "trackingSpeed"
          width: parent.width
          from: 0
          to: Math.max(1, editor.speeds.length - 1)
          stepSize: 1
          snapMode: Slider.SnapAlways
          // The slider moves between notches; the nearest one shows the draft's speed.
          value: {
            var speed = editor.draft.imported ? editor.draft.imported.tracking_speed : 0
            var best = 0
            for (var i = 1; i < editor.speeds.length; i++)
              if (Math.abs(editor.speeds[i] - speed) < Math.abs(editor.speeds[best] - speed)) best = i
            return best
          }
          Accessible.name: "Tracking speed"
          onMoved: editor.setTrackingSpeed(editor.speeds[Math.round(value)])
        }
        Item {
          width: parent.width
          height: slowLabel.implicitHeight
          Label { id: slowLabel; text: "Slow"; opacity: 0.65; font.pixelSize: 11 * editor.uiScale }
          Label { anchors.right: parent.right; text: "Fast"; opacity: 0.65; font.pixelSize: 11 * editor.uiScale }
        }
      }
    }

    Label {
      visible: !editor.imported
      width: parent.width
      text: editor.scrollMode && editor.draft.profile === "mac" ? "A starting curve inspired by macOS progressive scrolling. Apply, then flick vs creep to compare."
        : editor.draft.profile === "mac" ? "An experimental starting point inspired by Mac tracking; tune it to your hand."
        : editor.custom ? "Click a number and use ↑/↓; hold Shift for 10× steps. Type an exact value or drag the handles. Apply when ready."
        : "Choose Custom to edit a curve."
      opacity: 0.65
      font.pixelSize: 11 * editor.uiScale
    }

    Row {
      width: parent.width
      spacing: 8 * editor.uiScale
      Action {
        objectName: "applyCurve"
        text: editor.busy ? "Applying…" : "Apply & try"
        width: (parent.width - parent.spacing) / 2
        selected: true
        enabled: editor.canApply
        onPressed: forceActiveFocus() // Commit typed text before reading the draft.
        onClicked: editor.applyRequested(Curve.copy(editor.draft))
      }
      Action {
        objectName: "restoreCurve"
        text: "Restore previous"
        width: (parent.width - parent.spacing) / 2
        enabled: editor.canRestore && !editor.busy
        onClicked: editor.restoreRequested()
      }
    }
    Label {
      width: parent.width
      objectName: "curveStatus"
      text: editor.settingsError ? "Settings error: " + editor.settingsError : editor.busy ? "Applying curve…" : editor.dirty ? "Curve preview · Apply to feel the change." : "Applied · try small corrections and longer movements below."
      opacity: 0.7
      font.pixelSize: 11 * editor.uiScale
    }

    Rectangle {
      id: practice
      visible: !editor.scrollMode
      width: parent.width
      height: 100 * editor.uiScale
      radius: 6 * editor.uiScale
      color: Qt.alpha(editor.foreground, 0.035)
      border.color: Qt.alpha(editor.foreground, 0.15)
      Label { x: 10 * editor.uiScale; y: 8 * editor.uiScale; text: "Target practice · " + editor.hits + " hits"; font.pixelSize: 11 * editor.uiScale; opacity: 0.6 }
      Rectangle {
        objectName: "practiceTarget"
        readonly property var positions: [[0.12, 0.65], [0.85, 0.50], [0.79, 0.72], [0.2, 0.42], [0.25, 0.68], [0.65, 0.45]]
        readonly property var spot: positions[editor.targetIndex % positions.length]
        width: (editor.targetIndex % 2 ? 14 : 22) * editor.uiScale
        height: width
        radius: width / 2
        x: spot[0] * (practice.width - width)
        y: spot[1] * (practice.height - height)
        color: Qt.alpha(editor.accent, 0.2)
        border.color: editor.accent
        border.width: 2
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { editor.hits++; editor.targetIndex++ } }
      }
    }
  }
}
