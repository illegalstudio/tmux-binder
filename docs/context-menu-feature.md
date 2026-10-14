# Context menus

Press m or right click a sidebar row to open a native tmux menu. The menu uses
the same selected row, client and application commands as keyboard navigation.
Names are entered in the sidebar; closing an item asks for confirmation there.

Session menus support selection, renaming and detaching. Window and pane menus
also support splits; pane menus provide zoom, break-to-window and mark actions.
Every menu includes new window, new session, refresh and global sidebar close.

## Request flow

1. The curses view publishes rendered row keys, scroll position and revision to
   the coordinator. These maps stay in memory.
2. The root mouse bindings recognize the explicit pane ownership option
   and send the mouse row, pane ID and originating client ID after button release.
   The m key sends the selected row key directly.
3. The coordinator resolves the current row and starts display-menu -M for that
   explicit client. Its event loop continues serving other views.
4. Menu callbacks send an action with stable IDs through the private socket.
   Input and confirmation actions return to the view; layout actions execute in
   the coordinator. Stale targets produce an error rather than acting on a
   different row.

The implementation uses no shared menu command file, row-map file, render receipt
file or signal broadcast. The existing content-pane mouse binding is preserved.
When migrating from the old plugin, the known legacy sidebar wrapper is removed
from that saved binding.

## Implementation

- scripts/binder/controller.py: bindings, menu construction and layout actions.
- scripts/binder/server.py: command routing and in-memory render maps.
- scripts/ui/sidebar_ui_lib/application.py: keyboard/mouse input and prompts.
- scripts/features/context-menu/: compatible shell entrypoints.

Tmux 3.4+ is required for display-menu -M. Opening after the initial button
release prevents that release from dismissing the menu. Keyboard-opened menus
accept mouse input too.

The PTY integration test exercises native menus with keyboard and mouse callbacks. Coordinator
integration tests cover split, zoom, mark, break-to-window and legacy binding
migration on real isolated tmux servers.
