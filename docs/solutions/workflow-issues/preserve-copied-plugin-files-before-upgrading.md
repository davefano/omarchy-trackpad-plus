---
title: "Preserve copied plugin files before reconciling an installed Git checkout"
module: "Trackpad Plus installation and upgrades"
date: "2026-10-08"
problem_type: workflow_issue
component: development_workflow
severity: medium
category: workflow-issues
applies_when:
  - "An installed Git-managed plugin contains manually copied development files"
  - "The displayed release version differs from the installed checkout history"
tags: ["installation", "upgrades", "local-changes", "settings-preservation"]
---

# Preserve copied plugin files before reconciling an installed Git checkout

## Context

During the October 2026 Trackpad Plus release installation, the installed clone had an older Git HEAD but a newer manifest and runtime files. Its four modified tracked files and three untracked runtime files all byte-matched a known development commit. The displayed version therefore described the copied manifest, not the checkout's provenance. The footer reads the manifest directly ([Panel.qml](../../../Panel.qml), lines 17–24).

Earlier installation reports describe a locally installed custom build and a separately installed privileged helper (session history). Those reports do not establish the installed Git HEAD or how files were copied; the later inspection supplied that evidence.

The ordinary [upgrade procedure](../../../README.md#install-and-upgrade) covers backups and the native updater. This case needs an extra decision before updating: determine whether each dirty file is an already-incorporated development copy or an independent local change. We inspected and reconciled the checkout before invoking the updater; this was not a diagnosis based on a failed update attempt.

## Guidance

1. Inspect the installed clone separately from the development checkout. Record its Git HEAD, manifest version, tracked modifications, and untracked files. Do not treat the footer as proof of which commit is installed.
2. Make a private backup outside the plugin tree, including its Git metadata, shell layout, saved trackpad state, and generated Hyprland rules. Preserve any separately installed companion files being replaced. Verify copied regular files by hash and preserve symlinks. Use the README's [state paths and rollback guidance](../../../README.md#migrate-from-the-original-widget-or-local-customization).
3. Compare **every** modified tracked file and untracked runtime file byte-for-byte with the known development revision. Confirm that the intended release incorporates that content or its reviewed replacement. Preserve and reconcile unknown differences individually; a match for one file does not classify the others.
4. After classification and backup, stash only the identified copied files, including untracked ones. Keep the backup and stash for recovery. Avoid `reset --hard` or `clean -fd` as shortcuts in the installed clone. Do not blindly reapply the old stash over the release.
5. Stop the old overview companion while its matching files are still installed, then use the native updater and restart the shell as documented. Handle separately installed helpers or services through their own installation instructions.
6. Verify the installed HEAD against the intended release tag, the manifest, and the working tree. Compare effective per-device preferences and Restore previous state before and after migration; a changed schema number alone does not mean preferences changed. Compare generated device rules and shell layout, check Hyprland configuration errors, and verify the typing guard if it was restarted.

## Why This Matters

A copied manifest can make an old checkout look current. Erasing its dirty files may discard genuine local edits, while reapplying copied development files after upgrading can overwrite newer release code. Classification gives a reason to set specific files aside without assuming all local changes are disposable.

Source identity and saved-state preservation answer different questions. The release identifier is separate from the settings schema ([DEVELOPMENT.md](../../../DEVELOPMENT.md), lines 22–24), and migration may add metadata while retaining effective preferences ([README.md](../../../README.md), lines 422–427). Compare those preferences rather than requiring the entire settings file to remain byte-identical. Retain matching plugin and state backups for rollback.

## When to Apply

- A development build was copied into an existing Git-managed desktop installation.
- The version label appears current, but Git HEAD or the working tree indicates otherwise.
- An upgrade must preserve local edits, per-device preferences, and Restore previous state.

## Examples

Read-only inspection from the installed plugin directory:

```sh
git rev-parse HEAD
git status --short --untracked-files=all
cat manifest.json
```

In this installation, the classified files were `DEVELOPMENT.md`, `Panel.qml`, `README.md`, `manifest.json`, `PalmSettings.qml`, `palm-system.py`, and `palm.py`. All seven matched the known development revision. After a verified backup, we used a stash restricted to those paths and completed the native update. The installed HEAD then matched the intended release, the working tree was clean, effective preferences and Restore previous curves were preserved, and generated rules and shell layout remained byte-identical. The state schema advanced from 4 to 6. These are observations from this installation, not a guarantee that every future migration leaves generated files identical.

## Related

- [Install and upgrade](../../../README.md#install-and-upgrade): native update procedure and companion shutdown.
- [Release versions](../../../DEVELOPMENT.md#release-versions): manifest label and settings schema are separate.
- [Development installation guidance](../../../DEVELOPMENT.md), lines 245–251: copying files for unpublished builds.
- [Migration and rollback](../../../README.md#migrate-from-the-original-widget-or-local-customization): state paths and matching backups.
