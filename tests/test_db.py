"""Startup fixups against scratch databases: they upgrade what they were
written for and leave every user edit alone, across restarts."""

import sqlite3
from collections import Counter

import pytest

from app import db
from app.main import run_startup_migrations

SEEDED = {"Terminal", "Mic", "Volume", "Headphones", "Audio Switch", "Screenshot", "VPN", "Close Agent"}


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "controlhub.db")
    run_startup_migrations()
    return tmp_path / "controlhub.db"


def sql(path, statement, args=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(statement, args).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def labels(path):
    return Counter(label for (label,) in sql(path, "SELECT label FROM item"))


def add_tile(path, label, type_, row=4, col=0):
    sql(
        path,
        "INSERT INTO item (workspace_id, row, col, label, icon, kind, type, params) "
        "VALUES (1, ?, ?, ?, 'globe', 'action', ?, '{}')",
        (row, col, label, type_),
    )


def test_a_fresh_database_gets_every_seeded_tile_once(fresh_db):
    run_startup_migrations()
    counts = labels(fresh_db)
    assert set(counts) == SEEDED
    assert all(n == 1 for n in counts.values()), counts


def test_user_tiles_named_like_old_placeholders_survive(fresh_db):
    add_tile(fresh_db, "Spotify", "open_url", col=0)
    add_tile(fresh_db, "Camera", "launch_app", col=1)
    add_tile(fresh_db, "Lights", "toggle", col=2)  # the real dead placeholder
    run_startup_migrations()
    run_startup_migrations()
    counts = labels(fresh_db)
    assert counts["Spotify"] == 1
    assert counts["Camera"] == 1
    assert "Lights" not in counts
    ((camera_type,),) = sql(fresh_db, "SELECT type FROM item WHERE label = 'Camera'")
    assert camera_type == "launch_app"


def test_deleted_and_renamed_seeded_tiles_stay_that_way(fresh_db):
    sql(fresh_db, "DELETE FROM item WHERE label IN ('Screenshot', 'VPN', 'Close Agent')")
    sql(fresh_db, "UPDATE item SET label = 'Cans' WHERE label = 'Headphones'")
    sql(fresh_db, "UPDATE item SET state_key = NULL WHERE label = 'Audio Switch'")
    run_startup_migrations()
    run_startup_migrations()
    counts = labels(fresh_db)
    assert not {"Screenshot", "VPN", "Close Agent", "Headphones"} & set(counts)
    assert counts["Cans"] == 1
    ((state_key,),) = sql(fresh_db, "SELECT state_key FROM item WHERE label = 'Audio Switch'")
    assert state_key is None


def test_a_database_from_before_the_migration_table_is_not_duplicated(fresh_db):
    # What every existing install looks like on its first start with this
    # build: all the seeded tiles present, no record of any fixup.
    sql(fresh_db, "DROP TABLE schema_migration")
    run_startup_migrations()
    counts = labels(fresh_db)
    assert set(counts) == SEEDED and all(n == 1 for n in counts.values()), counts

    # ...and from then on a deletion sticks.
    sql(fresh_db, "DELETE FROM item WHERE label = 'Screenshot'")
    run_startup_migrations()
    assert "Screenshot" not in labels(fresh_db)
