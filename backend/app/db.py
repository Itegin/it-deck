import os
import sqlite3
from pathlib import Path

# Default matches the Docker bind mount (./data:/app/data) unchanged. The
# standalone launcher points this at %LOCALAPPDATA%\IT-Deck instead, since
# there's no /app there.
DB_PATH = Path(os.environ.get("ITDECK_DATA_DIR", "/app/data")) / "controlhub.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # sqlite3 defaults FK enforcement to off per-connection; item.workspace_id's
    # ON DELETE CASCADE only fires if this is set on every connection that writes.
    conn.execute("PRAGMA foreign_keys = ON")
    # NORMAL only fsyncs at WAL checkpoints, not every commit; safe under WAL
    # (survives app crashes) and far faster than FULL for a local single-user
    # app. Per connection like foreign_keys: set once in init_db() it only
    # ever applied to that one connection.
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        # WAL lets the UI (reader) and agent actions (writer) hit the db at the
        # same time instead of blocking each other behind sqlite's default lock.
        # Persistent: stored in the database file, so once is enough.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspace (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                position INTEGER NOT NULL,
                grid_cols INTEGER NOT NULL DEFAULT 3,
                grid_rows INTEGER NOT NULL DEFAULT 5
            );

            CREATE TABLE IF NOT EXISTS item (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
                row INTEGER NOT NULL,
                col INTEGER NOT NULL,
                width INTEGER DEFAULT 1,
                height INTEGER DEFAULT 1,
                label TEXT NOT NULL,
                icon TEXT,
                color TEXT DEFAULT '#2a2f38',
                kind TEXT NOT NULL,
                type TEXT NOT NULL,
                target TEXT NOT NULL DEFAULT 'windows',
                params TEXT NOT NULL DEFAULT '{}',
                state_key TEXT,
                press_count INTEGER NOT NULL DEFAULT 0,
                last_pressed TEXT
            );

            -- App-wide key/value settings, as opposed to the per-workspace
            -- and per-item columns above. Deliberately not a column on
            -- `workspace`: the first entry is the Dashboard's visual theme,
            -- which is one choice shared by every panel pointed at this
            -- backend -- putting it on a workspace row would make the deck
            -- you switch to silently change how the app looks.
            --
            -- Rows are created on first write (see api/settings.py's UPSERT),
            -- so there is no seed and no fixup for this table: an absent row
            -- means "never set", and each reader supplies its own default.
            CREATE TABLE IF NOT EXISTS setting (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- Which one-shot fixups this database has already had. The
            -- insert-type fixups used to decide by looking for their own
            -- label, so a seeded tile the user deleted or renamed came back
            -- on the next start (see _run_once below). Additive: an older
            -- build ignores the table, and a database without it gets it
            -- here on first start.
            CREATE TABLE IF NOT EXISTS schema_migration (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        # The quick-launch bar (v0.5.0). A schema step, not a value fixup: it
        # is keyed on the column being absent, so it runs exactly once per
        # database and can never undo a Studio edit. A dock item is a 1x1
        # action outside the grid; its `col` is its position in the bar and
        # its `row` is always 0. Every existing row gets 0 -- in the grid --
        # so an upgraded deck looks exactly as it did.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(item)")}
        if "dock" not in columns:
            conn.execute("ALTER TABLE item ADD COLUMN dock INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    finally:
        conn.close()


def _already_applied(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM schema_migration WHERE name = ?", (name,)).fetchone() is not None


def _mark_applied(conn: sqlite3.Connection, name: str) -> None:
    # Same connection and transaction as the fixup's own writes, committed
    # together by the caller: a crash between the two can't leave a fixup
    # recorded as done that never ran, or run twice.
    conn.execute("INSERT OR IGNORE INTO schema_migration (name) VALUES (?)", (name,))


def seed_if_empty() -> None:
    conn = get_connection()
    try:
        (workspace_count,) = conn.execute("SELECT COUNT(*) FROM workspace").fetchone()
        if workspace_count > 0:
            return

        cur = conn.execute(
            "INSERT INTO workspace (name, position, grid_cols, grid_rows) VALUES (?, ?, ?, ?)",
            ("Home", 0, 3, 5),
        )
        workspace_id = cur.lastrowid

        # row, col, label, icon, color, kind, type -- just enough to render a grid on day 1.
        # No placeholder rows here (an earlier version seeded Lights/Spotify/
        # Sleep PC with types no handler ever implemented) -- a fresh install
        # should only ever show tiles that actually do something. See
        # fixup_remove_placeholder_tiles() for the equivalent cleanup on an
        # already-seeded DB.
        fake_items = [
            (1, 0, "Terminal", "terminal", "#2a2f38", "action", "launch"),
            (1, 1, "Camera", "camera", "#e0575b", "action", "toggle"),
            (1, 2, "Volume", "speaker", "#8e5ff5", "action", "run"),
        ]
        conn.executemany(
            """
            INSERT INTO item (workspace_id, row, col, label, icon, color, kind, type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(workspace_id, *item) for item in fake_items],
        )
        conn.commit()
    finally:
        conn.close()


# What the 'Terminal' tile launches. Windows Terminal, not Notepad: the tile
# is called Terminal, and a tile named Terminal that opens a text editor is
# not a feature anyone wants twice. `wt.exe` bare rather than an absolute
# path, because Windows Terminal lives behind a per-user Store execution alias
# in %LOCALAPPDATA%\Microsoft\WindowsApps -- baking one user's path into a
# seeded row would break for every other user. CreateProcess searches PATH,
# which is where that alias directory already is (verified: launches via the
# agent's detached path in 0.06s).
#
# fallback_path covers a machine with no Windows Terminal installed (plain
# Windows 10); see handle_launch_app.
# process_name is spelled out rather than left for handle_force_stop to
# derive: it falls back to os.path.basename(path), which would give "wt.exe",
# and no running process is ever called that -- `wt.exe` is a launcher alias
# and the process it starts is WindowsTerminal.exe. Long-press -> Force Stop
# would have matched nothing and still reported "ok". (It names the wt.exe
# case only; a machine falling back to powershell.exe gets a Force Stop that
# matches nothing, which is the same as before and better than killing every
# PowerShell on the box.)
TERMINAL_PARAMS = (
    '{"path":"wt.exe","fallback_path":"powershell.exe","process_name":"WindowsTerminal.exe"}'
)

# Every value this tile has shipped with, so the fixup below can tell "still
# on a default nobody touched" from "the user chose this" -- see there. Add to
# this list rather than replacing it whenever TERMINAL_PARAMS changes, or the
# next change silently stops upgrading installs that took the previous one.
_DEFAULT_TERMINAL_PARAMS = (
    '{"path":"notepad.exe"}',
    '{"path":"wt.exe","fallback_path":"powershell.exe"}',
)


def fixup_legacy_seed() -> None:
    # Not a one-time migration: seed_if_empty only fires once per fresh db, so
    # early installs may already have a 'Terminal' row stuck with the old
    # placeholder type/params. Re-running this on every startup is the simplest
    # way to backfill those installs; it's a no-op once the row already
    # matches, so it's safe to keep calling forever.
    #
    # The params UPDATE is now guarded on the old value instead of firing
    # unconditionally. That matters twice over: it stops this from reverting a
    # Terminal tile someone repointed in Studio (the always-on-reapply bug
    # fixup_volume_item already had to be fixed out of), and it means moving
    # the default from Notepad to Windows Terminal upgrades the installs that
    # never touched it without overwriting the ones that did.
    #
    # The type UPDATE needed the same treatment and took longer to get it.
    # `type <> 'launch_app'` reads like a guard and is not one: it is "every
    # Terminal row that is not already what I want", which is the unguarded
    # case wearing a disguise. Replacing this tile with the clock widget in
    # Studio and leaving its label alone left a row at kind='widget',
    # type='clock_weather' -- and one restart later this statement had put it
    # back to type='launch_app'. The deck then has a widget row with a type
    # no widget is registered for, so mountWidget() declines it and the tile
    # falls back to its icon and label: the Terminal tile, apparently back
    # from the dead. Reproduced against a scratch DB, which is also where the
    # `kind = 'action'` clause below comes from -- see fixup_widget_types().
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE item SET type = 'launch_app'
            WHERE label = 'Terminal' AND kind = 'action' AND type = 'launch'
            """
        )
        placeholders = ", ".join("?" for _ in _DEFAULT_TERMINAL_PARAMS)
        conn.execute(
            f"""
            UPDATE item
            SET params = ?
            WHERE label = 'Terminal' AND kind = 'action'
              AND (params IS NULL OR params = '' OR params = '{{}}'
                   OR params IN ({placeholders}))
            """,
            (TERMINAL_PARAMS, *_DEFAULT_TERMINAL_PARAMS),
        )
        conn.commit()
    finally:
        conn.close()


def fixup_remove_placeholder_tiles() -> None:
    # seed_if_empty() used to seed Lights/Spotify/Sleep PC with types
    # (toggle/launch/run) that no handler was ever registered for -- pressing
    # any of them just returns "unknown command". Deleting them from
    # seed_if_empty only stops NEW installs from getting them; an
    # already-seeded DB (this project's own dev DB, Athlon's, anyone who
    # installed before this fixup existed) keeps them forever otherwise,
    # and nobody deletes three dead tiles by hand. Plain DELETE, not an
    # UPDATE-based rename like fixup_mic_item() -- there's no real feature
    # to migrate these into, they're just gone.
    conn = get_connection()
    try:
        # Guarded on the placeholder's own dead type as well as its label.
        # Label alone deleted any tile a user named "Spotify" -- a shipped
        # website preset and logo -- on every single start.
        conn.execute(
            """
            DELETE FROM item
            WHERE (label = 'Lights' AND type = 'toggle')
               OR (label = 'Spotify' AND type = 'launch')
               OR (label = 'Sleep PC' AND type = 'run')
            """
        )
        conn.commit()
    finally:
        conn.close()


def fixup_mic_item() -> None:
    # Same idempotent backfill approach as fixup_legacy_seed(): converts the
    # placeholder 'Camera' row left by seed_if_empty into the real mic mute
    # toggle action. No-op once the row already matches, safe every startup.
    # Guarded on the placeholder's type ('toggle', which no handler serves):
    # on the label alone, any action tile a user named "Camera" was turned
    # into a mic button on the next start.
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE item
            SET label = 'Mic',
                type = 'audio_mute_toggle',
                target = 'windows',
                params = '{"device":"microphone","active_style":"alert"}',
                state_key = 'mic.muted'
            WHERE label = 'Camera' AND kind = 'action' AND type = 'toggle'
            """
        )
        # The UPDATE above only ever matches once -- the label flips away
        # from 'Camera' on the first run, so installs that already migrated
        # past it (before active_style existed) would never pick it up from
        # that statement alone. Backfill it separately, keyed on the label
        # the row actually settles into.
        #
        # Both backfills are guarded on the values they are upgrading *from*.
        # Unguarded, `WHERE label = 'Mic'` matched on every single startup and
        # reapplied these columns forever -- silently reverting any params or
        # icon a user had set on this tile in Studio. That is the exact
        # always-on-reapply bug fixup_volume_item documents having been fixed
        # out of, and fixup_legacy_seed's Terminal params after it; this row
        # was simply missed. Guarding it means an untouched install still
        # upgrades and an edited one is left alone.
        conn.execute(
            """
            UPDATE item
            SET params = '{"device":"microphone","active_style":"alert"}'
            WHERE label = 'Mic' AND kind = 'action'
              AND (params IS NULL OR params = '' OR params = '{}'
                   OR params = '{"device":"microphone"}')
            """
        )
        conn.execute(
            "UPDATE item SET icon = 'mic' WHERE label = 'Mic' AND kind = 'action' "
            "AND (icon IS NULL OR icon = '' OR icon = 'camera')"
        )
        conn.commit()
    finally:
        conn.close()


def fixup_volume_item() -> None:
    # Unlike Camera -> Mic, 'Volume' keeps its label across every run, so a
    # bare `WHERE label = 'Volume'` would match (and reapply identical
    # values) on every startup forever. That was tolerable while db.py was
    # the only writer, but Studio Mode now edits these same columns through
    # PUT /api/items/{id} -- an always-on reapply silently reverted every
    # Studio change to this row (target, params, placement) on the next
    # container restart. Guarded on the pre-migration type instead, the
    # same way fixup_audio_switch_state_key() guards on state_key IS NULL:
    # the seeded row starts as type='run', so this still fires once on an
    # unmigrated install and never touches the row again afterwards.
    #
    # It said that while actually testing `type <> 'audio_volume_set'`, which
    # is not the same clause and not a guard at all -- "anything that is not
    # already the target" matches every deliberate change too. This row moves
    # and resizes as well as retyping, so turning Volume into a widget in
    # Studio got the widget dragged to (2,0), stretched to width 2 and handed
    # speaker params on the next restart. Now spelled as the value it
    # upgrades *from*, plus kind='action' so a converted tile is out of reach
    # whatever its type says.
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE item
            SET type = 'audio_volume_set',
                target = 'windows',
                params = '{"device":"speaker","active_style":"normal"}',
                state_key = 'speaker.volume',
                width = 2,
                row = 2,
                col = 0
            WHERE label = 'Volume' AND kind = 'action' AND type = 'run'
            """
        )
        conn.commit()
    finally:
        conn.close()


def fixup_day4_items() -> None:
    # Unlike the UPDATE-based fixups above, these are brand-new rows with no
    # earlier placeholder to rename -- idempotency here means "insert only
    # if a row with this label doesn't already exist yet", checked per item
    # rather than via a single WHERE clause.
    #
    # Placement assumes fixup_volume_item() has already run in this same
    # startup and moved 'Volume' to (row=2, col=0, width=2): Headphones
    # takes the row1/col2 cell that move vacates. Must run after it in
    # main.py's startup sequence, or the two fixups briefly disagree about
    # who owns that cell.
    #
    # One-shot since schema_migration: the per-label check below still stops a
    # duplicate on the first run after an upgrade, and the record stops the
    # run after that from putting back a tile the user has since deleted or
    # renamed (tech debt #24).
    conn = get_connection()
    try:
        if _already_applied(conn, "day4_items"):
            return
        (workspace_id,) = conn.execute(
            "SELECT id FROM workspace ORDER BY position LIMIT 1"
        ).fetchone()

        # row, col, width, label, icon, kind, type, target, params, state_key
        new_items = [
            (1, 2, 1, "Headphones", "headphones", "action", "audio_mute_toggle",
             "windows", '{"device":"speaker","active_style":"alert"}', "speaker.muted"),
            (2, 2, 1, "Audio Switch", "audio-switch", "action", "audio_switch",
             "windows", "{}", None),
            (3, 0, 1, "Screenshot", "camera", "action", "screenshot",
             "windows", "{}", None),
        ]
        for row, col, width, label, icon, kind, type_, target, params, state_key in new_items:
            exists = conn.execute("SELECT 1 FROM item WHERE label = ?", (label,)).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO item
                    (workspace_id, row, col, width, label, icon, kind, type, target, params, state_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (workspace_id, row, col, width, label, icon, kind, type_, target, params, state_key),
            )
        _mark_applied(conn, "day4_items")
        conn.commit()
    finally:
        conn.close()


def fixup_audio_switch_state_key() -> None:
    # 'Audio Switch' was inserted by fixup_day4_items() with state_key=None
    # -- there was nothing to key it to until the poller started reporting
    # speaker.device_name. Guarded by state_key IS NULL (not a plain label
    # match) so a manual state_key set via Studio isn't clobbered on the
    # next startup, unlike fixup_volume_item()'s always-reapply pattern.
    #
    # One-shot as well: `state_key IS NULL` also matched a state_key the user
    # had cleared on purpose, and refilled it on every start.
    conn = get_connection()
    try:
        if _already_applied(conn, "audio_switch_state_key"):
            return
        conn.execute(
            """
            UPDATE item
            SET state_key = 'speaker.device_name'
            WHERE label = 'Audio Switch' AND kind = 'action' AND state_key IS NULL
            """
        )
        _mark_applied(conn, "audio_switch_state_key")
        conn.commit()
    finally:
        conn.close()


def fixup_toggle_off_colors() -> None:
    # 'Mic' and 'VPN' were seeded with an off-state colour drawn from the same
    # hue as their own on-state, so the tile said nothing about which state it
    # was in:
    #
    #   Mic   off #e0575b vs alert #dc2626 -> 1.31:1
    #   VPN   off #0d9488 vs active #0d9488 -> 1.00:1, i.e. identical
    #
    # WCAG 1.4.11 wants 3:1 between the colours that identify a component's
    # state. Both move to the neutral tile colour already used by Terminal,
    # Headphones, Audio Switch and Screenshot -- the schema default, not a new
    # value -- which puts them at 2.78:1 against the alert red and 3.59:1
    # against the active teal. (The alert pair is still marginally short of
    # 3:1; closing it means repainting the shared --color-alert, which is also
    # every error message and destructive control in the app, so it is left
    # as a deliberate call rather than made here.)
    #
    # Guarded on the exact colour being replaced, not on the label alone: this
    # must fire once on an unmigrated install and never again, or it would
    # revert a colour deliberately chosen in Studio on the next restart --
    # the scar fixup_volume_item() documents. It touches `color` only, which no
    # other fixup writes for either row, so fixup_mic_item()'s params/icon
    # UPDATEs cannot undo it either.
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE item SET color = '#2a2f38' "
            "WHERE label = 'Mic' AND kind = 'action' AND color = '#e0575b'"
        )
        conn.execute(
            "UPDATE item SET color = '#2a2f38' "
            "WHERE label = 'VPN' AND kind = 'action' AND color = '#0d9488'"
        )
        conn.commit()
    finally:
        conn.close()


def fixup_close_agent_item() -> None:
    # The reference deck (the author's own, running on the Athlon server) has
    # a "Close Agent" tile and standalone installs did not -- reported as
    # "there's no button to close the agent". The agent_shutdown handler has
    # existed all along; only the tile was missing.
    #
    # The cell is chosen at run time rather than hardcoded, unlike every
    # fixup above it. Those were written against one known layout and this
    # one cannot be: a freshly seeded db and an already-migrated one put the
    # same tiles in different rows (verified -- a fresh db lays out rows 1-3
    # where an older one uses 0-2), so any fixed cell collides on one of
    # them. First free cell in row-major order, within the workspace's own
    # declared grid.
    #
    # Worth knowing before pressing it: the agent exits with code 0, which the
    # launcher's supervisor deliberately treats as "stopped on request" and
    # does not respawn (see AGENT_DELIBERATE_EXIT_CODES). With the console now
    # hidden there is no way to start it again from the desktop -- quit
    # IT-Deck from its window and relaunch.
    #
    # One-shot once the tile exists (inserted here or found already there), so
    # deleting it in Studio sticks. A full grid is not recorded: the next start
    # tries again, as it always has.
    conn = get_connection()
    try:
        if _already_applied(conn, "close_agent_item"):
            return
        if conn.execute("SELECT 1 FROM item WHERE label = 'Close Agent'").fetchone():
            _mark_applied(conn, "close_agent_item")
            conn.commit()
            return

        grid = conn.execute(
            "SELECT grid_cols, grid_rows FROM workspace WHERE id = 1"
        ).fetchone()
        if grid is None:
            return
        cols, rows = grid["grid_cols"], grid["grid_rows"]
        taken = {
            (r["row"], r["col"])
            for r in conn.execute("SELECT row, col FROM item WHERE workspace_id = 1 AND dock = 0")
        }
        # Scanned from the last occupied row, not from (0,0). The first free
        # cell overall is the top-left corner, and putting a "stop everything"
        # button in the most prominent, most mis-tappable spot on the deck is
        # the wrong place for it. Starting at the bottom of the existing
        # tiles lands it beside VPN in both layouts, which is where the
        # reference deck keeps it.
        first_row = max((r for r, _ in taken), default=0) if taken else 0
        cell = next(
            (
                (r, c)
                for r in range(first_row, rows)
                for c in range(cols)
                if (r, c) not in taken
            ),
            None,
        )
        if cell is None:
            # Nothing free below; fall back to anywhere at all before giving up.
            cell = next(
                ((r, c) for r in range(rows) for c in range(cols) if (r, c) not in taken),
                None,
            )
        if cell is None:
            # A full grid is not an error worth failing startup over -- the
            # tile can be added by hand in Studio, which is also where a
            # person would make room for it.
            return

        conn.execute(
            """
            INSERT INTO item (workspace_id, row, col, width, height, label, icon,
                color, kind, type, target, params, state_key)
            VALUES (1, ?, ?, 1, 1, 'Close Agent', 'power', '#2a2f38', 'action',
                'agent_shutdown', 'windows', '{}', NULL)
            """,
            cell,
        )
        _mark_applied(conn, "close_agent_item")
        conn.commit()
    finally:
        conn.close()


def fixup_widget_types() -> None:
    """Put back the type on widget rows an earlier fixup overwrote.

    Only `fixup_legacy_seed()` could produce this shape -- a row with
    `kind = 'widget'` and `type = 'launch_app'` -- because it is the one
    statement that ever wrote a type onto a row it had not just created, and
    `launch_app` is the value it wrote. A real launch_app tile is an action,
    never a widget, so the combination is unambiguous damage rather than
    anything a person could have chosen in Studio.

    The deck cannot show such a row at all: `mountWidget()` looks `type` up in
    WIDGETS, finds nothing, declines, and the tile falls back to its icon and
    label. On the install that reported this, that fallback was the Terminal
    tile the widget had replaced, reappearing after every restart.

    `clock_weather` is not a guess here: it was the only widget type when
    that bug existed, so every row it damaged was a clock. The second widget
    (`pc_stats`, v0.5.5) came long after the bug was fixed and is never
    stored as `launch_app`, so the match below can only ever find the old
    damage -- keep it that narrow if more widget types are added.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE item SET type = 'clock_weather' "
            "WHERE kind = 'widget' AND type = 'launch_app'"
        )
        conn.commit()
    finally:
        conn.close()


def fixup_vpn_tile_type() -> None:
    """Move an unconfigured VPN tile from `process_toggle` to `launch_app`.

    The reference deck's VPN tile is a `launch_app` holding the client's path,
    with `state_key = vpn.running` so the poller lights it. Standalone seeded
    a `process_toggle` instead, and that was the wrong shape for the job:

    - it needs `process_name`/`path` params that nothing seeds, so an
      untouched tile could only ever answer "not configured yet";
    - it is a *toggle*, so the second press stops the VPN. Combined with a
      state that read false on a freshly started agent, that is how a tap
      meaning "connect" turned into "disconnect".

    Only unconfigured tiles are converted. A `process_toggle` someone gave
    real params to is a deliberate setup -- toggling is a legitimate thing to
    want -- and is left exactly as it is.
    """
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE item
            SET type = 'launch_app'
            WHERE label = 'VPN' AND kind = 'action'
              AND type = 'process_toggle'
              AND params NOT LIKE '%process_name%'
              AND params NOT LIKE '%"path"%'
            """
        )
        conn.commit()
    finally:
        conn.close()


def fixup_vpn_item() -> None:
    # Same insert-if-missing idempotency as fixup_day4_items(). Placement
    # is (row=3, col=1): the originally proposed (row=3, col=0) collides
    # with 'Screenshot', which fixup_day4_items() already put there --
    # verified against a scratch DB before writing this, not assumed.
    #
    # workspace_id is hardcoded to 1 here (unlike fixup_day4_items()'s
    # dynamic `SELECT id FROM workspace`) -- confirmed safe against the
    # same scratch DB (this app has only ever had the one seeded
    # workspace, which gets id=1), but it's worth knowing this fixup
    # would silently insert against the wrong workspace if that ever
    # changes, where fixup_day4_items() would not.
    #
    # One-shot since schema_migration, for the same reason as
    # fixup_day4_items(): a deleted VPN tile used to come back on every start.
    conn = get_connection()
    try:
        if _already_applied(conn, "vpn_item"):
            return
        conn.execute(
            """
            INSERT INTO item (workspace_id, row, col, width, height, label, icon,
                color, kind, type, target, params, state_key)
            SELECT 1, 3, 1, 1, 1, 'VPN', 'shield', '#0d9488', 'action',
                'launch_app', 'windows', '{"active_style":"normal"}', 'vpn.running'
            WHERE NOT EXISTS (SELECT 1 FROM item WHERE label='VPN')
            """
        )
        _mark_applied(conn, "vpn_item")
        conn.commit()
    finally:
        conn.close()
