// Thirty-second check of macOS trackpad acceleration for Trackpad Plus.
//
//   swiftc -O tools/macos/probe.swift -o /tmp/trackpad-probe && /tmp/trackpad-probe probe.csv
//   python3 tools/macos/export-profile.py --check probe.csv --profile PROFILE.json [--write]
//   Option: --seconds N (default 30).
//
// A full-screen window records every pointer event — the accelerated location and Apple's
// unaccelerated counts (CGEvent fields 170/171) — plus AppKit's public NSTouch positions.
// It deliberately does not read the private MultitouchSupport framework: starting a device
// there switches macOS to a slower, smoothed pointer path, so the pointer stops feeling
// native while it runs. Nothing is sent anywhere; the CSV stays where you ask.
import AppKit
import Foundation

let arguments = Array(CommandLine.arguments.dropFirst())
func usage(_ reason: String = "") -> Never {
    FileHandle.standardError.write((reason + "\nUsage: trackpad-probe OUTPUT.csv [--seconds 1...300]\n").data(using: .utf8)!)
    exit(2)
}
guard let outputPath = arguments.first, !outputPath.hasPrefix("--"),
      arguments.count == 1 || (arguments.count == 3 && arguments[1] == "--seconds") else {
    usage()
}
var recordSeconds = 30.0
if arguments.count == 3 {
    guard let seconds = Double(arguments[2]), seconds.isFinite, seconds >= 1, seconds <= 300 else {
        usage("Recording duration must be finite and between 1 and 300 seconds.")
    }
    recordSeconds = seconds
}
let idleLimitSeconds = 180.0

final class Log {
    private var lines: [String] = []
    private(set) var pointerEvents = 0
    private(set) var touchFrames = 0
    private(set) var speedBins = [0, 0, 0]  // slow, medium, fast pointer events

    func add(_ line: String, touch: Bool = false, bin: Int? = nil) {
        lines.append(line)
        if touch { touchFrames += 1 } else { pointerEvents += 1 }
        if let bin { speedBins[bin] += 1 }
    }

    func write(to url: URL, header: [String]) throws {
        try (header + lines).joined(separator: "\n").appending("\n").write(to: url, atomically: true, encoding: .utf8)
    }
}

let log = Log()

final class ProbeWindow: NSWindow {
    override var canBecomeKey: Bool { true }
}

final class ProbeView: NSView {
    var started: TimeInterval?
    var lastEvent: TimeInterval?
    let opened = ProcessInfo.processInfo.systemUptime
    var finish: () -> Void = {}

