#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/real_tmux_testlib.sh"
real_tmux_start_server
real_tmux_source_plugin
real_tmux_attach_control_client_info work "$TEST_TMP/client.log"
main="$(real_tmux display-message -p -t work '#{pane_id}')"
window="$(real_tmux display-message -p -t "$main" '#{window_id}')"
real_tmux set-option -p -t "$main" allow-set-title off
real_tmux select-pane -t "$main" -T claude
real_tmux set-option -g @tmux_pane_tree_focus_on_open 0
real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/sidebar/toggle-sidebar.sh" "$main" "$window" >/dev/null
sidebar="$(real_tmux_wait_for_sidebar_pane "$window")"
real_tmux_wait_for_capture "$sidebar" claude >/dev/null
real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/state/update-pane-state.sh" --pane "$main" --app claude --status needs-input >/dev/null
real_tmux_wait_for_capture "$sidebar" 'claude ❓' >/dev/null
real_tmux select-pane -t "$sidebar"
real_tmux select-pane -t "$main"
for _attempt in $(seq 1 100); do
  frame="$(real_tmux capture-pane -pt "$sidebar")"
  case "$frame" in *'❓'*) sleep .05 ;; *) break ;; esac
done
case "$frame" in *'❓'*) fail 'focusing the pane did not acknowledge input' ;; esac
real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/state/update-pane-state.sh" --pane "$main" --app claude --status done >/dev/null
real_tmux_wait_for_capture "$sidebar" 'claude ✅' >/dev/null
real_tmux select-pane -t "$sidebar"
real_tmux select-pane -t "$main"
real_tmux_wait_for_capture "$sidebar" 'claude ✅' >/dev/null
kill "$REAL_TMUX_CLIENT_PID" 2>/dev/null || true
printf 'Verified rendered agent badges, focus acknowledgement and retained completion state.\n'
