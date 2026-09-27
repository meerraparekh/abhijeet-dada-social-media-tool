"""A corrupted/unreadable session.json must not take down the whole session
list - regression test for a real bug hit in production (GET /api/sessions
returned 500 because of one bad file among otherwise-valid sessions)."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))


def test_list_all_skips_a_corrupted_session_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SATSANG_DATA_DIR", str(tmp_path / "data"))
    for mod in list(sys.modules):
        if mod in ("config", "store", "schemas"):
            sys.modules.pop(mod, None)

    import store
    from schemas import Session

    good = Session(name="Good session")
    store.create(good)

    corrupt_dir = store.session_dir("corrupt-one")
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "session.json").write_text("{not valid json at all", encoding="utf-8")

    sessions = store.list_all()
    assert [s.id for s in sessions] == [good.id]
