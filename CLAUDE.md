# tmux-binder development

A tmux plugin with one Python coordinator per server and one lightweight curses
view per sidebar pane. Bash files are compatibility launchers.

## Architecture

- sidebar.tmux is the executable TPM entrypoint. tmux-pane-tree.tmux is a
  no-op when executed by TPM. Both support tmux source-file.
- tmux-pane-tree.conf starts/configures the coordinator. sidebar.conf sources
  the same entrypoint for compatibility.
- scripts/binder.py dispatches commands through binder/cli.py.
- binder/runtime.py owns private Unix sockets, bootstrap locking, protocol
  versioning and atomic persistence. Runtime paths include socket hash and server
  PID. Socket-specific preferences survive server restarts.
- binder/tmux.py groups commands through source-file stdin with explicit quoting.
  Snapshot fields use byte-length prefixes, so names may contain separators,
  quotes, Unicode and newlines.
- binder/controller.py owns application state and mutations. Tmux owns the real
  topology. binder/model.py projects cached data into tree rows without I/O.
- binder/server.py serializes commands, coalesces notifications, broadcasts
  snapshots and processes render acknowledgements.
- Native tmux hooks signal a wait-for channel. The listener is an unattached
  command client, not a mutex or an attached control-mode client. Rearm it after
  any code path that drains pending notifications, including request dispatch.
- Title hooks deduplicate normalized titles in a pane option before signaling.
  Braille spinner frames are equivalent; entering or leaving that state is not.
  Use explicit regex members because Unicode ranges depend on server collation.
- binder/agents.py normalizes all six integrations and owns ordering,
  subagent lifecycle and detection. Codex terminal probes run centrally.
- binder/installer.py owns optional agent config changes and backups.
- UI code stays in scripts/ui/sidebar_ui_lib/. Views handle input, search,
  scroll, prompts and curses drawing. They do not query tmux or read agent files.
- scripts/features/ and examples/ preserve public shell paths as launchers.
  Do not put business logic in them.

## Invariants

- One coordinator per server, one owned sidebar per window, one global
  open/closed state. Linked windows have one view and multiple tree branches.
- Keep preferred width separate from constrained geometry. Automatic resizing,
  zoom and terminal changes must not overwrite user preferences.
  Compute the minimum content width from the complete layout, including split
  borders. Contain a no-space failure per window until its geometry changes.
- Use explicit client/session/window/pane IDs for actions. A shared tmux pane
  has one shared terminal, so its clients share the curses cursor.
- User close requests and removing a sidebar close all views. A process failure
  is recoverable and must preserve content panes.
- Guard automatic window naming before focusing a sidebar. Keep the original
  format and its local/inherited scope in coordinator state, restore it on close,
  and honor global format changes for inherited windows. The namespaced window
  format source is only for native tmux evaluation, without polling or subprocesses.
- Render acknowledgements prepare destination selection before navigation.
- Live preview transfers search, collapse, scroll and history to the destination
  view before switching the originating client. Monotonic input IDs prevent an
  older snapshot from replacing newer keyboard input. Coalesce previews per
  client, and never acknowledge agent attention until content receives focus.
  Vertical navigation and top/bottom jumps skip session titles; branch controls
  and explicit session actions can still select them.
- Window branches start collapsed. Persist explicit window expansions and transfer
  them with preview state so snapshots do not close branches the user opened.
  Retain pane rows in compact projections so expansion and search can reveal them.
  Acknowledged preview state must not override newer persisted state on view restart.
- Keep styling understated and integrated with tmux. Project structured labels,
  icons and badges separately from legacy text, reserve room for status badges,
  and keep one visual row per tree item. Render receipts include the content
  viewport height so mouse actions cannot target rows underneath footer hints.
  Clear the physical curses screen when visible row order or geometry changes;
  retain incremental row updates while the layout is stable.
- Keep @tmux_pane_tree_* options and legacy @tmux_sidebar_* compatibility at
  the boundary. New runtime state belongs in the coordinator, not flat tmux
  options or per-view IPC files.
- State and cleanup are scoped to the exact server generation. Never inspect
  another server's pane files to infer liveness.
- Never replay a mutation after an ambiguous disconnect. Read-only requests may
  reconnect once.

## Style

Use Python 3.10+, future annotations, typed signatures and pathlib. Keep pure
functions separate from I/O. Dataclasses and small stateful services are
appropriate when they own a lifecycle. Use the standard library.

Keep shell entrypoints executable, start them with Bash and set -euo pipefail,
quote expansions and use exec to launch Python. Avoid new shell libraries.

Report errors with context. Do not add alternate implementations or silent
fallback paths. Compatibility handling must be explicit.

## Testing and live development

Run bash tests/run.sh after changes. Add meaningful tests for new behavior.
Python unit tests cover domain transitions and projection. Real tmux tests cover
hooks, clients, geometry, lifecycle and rendering; a fake tmux cannot establish
those contracts. PTY tests exercise actual terminal input and native menus.

Every integration test must use a private socket and private state root, stop
its server/coordinator, and remove its own temporary directory on every exit.

Never test new UI or hooks in the user's sessions. Use isolated servers first.
For the symlinked development checkout, run bash scripts/reload-live.sh after
validation. It loads code and restarts views while preserving content panes.
Agent hook installation is separate and opt-in.
