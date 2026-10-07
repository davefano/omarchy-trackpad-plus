#!/usr/bin/env bash
# Run from any directory; every suite uses temporary state or an offscreen shell.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
check_scope=${1:-portable}
if [[ $# -gt 1 || ( $check_scope != portable && $check_scope != host ) ]]; then
  echo 'Usage: bash tools/check.sh [portable|host]' >&2
  exit 2
fi

run() {
  printf '\nRunning:'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

export PYTHONDONTWRITEBYTECODE=1
run python3 -m json.tool manifest.json /dev/null
run python3 test_trackpads.py
run python3 test_pointer_profiles.py
run python3 tools/macos/test_export_profile.py
run python3 test_palm.py
run python3 test_typing_guard.py
run python3 test_gestures.py
run node test-selection.js
run node test-overview-model.js
run python3 test_overview_control.py
run python3 tools/overview-probe/test_lock_watch.py
run python3 test_install.py
run perl -c touchpad-state
run bash -n touchpad-sensitivity
run bash -n tools/check.sh
run git diff --check

if [[ $check_scope == host ]]; then
  run omarchy plugin validate .
  run python3 test_overview_ipc.py
  run python3 test_ipc.py
  run python3 lint-qml.py
  for qml_test in tst_curve.qml tst_palm.qml tst_gestures.qml tst_overview.qml; do
    run env QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME=basic \
      QT_QUICK_BACKEND=software QT_QUICK_CONTROLS_STYLE=Basic \
      /usr/lib/qt6/bin/qmltestrunner -input "$qml_test"
  done
fi

printf '\nAll %s checks passed. Live desktop checks are separate.\n' "$check_scope"
