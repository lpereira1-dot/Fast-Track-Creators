from fast_track.email_utils import is_valid_email


def test_is_valid_email_accepts_typical_addresses():
    assert is_valid_email("ava@example.com")
    assert is_valid_email("  liam.chen@gmail.com  ")


def test_is_valid_email_rejects_handles_and_partial_strings():
    assert not is_valid_email("")
    assert not is_valid_email("athomewithkristinsmith")
    assert not is_valid_email("makenalynn22@")
    assert not is_valid_email("elianadcolli@gm")
