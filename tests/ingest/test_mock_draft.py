import pytest

from ffdo.domain.constants import STANDARD_HALF_PPR
from ffdo.ingest import mock_draft

# Real captured /v1/draft/<id> response, BEFORE the connecting user joined a
# slot (draft_order is null -- no one has claimed a seat yet).
MOCK_DRAFT_PRE_DRAFT = {
    "created": 1787468015451,
    "creators": ["461997611847512064"],
    "draft_id": "1397145756879605760",
    "draft_order": None,
    "last_message_id": "1397145756879605760",
    "last_message_time": 1787468015451,
    "last_picked": None,
    "league_id": None,
    "metadata": {"description": "", "name": "", "scoring_type": "ppr"},
    "season": "2026",
    "season_type": "regular",
    "settings": {
        "autostart": 0, "cpu_autopick": 1, "pick_timer": 120, "rounds": 15,
        "slots_def": 1, "slots_flex": 2, "slots_k": 1, "slots_qb": 1,
        "slots_rb": 2, "slots_te": 1, "slots_wr": 2, "teams": 10,
    },
    "slot_to_roster_id": {str(i): i for i in range(1, 11)},
    "sport": "nfl", "start_time": None, "status": "pre_draft", "type": "snake",
}

# The SAME draft, re-fetched after the user joined slot 1 and drafted twice.
# Note scoring_type changed from "ppr" to "half_ppr" between the two fetches
# -- confirmed real behavior (the mock's settings were adjusted before the
# draft started), not a fixture inconsistency. This is exactly why scoring
# must be re-derived fresh on every poll, never cached from connect time.
MOCK_DRAFT_MID_DRAFT = {
    **MOCK_DRAFT_PRE_DRAFT,
    "draft_order": {"461997611847512064": 1},
    "last_message_id": "1397150696637247488",
    "last_message_time": 1787469193185,
    "last_picked": 1787469226495,
    "metadata": {"description": "", "name": "", "scoring_type": "half_ppr",
                 "show_team_names": "0"},
    "start_time": 1787469193178, "status": "drafting",
}

# Real /v1/draft/<id>/picks payload, trimmed to the picks that matter for
# the backfill test: pick_no 1 is the connecting human's own first-round
# pick (draft_slot 1); pick_no 11 is a CPU pick in round 2, where snake
# order reverses draft_slot back to 10 -- proving the backfill keys off
# draft_slot, not position-in-the-list or round-parity assumptions.
MOCK_DRAFT_PICKS_RAW = [
    {"draft_id": "1397145756879605760", "draft_slot": 1, "pick_no": 1,
     "picked_by": "461997611847512064", "player_id": "9221",
     "roster_id": None, "round": 1,
     "metadata": {"position": "RB"}},
    {"draft_id": "1397145756879605760", "draft_slot": 2, "pick_no": 2,
     "picked_by": "", "player_id": "9509", "roster_id": None, "round": 1,
     "metadata": {"position": "RB"}},
    {"draft_id": "1397145756879605760", "draft_slot": 10, "pick_no": 11,
     "picked_by": "", "player_id": "4866", "roster_id": None, "round": 2,
     "metadata": {"position": "RB"}},
]


def test_is_mock_draft_is_true_when_league_id_is_null():
    assert mock_draft.is_mock_draft(MOCK_DRAFT_PRE_DRAFT) is True


def test_is_mock_draft_is_false_for_a_real_league_draft():
    real_draft = {**MOCK_DRAFT_PRE_DRAFT, "league_id": "1389375982783180800"}
    assert mock_draft.is_mock_draft(real_draft) is False


def test_roster_positions_from_slots_matches_the_real_mock_draft():
    """settings has no slots_bn -- bench must be inferred as
    rounds - sum(starters): 1+2+2+1+1+1+2 = 10 starters, 15 rounds -> 5 bench."""
    positions = mock_draft.roster_positions_from_slots(MOCK_DRAFT_PRE_DRAFT["settings"])
    assert positions.count("QB") == 1
    assert positions.count("RB") == 2
    assert positions.count("WR") == 2
    assert positions.count("TE") == 1
    assert positions.count("K") == 1
    assert positions.count("DEF") == 1
    assert positions.count("FLEX") == 2
    assert positions.count("BN") == 5
    assert len(positions) == 15  # matches settings["rounds"]


def test_roster_positions_from_slots_uses_explicit_slots_bn_when_present():
    """Real league drafts DO carry slots_bn explicitly (confirmed against
    the user's actual "Chopped" league draft) -- when present it must win
    over the rounds-minus-starters inference."""
    settings = {
        "slots_qb": 1, "slots_rb": 2, "slots_wr": 2, "slots_te": 1,
        "slots_flex": 2, "slots_bn": 6, "rounds": 14, "teams": 19,
    }
    positions = mock_draft.roster_positions_from_slots(settings)
    assert positions.count("BN") == 6
    assert len(positions) == 14


