# Trackpad Plus for Omarchy

**Make your trackpad feel right.**

Fine-grained, per-device trackpad controls and pointer-feel tuning for
[Omarchy](https://omarchy.org). Independently maintained by David Fano;
not an official Omarchy project or endorsed by the Omarchy team.

## Project history

Trackpad Plus for Omarchy began as a fork of Andrew Kent’s
[omarchy-touchpad-widget](https://github.com/awkent01/omarchy-touchpad-widget).
It is independently maintained and has expanded to include per-device
controls, Apple trackpad support, and advanced pointer-feel tuning.
The original project and this derivative are licensed under the MIT License.

## Three tabs, one selected trackpad

Select a trackpad at the top, then choose **Pointer**, **Scrolling**, or
**Gestures**. The top-right gear contains **Enable trackpad** and **Device scale**.
Pointer and scrolling settings are saved separately for each device group;
gestures apply across trackpads.

<table>
  <tr><th>Pointer</th><th>Scrolling</th><th>Gestures</th></tr>
  <tr>
    <td valign="top"><img src="assets/screenshots/pointer-tab.png" alt="Pointer tab on an Apple trackpad: Custom pointer feel, Tap to Click, Disable While Typing, and Two-Finger Right Click" width="260"></td>
    <td valign="top"><img src="assets/screenshots/scrolling-tab.png" alt="Scrolling tab on an Apple trackpad: Scroll Speed at 0.10× and Natural Scrolling off" width="260"></td>
    <td valign="top"><img src="assets/screenshots/gestures-tab.png" alt="Gestures tab: three-finger workspace swipes, distance 200, and the Trackpad Plus overview provider with test and apply controls" width="260"></td>
  </tr>
</table>

- **Pointer:** choose a pointer-feel profile or edit its acceleration curve;
  configure tapping, typing protection, and two-finger right click. System and
  Flat profiles also expose **Pointer Speed**.
- **Scrolling:** adjust **Scroll Speed** and **Natural Scrolling** independently
  of the pointer curve.
- **Gestures:** configure horizontal workspace swipes and an optional upward
  swipe for the workspace overview. Test the overview before applying gestures.

### Available controls

- Enable or disable the selected trackpad.
- Scroll speed (0.01–1.00, in 0.01 steps) with a per-device scale, and pointer speed (−1.0–1.0).
- Pointer feel: System (adaptive), Flat, Mac-inspired, and Custom profiles.
- Visual acceleration editor with draggable precision, acceleration start/end, and fast-swipe
  handles, keyboard adjustment, target practice, and Restore previous.
- Natural scrolling, tap to click, disable while typing, and clickfinger behavior.
- Keyboard navigation through device selection, sliders, and switches.

Select a detected trackpad at the top. The **Enable trackpad** switch inside the gear menu turns
that trackpad on or off. **Natural Scrolling** controls scroll direction;
**Tap to Click** enables tapping instead of pressing; **Disable While Typing**
reduces accidental input while typing; **Two-Finger Right Click** enables a
secondary click by pressing with two fingers.

The sliders and toggles save as you use them. Pointer-curve edits stay in
preview until you press **Apply & try**.

The footer shows the installed version, starting with **2026.09.13.0**. Releases
use **YYYY.MM.DD.N**: release date followed by a revision starting at 0 and
increasing for additional releases that day. The tab screenshots show
**2026.09.14.1**; the footer reflects the version you have installed.

Built-in Apple trackpads are recognized as Apple: Apple Silicon
(`apple-mtp-multi-touch`, `apple-spi-trackpad`) and Intel Macs (`bcm5974`,
`apple-spi-touchpad`, `apple-inc.-apple-internal-keyboard-/-trackpad-1`).
Fresh installations group them with Apple Magic Trackpad interfaces.
If an upgrade finds a previously saved built-in trackpad group, it keeps every
setting and Restore previous entry. A lone group becomes **Apple**; if other
Apple preferences already exist, the older group stays separate as
**Apple (device name)** so neither set of preferences is overwritten. The known
Dell touchpad is labeled Dell; other devices containing `touchpad` or `trackpad`
in their Hyprland name are listed by that name. Disconnected devices retain their
saved settings, and newly attached devices are discovered during state refreshes.
Synaptics TM touchpads (including Lenovo and HP models) are recognized by their
`TM` part number even though names like `synaptics-tm3512-010` and `synaptics-tm3381-002` contain neither
word. The part number is matched against the whole name, so a Synaptics mouse or
any name carrying a suffix cannot match it. The separate TrackPoint is excluded.
Other trackpads whose names omit both words may still need an explicit detection
rule.

## Workspace gestures

Open the **Gestures** tab. Pointer and scrolling changes save as you use them,
including pending slider edits before switching tabs. Gesture changes stay in a
draft until you press **Apply gestures**.

In **Gestures**, enable horizontal workspace swiping, choose **3 or 4 fingers**,
optionally reverse direction, and adjust **Swipe distance** (50–2000). Lower
values cover a workspace with less finger travel. Up/Down changes distance by
10; Shift+Up/Down changes it by 100. These are compositor units, not millimeters.
Swipe completion also depends on velocity and Hyprland's cancellation threshold.
Gesture bindings apply across trackpads; distance also affects touchscreen
workspace swipes. They are independent of the selected device's pointer curve.

Press **Apply gestures** to save and reload Hyprland's input configuration.
A simple existing horizontal workspace binding in `~/.config/hypr/input.lua`
is adopted into a marked block with a backup; it is not registered twice.
**Restore original** removes that block and restores the original binding,
keeping unrelated input edits. **Reload settings** discards the gesture draft
and reads the saved configuration again. Conflicting, conditional, indirect, or
otherwise unsupported gesture definitions show an explanation and remain under
manual control.

Trackpad Plus backs up the original input file to
`~/.local/state/omarchy/local-touchpads/gesture-input-original.lua.txt`.
A failed reload restores the previous file. An interrupted write leaves a
recovery journal; the next gesture read attempts recovery, but refuses to
replace a file that has since been manually changed. Existing configuration
errors must be resolved before applying gestures.

### Stow and symlinked dotfiles

Gesture settings support a symlinked `input.lua`, `hypr` directory, or `.config`
directory. For example, `~/.config/hypr/input.lua` may point to
`~/.dotfiles/hypr/.config/hypr/input.lua`. Apply and Restore original update the
real dotfile and preserve the symlink, so changes also appear in your dotfiles
repository. No unstowing or copying is needed. `XDG_CONFIG_HOME` is respected.

Targets must be regular files owned by your user, with no hard links or write
access for other users; their directories must also pass ownership and permission
checks. Broken links and symlink loops are rejected. If a link changes target
during an edit or pending recovery, the operation stops and keeps the recovery
journal. Restore the original link before retrying recovery. These permissions
apply to editable config targets; private Trackpad Plus state files continue to
reject symlinks entirely.

### Workspace overview (experimental)

Choose an **Overview provider** in **Gestures**:

- **Trackpad Plus** runs a separate Quickshell overview included in this repository.
  It needs no HyMission installation or native compositor hooks. This is the
  experimental option being tested on a MacBook Air M2 running Linux; x86_64
  rendering has not yet been verified.
- **HyMission** uses the separately installed compositor plugin described below.
  Existing HyMission settings keep that provider until you explicitly change it.

For the built-in option, select **Trackpad Plus**, use **Test overview**, and
confirm that your windows render and selection works before enabling **Swipe up
for overview** and pressing **Apply gestures**. Installing or inspecting the
companion does not enable gestures. A successful process start or IPC connection
alone is not proof that previews work on your system.

Swipe up with your selected three or four fingers to open; swipe down or press
Escape to dismiss. Click a window to focus it, or a workspace
thumbnail in the top strip to switch there, including an existing empty workspace.
Both selections close the overview immediately. The **+** tile creates and
enters an unused numbered workspace. Tab or arrow keys move through controls;
Enter selects.
Horizontal swipes retain native workspace switching and update the open overview.

The overview uses your current Omarchy wallpaper. A translucent workspace strip
slides down from the top, with miniature desktops showing up to three windows
in their desktop positions; a count marks additional windows. The strip shows
ordinary workspaces on the monitor where you opened it and scrolls horizontally
when needed. Larger previews below preserve window proportions. The main area
shows up to six windows at once, with page controls for additional windows. Other
monitors, special workspaces, and hidden group members are excluded. Previews are static
snapshots captured on opening or paging, not live video, and the transition does
not follow your fingers continuously. An unavailable preview remains selectable.

The companion runs outside both Hyprland and the bar, starts on demand, and can
stay idle after dismissal. Hidden and locked views release their capture sources;
an unknown lock state prevents opening. Unlocking does not reopen it. Preview
images and window titles are not written to files, logs, or network services.
Capture is limited to visible thumbnails and the current window page, with two simultaneous requests and a
per-card deadline. Large source windows can still require large graphics buffers.

Gesture block schemas 2–6 remain readable and restorable. An explicit edit
writes schema 7 with the selected provider; this is separate from the pointer
settings schema. With Trackpad Plus selected, Hyprland 0.56.2 gesture callbacks
open the overview as soon as the upward swipe is recognized, without waiting for
finger release. Reversing or cancelling the swipe afterward does not undo the
opening, and release never opens it a second time. Downward swipes still close
only on a non-cancelled finish. **Restore original** restores the saved input bindings without
changing your trackpad values or uninstalling either provider.
After upgrading, press **Apply gestures** once to keep your gesture values and
add the overview-only rule that disables the compositor fade. The workspace strip
still slides down. The companion preloads and retains a bounded wallpaper image
between openings; window previews are released on close. If wallpaper loading
fails or exceeds one second, a stable theme-colored fallback is shown for that opening.

### Built with Quickshell

The built-in overview is powered by [Quickshell](https://quickshell.org/), led by
[outfoxxed](https://outfoxxed.me/) and its contributors. Its
[Hyprland integration](https://quickshell.org/docs/v0.3.1/types/Quickshell.Hyprland/Hyprland/)
provides workspace, window, and monitor information; its
[Wayland screencopy API](https://quickshell.org/docs/v0.3.1/types/Quickshell.Wayland/ScreencopyView/)
provides the window snapshots. Thank you to the Quickshell contributors for the
building blocks that make this overview possible.

### Overview inspiration: HyMission

We learned from [HyMission](https://github.com/gfhdhytghd/hymission), created by
[gfhdhytghd](https://github.com/gfhdhytghd), particularly its Mission Control-style
workspace strip and trackpad gestures. Thank you to its contributors for making
that work available to study.

Trackpad Plus's built-in overview is implemented here as a separate Quickshell
companion. HyMission remains a separately maintained, optional provider with its
own [GPL-3.0 license](https://github.com/gfhdhytghd/hymission/blob/master/LICENSE).
You do not need to install it to use the **Trackpad Plus** overview provider.

### Optional HyMission provider

[HyMission](https://github.com/gfhdhytghd/hymission) is an **optional runtime
dependency only when you select HyMission** as the overview provider. It renders the overview
inside Hyprland; Trackpad Plus configures the gestures. HyMission is separately
installed and maintained, and is not bundled with Trackpad Plus. Horizontal
workspace swiping works without it.

**Compatibility:** HyMission's overview currently requires an **x86_64** system.
Hyprland 0.56.2 disables the function hooks it needs on ARM64, including Apple
Silicon Macs. The plugin can load there but fails when opening the overview with
`surface pass hook attach failed`. Trackpad Plus disables the **HyMission** overview option
on unsupported architectures. Pointer tuning, scrolling, and native horizontal
workspace swipes still work on ARM64.

With **HyMission** selected, the Gestures tab checks the architecture and whether
HyMission is loaded before enabling **Swipe up for overview**. Turn it on, choose 3 or 4 fingers, then press **Apply gestures**:

- Swipe up to open an overview of your workspaces and windows.
- Swipe down to return. HyMission can also close an open overview with an upward swipe.
- Swipe horizontally to switch workspaces when **Workspace swipe** is enabled.

HyMission's overview follows your finger continuously. It uses its default
vertical direction; an existing `gesture_invert_vertical` override still applies.
Trackpad Plus refuses to replace an existing manual vertical gesture. Resolve
that binding in your config first. **Restore original** removes the managed
overview gesture along with the other managed gestures; it does not uninstall
HyMission.

**Install HyMission ↗** opens the [upstream installation instructions](https://github.com/gfhdhytghd/hymission#installation).
Use a release matching your Hyprland version. The gesture integration targets
HyMission's `v0.7.0-v0.56.2` API and Hyprland 0.56.2. Automated tests cover
configuration and fallback behavior; rendering could not be validated on the
ARM64 development machine. Building and loading the plugin does not establish
overview compatibility.

For systems with `hyprpm`, the upstream installation flow is:

```sh
hyprpm update
hyprpm add https://github.com/gfhdhytghd/hymission
hyprpm enable hymission
hyprpm reload
```

Add `o.exec_on_start("hyprpm reload")` to `~/.config/hypr/autostart.lua` for
future logins if it is not already configured. If `hyprpm` is unavailable, use
HyMission's manual build instructions with headers matching the running
Hyprland build. A manually built plugin needs rebuilding after a Hyprland ABI
change; do not reuse a binary from another machine or compositor version.

After loading the plugin, click **Reload settings** in Trackpad Plus and enable
the overview. If HyMission later becomes unavailable, the saved config skips
overview registration and falls back to native horizontal swiping. The widget
shows the missing dependency and still lets you turn overview off or restore
your original settings. Your pointer and scrolling settings are independent.

## The top-right gear: Device scale

<img src="assets/screenshots/device-scale.png" alt="Annotated screenshot pointing from the gear beside Apple to the Device scale setting, set to 1.00" width="340">

Click the **gear beside the trackpad name** to reveal **Enable trackpad** and
**Device scale**. Click it again to close the settings. The annotated image above
shows the scale control before Enable trackpad moved into this menu. This adapts the available range to the selected
trackpad's sensitivity. It defaults to **1×** and accepts **0.10–10.00×**.
There is no automatic Apple/PC multiplier.

The scale has two effects:

| Control | At 1× scale | At 3× scale |
| --- | --- | --- |
| Scroll Speed | A slider value of 0.50 sends 0.50× to Hyprland. | A slider value of 0.50 sends 1.50× to Hyprland. |
| Acceleration editor | The vertical chart range is 0–1×. | The vertical chart range is 0–3×. |

The scroll slider always runs from **0.01 to 1.00**. Its effective scroll factor
is **slider value × Device scale**. Increasing the scale changes scrolling
immediately when the value is committed. A less sensitive trackpad may benefit
from a wider range, such as 3×.

For the pointer curve, Device scale changes the chart range and gain controls.
**It does not multiply or overwrite your existing curve.** Adjust the curve and
press **Apply & try** to change pointer movement. If an existing curve exceeds
the chart range, the editor shows a notice and preserves its values. Pointer
Speed for System and Flat profiles is also unchanged by Device scale.

Type a scale and press Enter, or use Up/Down for **0.10** steps.
Shift+Up/Down uses **1.00** steps. The setting is saved for that device group.

Upgrading preserves effective scroll speeds: existing values up to 1 keep a 1×
scale, while values above 1 receive a matching scale. Schema 4 stores the
effective `scroll_factor` and separate `scroll_scale`; only the effective factor
is emitted to Hyprland. Back up both plugin and settings before upgrading;
downgrading requires restoring the matching settings backup.

## How pointer feel works

<img src="assets/screenshots/pointer-feel.png" alt="Custom pointer curve on a MacBook Air M2: precision 0.0100, start 0%, end 100%, and fast swipes 0.3500, on a 0–1× chart" width="430">

The graph connects **how fast your fingers move** to **how much the cursor
moves**. Left to right is slower to faster finger movement. Up and down is cursor
travel multiplier, or gain. Slower finger movement gives you finer corrections;
faster movement lets you cover more distance. The response follows finger speed,
not how close the cursor is to a button or target.

Choose **Custom** to adjust the curve, or **Mac-inspired** for a starting shape:

| Control | What it changes |
| --- | --- |
| **Precision ×** | Gain at the slow end. Lower values make small corrections finer. Drag the left circle vertically. |
| **Start %** | Where acceleration begins. Below this threshold, gain stays at the Precision value. Move the left square horizontally. |
| **End %** | Where acceleration reaches the Fast swipes value. Moving it right spreads the transition over a wider range of finger speeds. |
| **Fast swipes ×** | Gain at the fast end and beyond. Drag the right circle vertically. It cannot be lower than Precision. |

Start and End are relative positions on the graph, not physical speed units.
The two square handles control horizontal thresholds; the two circles control
gain. When End is at 100%, its square is offset vertically so it remains
separately clickable beside the fast-swipe circle.

Each value has an editable number spinner. Click the number and use Up/Down,
click its arrow buttons, or type a value and press Enter. Gain steps are
**0.001×**; threshold steps are **one percentage point**. Hold Shift with ↑/↓ for
10× steps: **0.01× gain** or **10 percentage points**. Typed values allow four
decimal places for gain and two for percentages. Gain can go down to **0.01×**.
Tab between controls; arrow keys also adjust a focused graph handle.

Press **Apply & try** to commit the draft, including a number you just typed,
then use the target-practice area for small corrections and longer movements.
**Restore previous** swaps back to the profile used before the last Apply on
that device, including after a restart. Applied settings persist across shell
restarts and Hyprland reloads. Escape returns to the main panel.

**System** uses libinput adaptive acceleration with your saved Pointer Speed.
**Flat** uses a constant response with that speed setting. In Custom and
Mac-inspired mode, the curve replaces Pointer Speed; its saved value is retained
for when you return to System or Flat.

The Mac-inspired preset is an experimental approximation. Its base curve uses
0.30× precision, Start at 20%, End at 70%, and 1.60× fast swipes. Choosing it with
a lower Device scale reduces both gains proportionally to fit: at 1× scale,
that gives 0.1875× precision and 1.00× fast swipes. Existing curves change only
when you explicitly apply an edit or preset. This editor does not add scroll
momentum or change gestures or haptic feedback.

<details>
<summary>How the curve reaches libinput</summary>

The backend converts the curve into 43 evenly spaced output-velocity samples
for libinput's native custom profile. The graph plots the response interpolated
from those same samples. Two samples beyond the visible graph keep fast swipes
at constant gain, including when End is 100%.

Each custom curve is validated with the installed libinput library before it
is applied or saved. Libinput accepts at most 64 points; Hyprland 0.56 does not
report point-validation failures through `hyprctl eval`, so the compositor's
response alone is insufficient. Custom profiles use an identity scroll curve
before the separate scroll multiplier. Legacy three-handle curves preserve
their intended shape during migration and appear as Custom.

The curve is defined per millimetre of finger travel, so it feels the same on
trackpads with different sensor densities. For touchpads, libinput's custom
profile receives raw device units instead of its usual 1000 dpi normalized
units, so the backend spaces the samples by each device's resolution: a 96
units/mm MacBook Pro sensor gets 2.4× the sample spacing, a 47 units/mm Magic
Trackpad 2 gets 1.2×. Resolution comes from udev hwdb overrides
(`EVDEV_ABS_00`) or, for Magic Trackpads, the kernel driver's known value;
devices with neither keep the unscaled samples. A group's interfaces each get
their own spacing.

</details>

## David's MacBook Air M2 settings

These are David Fano's settings for the **built-in MacBook Air M2 trackpad** as
of September 13, 2026, shown in the screenshots above. Use them as a starting
point and adjust for your display, trackpad, and preferred feel.

| Setting | Value |
| --- | --- |
| Trackpad | Enabled |
| Device scale | **1.00×** |
| Scroll Speed | **0.10** → effective **0.10×** |
| Pointer feel | **Custom** |
| Precision | **0.0100×** |
| Start | **0.00%** |
| End | **100.00%** |
| Fast swipes | **0.3500×** |
| Natural Scrolling | Off |
| Tap to Click | On |
| Disable While Typing | On |
| Two-Finger Right Click | On |
| Saved Pointer Speed | +0.6; used only with System or Flat |

This curve starts near 0.01× gain for slow corrections and rises smoothly to
0.35× for fast swipes. Start at 0% and End at 100% spread the transition across
the entire graph, with no initial constant-gain region. Scrolling is tuned
separately to 0.10×. To reproduce this setup, keep Device scale at 1.00, set
Scroll Speed to 0.10, choose Custom, enter the four curve values, and press
**Apply & try**. Set the toggles as listed above.

The new Gestures screenshot shows **3 fingers**, **Swipe distance 200**,
**Workspace swipe** on, **Reverse direction** off, and **Trackpad Plus** selected
with **Swipe up for overview** on. To use that setup, first select **Test overview**,
then press **Apply gestures**. These gesture values apply across trackpads and
are separate from the per-device settings in the table.

## Install and upgrade

Requires Omarchy's Quickshell shell and Lua-based Hyprland configuration
(tested with Hyprland 0.56.2), Python 3, libinput with custom acceleration support
(`libinput.so.10`), `hyprctl`, and GNU `timeout` (coreutils). No elevated privileges
are required. Older Hyprland configurations using `.conf` syntax are unsupported.

```sh
omarchy plugin add https://github.com/davefano/omarchy-trackpad-plus.git --enable
```

For an unattended installation, append `--yes`. The plugin ID is
`davefano.trackpad-plus`. Settings initialize automatically on the first state
read. Newly discovered devices are listed without generating overrides until
you make their first edit. That edit applies all shown settings for that device;
initial values come from global defaults and recognized legacy sensitivity rules,
so review them if you already have custom per-device configuration.
Existing saved Trackpad Plus settings remain active.
The widget appears when a supported trackpad is detected or remembered.

To upgrade an installed Git-managed copy:

```sh
trackpad_plugin="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/davefano.trackpad-plus"
# Stop the companion while its current files are still installed.
if test -f "$trackpad_plugin/overview-control.py"; then
  python3 "$trackpad_plugin/overview-control.py" stop
fi
omarchy plugin update davefano.trackpad-plus
omarchy restart shell
```

Back up local plugin edits and the state paths below before updating; develop
in a separate checkout. Run these commands from your Hyprland desktop session.
If stopping the companion reports an ownership or compatibility error, resolve
that error before replacing its files. An upgrade starts no overview automatically;
your next explicit open starts the new version.

## Migrate from the original widget or local customization

The old IDs are `awkent01.touchpad` and `local.touchpads`. Keep only one trackpad
plugin enabled. First back up the installed plugin, shell layout, and state:

```sh
old_id=awkent01.touchpad  # Use local.touchpads for the earlier customization.
trackpad_backup="$HOME/.local/state/omarchy/backups/trackpad-plus-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$trackpad_backup"
cp -a "$HOME/.config/omarchy/plugins/$old_id" "$trackpad_backup/"
cp -a "$HOME/.config/omarchy/shell.json" "$trackpad_backup/shell.json"
trackpad_state="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy"
if test -d "$trackpad_state/local-touchpads"; then
  cp -a "$trackpad_state/local-touchpads" "$trackpad_backup/"
fi
if test -d "$trackpad_state/toggles/hypr"; then
  cp -a "$trackpad_state/toggles/hypr" "$trackpad_backup/hypr-toggles"
fi
omarchy plugin add https://github.com/davefano/omarchy-trackpad-plus.git --yes
omarchy plugin disable "$old_id"
omarchy plugin enable davefano.trackpad-plus --section right
omarchy restart shell
hyprctl reload
hyprctl configerrors
```

The old plugin remains installed for rollback. Do **not** delete the existing
state or generated Lua during migration. Trackpad Plus uses the same
`local-touchpads/settings.json` and `toggles/hypr/zz-local-touchpads.lua` paths,
including per-device curves and Restore previous. Recognized legacy per-device
sensitivity rules in `touchpad-settings.lua` are imported on first initialization.
Other device values initially inherit global touchpad defaults; arbitrary edits
from other configuration tools are not imported.

Update any keybindings or scripts that use the old IPC target. To roll back
without changing your current trackpad values:

```sh
omarchy plugin disable davefano.trackpad-plus
omarchy plugin enable "$old_id" --section right
omarchy restart shell
hyprctl reload
hyprctl configerrors
```

For an exact pre-migration restore, disable both plugins, restore the backed-up
plugin and `shell.json`, and copy the backed-up `local-touchpads` and
`hypr-toggles` files back to their original locations before restarting the shell
and reloading Hyprland. This also restores settings you changed after migration.
Keep the backup path printed by `echo "$trackpad_backup"`.

## IPC

```sh
omarchy-shell davefano.trackpad-plus open
omarchy-shell davefano.trackpad-plus close
omarchy-shell davefano.trackpad-plus toggle
omarchy-shell davefano.trackpad-plus show
omarchy-shell davefano.trackpad-plus hide
```

The separate overview has its own controller; these commands do not open the
trackpad settings panel:

```sh
trackpad_plugin="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/davefano.trackpad-plus"
python3 "$trackpad_plugin/overview-control.py" status
python3 "$trackpad_plugin/overview-control.py" start
python3 "$trackpad_plugin/overview-control.py" open
python3 "$trackpad_plugin/overview-control.py" close
python3 "$trackpad_plugin/overview-control.py" toggle
python3 "$trackpad_plugin/overview-control.py" stop
```

`start` launches an idle companion without capturing. `open` and `toggle` may
launch and display it. `status`, `close`, and `stop` never launch a missing
instance. The controller verifies the process, compositor session, and version
before sending commands. Its ownership record lives in a private
`$XDG_RUNTIME_DIR/trackpad-plus-overview-*` directory, separate from saved settings.
Use the controller rather than starting `overview/shell.qml` directly.

## Persistence and process behavior

Settings live under `$XDG_STATE_HOME` (default `~/.local/state`):

- `omarchy/local-touchpads/settings.json`: per-device values and previous profile.
- `omarchy/toggles/hypr/zz-local-touchpads.lua`: literal per-device rules loaded
  by Omarchy on Hyprland reload.

The historical filenames are intentional compatibility interfaces, independent
of the plugin ID. `trackpads.py` serializes operations with a file lock and
atomically replaces each file. Before a live edit, it records the previous state
in `local-touchpads/settings.pending.json`. Failed edits attempt rollback; if a
process is interrupted or rollback fails, the next read restores that snapshot.
An interrupted edit may therefore need to be applied again. Rolling back a
device's first edit uses `hyprctl reload config-only` to restore its original
configuration, without reloading monitors. Without a pending
edit, missing or inconsistent generated rules are rebuilt from saved JSON.
These files are not a single filesystem transaction; recovery requires writable
storage and a responding compositor.

State files must be regular files owned by your user, without hard links or
write access for other users. State directory paths must not contain symlinks
(the home directory itself is resolved first). Use an absolute `XDG_STATE_HOME`
pointing to real, privately writable directories. New files have mode 0600.
Unsupported state versions are rejected without migrating or overwriting them.

State reads have a 15-second deadline; writes have 10 seconds, followed by a
2-second forced-kill deadline. Timed-out actions release the queue and report an
error. Each compositor request also has a four-second deadline and a 1 MiB
output limit; lock acquisition has a two-second deadline. Consecutive slider
updates are combined and the pending action queue is limited to 128 entries.
Stale poll results are discarded after newer edits. The panel shows saved
settings; external configuration changes are not automatically imported.

Application-specific Hyprland `scroll_touchpad` window rules can override the
per-device scroll factor. If scrolling changes in some apps but not others,
check those rules in your configuration. The plugin does not rewrite window
rules or application settings.

## Development and testing

See [DEVELOPMENT.md](DEVELOPMENT.md) for the complete suite, architecture, and
live verification checklist. The [release safety review](docs/safety-review.md)
records tested failure cases and remaining compatibility limits. Report bugs through
[GitHub Issues](https://github.com/davefano/omarchy-trackpad-plus/issues).

## Removal

```sh
# If you enabled gesture management, first use Gestures → Restore original.
trackpad_plugin="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/davefano.trackpad-plus"
if test -f "$trackpad_plugin/overview-control.py"; then
  python3 "$trackpad_plugin/overview-control.py" stop
fi
omarchy plugin remove davefano.trackpad-plus
```

Stop the companion before removing or replacing its files. To roll back only
the overview, turn off **Swipe up for overview**, apply, and stop the companion;
pointer controls and native horizontal swipes remain available. To restore a
previous plugin version, stop this companion first, restore your backed-up plugin
and matching gesture configuration, and restart the shell. Keep the per-device
state backup for an exact rollback.

Removal preserves settings and generated device rules. To stop applying those
rules while retaining a recoverable copy, move `zz-local-touchpads.lua` outside
the `toggles/hypr` directory and run `hyprctl reload`. Keep `settings.json` to
reuse your values on reinstall. Remove it only if you want fresh defaults.

## License

[MIT](LICENSE). Copyright 2026 Andrew Kent and David Fano. The complete original
commit history and Andrew Kent's copyright notice are preserved.
