"""scripts/check_release.py: the gate a manual release runs first."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_release  # noqa: E402


def make_repo(tmp_path, version, whats_new=None):
    (tmp_path / "standalone").mkdir()
    (tmp_path / "standalone" / "launcher.py").write_text(f'ITDECK_VERSION = "{version}"\n')
    (tmp_path / "frontend").mkdir()
    if whats_new is not None:
        (tmp_path / "frontend" / "whats-new.json").write_text(json.dumps(whats_new))
    return tmp_path


def test_version_must_match_the_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(check_release, "REPO", make_repo(tmp_path, "0.5.2"))
    assert check_release.problems("v0.5.2") == []
    assert "ITDECK_VERSION is 0.5.2" in check_release.problems("v0.5.3")[0]


def test_a_release_needs_its_whats_new_entry(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, "0.5.6", [{"version": "0.5.5", "en": ["x"], "ru": ["x"]}])
    monkeypatch.setattr(check_release, "REPO", repo)
    assert "no entry for 0.5.6" in check_release.problems("v0.5.6")[0]
