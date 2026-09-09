import sqlite3
from pathlib import Path

DB_PATH = Path("/app/data/controlhub.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # sqlite3 defaults FK enforcement to off per-connection; item.workspace_id's
    # ON DELETE CASCADE only fires if this is set on every connection that writes.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        # WAL lets the UI (reader) and agent actions (writer) hit the db at the
        # same time instead of blocking each other behind sqlite's default lock.
        conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL only fsyncs at WAL checkpoints, not every commit; safe under WAL
        # (survives app crashes) and far faster than FULL for a local single-user app.
        conn.execute("PRAGMA synchronous=NORMAL")
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
            """
        )
        conn.commit()
    finally:
        conn.close()


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

        # row, col, label, icon, color, kind, type -- just enough to render a grid on day 1
        fake_items = [
            (0, 0, "Lights", "lightbulb", "#f2c14e", "action", "toggle"),
            (0, 1, "Spotify", "music", "#1db954", "action", "launch"),
            (0, 2, "Sleep PC", "moon", "#4e6ef2", "action", "run"),
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


def fixup_legacy_seed() -> None:
    # Not a one-time migration: seed_if_empty only fires once per fresh db, so
    # early installs may already have a 'Terminal' row stuck with the old
    # placeholder type/params. Re-running this UPDATE on every startup is the
    # simplest way to backfill those installs; it's a no-op once the row
    # already matches, so it's safe to keep calling forever.
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE item
            SET type = 'launch_app', params = '{"path":"notepad.exe"}'
            WHERE label = 'Terminal'
            """
        )
        conn.commit()
    finally:
        conn.close()


def fixup_mic_item() -> None:
    # Same idempotent backfill approach as fixup_legacy_seed(): converts the
    # placeholder 'Camera' row left by seed_if_empty into the real mic mute
    # toggle action. No-op once the row already matches, safe every startup.
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
            WHERE label = 'Camera'
            """
        )
        # The UPDATE above only ever matches once -- the label flips away
        # from 'Camera' on the first run, so installs that already migrated
        # past it (before active_style existed) would never pick it up from
        # that statement alone. Backfill it separately, keyed on the label
        # the row actually settles into.
        conn.execute(
            """
            UPDATE item
            SET params = '{"device":"microphone","active_style":"alert"}',
                icon = 'mic'
            WHERE label = 'Mic'
            """
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
            WHERE label = 'Volume' AND type <> 'audio_volume_set'
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
    conn = get_connection()
    try:
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
        conn.commit()
    finally:
        conn.close()


def fixup_audio_switch_state_key() -> None:
    # 'Audio Switch' was inserted by fixup_day4_items() with state_key=None
    # -- there was nothing to key it to until the poller started reporting
    # speaker.device_name. Guarded by state_key IS NULL (not a plain label
    # match) so a manual state_key set via Studio isn't clobbered on the
    # next startup, unlike fixup_volume_item()'s always-reapply pattern.
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE item
            SET state_key = 'speaker.device_name'
            WHERE label = 'Audio Switch' AND state_key IS NULL
            """
        )
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
    # other fixup writes for either row, so fixup_mic_item()'s unguarded
    # params/icon UPDATE cannot undo it either.
    conn = get_connection()
    try:
        conn.execute("UPDATE item SET color = '#2a2f38' WHERE label = 'Mic' AND color = '#e0575b'")
        conn.execute("UPDATE item SET color = '#2a2f38' WHERE label = 'VPN' AND color = '#0d9488'")
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
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO item (workspace_id, row, col, width, height, label, icon,
                color, kind, type, target, params, state_key)
            SELECT 1, 3, 1, 1, 1, 'VPN', 'shield', '#0d9488', 'action',
                'process_toggle', 'windows', '{"active_style":"normal"}', 'vpn.running'
            WHERE NOT EXISTS (SELECT 1 FROM item WHERE label='VPN')
            """
        )
        conn.commit()
    finally:
        conn.close()
