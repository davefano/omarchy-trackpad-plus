# Release safety review

## Current release scope — September 15, 2026

Runtime source: `d6d35e13026ff4af4a4b88722eadee2697870a85`, version
`2026.09.15.2`. The earlier review below is retained as historical evidence;
its test counts and description of the runtime do not cover the later gesture,
Stow, or overview work. Current commands, host versions, observed results, and
pending release checks are recorded in [MARKETPLACE.md](../MARKETPLACE.md).
This is a maintainer source review and test report, not an independent audit.

The complete host suite passed on an M2/aarch64 Omarchy 4.0.3 host: 154 Python
tests, both Node suites, all five inherited panel IPC commands, 43 Qt test
entries, lint of 12 QML sources, manifest validation, and legacy syntax checks.
The live overview harness passed actual preview rendering and lifecycle checks
over ten cycles. Those results do not establish x86_64 rendering compatibility
or physical gesture feel, and they do not replace the interactive lock test.

### Boundaries added since the original review

- **Gestures:** explicit Apply writes a marked block in the user's `input.lua`
  and reloads input configuration. The original file is backed up. Conflicting
  definitions are rejected. Restore original removes the managed block while
  preserving unrelated edits. Failure recovery checks the current content
  before replacing it; a concurrent manual edit can require manual recovery.
- **Stow:** editable config may resolve through user-owned symlinks. Atomic
  writes target the resolved regular file and preserve the links. The journal
  binds recovery to that resolved target; retargeting a link prevents replay.
  Private state, backups, and journals still reject symlinks and hard links.
- **Overview controller:** a separate user process starts only on explicit
  start/open/toggle actions. Process identity, version, compositor session, and
  token checks precede IPC mutations or termination. Runtime ownership records
  live in a private directory under `XDG_RUNTIME_DIR`; status/close/stop do not
  launch a missing companion. Stop it before replacing or removing its files.
- **Capture and lock state:** Quickshell captures static window previews in
  memory. The overview reads workspace/window metadata, including titles, and
  the current wallpaper. No plugin code writes previews or titles to files,
  telemetry, or network services. Hidden/locked views release capture sources;
  unknown lock state prevents opening. Real offscreen IPC tests exercise lock,
  unlock, and observer failure with a fake observer; the live lock test remains
  a distinct acceptance check.
- **Dependencies:** Python invokes bounded compositor and Quickshell commands.
  HyMission remains optional, separately installed, and subject to its own
  license and architecture limitations. The plugin does not install it.
  Following its README link leaves the plugin for the upstream instructions.

The inherited helpers remain shipped for compatibility and are included in
source review and syntax checks. The plugin runtime uses no privileged commands
or package installation. The CI workflow installs test dependencies on a
disposable GitHub runner; that is separate from plugin installation.

### Remaining limits

First edits apply all displayed settings for the selected device group, whose
initial values need not reflect arbitrary existing per-device customizations.
Previously configured groups can regenerate their saved overrides during state
refresh. Removal preserves those overrides unless the user follows the README
cleanup steps. Gesture restoration must precede removal to avoid leaving
bindings that refer to removed files.

The overview remains experimental. Preview display sizes do not bound the
compositor's source-buffer allocations, and ten live cycles do not establish
a long-term memory bound. No new claim about secret scanning across Git history
is made by this update. PR #12 and later runtime commits are outside these
results and require their own review and regression testing.

## Historical review — September 13, 2026

Reviewed on 2026-09-13, starting from `a95581a` (2.0.3). The fixes accompany
this document on `fix/release-safety-review`. This is a source review and
regression test pass, not an independent security certification.

## Findings addressed

| Area | Finding | Change |
| --- | --- | --- |
| First installation | Reading state generated device overrides from global defaults; a later reload could replace existing per-device customization without an edit. | Newly discovered devices produce no overrides until their first edit. Previously saved settings retain their behavior. |
| Interrupted edits | A terminated helper could leave live settings changed while both saved files still matched the previous state. | Write a recovery journal before applying; restore its snapshot on the next read. |
| Failed updates | A rejected or partially applied compositor request was outside the rollback handler. A failed disk rollback could also prevent live rollback. | Attempt rollback for apply errors, attempt disk and live restoration independently, preserve the original error, and retain the journal when recovery cannot finish. |
| State files | Reads followed symlinks and could block on FIFOs; parent directories were not pinned during writes. | Use descriptor-relative, no-follow operations, regular-file/ownership/link checks, bounded reads, private temporary files, and file/directory synchronization. |
| Compatibility | A future state version was silently rewritten as version 3. Malformed commands could cause initialization or migration before rejection. | Reject unsupported schemas and invalid commands before changing settings. Validate the complete migrated state, device names, settings, and undo records. |
| Resource use | Compositor output and direct CLI lock waits were unbounded. Repeated UI actions could grow the queue. | Cap responses at 1 MiB, requests at four seconds, lock acquisition at two seconds, and pending actions at 128. Combine consecutive scalar changes. |
| UI correctness | Encoded spaces broke backend paths, removed devices and undo records could remain stale, and a failed save could still display “Applied.” | Decode local paths, clear stale state, and display errors in the curve editor. |
| Curve rendering | Each of 161 plotted positions regenerated all 43 native samples. | Generate samples once per paint and reuse them without changing the plotted response. |
| Test artifacts | The QML test wrote a screenshot to a predictable shared temporary path. | Remove the unnecessary file write while keeping the rendering assertion. |

