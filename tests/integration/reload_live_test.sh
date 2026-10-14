#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "$0")/real_tmux_testlib.sh"

real_tmux_start_server
checkout_link="$TEST_TMP/tmux-binder"
ln -s "$REPO_ROOT" "$checkout_link"
real_tmux_source_file "$checkout_link/tmux-pane-tree.conf"
real_tmux set-option -g @tmux_sidebar_enabled 1
real_tmux set-option -g @tmux_sidebar_focus_on_open 0

window_id="$(real_tmux display-message -p -t work:editor '#{window_id}')"
content_pane="$(real_tmux display-message -p -t work:editor '#{pane_id}')"
content_pid="$(real_tmux display-message -p -t "$content_pane" '#{pane_pid}')"
printf -v ensure_command '%q %q %q' \
  "$checkout_link/scripts/features/sidebar/ensure-sidebar-pane.sh" "$content_pane" "$window_id"
real_tmux run-shell "$ensure_command"
sidebar_pane="$(real_tmux_wait_for_sidebar_pane "$window_id")"
real_tmux_wait_for_capture "$sidebar_pane" editor >/dev/null
sidebar_pid="$(real_tmux display-message -p -t "$sidebar_pane" '#{pane_pid}')"

real_tmux_run_shell_capture bash "$checkout_link/scripts/reload-live.sh"
real_tmux_wait_for_capture "$sidebar_pane" editor >/dev/null

assert_eq "$(real_tmux display-message -p -t "$content_pane" '#{pane_pid}')" "$content_pid"
assert_eq "$(real_tmux list-panes -t "$window_id" -F '#{pane_id}' | wc -l | tr -d ' ')" 2
new_sidebar_pid="$(real_tmux display-message -p -t "$sidebar_pane" '#{pane_pid}')"
[ "$new_sidebar_pid" != "$sidebar_pid" ] || fail 'sidebar process was not reloaded'
