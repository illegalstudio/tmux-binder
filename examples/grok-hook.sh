#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
PLUGIN_DIR="${TMUX_PANE_TREE_PLUGIN_DIR:-${TMUX_SIDEBAR_PLUGIN_DIR:-$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)}}"
exec python3 "$PLUGIN_DIR/scripts/binder.py" hook grok "$@"
