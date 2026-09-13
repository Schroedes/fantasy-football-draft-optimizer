"""Isolates every test in tests/api/ from whatever `data/ffdo.db` may or may
not exist on disk in the CWD the suite happens to run from.

`ffdo.api.app._STORE` is a module-level `LeagueStore` pointed at
`data/ffdo.db` (with the one-shot `session.json` migration wired in) so the
free functions built on it -- `_load_league()` above all -- work with no app
instance in hand. Without this fixture, any test that hits a league-scoped
endpoint would silently read (and write!) the real store the moment anyone
follows this repo's own connect flow locally. Autouse + function-scoped keeps
every test pointed at a throwaway store backed by pytest's `tmp_path`,
regardless of whether it also does its own
`monkeypatch.setattr(app_mod, "_STORE", ...)`.

Note the deliberate absence of `legacy_session_path=` here: the migration is a
production-startup concern, tested directly in tests/api/test_store.py, and
wiring it into every API test would make each one's behavior depend on a
`session.json` that may or may not exist in the CWD -- exactly the coupling
this fixture exists to remove.
"""

from __future__ import annotations

import pytest

from ffdo.api import app as app_mod
from ffdo.api.store import LeagueStore


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "_STORE", LeagueStore(tmp_path / "ffdo.db"))
