from ffdo.ingest.league import detect_format


def test_detect_format_dynasty_from_settings_type_2():
    assert detect_format({"settings": {"type": 2}}) == "dynasty"


def test_detect_format_keeper_from_settings_type_1():
    assert detect_format({"settings": {"type": 1}}) == "keeper"


def test_detect_format_keeper_from_max_keepers_or_previous_league():
    assert detect_format({"settings": {"type": 0, "max_keepers": 2}}) == "keeper"
    assert detect_format({"settings": {"type": 0}, "previous_league_id": "L0"}) == "keeper"


def test_detect_format_redraft_by_default():
    assert detect_format({"settings": {"type": 0}}) == "redraft"
    assert detect_format({}) == "redraft"
