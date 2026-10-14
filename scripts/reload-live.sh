#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"

tmux source-file "$PLUGIN_ROOT/tmux-pane-tree.conf"
bash "$PLUGIN_ROOT/scripts/features/sidebar/reload-sidebar-panes.sh"
