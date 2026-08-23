import json

from ffdo.api.session import SessionStore
from ffdo.domain.models import Session


def _session(**overrides):
    base = dict(
        username="tester", user_id="U1", league_id="L1", draft_id="D1",
        roster_id=3, league_name="Test League", season=2026, num_teams=12,
        budget=200,
        roster_positions=("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                          "BN", "BN", "BN", "BN", "BN"),
        scoring_settings={"rec": 0.5}, draft_type="auction",
        draft_status="pre_draft", rounds=13,
        connected_at="2026-08-22T00:00:00+00:00",
    )
    return Session(**{**base, **overrides})


def test_get_returns_none_when_no_file_exists(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    assert store.get() is None


def test_save_then_get_round_trips_the_session(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    session = _session()
    store.save(session)
    assert store.get() == session


def test_a_fresh_store_reads_what_a_prior_store_saved(tmp_path):
    """Simulates a process restart: a new SessionStore pointed at the same
    path must recover the previously-connected session from disk."""
    path = tmp_path / "session.json"
    SessionStore(path).save(_session())

    loaded = SessionStore(path).get()
    assert loaded == _session()


def test_save_then_get_round_trips_rounds(tmp_path):
    """`rounds` is the actual draft round count (distinct from roster size,
    which the main screen used to substitute for it) -- must survive the
    JSON round trip like every other field."""
    store = SessionStore(tmp_path / "session.json")
    session = _session(rounds=16)
    store.save(session)
    assert store.get().rounds == 16


def test_save_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "session.json"
    SessionStore(path).save(_session())
    assert path.exists()


def test_load_returns_none_for_malformed_json(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert SessionStore(path).get() is None


def test_clear_deletes_the_file_and_resets_the_cache(tmp_path):
    path = tmp_path / "session.json"
    store = SessionStore(path)
    store.save(_session())
    store.clear()
    assert store.get() is None
    assert not path.exists()


def test_save_then_get_round_trips_espn_provider_and_cookies(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    session = _session(provider="espn", espn_s2="s2-value",
                       swid="{00000004-0000-0000-0000-000000000000}")
    store.save(session)
    loaded = store.get()
    assert loaded.provider == "espn"
    assert loaded.espn_s2 == "s2-value"
    assert loaded.swid == "{00000004-0000-0000-0000-000000000000}"


def test_a_session_file_written_before_this_feature_loads_as_sleeper(tmp_path):
    """A pre-existing data/session.json has no provider/espn_s2/swid keys at
    all -- must load as a Sleeper session, not crash or silently misreport
    the provider."""
    path = tmp_path / "session.json"
    old_style = {
        "username": "tester", "user_id": "U1", "league_id": "L1", "draft_id": "D1",
        "roster_id": 3, "league_name": "Test League", "season": 2026, "num_teams": 12,
        "budget": 200,
        "roster_positions": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX",
                             "BN", "BN", "BN", "BN", "BN"],
        "scoring_settings": {"rec": 0.5}, "draft_type": "auction",
        "draft_status": "pre_draft", "rounds": 13,
        "connected_at": "2026-08-22T00:00:00+00:00",
    }
    path.write_text(json.dumps(old_style), encoding="utf-8")

    loaded = SessionStore(path).get()
    assert loaded.provider == "sleeper"
    assert loaded.espn_s2 is None
    assert loaded.swid is None
