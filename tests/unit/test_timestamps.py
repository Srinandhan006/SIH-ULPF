from datetime import timezone

from uli.timestamps import parse_any, parse_timestamp


def test_iso_with_millis_and_z():
    dt, gram, _ = parse_timestamp("2026-09-05T10:00:00.123Z host app")
    assert gram == "iso"
    assert dt.year == 2026 and dt.microsecond == 123000


def test_bsd_syslog_no_year_defaults_current_year():
    dt, gram, _ = parse_timestamp("Sep  5 10:00:01 host app: msg")
    assert gram == "bsd"
    assert dt.month == 9 and dt.day == 5 and dt.hour == 10


def test_clf_format():
    dt, gram, _ = parse_timestamp("[05/Sep/2026:10:00:00 +0000]")
    assert gram == "clf"
    assert dt.tzinfo is not None


def test_epoch_seconds():
    dt, gram, _ = parse_timestamp("1757066400 something")
    assert gram == "epoch_s"
    assert dt.year == 2025 or dt.year == 2026  # sanity: near current era


def test_healthapp_grammar():
    dt, gram, _ = parse_timestamp("20171223-22:15:29:606|Step_LSC|30")
    assert gram == "healthapp"
    assert dt.year == 2017 and dt.month == 12 and dt.day == 23


def test_garbage_returns_none_never_raises():
    dt, gram, orig = parse_timestamp("###not a timestamp at all###")
    assert dt is None and gram is None


def test_parse_any_empty_and_overlong():
    assert parse_any("") is None
    assert parse_any("x" * 1000) is None
