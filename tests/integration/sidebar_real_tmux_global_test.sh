#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "$0")/real_tmux_testlib.sh"

sidebar_count() {
  real_tmux list-panes -a -F '#{@tmux_sidebar_owned}' | awk '$0 == "1" { count++ } END { print count + 0 }'
}

wait_for_sidebar_count() {
  local expected="$1"
  local actual=""
  for _attempt in $(seq 1 100); do
    actual="$(sidebar_count)"
    [ "$actual" = "$expected" ] && return 0
    sleep 0.05
  done
  fail "expected $expected global sidebars, got $actual"
}

real_tmux_start_server
real_tmux_source_file "$REPO_ROOT/${TMUX_BINDER_TEST_CONFIG:-tmux-pane-tree.conf}"
real_tmux new-session -d -s other -n logs
real_tmux new-window -d -t work -n hidden
source_window="$(real_tmux display-message -p -t work:editor '#{window_id}')"
source_pane="$(real_tmux display-message -p -t work:editor '#{pane_id}')"
other_window="$(real_tmux display-message -p -t other:logs '#{window_id}')"
other_pane="$(real_tmux display-message -p -t other:logs '#{pane_id}')"
source_layout="$(real_tmux display-message -p -t "$source_pane" '#{window_layout}')"
other_layout="$(real_tmux display-message -p -t "$other_pane" '#{window_layout}')"
content_processes="$(real_tmux list-panes -a -F '#{pane_id}|#{pane_pid}')"

real_tmux_attach_control_client_info work "$TEST_TMP/work-client.log"
source_client_pid="$REAL_TMUX_CLIENT_PID"
real_tmux_attach_control_client_info other "$TEST_TMP/other-client.log"
other_client_pid="$REAL_TMUX_CLIENT_PID"
real_tmux set-option -g @tmux_pane_tree_focus_on_open 0
for _attempt in $(seq 1 100); do
  clients_before="$(real_tmux list-clients -F '#{client_pid}|#{session_name}|#{window_id}|#{pane_id}' | sort)"
  client_count="$(printf '%s\n' "$clients_before" | wc -l | tr -d ' ')"
  [ "$client_count" = 2 ] && break
  sleep 0.05
done
assert_eq "$client_count" 2

real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/sidebar/toggle-sidebar.sh" "$source_pane" "$source_window" >/dev/null
wait_for_sidebar_count 3
for window in "$source_window" "$other_window" work:hidden; do
  pane="$(real_tmux_wait_for_sidebar_pane "$window")"
  real_tmux_wait_for_capture "$pane" other >/dev/null
done
assert_eq "$(real_tmux list-clients -F '#{client_pid}|#{session_name}|#{window_id}|#{pane_id}' | sort)" "$clients_before"

real_tmux new-session -d -s future -n future
future_window="$(real_tmux display-message -p -t future '#{window_id}')"
future_sidebar="$(real_tmux_wait_for_sidebar_pane "$future_window")"
real_tmux_wait_for_capture "$future_sidebar" future >/dev/null
real_tmux new-window -d -t other -n future-window
new_sidebar="$(real_tmux_wait_for_sidebar_pane other:future-window)"
real_tmux_wait_for_capture "$new_sidebar" future-window >/dev/null
wait_for_sidebar_count 5

real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/sidebar/toggle-sidebar.sh" "$other_pane" "$other_window" >/dev/null
wait_for_sidebar_count 0
assert_eq "$(real_tmux show-options -gv @tmux_sidebar_enabled)" 0
assert_eq "$(real_tmux display-message -p -t "$source_pane" '#{window_layout}')" "$source_layout"
assert_eq "$(real_tmux display-message -p -t "$other_pane" '#{window_layout}')" "$other_layout"
for process in $content_processes; do
  assert_contains "$(real_tmux list-panes -a -F '#{pane_id}|#{pane_pid}')" "$process"
done

real_tmux new-window -d -t future: -n closed-window
sleep 0.2
assert_eq "$(sidebar_count)" 0
real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/sidebar/toggle-sidebar.sh" "$other_pane" "$other_window" >/dev/null
wait_for_sidebar_count 6
source_sidebar="$(real_tmux_wait_for_sidebar_pane "$source_window")"
real_tmux send-keys -t "$source_sidebar" q
wait_for_sidebar_count 0
assert_eq "$(real_tmux show-options -gv @tmux_sidebar_enabled)" 0

real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/sidebar/toggle-sidebar.sh" "$source_pane" "$source_window" >/dev/null
wait_for_sidebar_count 6
source_sidebar="$(real_tmux_wait_for_sidebar_pane "$source_window")"
real_tmux kill-pane -t "$source_sidebar"
wait_for_sidebar_count 0
assert_eq "$(real_tmux show-options -gv @tmux_sidebar_enabled)" 0

real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/sidebar/toggle-sidebar.sh" "$source_pane" "$source_window" >/dev/null
wait_for_sidebar_count 6
source_sidebar="$(real_tmux_wait_for_sidebar_pane "$source_window")"
sidebar_pid="$(real_tmux display-message -p -t "$source_sidebar" '#{pane_pid}')"
kill "$sidebar_pid"
for _attempt in $(seq 1 100); do
  recovered_pid="$(real_tmux display-message -p -t "$source_sidebar" '#{pane_pid}')"
  [ "$recovered_pid" != "$sidebar_pid" ] && break
  sleep 0.05
done
[ "$recovered_pid" != "$sidebar_pid" ] || fail 'failed view was not recovered'
wait_for_sidebar_count 6
real_tmux_wait_for_capture "$source_sidebar" work >/dev/null
real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/sidebar/close-sidebar.sh" >/dev/null
wait_for_sidebar_count 0

real_tmux set-option -g @tmux_pane_tree_focus_on_open 1
real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/sidebar/toggle-sidebar.sh" "$source_pane" "$source_window" >/dev/null
wait_for_sidebar_count 6
source_sidebar="$(real_tmux_wait_for_sidebar_pane "$source_window")"
clients="$(real_tmux list-clients -F '#{session_name}|#{pane_id}')"
assert_contains "$clients" "work|$source_sidebar"
assert_contains "$clients" "other|$other_pane"
real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/sidebar/close-sidebar.sh" >/dev/null
wait_for_sidebar_count 0

kill "$source_client_pid" "$other_client_pid" 2>/dev/null || true
printf 'Verified global opening, closing, future sessions/windows and unchanged content processes.\n'
