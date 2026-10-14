# Changelog

All notable project versions are documented here.

## Unreleased

- Rebuilt the runtime around one Python coordinator per tmux server. Native hooks,
  cached topology and push updates replace repeated shell pipelines and per-view
  tmux polling. Bash entrypoints and existing configuration options stay compatible.
- Open and close the sidebar globally across all sessions and windows. New windows
  inherit visibility, and linked windows share one sidebar pane.
- Preserve the chosen width across windows, clients, zoom and reloads. Clamp it to
  the minimum space needed by the complete content layout without changing the
  saved preference.
- Recover failed views and reconnect after coordinator failure while keeping
  content processes. Isolate runtime state by server and server generation.
- Add Unicode search and name prompts, persistent collapsed branches, terminal-cell
  clipping and incremental curses drawing. Keep native context menus and layout
  actions, with explicit client and row targets.
- Normalize all six agent integrations centrally. Preserve subagent tracking,
  completion suppression, focus acknowledgement and live command/title detection,
  including Claude version commands and idle titles. Ignore cosmetic spinner
  frames in title notifications.
- Move agent-hook installation and removal into Python, preserve unrelated hooks
  and retain the three latest backups per configuration file.
- Add coordinator diagnostics, bounded logs, real tmux/PTY regressions and an idle
  benchmark. Require Python 3.10+ with curses and tmux 3.4+; no pip packages.

## 0.3.3

- Fixed macOS+fish bug where Ctrl-b t stacked new sidebars because detection only matched `python` as `pane_current_command`; allowlist now covers common shells while still excluding agent CLIs like codex/cursor.
- Hardened the live-tmux integration test helper against CI flakes by widening the run-shell poll budget and dropping login-shell profile loading.
- Release commit `c4ab2b4`.

## 0.3.2

- Added built-in Pi and Kiro hook installers, example hook scripts, parser support, and sidebar icons/status badges.
- Documented the Pi and Kiro hook setup and expanded runtime coverage for the new examples and installers.
- Persisted sidebar width across sessions so reopened sidebars keep the user-selected width.
- Fixed a recursive sidebar spawning race when pane selection re-entered sidebar creation.
- Release commit `fa24093`.

## 0.3.1

- Follow-up fixes for the subagent detection and suppression work.
- Tagged on commit `aa1e7f3`.

## 0.3.0

- Added the initial subagent detection and shared suppression state.
- Tagged on commit `199de73`.

## 0.2.0

- Landed Nerd Font icon and badge support for the sidebar UI.
- Tagged on commit `3631ceb`.

## 0.1.0

- Initial project release.
- Tagged on commit `73714a4`.
