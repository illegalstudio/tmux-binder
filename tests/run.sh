#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/scripts:$REPO_ROOT/tests/integration"
export PYTHONDONTWRITEBYTECODE=1
if [ "$#" -gt 0 ]; then
  for path in "$@"; do
    case "$path" in
      *.py) python3 "$path" ;;
      *.sh) bash "$path" ;;
      *) printf 'Unsupported test file: %s\n' "$path" >&2; exit 1 ;;
    esac
  done
else
  python3 -m unittest discover -s tests/unit -v
  for path in tests/integration/*_test.py; do
    python3 "$path" -v
  done
  for path in tests/integration/*_test.sh; do
    printf 'RUN %s\n' "$path"
    bash "$path"
  done
fi