## Validation

- Python backend: 29 tests, including injection rejection, Apple grouping,
  device isolation, curve/undo migration, native libinput acceptance, and
  JS/Python sample equivalence.
- Filesystem failure tests: symlinks, parent symlinks, FIFOs, hard links,
  oversized input, private file permissions, failures at either persistence
  file, persistent disk failure, and failed rollback.
- Crash recovery tests: matching old JSON/Lua with a pending live edit, a crash
  after both files were written, and termination of an actual helper process
  after its fake compositor received an edit.
- Repeated file operations and native validation: 100 iterations with no
  increase in open file descriptors. Native configuration objects are destroyed
  in `finally`; subprocesses are killed and reaped on request failure.
- Installation: 11 tests against a fake compositor and temporary state,
  including first-run isolation, future-version preservation, process deadlines,
  excessive output, persistence, and recovery. The plugin path contains spaces.
- Node: actual panel functions exercised for callback ordering, stale reads,
  device selection, undo, queue limits, timeouts, and cached curve equivalence.
- IPC: all five commands against the installed Omarchy base Panel in a separate
  offscreen Quickshell instance.
- Qt: 11 passing entries, including initialization/cleanup, covering the real
  curve editor's keyboard, mouse, spinners, Apply, undo, bounds, and error status.
- `qmllint`: all three QML files pass the repository checker. It explicitly
  permits 76 existing diagnostics for missing Omarchy/Quickshell host metadata;
  the editor and its test have no warnings.
- Legacy Perl and Bash syntax, Git whitespace checks, and manifest inspection.
- Read-only host checks: current saved state passes validation without
  migration, generated Lua matches, actual custom curves pass native libinput
  validation, and one connected Apple group is discovered. Settings file hashes
  remain unchanged; no Hyprland config errors or failed system/user services.

## Privacy and trust boundaries

The current plugin runtime contains no telemetry, HTTP requests, credential
access, root commands, or package installation. It reads compositor device/options
data and its state, writes its own state and generated per-device Lua, and invokes
`hyprctl`. Failed first edits can require a config-only reload to remove their
runtime override. Generated Lua contains validated literals, without file reads
or execution of user-supplied code. The inherited helpers were also reviewed;
the current panel does not invoke them.

Common private-key and access-token patterns were absent from current tracked
source and reachable Git history. The shipped images were visually inspected.
Pattern scanning cannot identify every possible secret. This review also does
not prove the absence of every native-memory leak or defect in dependencies.

The plugin runs with the desktop user's permissions, not in a security sandbox.
These file checks prevent unsafe path handling; they cannot protect a session
already controlled by another process with the same user privileges.

## Compatibility limits and remaining release checks

- The updated branch has not been installed into the active desktop during this
  review. Before publishing these fixes, perform the development guide's live
  Apply/Restore, shell-restart, and Hyprland-reload checks with a settings backup.
- Automated compositor tests use fixtures. Physical testing here covers one
  built-in Apple trackpad; other hardware still needs hands-on coverage.
- First edits apply all values shown for the selected group, initially based on
  global defaults and recognized legacy sensitivity rules. Arbitrary existing
  per-device settings are not imported. All Apple interfaces share one group.
- Hyprland window rules and application behavior can override scrolling. The
  plugin does not rewrite those rules. New interfaces in an already configured
  group receive persisted rules on reload or the next explicit group edit.
- Saving two files and applying compositor state is not a single transaction.
  Recovery needs writable storage and a working compositor. A crash can discard
  the most recent unconfirmed edit. Sudden power loss and failing physical
  storage were not tested; process termination and write failures were simulated.
- Symlinked state directories and shared writable state files now fail closed.
  Use real, privately writable directories and an absolute `XDG_STATE_HOME`.
  If relocating state, ensure Omarchy loads the corresponding generated Lua.
- Tested host dependencies: Hyprland 0.56.2, libinput 1.31.3, Qt 6.11.2. macOS
  profiles were also tested with libinput 1.32.0 on Asahi Linux (MacBook Pro 14", M1 Pro).
  The plugin requires Lua-based Hyprland configuration and native custom-profile
  support. A successful API response cannot prove every hardware option took
  effect on every supported device.

## Addendum: macOS pointer profiles (2026-09-30)

Profiles are untrusted data. Each file in the profiles directory passes the same
private-file checks as state (regular file, owned by the user, no hard links, not
writable by others; the directory may be a Stow link through trusted directories),
is read up to 64 KiB, and is parsed against a strict schema with an exact key set and
bounded finite numbers; at most 32 files are listed. Only the converted, natively
validated `custom <step> <points>` literal reaches the generated Lua. Apply names a
file by its SHA-256, so a file changed after listing is refused rather than applied.
The listing runs as a bounded panel process, and the display query reuses the
existing `hyprctl` deadline and output limit. The runtime still never reads input
devices, runs as root, or uses the network; measuring an unknown trackpad's
resolution is a documented, one-time, read-only `sudo` command the user runs.

The macOS exporter is a separate tool run by the user on a Mac. It runs read-only
system commands (`ioreg`, `defaults`, `system_profiler`, `sw_vers`, and an
`osascript` display query) with a 30-second deadline and stores no serial numbers,
user names or other identifiers. The optional check uses AppKit's public touch API.

Original Git history and both MIT copyright notices remain intact.
