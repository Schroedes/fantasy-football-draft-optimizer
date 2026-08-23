"""Isolates every test in tests/api/ from whatever `data/session.json` may or
may not exist on disk in the CWD the suite happens to run from.

`ffdo.api.app._league_id()` / `_draft_id()` / `_roster_id()` all consult the
module-level `_SESSION_STORE` before falling back to env vars/defaults. A
handful of pre-existing tests in test_app.py call those functions directly
without ever touching `_SESSION_STORE`, so without this fixture they'd
silently start reading a real connected session the moment anyone follows
this repo's own connect flow locally (which writes `data/session.json`
relative to the CWD). Autouse + function-scoped keeps every test -- old and
new -- pointed at a throwaway store backed by pytest's `tmp_path`, regardless
of whether it also does its own `monkeypatch.setattr(app_mod, "_SESSION_STORE", ...)`.
"""

from __future__ import annotations

import pytest

from ffdo.api import app as app_mod
from ffdo.api.session import SessionStore


@pytest.fixture(autouse=True)
def _isolated_session_store(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_SESSION_STORE", SessionStore(tmp_path / "session.json"))
