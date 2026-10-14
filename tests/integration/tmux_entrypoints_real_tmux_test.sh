#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/real_tmux_testlib.sh"
real_tmux_start_server
previous_hooks=""
for entrypoint in "$REPO_ROOT"/*.tmux "$REPO_ROOT/tmux-pane-tree.conf" "$REPO_ROOT/sidebar.conf"; do
  real_tmux_source_file "$entrypoint"
  real_tmux_run_shell_capture python3 "$REPO_ROOT/scripts/binder.py" configure >/dev/null
  assert_eq "$(real_tmux show-options -gqv @tmux_pane_tree_dir)" "$REPO_ROOT"
  bindings="$(real_tmux list-keys -T prefix)"
  assert_contains "$bindings" 'binder.py toggle'
  assert_contains "$bindings" 'binder.py focus'
  hooks="$({ real_tmux show-hooks -g; real_tmux show-hooks -gw; } | grep -F 'tmux-binder-events')"
  count="$(printf '%s\n' "$hooks" | wc -l | tr -d ' ')"
  [ "$count" -ge 20 ] || fail "expected native event hooks, got $count"
  if [ -n "$previous_hooks" ]; then
    assert_eq "$hooks" "$previous_hooks"
  fi
  previous_hooks="$hooks"
done
for entrypoint in "$REPO_ROOT"/*.tmux; do
  [ -x "$entrypoint" ] || fail "TPM entrypoint is not executable: $entrypoint"
  real_tmux_run_shell_capture "$entrypoint" >/dev/null
done
printf 'Verified TPM, primary and legacy configuration entrypoints and idempotent native hooks.\n'
