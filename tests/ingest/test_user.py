from ffdo.ingest import user


def test_parse_extracts_user_id_and_display_name():
    raw = {"user_id": "437507358097141760", "username": "handle",
          "display_name": "Handle Display Name"}
    user_id, display_name = user.parse(raw)
    assert user_id == "437507358097141760"
    assert display_name == "Handle Display Name"


def test_parse_falls_back_to_username_when_display_name_is_missing():
    raw = {"user_id": "1", "username": "handle", "display_name": None}
    user_id, display_name = user.parse(raw)
    assert display_name == "handle"
