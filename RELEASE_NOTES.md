# 2026.10.07.0 — Pointer profiles, progressive scrolling and gestures

This release brings together contributor PRs #12–#16, with integration fixes,
regression tests and expanded Linux/macOS CI. Existing settings remain saved;
new pointer profiles, progressive scrolling and vertical gestures are optional.

## What's new

- **Experimental macOS pointer profiles:** import exported Apple acceleration
  parameters, choose a profile and Tracking speed, then Apply. System, Flat,
  Mac-inspired and Custom remain available. Imported Undo works without the
  source file, and changed display scale prompts Re-apply.
- **Sensor-aware pointer curves:** explicit Mac-inspired/Custom Apply uses known
  per-interface resolution. Existing curves retain their old feel until edited;
  calibration and Undo survive disconnection. Ambiguous sensors are not guessed.
- **Progressive scrolling:** an independent 1× slow / 2× fast default curve,
  with its own editor, before the existing Scroll Speed multiplier. Existing
  installations remain linear until enabled.
- **Optional vertical gestures:** swipe up for fullscreen and down for Omarchy's
  scratchpad, using the selected 3/4-finger count. These actions and overview
  are mutually exclusive; manual bindings and Restore original remain supported.
- **Expanded checks:** portable Linux CI, native Mac probe compilation and CLI
  tests, plus the complete Omarchy host suite.

## How to use it

### macOS pointer profiles

Export on the Mac with `python3 tools/macos/export-profile.py`, then copy the
JSON to the Linux machine:

```sh
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/trackpad-plus/profiles"
install -m 600 YOUR_PROFILE.json "${XDG_CONFIG_HOME:-$HOME/.config}/trackpad-plus/profiles/"
```

Open **Pointer → Pointer feel → macOS**, select the profile and Tracking speed,
then **Apply & try**. Unknown interface resolution is reported before Apply;
follow the setup instructions to supply it. New interfaces without a converted
curve keep native tracking and scrolling and show an incomplete-import notice.
Provide their resolution and use **Re-apply for all interfaces** to recover.
Known converted interfaces keep their existing profile and progressive scrolling.

