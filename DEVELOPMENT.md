# Developing Trackpad Plus

Clone the independent repository and work outside the installed plugin:

```sh
git clone https://github.com/davefano/omarchy-trackpad-plus.git
cd omarchy-trackpad-plus
```

`main` is the maintained release line. Preserve original Git history and MIT
notices. Contributions should describe the observable behavior and validation.
Do not commit runtime settings, private configuration, caches, or backups.

## Release versions

Use `YYYY.MM.DD.N`, starting with `2026.09.13.0`. The date is the release
date, with zero-padded month and day; `N` starts at 0 and increases for each
release on that date. Compare the four components numerically, not as text
(revision 10 follows revision 9). Never reuse a published version or move the
date backward.

Before publishing a release, update `version` in `manifest.json`. The main
widget reads that file for its footer, so there is only one version to update.
This release identifier is separate from the backend's settings schema version.

## Architecture

- `Panel.qml`: Omarchy bar widget, device selection, debounced action queue,
  deadlines, and rejection of stale reads.
- `CurveEditor.qml` / `Curve.js`: draft curve editing, spinners, presets, and
  target practice. Only Apply changes the live profile.
- `trackpads.py`: device discovery, validation, file locking, persistence, and
  per-device `hl.device` updates. The libinput validator creates configuration
  objects without opening devices. Keep its sampled curve in sync with Curve.js.
  Curve.js plots gain per normalized unit; the backend alone scales the native
  sample spacing by each device's resolution (sysfs name/IDs and the udev
  database, both readable without input permissions). Tests point
  `SYSFS_INPUT`/`UDEV_DATA` at temporary trees so host devices never leak in.
- `gestures.py` / `GestureEditor.qml`: global workspace gestures, explicit
  adoption of literal bindings in input.lua, marked-block persistence and
  compare-before-restore recovery. Uses the existing bounded subprocesses,
  secure file writes, and state lock; never mixes gestures into device settings.
  Managed block schema 7 opens the overview on upward gesture recognition;
  there is no upward finish callback to reopen or undo it. Downward finish
  retains cancellation handling. A scoped layer rule suppresses compositor fades
  for the built-in overview only. Schemas 2–6 remain readable and restorable; legacy overview maps to HyMission, and
  only an explicit edit upgrades the block. Provider detection must not start
  capture or replace the user's selection. HyMission's architecture guard applies
  only to that provider. Native horizontal bindings remain independent.
  Config resolution supports Stow file/directory links with bounded link traversal
  and directory ownership checks. Reads and atomic writes use the resolved target
  through the existing no-follow helpers, preserving the user's links. Gesture
  recovery journal version 2 records that target; recovery checks both its identity
  and expected contents. Version 1 journals remain recoverable for non-linked paths.
  Conflict scanning follows linked config directories, deduplicates directory
  cycles, and retains each file alias's Lua/conf extension. Missing non-config
  entries are skipped; missing Lua/conf entries and trust failures remain fatal.
  Recovery rechecks the target after reload before clearing its journal. Private
  state, backups, and journals retain their stricter no-symlink policy.
- `overview-control.py`: bounded, session-specific launcher and controller with
  a private runtime lock, process ownership checks, and versioned IPC handshake.
  Status/close/stop do not start a companion. A later close cancels older queued
  opens, and startup and IPC share one operation deadline. It imports secure file helpers from
  `trackpads.py` but never edits pointer or gesture settings.
- `overview/Session.qml` / `lock-watch.py`: authenticated control and fail-closed
  Wayland lock observation. Visibility is permission to build the view, not proof
  that a frame rendered. Unknown lock state closes or prevents opening.
- `overview/Model.js`: pure workspace filtering, stable window identities, and
  selection. Named and existing empty ordinary workspaces are included on the
  invocation monitor; special workspaces and hidden group members are excluded.