    override var acceptsFirstResponder: Bool { true }
    override func touchesBegan(with event: NSEvent) { recordTouches(event) }
    override func touchesMoved(with event: NSEvent) { recordTouches(event) }
    override func touchesEnded(with event: NSEvent) { recordTouches(event) }
    override func touchesCancelled(with event: NSEvent) { recordTouches(event) }
    override func mouseMoved(with event: NSEvent) { record(event) }
    override func mouseDragged(with event: NSEvent) { record(event) }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 { finish() } // Escape
    }

    func record(_ event: NSEvent) {
        guard let cg = event.cgEvent else { return }
        let now = ProcessInfo.processInfo.systemUptime
        if started == nil { started = now }
        let gap = lastEvent.map { event.timestamp - $0 } ?? 0
        lastEvent = event.timestamp
        let ux = cg.getIntegerValueField(.eventUnacceleratedPointerMovementX)
        let uy = cg.getIntegerValueField(.eventUnacceleratedPointerMovementY)
        let uxd = cg.getDoubleValueField(.eventUnacceleratedPointerMovementX)
        let uyd = cg.getDoubleValueField(.eventUnacceleratedPointerMovementY)
        let mx = cg.getDoubleValueField(.mouseEventDeltaX)
        let my = cg.getDoubleValueField(.mouseEventDeltaY)
        let location = cg.location
        // Rough finger speed for the coverage bars only: 400 counts per inch.
        var bin: Int?
        if gap > 0 && gap < 0.05 {
            let speed = hypot(Double(ux), Double(uy)) / gap * 25.4 / 400
            bin = speed < 30 ? 0 : speed < 150 ? 1 : 2
        }
        log.add("P,\(event.timestamp),\(now),\(location.x),\(location.y),\(event.deltaX),\(event.deltaY),\(ux),\(uy),\(uxd),\(uyd),\(mx),\(my)", bin: bin)
    }

    // NSTouch positions are normalised to the trackpad; deviceSize is in points (1/72 in).
    func recordTouches(_ event: NSEvent) {
        let touches = event.touches(matching: .any, in: self)
        let now = ProcessInfo.processInfo.systemUptime
        let touching = touches.filter { $0.phase != .ended && $0.phase != .cancelled }
        guard let touch = touching.first ?? touches.first else { return }
        let position = touch.normalizedPosition, size = touch.deviceSize
        log.add("N,\(event.timestamp),\(now),\(touch.phase.rawValue),\(touching.count),\(touch.identity.hash),\(position.x),\(position.y),\(size.width),\(size.height)", touch: true)
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor(calibratedWhite: 0.09, alpha: 1).setFill()
        bounds.fill()
        let now = ProcessInfo.processInfo.systemUptime
        let remaining = started.map { max(0, recordSeconds - (now - $0)) }
        var text = """
        Trackpad Plus · \(Int(recordSeconds))-second check

        Use ONE finger and do not click. Move the pointer around this screen:
          1. tiny, careful movements for a few seconds
          2. ordinary movements at a normal pace
          3. quick flicks back and forth

        """
        text += remaining.map { String(format: "Recording… %.0f s left", $0) } ?? "Recording starts when the pointer moves."
        text += "\n\(log.pointerEvents) pointer events · \(log.touchFrames) touch frames"
        let bins = log.speedBins
        text += "\n\nCoverage   slow \(bar(bins[0], 400))   medium \(bar(bins[1], 400))   fast \(bar(bins[2], 150))"
        text += "\n\nEsc stops early."
        let style = NSMutableParagraphStyle()
        style.lineSpacing = 6
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 18, weight: .regular),
            .foregroundColor: NSColor(calibratedWhite: 0.92, alpha: 1), .paragraphStyle: style]
        NSAttributedString(string: text, attributes: attributes).draw(in: bounds.insetBy(dx: 80, dy: 80))
    }

    func bar(_ count: Int, _ target: Int) -> String {
        let filled = min(10, count * 10 / target)
        return String(repeating: "█", count: filled) + String(repeating: "·", count: 10 - filled)
    }
}

let output = URL(fileURLWithPath: outputPath)
let app = NSApplication.shared
app.setActivationPolicy(.regular)
NSEvent.isMouseCoalescingEnabled = false  // one row per HID event

guard let screen = NSScreen.screens.first(where: { screen in
    let id = (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0
    return CGDisplayIsBuiltin(id) != 0
}) ?? NSScreen.main,
      let displayNumber = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else {
    usage("A graphical desktop with an available display is required.")
}
let window = ProbeWindow(contentRect: screen.frame, styleMask: .borderless, backing: .buffered, defer: false)
window.level = .mainMenu + 1
window.acceptsMouseMovedEvents = true
let view = ProbeView(frame: NSRect(origin: .zero, size: screen.frame.size))
window.contentView = view
view.allowedTouchTypes = [.indirect]
view.wantsRestingTouches = true
let bounds = CGDisplayBounds(displayNumber.uint32Value)
let header = [
    "# trackpad-plus probe 2",
    "# screen_points,\(screen.frame.width),\(screen.frame.height),backing_scale,\(screen.backingScaleFactor)",
    "# global_bounds,\(bounds.minX),\(bounds.minY),\(bounds.maxX),\(bounds.maxY)",
    "# columns P,timestamp,received,x,y,deltaX,deltaY,unaccel_x,unaccel_y,unaccel_x_double,unaccel_y_double,delta_x_field,delta_y_field",
    "# columns N,timestamp,received,phase,touching,identity,normalized_x,normalized_y,device_width_pt,device_height_pt",
]

var done = false
view.finish = {
    guard !done else { return }
    done = true
    do {
        try log.write(to: output, header: header)
        print("Wrote \(output.path): \(log.pointerEvents) pointer events, \(log.touchFrames) touch frames.")
    } catch {
        FileHandle.standardError.write("Could not write \(output.path): \(error)\n".data(using: .utf8)!)
    }
    app.terminate(nil)
}
Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in
    let now = ProcessInfo.processInfo.systemUptime
    if let started = view.started, now - started >= recordSeconds { view.finish() }
    if view.started == nil && now - view.opened > idleLimitSeconds { view.finish() }
    view.needsDisplay = true
}
window.makeKeyAndOrderFront(nil)
window.makeFirstResponder(view)
app.activate(ignoringOtherApps: true)
app.run()