def test_roster_positions_from_slots_handles_super_flex():
    settings = {"slots_qb": 1, "slots_super_flex": 1, "slots_bn": 2, "rounds": 4}
    positions = mock_draft.roster_positions_from_slots(settings)
    assert positions.count("QB") == 1
    assert positions.count("SUPER_FLEX") == 1
    assert positions.count("BN") == 2


def test_roster_positions_from_slots_raises_for_an_unrecognized_slot_key():
    settings = {"slots_qb": 1, "slots_idp_flex": 1, "rounds": 5}
    with pytest.raises(mock_draft.MockDraftError, match="slots_idp_flex"):
        mock_draft.roster_positions_from_slots(settings)


def test_scoring_settings_for_half_ppr_matches_the_verified_constant():
    assert mock_draft.scoring_settings_for_preset("half_ppr") == dict(STANDARD_HALF_PPR)


def test_scoring_settings_for_ppr_is_half_ppr_with_full_reception_credit():
    settings = mock_draft.scoring_settings_for_preset("ppr")
    assert settings["rec"] == 1.0
    assert settings["rec_yd"] == STANDARD_HALF_PPR["rec_yd"]
    assert settings["pass_td"] == STANDARD_HALF_PPR["pass_td"]


def test_scoring_settings_for_standard_has_no_reception_credit():
    settings = mock_draft.scoring_settings_for_preset("standard")
    assert settings["rec"] == 0.0
    assert settings["rush_td"] == STANDARD_HALF_PPR["rush_td"]


def test_scoring_settings_for_preset_rejects_dynasty_2qb():
    """dynasty_2qb is a real scoring_type value (confirmed against the
    user's actual league history) -- it encodes roster format, not a PPR
    level, and this app has no dynasty support. Must fail explicitly, not
    silently approximate."""
    with pytest.raises(mock_draft.MockDraftError, match="dynasty_2qb"):
        mock_draft.scoring_settings_for_preset("dynasty_2qb")


def test_scoring_settings_for_preset_rejects_unknown_values():
    with pytest.raises(mock_draft.MockDraftError, match="whatever_this_is"):
        mock_draft.scoring_settings_for_preset("whatever_this_is")


def test_build_league_profile_from_the_real_mock_draft():
    lg = mock_draft.build_league_profile(MOCK_DRAFT_MID_DRAFT)
    assert lg.league_id == ""
    assert lg.season == 2026
    assert lg.num_teams == 10
    assert lg.budget is None  # snake mock, no budget field
    assert lg.status == "drafting"
    assert lg.scoring_settings["rec"] == 0.5  # half_ppr, per MOCK_DRAFT_MID_DRAFT
    assert len(lg.roster_positions) == 15


def test_resolve_roster_id_is_none_before_the_user_has_joined_a_slot():
    assert mock_draft.resolve_roster_id(
        MOCK_DRAFT_PRE_DRAFT, "461997611847512064") is None


def test_resolve_roster_id_after_joining_slot_1():
    assert mock_draft.resolve_roster_id(
        MOCK_DRAFT_MID_DRAFT, "461997611847512064") == 1


def test_resolve_roster_id_is_none_for_a_user_who_never_joined():
    assert mock_draft.resolve_roster_id(
        MOCK_DRAFT_MID_DRAFT, "someone-else-entirely") is None


def test_backfill_roster_ids_corrects_every_picks_null_roster_id():
    """The core regression guard: Sleeper returns roster_id: null for every
    mock-draft pick, even the connecting human's own pick_no 1. Without this
    fix, every 'your roster' computation downstream (your_spent,
    your_slots_left, auction.positional_budget) silently stays stuck at the
    fresh-roster fallback forever."""
    backfilled = mock_draft.backfill_roster_ids(
        MOCK_DRAFT_PICKS_RAW, MOCK_DRAFT_MID_DRAFT)

    by_pick_no = {p["pick_no"]: p for p in backfilled}
    assert by_pick_no[1]["roster_id"] == 1  # draft_slot 1 -> roster_id 1 (the human)
    assert by_pick_no[2]["roster_id"] == 2  # draft_slot 2 -> roster_id 2
    # Round 2, snake-reversed: draft_slot 10 -> roster_id 10, NOT related to
    # pick_no or round in any other way -- proves the mapping is purely
    # draft_slot -> slot_to_roster_id, ignoring everything else.
    assert by_pick_no[11]["roster_id"] == 10


def test_backfill_roster_ids_does_not_mutate_the_input():
    original = [dict(p) for p in MOCK_DRAFT_PICKS_RAW]
    mock_draft.backfill_roster_ids(MOCK_DRAFT_PICKS_RAW, MOCK_DRAFT_MID_DRAFT)
    assert MOCK_DRAFT_PICKS_RAW == original
