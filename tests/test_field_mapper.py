from fast_track.api.field_mapper import extract_records, first_present, get_path


def test_get_path_nested():
    payload = {"Publisher": {"Email": "a@example.com"}}
    assert get_path(payload, "Publisher.Email") == "a@example.com"
    assert get_path(payload, "Publisher.Missing") is None
    assert get_path(payload, "Missing.Path") is None


def test_first_present_tries_candidates_in_order():
    payload = {"email": "a@example.com"}
    assert first_present(payload, ["Email", "email", "EmailAddress"]) == "a@example.com"
    assert first_present(payload, ["Email", "EmailAddress"]) is None


def test_first_present_skips_empty_strings():
    payload = {"Name": "", "FullName": "Ava"}
    assert first_present(payload, ["Name", "FullName"]) == "Ava"


def test_extract_records_from_list_payload():
    assert extract_records([{"a": 1}], ["data"]) == [{"a": 1}]


def test_extract_records_from_configured_root():
    payload = {"publishers": [{"a": 1}]}
    assert extract_records(payload, ["publishers"]) == [{"a": 1}]


def test_extract_records_falls_back_to_common_envelope_keys():
    payload = {"results": [{"a": 1}]}
    assert extract_records(payload, ["data"]) == [{"a": 1}]


def test_extract_records_returns_empty_list_when_nothing_matches():
    assert extract_records({"unexpected": "shape"}, ["data"]) == []