This models pointer movement only. Timing, stroke starts and filtering differ
from macOS; matching Apple's scrolling or inertia is separate work. The M1 Pro
measurements are contributor-reported evidence, not a guarantee for other devices.
See [profile setup and limits](README.md#experimental-macos-pointer-profiles) and
[the exporter/measurement guide](tools/macos/README.md).

### Scrolling and gestures

Open **Scrolling → Progressive Scrolling** to enable the curve, then use
**Scroll acceleration** to tune it. Scroll Speed still multiplies the result.
Libinput requires custom pointer acceleration for progressive scrolling:
turning it on from System/Flat selects a Mac-inspired pointer preset; choosing
System/Flat later turns progressive scrolling off. This adds no inertia after lift.

Open **Gestures**, select workspace swiping and a 3/4-finger count, then choose
**Swipe up for fullscreen** or **Swipe down for scratchpad** and press **Apply gestures**. Enabling overview clears those options. **Restore original** returns
to the previous bindings. See [scrolling](README.md#progressive-scrolling) and
[gestures](README.md#workspace-gestures) for details.

## Fixes and data safety

- Fractional Mac recordings retain double-precision raw motion; failed checks
  never write measured constants into the profile.
- Imported names render as literal text rather than loading markup or remote images.
- Apply/Undo waits for authoritative converted state, with save errors retained.
- Native/progressive transitions remove dormant imported metadata consistently.
- Low-event-rate conversion now follows Apple's timing clamp.
- Newly discovered interfaces expose incomplete imports and a safe recovery path.

Profiles remain bounded, validated data with private-file checks and a digest
check before Apply. Converted profiles and per-interface metadata are saved for
exact Undo. Settings schema 6 accepts earlier schemas without recalibrating
existing curves. Save matching plugin/state backups before upgrading: an older
backend refuses schema 6, so rollback requires its corresponding state backup.

## Install or update

For a new installation:

```sh
omarchy plugin add https://github.com/davefano/omarchy-trackpad-plus.git --enable
```

For an existing Git-managed installation, back up local edits and settings first:

```sh
trackpad_plugin="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/davefano.trackpad-plus"
python3 "$trackpad_plugin/overview-control.py" stop
omarchy plugin update davefano.trackpad-plus --yes
omarchy restart shell
```

The optional Apple typing guard remains separate from plugin updates. To update
an already installed guard, copy its release files and restart the user service:

```sh
install -Dm644 "$trackpad_plugin/trackpad-typing-guard.py" "$HOME/.local/lib/trackpad-typing-guard.py"
install -Dm644 "$trackpad_plugin/trackpad-typing-guard.service" "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/trackpad-typing-guard.service"
systemctl --user daemon-reload
systemctl --user enable --now trackpad-typing-guard.service
systemctl --user restart trackpad-typing-guard.service
```

It pauses the saved Apple group during typing and resumes about 0.55 seconds
later. Verify a nonzero keyboard count and increasing pause/resume counters.
The native Magic Trackpad palm controls remain separate and model-specific;
this release does not install new system palm rules automatically.

## Verification and credits

The reviewed runtime passed the full Omarchy host suite, Linux portable CI and
macOS compile/invalid-argument CI. Regression coverage includes migration,
calibration, imported/ordinary Undo, fractional verification, literal text,
interface discovery/recovery, scrolling, gestures, installation and IPC.
Physical Mac recording and tracking comparisons still require hardware testing.

Thanks to [niconistal](https://github.com/niconistal),
[tylerflint](https://github.com/tylerflint),
[Skeptomenos](https://github.com/Skeptomenos) and
[benpbolton](https://github.com/benpbolton) for their contributions. Original
commits and license notices are retained.

---

# 2026.10.05.0 — Apple palm rejection and typing protection

This release adds native palm-rejection controls for the external Apple Magic
Trackpad model 0265 and an optional typing guard for the saved Apple device group.
It also fixes the local guard's missing desktop-login startup configuration.

## Apple palm rejection

Open **Apple → Pointer → Palm rejection** to choose the system default or a
custom contact-size threshold. Lower thresholds reject smaller contacts but
can also interfere with fingers and gestures. The tested model's default is
900; 700 worked for one measured setup and is not a universal recommendation.

Install the helper from the installed plugin directory or a trusted checkout:

```sh
sudo install -Dm644 palm-system.py /usr/local/libexec/trackpad-plus-palm.py
```

Select the threshold, press **Apply palm settings**, and authorize the change.
Log out and back in to activate it. The panel reports pending changes and never
logs you out automatically. USB and Bluetooth use matching thresholds. The
helper backs up existing rules, preserves unrelated sections, and refuses
conflicting manual rules. **System default** removes the managed overrides.

The control is limited to model 0265; other Apple models and Dell pads retain
their native defaults. [Full instructions](README.md#apple-palm-rejection).

## Optional typing protection for external Apple trackpads

The external Magic Trackpad tested here does not support native
disable-while-typing, so the panel's switch alone does not suppress touches.
The bundled optional guard pauses the saved `apple` group during ordinary
physical-keyboard typing and resumes about 0.55 seconds after typing stops.
Ctrl, Alt, and Super shortcuts do not trigger the pause. No typed text is logged.

From the installed plugin directory or a trusted checkout:

```sh
install -Dm644 trackpad-typing-guard.py "$HOME/.local/lib/trackpad-typing-guard.py"
install -Dm644 trackpad-typing-guard.service "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/trackpad-typing-guard.service"
systemctl --user daemon-reload
systemctl --user enable --now trackpad-typing-guard.service
python3 "$HOME/.local/lib/trackpad-typing-guard.py" status
```

First connect and select the Apple pad in Trackpad Plus, and leave **Enable
trackpad** and **Disable While Typing** on. Keyboard device read access is
required. Verify a nonzero `keyboards` count, then type while touching the pad:
the pointer should pause and resume afterward. `pauses` and `resumes` should
increase. The enabled service returns at desktop login; merely starting it does
not persist protection after logout or reboot. It does not protect Dell or
separately saved built-in Apple groups.

Plugin installation does not automatically install this service. Repeat the
copy commands and restart the service when updating its separate copy.
[Verification, update, and removal instructions](README.md#optional-apple-typing-guard).

## Install or upgrade

New installation:

```sh
omarchy plugin add https://github.com/davefano/omarchy-trackpad-plus.git --enable
```

For an existing Git-managed copy, back up local edits and settings first:

```sh
trackpad_plugin="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/davefano.trackpad-plus"
python3 "$trackpad_plugin/overview-control.py" stop
omarchy plugin update davefano.trackpad-plus
omarchy restart shell
```

Then open the installed directory to run either optional setup above:

```sh
cd "${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/davefano.trackpad-plus"
```

Existing pointer and scrolling preferences remain saved. Optional palm settings
and the typing guard require their own setup. See the
[README](README.md#install-and-upgrade) for migration and rollback details.

## Verification

Regression coverage includes palm-rule validation, conflict handling and
preservation; typing pause/resume and shutdown restoration; and inclusion of
the guard and login-enabled service in an installation made from tracked files.
The typing guard was physically verified on a Dell XPS with an external Apple
Magic Trackpad. Other hardware needs its own typing test.
