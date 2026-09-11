from ffdo.domain.constants import INJURY_OUT_STATUSES


def test_injury_out_statuses_covers_the_known_hard_outs():
    assert INJURY_OUT_STATUSES == {"IR", "PUP", "Out", "Sus"}
