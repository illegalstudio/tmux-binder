# tmux-binder

[![Tests](https://github.com/illegalstudio/tmux-binder/actions/workflows/test.yml/badge.svg)](https://github.com/illegalstudio/tmux-binder/actions/workflows/test.yml)

An independently maintained fork of
[tmux-pane-tree](https://github.com/sandudorogan/tmux-pane-tree).

A persistent session tree on the left of every tmux window, with live status
badges for `claude`, `codex`, `cursor`, `opencode`, `pi`, and `kiro`.

```
  ┌─ Sidebar ──────────────┬────────────────────────────────┐
  │  work                  │$ claude                        │
  │   ▾ api                │                                │
  │     C claude         ❓│Working on your request...      │
  │     $ bash             │                                │
  │▶  ▸ jobs             ⏳│                                │
  │  ops                   │                                │
  │   ▸ logs               │                                │
  │                        │                                │
  │                        │                                │
  │= expand  / search      │                                │
  └────────────────────────┴────────────────────────────────┘
```

![tmux-pane-tree showcase](images/showcase.gif)

- **Tree view** of sessions, windows, and panes. `Enter` jumps to a pane.
- **Agent badges** update live from agent hooks.
- **Global visibility**: open or close once for every window and session.
- **One coordinator per server**: native events and shared state, with sleeping UI processes at idle.
- **Unicode search and prompts**, collapsible branches, and native context menus.
- **Manage sessions** from the sidebar: add, rename, close.

| Badge | Status           | Meaning                          |
| :---: | ---------------- | -------------------------------- |
| `⏳`  | running          | Agent is working                 |
| `↳⏳` | subagent-running | Agent is delegating to subagents |
| `❓`  | needs-input      | Waiting for permission or input  |
| `✅`  | done             | Finished                         |
| `❌`  | error            | Something went wrong             |

`needs-input` clears when you focus the pane. `done` stays until the next run.

## Install

Requires tmux 3.4+, Python 3.10+ with curses, and Bash. No pip packages are required.

**TPM**

```tmux
set -g @plugin 'illegalstudio/tmux-binder'
set -g @tmux_pane_tree_install_agent_hooks 1   # optional: wire agent hooks on load
```

Reload tmux, then `prefix + I`.

**Manual**

```bash
git clone https://github.com/illegalstudio/tmux-binder ~/.config/tmux/plugins/tmux-binder
```

```tmux
source-file ~/.config/tmux/plugins/tmux-binder/tmux-pane-tree.conf
```

To patch Claude Code, Codex, Cursor, OpenCode, Pi, and Kiro hook config after a manual install:

```bash
bash ~/.config/tmux/plugins/tmux-binder/scripts/features/hooks/install-agent-hooks.sh
```

The `tmux-pane-tree.conf` entry point, `@tmux_pane_tree_*` options and legacy
`@tmux_sidebar_*` options remain compatible with existing configurations.

## Usage

| Key          | Action                                        |
| ------------ | --------------------------------------------- |
| `prefix t`   | Toggle the sidebar                            |
| `prefix T`   | Focus the sidebar, or return to the main pane |

Inside the sidebar:

| Key                  | Action                            |
| -------------------- | --------------------------------- |
| `j` / `k`, arrows    | Move selection and preview live   |
| `gg` / `G`           | Top / bottom                      |
| `Ctrl+o` / `Ctrl+i`  | Jump list back / forward          |
| `Enter`              | Focus the selected content        |
| `+` / `=`            | Expand selected branch            |
| `-`                  | Collapse selected branch          |
| `aw` / `as`          | Add window / session (prompts)    |
| `rw` / `rs`          | Rename window / session           |
| `x`                  | Close item; confirm for headers   |
| `f`                  | Toggle pane filter                |
| `p`                  | Toggle hide-panes mode            |
| `Ctrl+l`             | Return focus to the main pane     |
| `q`                  | Close the sidebar                 |

Names are entered inside the sidebar. Ctrl+u clears the prompt and Escape
cancels it. Search and names accept Unicode. Use h/l or the left/right arrows
to collapse and expand window branches. Use / to search and n/N to move between matches.
Use m or right click to open the context menu. Collapsed branches survive reloads.
Context menus also provide splitting, zoom, marking and moving a pane to a new
window. Session menus can detach their clients.

Pane lists start collapsed, leaving sessions and windows visible. Select a window
and press `l`, Right, `+` or `=` to show its panes, or `h`, Left or `-` to hide them.
Collapsing from a pane returns to its window row. Session headings always remain
expanded, keeping every window reachable with j/k. Explicit expansions survive
navigation and reloads; new windows start collapsed. Collapsed windows retain their most urgent agent
badge, and search opens matching panes automatically.

The tree uses compact indentation and `▸` / `▾` branch indicators. Session
headings, a full-row selection highlight and muted guides follow your tmux theme.
Status badges stay aligned on the right, even when a long name is truncated.
A small key hint appears at the bottom while the sidebar has focus.

Moving through window and pane rows immediately shows their live window contents.
Session titles stay visible and are skipped by j/k, the up/down arrows and gg/G.
Keyboard focus stays in the sidebar, so you can continue browsing.
Enter or Ctrl+l focuses the selected content. Search, collapsed branches and
navigation history follow you across sessions and windows. Previewing a pane
keeps its attention badge until you actually focus it.

Window names remain stable while the sidebar has focus. Tmux's automatic naming
and custom name formats continue when you return to a content pane. Manually
assigned names are preserved.

Set `@tmux_pane_tree_live_preview` to `off` to browse without changing the visible
window until Enter. This also keeps the previous navigation through pane rows.

New windows and sessions insert next to the selected row. Closing the last
pane removes its window, and the last window removes its session.

The sidebar has one shared open/closed state. `prefix t` opens it in all
windows and sessions, including detached ones, or closes it everywhere.
New windows and sessions inherit that state. q and removing a sidebar pane also
close it globally. A failed sidebar process is restarted in place, preserving
content panes. The coordinator also reconnects after an unexpected exit.

The jump list starts from the pane you came from. The final `Ctrl+o` returns
you there. It resets when the sidebar loses focus.

## Configuration

Set options with `set -g` in your tmux config.

| Option                                    | Default | Description                                   |
| ----------------------------------------- | :-----: | --------------------------------------------- |
| `@tmux_pane_tree_width`                   |  `25`   | Sidebar column width                          |
| `@tmux_pane_tree_focus_on_open`           |   `1`   | Focus the sidebar when toggled open           |
| `@tmux_pane_tree_live_preview`            |  `on`   | Preview selected content while browsing       |
| `@tmux_pane_tree_toggle_key`              |   `t`   | Prefix key to toggle                          |
| `@tmux_pane_tree_focus_key`               |   `T`   | Prefix key to focus                           |
| `@tmux_pane_tree_session_order`           |    -    | Comma-separated session order, rest follow    |
| `@tmux_pane_tree_filter`                  |    -    | Comma-separated pane filter (see below)       |
| `@tmux_pane_tree_hide_panes`              |  `off`  | Show only sessions and windows                |
| `@tmux_pane_tree_collapse_panes`          |  `on`   | Start window branches with panes collapsed    |
| `@tmux_pane_tree_compact_single_panes`    |  `off`  | Fold single-pane windows into one row         |
| `@tmux_pane_tree_scrolloff`               |   `8`   | Rows kept visible around the cursor, `0` off  |
| `@tmux_pane_tree_icon_theme`              | `auto`  | `auto`, `ascii`, `unicode`, or `nerdfont`     |
| `@tmux_pane_tree_icon_<app>`              |    -    | Override one app icon                         |
| `@tmux_pane_tree_badge_<status>`          |    -    | Override one status badge                     |
| `@tmux_pane_tree_color_session`           |    -    | Session name color, tmux color or hex         |
| `@tmux_pane_tree_color_window`            |    -    | Window name color, tmux color or hex          |
| `@tmux_pane_tree_color_pane`              |    -    | Pane name color, tmux color or hex            |
| `@tmux_pane_tree_install_agent_hooks`     |   `0`   | Install agent hooks when the plugin loads     |
| `@tmux_pane_tree_<action>_shortcut`       |    -    | Override a sidebar shortcut (see below)       |

| Environment variable        | Description                                                  |
| --------------------------- | ------------------------------------------------------------ |
| `TMUX_PANE_TREE_STATE_DIR`  | State directory, default `$XDG_STATE_HOME/tmux-sidebar`      |
| `TMUX_PANE_TREE_PLUGIN_DIR` | Plugin root used by the example hooks                        |
| `TMUX_PANE_TREE_FONT_DIRS`  | Path-separated font directories checked by icon theme `auto` |

**Pane filter.** Case-insensitive match against pane command, title, and agent
metadata. `f` toggles it at runtime.

```tmux
set -g @tmux_pane_tree_filter "claude,codex,cursor,opencode"
```

**Compact single-pane windows.** Set `@tmux_pane_tree_compact_single_panes` to
`on` to show a window with one content pane as one selectable row. The row
retains the pane's agent status badge. The sidebar pane does not count, and
filtering does not turn a multi-pane window into a compact row.
Press `+` or `=` to reveal the pane row even in compact mode. Set
`@tmux_pane_tree_collapse_panes` to `off` to start ordinary windows expanded.

```tmux
set -g @tmux_pane_tree_compact_single_panes on
```

**Shortcuts.** Actions: `add_window` (`aw`), `add_session` (`as`), `go_top`
(`gg`), `go_bottom` (`G`), `jump_back` (`C-o`), `jump_forward` (`C-i`),
`rename_window` (`rw`), `rename_session` (`rs`), `toggle_filter` (`f`),
`close_pane` (`x`). If any shortcut is empty, duplicated, a prefix of another,
or uses the reserved `q`, all ten revert to defaults.

```tmux
set -g @tmux_pane_tree_close_pane_shortcut dd
```

**Icons.** `auto` picks `nerdfont` when a Nerd Font is installed on the tmux
host, else `ascii`. Installed is not the same as in use: if glyphs render
wrong, or the session is remote, set the theme explicitly. Badges follow the
theme. App ids for overrides: `claude`, `codex`, `opencode`, `cursor`, `pi`,
`kiro`, `shell`, `node`, `python`, `git`, `lazygit`, `yazi`, `ranger`, `bb`,
`cat`, `clojure`, `java`, `less`, `vim`, `ssh`, `pager`, `top`, `tmux`,
`unknown`.

```tmux
set -g @tmux_pane_tree_icon_theme ascii
set -g @tmux_pane_tree_icon_claude "A"
set -g @tmux_pane_tree_badge_running "*"
```

**Colors.** Unset colors derive from your tmux theme. Named colors, `colourN`
indices and hex values are supported. Selection uses `window-status-current-style`,
falling back to the active pane border accent. Inactive sidebars keep a muted
cursor. Terminals without 256-color support use a monochrome highlight.

**Sidebar width.** Drag its border or use tmux's pane resize commands. The
chosen width is saved and shared across existing and new windows, including
after closing and reopening the sidebar. It is also preserved when clients
with different terminal sizes share a session.
When a window is too narrow, the sidebar shrinks to leave room for every content
pane. The saved preferred width remains available for larger windows.

## Agent hooks

Agent wrappers parse their payload once and send a normalized event to the
coordinator. It orders updates, tracks subagents, saves one atomic state snapshot,
and publishes the resulting badges to every view.

Runtime data is isolated under `$STATE_DIR/servers/<socket-hash>-<server-pid>/`.
Preferences are stored separately by socket identity. Pane IDs from independent
tmux servers cannot overwrite each other.

`install-agent-hooks.sh` writes the wiring for every agent and keeps three
timestamped backups per file:

| Agent    | File                                          |
| -------- | --------------------------------------------- |
| Claude   | `~/.claude/settings.json`                     |
| Codex    | `~/.codex/config.toml` (`notify` line)        |
| Cursor   | `~/.cursor/hooks.json`                        |
| OpenCode | `~/.config/opencode/plugins/tmux-pane-tree.js`|
| Pi       | `~/.pi/agent/extensions/tmux-pane-tree.ts`    |
| Kiro     | `~/.kiro/agents/tmux-pane-tree.json`, `~/.kiro/settings/cli.json` |

It replaces the single `notify = [...]` line in `~/.codex/config.toml`, and
only rewrites Kiro's `chat.defaultAgent` when it is missing or still `kiro_default`.

**Manual wiring.** Copy from `examples/` and point each tool at its wrapper
under `scripts/features/hooks/`: Claude Code: `hook-claude.sh`, Codex:
`hook-codex.sh`, Cursor: `hook-cursor.sh`, OpenCode: `hook-opencode.sh`,
Pi: `hook-pi.sh`, Kiro: `hook-kiro.sh`.

Subagent completions are suppressed so only the parent session shows `done`.
For that to work:

- Claude Code needs `SessionStart`, `UserPromptSubmit`, `Stop`, `Notification`,
  `PermissionRequest`, `SessionEnd`, `SubagentStart`, `SubagentStop`.
- Cursor needs `sessionStart`, `sessionEnd`, `beforeSubmitPrompt`,
  `preToolUse`, `postToolUse`, `postToolUseFailure`, `subagentStart`,
  `subagentStop`, `afterAgentThought`, `afterAgentResponse`, `stop`.
  Cursor has no permission events, so `needs-input` comes from
  `postToolUseFailure` with `failure_type=permission_denied`.
- Codex suppression is best-effort via `permission_mode` and `session_id`.
  Without those markers the completion badge is kept.

Custom integrations call `update-pane-state.sh` directly with `--pane "$TMUX_PANE"`.

## Diagnostics and reload

Run from a tmux pane:

~~~bash
python3 /path/to/tmux-binder/scripts/binder.py doctor
~~~

This reports the coordinator PID, connected views, client count, tmux calls,
state directory and view recovery errors. `engine.log` in that state directory
contains bounded diagnostic logs. The coordinator has no network listener.

Configuration values update through native tmux events. Reload the plugin after
changing its tmux keybindings. To reload Python code from a development checkout:

~~~bash
bash scripts/reload-live.sh
~~~

This restarts the coordinator and sidebar processes, preserving content panes,
pane IDs and layouts. It does not install agent hooks. Hook installation is an
explicit action unless `@tmux_pane_tree_install_agent_hooks` is enabled.

To remove the plugin's agent hooks while preserving other integrations:

~~~bash
python3 scripts/binder.py install-hooks --remove
~~~

## Development

~~~
scripts/
  binder.py    command entrypoint
  binder/      coordinator, model, state, tmux adapter, agents and installer
  ui/          curses views and pure Unicode/input helpers
  features/    thin Bash compatibility entrypoints
tests/
  unit/        Python domain, protocol, UI and installer tests
  fixtures/    preserved agent event contracts
  integration/ isolated real tmux servers and PTY interaction
~~~

~~~bash
bash tests/run.sh
~~~

The suite requires tmux and runs isolated servers. It covers multiple clients and
servers, mouse resizing, zoom, shared visibility, rendered navigation, agent
ordering, recovery, Unicode prompts and native menus. It never uses the user's
tmux server. One file can be passed to the same runner.

For development, link the checkout into tmux's plugin directory and load the
configuration shown above. Move an existing plugin directory aside first.

~~~bash
mkdir -p ~/.config/tmux/plugins
ln -s "$PWD" ~/.config/tmux/plugins/tmux-binder
bash scripts/reload-live.sh
~~~

## License

MIT