- `overview/shell.qml`, `Overview.qml`, `WindowCard.qml`, and `Preview.qml`: separate
  Quickshell application, compositor adapter, a scrolling workspace strip, six-window pages, and single-frame
  captures. At most two requests run concurrently; each has a two-second deadline.
  Only visible cards retain previews. Display dimensions do not bound compositor
  buffer allocation. No preview or title is written to disk, logs, or a network.
  `PreparedWallpaper.qml` retains the background while hidden and gates each
  opening on readiness or a fixed fallback deadline. The foreground view is
  destroyed on close, releasing its capture sources. The application reads theme
  colors without importing the bar's QML components.
- `Model.js`: numeric helpers and inherited legacy parsing utilities.
- `touchpad-state` / `touchpad-sensitivity`: inherited legacy CLI helpers,
  retained for compatibility; the current panel uses `trackpads.py` instead.

Fresh `apple` settings groups include Magic Trackpad interfaces and the exact
built-in names in `BUILTIN_APPLE` (Apple Silicon and Intel). Migration rekeys a
lone legacy built-in group without changing its settings, undo history, or Lua.
When another Apple group already exists, both groups stay separate, including
unconfigured groups; discovery routes each remembered name to its saved owner.
Multiple legacy groups also stay separate. No preference wins by dictionary order.
Refresh commits validated JSON before reconciling generated rules, so a newly
attached interface inherits a configured group's settings immediately; a failed
apply or rule write retries on the next read. Normal refresh applies only changed
groups; interrupted saves reconcile all configured rules from authoritative JSON.
The `dell` ID recognizes
one known Dell hardware name; other trackpads use their compositor device name.
New groups have `configured: false` until their first explicit edit; generated
Lua omits these groups. Missing `configured` means true for compatibility with
existing saved settings. Failed first edits restore the original configuration
with a config-only reload because reapplying an empty Lua block cannot remove a
runtime device override.
Scroll scale is per-group settings metadata. The backend stores the effective
`scroll_factor`, and the panel displays that value divided by `scroll_scale`.
Scale changes rescale the effective value in the same journaled transaction;
queued slider edits must commit under their old scale first. Schema 4 migration
adds scale metadata without changing effective factors or generated Lua.
The editor also uses that scale as its vertical gain limit. Saved curves stay in
absolute gain units; changing the axis does not rescale them. Newly selected
Mac-inspired presets fit the available range, and the backend applies the exact
validated curve supplied by the editor, including when restoring a preset.
Do not change saved group IDs or historical state paths without a migration.

## Complete automated suite

Run on an Omarchy host with Python 3, Node.js, libinput, Quickshell, Qt 6 Quick
Controls/Test, and Qt development tools (`qmllint`, `qmltestrunner`):

```sh
python3 test_trackpads.py
python3 test_gestures.py
node test-selection.js
node test-overview-model.js
python3 test_overview_control.py
python3 test_overview_ipc.py
python3 tools/overview-probe/test_lock_watch.py
python3 test_install.py
python3 test_ipc.py
python3 lint-qml.py
QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME=basic QT_QUICK_BACKEND=software QT_QUICK_CONTROLS_STYLE=Basic \
  /usr/lib/qt6/bin/qmltestrunner -input tst_curve.qml
QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME=basic QT_QUICK_BACKEND=software QT_QUICK_CONTROLS_STYLE=Basic \
  /usr/lib/qt6/bin/qmltestrunner -input tst_gestures.qml
QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME=basic QT_QUICK_BACKEND=software QT_QUICK_CONTROLS_STYLE=Basic \
  /usr/lib/qt6/bin/qmltestrunner -input tst_overview.qml
perl -c touchpad-state
bash -n touchpad-sensitivity
git diff --check
```

`lint-qml.py` runs qmllint over the root QML sources and recursively under
`overview/`, using the installed Omarchy modules. The disposable live-probe
fixtures under `tools/overview-probe/` are excluded. It fails on all errors and warnings except specifically identified
missing metadata for Omarchy's dynamic bar/style properties and Quickshell's
`QProcess::ExitStatus`. These host API limitations are listed by the checker;
new or unrelated warnings fail. Pure editor and model-view tests must lint
without warnings. IPC and live checks cover the installed host interfaces.

