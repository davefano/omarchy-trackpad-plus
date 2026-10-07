// Editor gain controls are sampled as output velocities for libinput.
function defaults() { return { precision: 0.3, start: 0.8, end: 2.8, fast: 1.6 } }

// Slow two-finger motion stays near 1:1; faster flicks cover more distance.
function scrollDefaults() { return { precision: 1.0, start: 0.5, end: 2.2, fast: 2.0 } }

function scalePreset(curve, maximum) {
  var factor = Math.min(1, maximum / curve.fast)
  curve.precision = Math.max(0.01, Number((curve.precision * factor).toFixed(6)))
  curve.fast = Number((curve.fast * factor).toFixed(6))
  return curve
}

function presetForScale(maximum) { return scalePreset(defaults(), maximum) }

function copy(value) { return JSON.parse(JSON.stringify(value)) }

// Preserve the shape of saved three-handle curves when opening the new editor.
function normalize(curve) {
  if (curve.transition !== undefined)
    return { precision: curve.precision, start: 0, end: 2 * curve.transition, fast: curve.fast }
  return copy(curve)
}

function gain(curve, speed) {
  var t = Math.max(0, Math.min(1, (speed - curve.start) / (curve.end - curve.start)))
  return curve.precision + (curve.fast - curve.precision) * t * t * (3 - 2 * t)
}

function points(curve) {
  var out = []
  // Two samples beyond the visible 100% end keep extrapolation at constant gain.
  for (var i = 0; i <= 42; i++) out.push(Number((i * 0.1 * gain(curve, i * 0.1)).toFixed(6)))
  return out
}

// Draw the response that libinput actually interpolates, including its tail.
function sampledGain(curve, speed, samples) {
  var values = samples || points(curve)
  if (speed <= 0) return values[1] / 0.1
  var index = Math.min(values.length - 2, Math.floor(speed / 0.1))
  var fraction = speed / 0.1 - index
  return (values[index] + fraction * (values[index + 1] - values[index])) / speed
}

function adjust(curve, handle, value, precise, maximum) {
  var limit = maximum === undefined ? 10 : maximum
  var next = copy(curve)
  if (handle === 0) {
    next.precision = Math.max(0.01, Math.min(Math.max(limit, curve.precision), value))
    next.fast = Math.max(next.fast, next.precision)
  } else if (handle === 1) {
    var start = Math.max(0, Math.min(next.end - 0.2, value))
    next.start = Math.max(0, Math.min(next.end - 0.2, precise ? Math.round(start * 1000000) / 1000000 : Math.round(start * 20) / 20))
  } else if (handle === 2) {
    var end = Math.max(next.start + 0.2, Math.min(4, value))
    next.end = Math.max(next.start + 0.2, Math.min(4, precise ? Math.round(end * 1000000) / 1000000 : Math.round(end * 20) / 20))
  }
  else next.fast = Math.max(next.precision, Math.min(Math.max(limit, curve.fast), value))
  return next
}

function fromSettings(settings) {
  // Keep both the Mac-inspired preset and optional imported macOS profile.
  var preset = settings.curve_preset === "imported" || settings.curve_preset === "mac" ? settings.curve_preset : "custom"
  var feel = {
    profile: settings.accel_profile === "custom" ? preset : settings.accel_profile,
    curve: normalize(settings.curve || defaults())
  }
  // The converted macOS curve travels with the feel so Restore previous needs no file.
  if (feel.profile === "imported" && settings.imported_curve) feel.imported = copy(settings.imported_curve)
  return feel
}

// Profiles that replace libinput's adaptive/flat response, so Pointer Speed does not apply.
// Both built-in presets and imported profiles use custom libinput curves.
function usesCurve(profile) { return profile === "mac" || profile === "custom" || profile === "imported" }

function label(feel) {
  if (feel.profile === "imported") return "macOS · " + (feel.imported && feel.imported.name || "profile")
  return ({ adaptive: "System", flat: "Flat", mac: "Mac-inspired" })[feel.profile] || "Custom"
}

// Fresh Apply converts the file again; explicit pointer_restore sends its saved record separately.
function request(feel) {
  var value = { profile: feel.profile, curve: copy(feel.curve) }
  if (feel.profile === "imported") {
    var imported = feel.imported
    value.imported = { file: imported.file, sha256: imported.sha256 }
    if (imported.tracking_speed !== undefined) value.imported.tracking_speed = imported.tracking_speed
  }
  return value
}

// Equal feels apply the same curve: a reference and its converted curve share file, digest and speed.
function same(a, b) {
  function key(feel) {
    var value = { profile: feel.profile, curve: feel.curve }
    if (feel.profile === "imported")
      value.imported = feel.imported ? [feel.imported.file, feel.imported.sha256, feel.imported.tracking_speed] : null
    return JSON.stringify(value)
  }
  return key(a) === key(b)
}

function fromScrollSettings(settings) {
  return {
    profile: settings.scroll_curve_preset || "mac",
    curve: normalize(settings.scroll_curve || scrollDefaults())
  }
}

if (typeof module !== "undefined") module.exports = { defaults, scrollDefaults, presetForScale, copy, normalize, gain, points, sampledGain, adjust, fromSettings, fromScrollSettings, usesCurve, label, request, same }
