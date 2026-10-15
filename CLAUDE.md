# tmux-binder development

A tmux plugin with one Python coordinator per server and one lightweight curses
view per sidebar pane. Bash files are compatibility launchers.

Requires tmux 3.5+ for allow-set-title and display-menu -M. CI builds the pinned
minimum and current tmux versions and tests each with Python 3.10 and 3.14.

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
  Coalesce native signals with a hidden server environment latch: repeated
  wait-for signals can cancel a pending wakeup when no listener is present.
  The native listener clears its latch only after consuming the signal, before
  Python reads state. Give each coordinator process its own channel so stale
  notifications from an earlier process cannot cancel a fresh wakeup.
- Title hooks deduplicate normalized titles in a pane option before signaling.
  Braille spinner frames are equivalent; entering or leaving that state is not.
  Use explicit regex members because Unicode ranges depend on server collation.
- binder/agents.py normalizes all seven integrations and owns ordering,
  subagent lifecycle and detection. Codex terminal probes run centrally.
  Grok uses native camelCase payloads and the grok-native command. Ignore child
  completions, stale turn-end reports and duplicate Claude/Cursor compatibility
  invocations. Preserve explicit completion/error across Grok idle notifications.
- binder/installer.py owns optional agent config changes and backups. Migrate only
  recognized Binder-owned handlers, preserve foreign hooks and Codex trust state,
  and keep symlinks/file permissions during atomic writes. Installation/removal
  can target selected agents. Codex uses native hooks.json lifecycle events;
  never bypass its user hook-trust review or replace an unrelated notify command.
  Cursor installs observational events rather than tool-permission handlers.
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
  Native window/client resize hooks retain automatic-resize intent across a
  coalesced size round trip. Never infer a manual resize from that result.
  Keep both resize intents through non-reconciling refreshes. An explicit pane
  resize with the same recorded final width takes priority over automatic intent;
  programmatic resizing must not become a manual preference.
  Focus and preview restore the preferred width of a previously scaled sidebar.
- Use explicit client/session/window/pane IDs for actions. A shared tmux pane
  has one shared terminal, so its clients share the curses cursor.
- User close requests and removing a sidebar close all views. A process failure
  is recoverable and must preserve content panes.
  Content-pane exits are different: remove newly sidebar-only windows and move
  affected clients to live content before destroying a session. Skip existing
  sidebar-only destinations and detach when no live content remains. Never
  remove historical sidebar-only windows as collateral cleanup.
  Retain pending content exits across every topology snapshot, including reads
  after sidebar creation and geometry reconciliation, until cleanup completes.
  An explicit client entry into a historical sidebar-only window restores one
  working shell using its session_path. Do not respawn a newly exited working
  pane or recover other detached sessions; project selectors must retain their
  chosen session and directory.
- Guard automatic window naming before focusing a sidebar. Keep the original
  format and its local/inherited scope in coordinator state, restore it on close,
  and honor global format changes for inherited windows. The namespaced window
  format source is only for native tmux evaluation, without polling or subprocesses.
- Render acknowledgements prepare destination selection before navigation.
- Live preview transfers search, collapse, scroll and history to the destination
  view before switching the originating client. Monotonic input IDs prevent an
  older snapshot from replacing newer keyboard input. Coalesce previews per
  client, and never acknowledge agent attention until content receives focus.
  Reduce relative navigation inputs against one coordinator cursor per client
  before coalescing previews. Keep the pure reducer with the UI navigation code.
  Cancel obsolete handoffs as soon as a newer input arrives. Per-view input
  acknowledgements order deferred actions after movement; never replay deferred
  actions after reconnecting. Branch keys and top/bottom jumps use the same stream.
  Vertical navigation and top/bottom jumps skip session titles; branch controls
  only expand/collapse windows. Session headings stay expanded and explicit
  session actions can still select them. Drop legacy session collapses when
  restoring view state or accepting a preview handoff.
  Default rename keys are r for the selected window and R for its session.
  Both old rw/rs bindings remain configurable explicitly without prefix conflicts.
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
- Every window carries an agent summary, collapsed or expanded, over all of its
  content panes, including filtered pane rows. Count panes per status, never
  subagents or sidebar views. Keep input/error priority, per-pane detail and
  custom icon/badge overrides. Use the same monochrome status symbols for every
  icon theme and identify agents by name without default icons.
  Project structured status runs so the renderer colors each group separately,
  reserves right-aligned space and clips between groups in narrow panes. Preserve
  the native selection background while retaining semantic badge colors, without
  reversing the badge foreground. Include view dimensions in publication
  context so resizing wakes sleeping views without polling.
- Hook environment pane IDs can be stale under a shared agent daemon. Route by
  an explicit payload pane, a remembered agent session binding, or a unique
  workspace match among content panes of the same app. Never resolve ambiguity
  by the active pane or lowest ID. Preserve the no-tmux behavior for cwd-only
  hook payloads outside tmux.
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

## Releases

`make release` delegates through scripts/release.sh to the standalone stdlib
Python release utility in scripts/release.py. Keep release tooling separate
from the tmux runtime. Release from a clean main checkout matching origin/main;
consider local and remote canonical semver tags, recheck after confirmation,
create an annotated tag at the displayed commit and push only that tag.
Never delete or retry a tag automatically after an ambiguous push failure.
Use `make release-dry-run` for inspection. Test release mutations only in
temporary Git repositories with bare local remotes, never against origin.
The tag-triggered GitHub workflow reuses the complete test matrix before creating
a source-only GitHub Release. TPM keeps following main.