Installation tests copy Git-tracked files into temporary storage, use a fake
compositor, and verify initialization, device isolation, discovery, migration,
persistence, blocked locks, oversized compositor responses, and recovery after
terminating a live edit. Backend tests inject failures at each persistence file,
check recovery journals, reject unsafe state paths and future schemas, and
exercise repeated file/native operations for descriptor leaks. Stage new runtime source files
before running them. No real settings are changed by automated tests.

Node tests execute actual Panel.qml functions with controlled callback ordering.
The Qt tests exercise keyboard/mouse input, fine and Shift steps, typed Apply,
curve bounds, preview, undo, and layout. Python compares the JS curve with the
native payload and checks actual libinput acceptance, including rejection of
the former oversized 81-point payload. IPC tests use a separate offscreen shell
and temporary sockets to verify all five commands against Omarchy's base Panel.
Overview controller tests use isolated runtimes and fake processes to check
single-instance startup, ownership rejection, stale records, bounded failures,
and cleanup. Overview IPC tests run the actual Session component with an isolated
lock observer; they do not test GPU capture. The installed-copy test also verifies
all companion dependencies are tracked and non-starting commands remain idle.

## Live installation and release checks

Use the README's backup and migration procedure. For an unpublished checkout,
copy the tracked runtime files and manifest into a new user plugin directory
named `davefano.trackpad-plus`, validate it with `omarchy plugin validate`, and
rescan with `omarchy-shell shell rescanPlugins`. Disable the previous widget
before enabling this one. Never edit `/usr/share/omarchy`.

For a development overview test, run `python3 overview-control.py open` from the
checkout in your desktop session. Dismiss with Escape or `close`; use `status` to
inspect state and `stop` to end only that checkout's owned companion. Stop an
installed companion before testing a checkout in the same compositor session;
conflicting ownership must not be bypassed by deleting its record. Always stop
before replacing, moving, or deleting runtime files. There is no autostart service.

The overview remains experimental. The feasibility probe has verified capture,
inactive-workspace previews, focus release, and lock behavior on an M2 Linux
host with Hyprland 0.56.2, Quickshell 0.3.1, and Qt 6.11.2. Full integration checks
are separate; x86_64 rendering is not yet verified. Do not describe an IPC
handshake or successful QML import as rendering compatibility.

Before publishing:

1. Run the full suite and validate the manifest.
2. Check the bar icon, main panel, device selection, profiles, curve/spinners,
   and target practice. Multi-device isolation is also covered by fixtures.
3. Apply a reversible profile change, check the saved values and generated Lua,
   and restore the original settings, including the previous-profile record.
4. Restart the shell and reload Hyprland; verify persistence and
   `hyprctl configerrors`. Check `systemctl --user --failed`, `systemctl --failed`,
   and Quickshell logs for newly introduced failures.
5. Verify both provider paths and schema 2/3 restore preservation. Test the
   companion explicitly before enabling its gestures; ensure install/state reads
   do not start it. Confirm manual vertical bindings are not overwritten.
6. Exercise window and empty-workspace selection, paging, keyboard dismissal,
   native horizontal swipes, unavailable captures, and source-window removal.
   Test lock during capture, observer loss, unlock staying closed, and reopening.
   Confirm hidden capture counts are zero and normal input returns after killing
   only the companion. Review logs without recording window titles or pixels.
7. Measure cold/warm open time and resource retention over repeated open/close
   cycles, including large windows. Record tested hardware and limits before
   promoting the overview from experimental. The disposable probe's commands and
   evidence are in `tools/overview-probe/README.md`; interactive lock checks need
   someone present to unlock the desktop.
8. Keep a recovery copy of the previous plugin, shell layout, input.lua, and state.
   Stop the companion before any upgrade, rollback, or removal.

