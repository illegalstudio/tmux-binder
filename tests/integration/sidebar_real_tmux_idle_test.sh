#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/real_tmux_testlib.sh"
real_tmux_start_server
mkdir -p "$TEST_TMP/bin"
command_log="$TEST_TMP/sidebar-commands.log"
cat > "$TEST_TMP/bin/tmux" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$1" >> "$TMUX_BINDER_COMMAND_LOG"
exec "$REAL_TMUX_BIN" "$@"
WRAPPER
chmod +x "$TEST_TMP/bin/tmux"
real_tmux set-environment -g PATH "$TEST_TMP/bin:$PATH"
real_tmux set-environment -g TMUX_BINDER_COMMAND_LOG "$command_log"
real_tmux_source_plugin
real_tmux_attach_control_client_info work "$TEST_TMP/client.log"
real_tmux new-window -d -t work -n hidden
real_tmux set-option -g @tmux_pane_tree_focus_on_open 0
real_tmux_run_shell_capture bash "$REPO_ROOT/scripts/features/sidebar/toggle-sidebar.sh" >/dev/null
visible_sidebar="$(real_tmux_wait_for_sidebar_pane work:editor)"
hidden_sidebar="$(real_tmux_wait_for_sidebar_pane work:hidden)"
real_tmux_wait_for_capture "$hidden_sidebar" hidden >/dev/null
sleep 0.5
: > "$command_log"
sleep 3
assert_eq "$(wc -l < "$command_log" | tr -d ' ')" 0
real_tmux rename-window -t work:hidden updated-hidden
real_tmux_wait_for_capture "$hidden_sidebar" updated-hidden >/dev/null
real_tmux select-window -t work:updated-hidden
real_tmux_wait_for_capture "$hidden_sidebar" updated-hidden >/dev/null
sleep 0.5
: > "$command_log"
sleep 3
assert_eq "$(wc -l < "$command_log" | tr -d ' ')" 0
kill "$REAL_TMUX_CLIENT_PID" 2>/dev/null || true
printf 'Verified visible and hidden views issue no idle tmux queries and receive native updates.\n'