Check source diffs, images, license, and history before committing. Publish only
a verified clean tree. The upstream repository and the former personal fork
remain separate from this project's `origin`.

### M2 overview measurements (2026-09-14)

`python3 tools/overview-check/check.py --cycles 100` verified real terminal
previews on current and inactive workspaces, exact window selection, workspace
selection, close/focus restoration, and (in the original grid layout) dismissal after an external workspace
switch. Subsequent runs also checked a fullscreen terminal preview and forced companion
termination, observer cleanup, and a capture-free explicit restart. No images
were saved. The workload had two owned terminals plus the session's existing
windows across four ordinary workspaces on one monitor.

Cold open to a rendered preview: **337 ms**. First 30 warm opens: median
**172 ms**, p95 **202 ms**. Across 100 cycles, RSS was 157,168 KiB before,
167,712 KiB peak, and 157,072 KiB after; file descriptors were 44 before,
69 peak, and 43 after. These are observations on the M2/Hyprland 0.56.2 /
Quickshell 0.3.1 session, not a portability or performance guarantee. A release
performance envelope has not yet been agreed; the feature remains experimental.

For the initial schema-5 desktop-strip layout, alternating temporary builds (12 warm opens each)
measured median first-preview time of **174 ms** with the initial 30 ms capture
timer and **162 ms** with a coalesced next-event-turn kickoff. Both retain the
two-capture limit. These timings include controller/status IPC, can identify a
thumbnail as the first preview, and exclude physical gesture recognition and
completion of the 260 ms entrance animation. Gesture-start prewarming overlaps
the cold companion startup with finger movement; it never opens a view or
captures windows until a non-cancelled finish requests opening. Schema 6 replaces
that release-to-open behavior with opening on recognition, so the gesture's
remaining travel can overlap both startup and rendering. Its physical latency
has not been measured; the earlier prewarm measurements describe schema 5.

A subsequent 100-cycle run of the desktop-strip layout passed current, inactive,
and fullscreen fixture-pixel checks, one-click workspace entry, focus restoration,
and companion/observer teardown. Cold first preview was **308 ms**; the first 30
warm opens had a **174 ms** median and **184 ms** p95. RSS was 173,408 KiB before,
229,216 KiB peak, and 169,136 KiB settled; file descriptors were 49, 76, and 48.
This bounded run found no retained-capture or descriptor growth; it does not
establish a graphics-memory bound for arbitrary source windows.

The production lock check is interactive: `python3 tools/overview-check/check_lock.py`.
Run it only when ready to unlock the desktop after five seconds. It checks
capture teardown, rejected opens during lock and observer restart, no automatic
reopening after unlock, and audio-control responsiveness.

### Top workspace strip (2026-09-14)

The first strip iteration displayed one representative window per workspace.
The desktop composition now uses the current Omarchy wallpaper and up to three
windows in each thumbnail, with a count for the rest. At most seven intersecting
workspace tiles and six main-area window previews retain captures (27 total,
two pending at a time). Offscreen thumbnails unload their captures; lightweight workspace buttons
remain available for keyboard navigation. Clicking a workspace or window closes
the overview and enters it; the + tile also closes when creating a workspace.
Native workspace changes on the invocation monitor keep the overview open.
Escape restores the original focus only
when still on the original workspace. Switching focus to another monitor dismisses
instead of pulling that monitor's workspaces into the view.

The updated live harness checks actual current and inactive workspace thumbnail
pixels without activating the inactive workspace, verifies one-click workspace entry
and stays open across external workspace changes. It also verifies title/no-op
stability, fullscreen capture, exact window selection, and crash cleanup. A ten-cycle
M2 run of the first strip iteration recorded cold open 372 ms, warm median 201 ms, RSS settling to 156,608 KiB,
and 44 file descriptors after dismissal. These measurements remain experimental;
physical gesture feel and the full-view interactive lock check still need acceptance.
