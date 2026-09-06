from uli.fingerprint import classify, fingerprint, jaccard, shape_ngrams, tokenize
from uli.models import TokenType


def test_classify_basic_types():
    assert classify("192.168.1.1") == TokenType.IP4
    assert classify("2001:db8::1") == TokenType.IP6
    assert classify("aa:bb:cc:dd:ee:ff") == TokenType.MAC
    assert classify("123") == TokenType.NUM
    assert classify("hello") == TokenType.WORD
    assert classify("user@example.com") == TokenType.EMAIL
    assert classify("https://example.com/x") == TokenType.URL
    assert classify("/var/log/syslog") == TokenType.PATH


def test_timestamp_survives_tokenization_as_one_token():
    toks = tokenize("Sep  5 10:00:01 host app: msg")
    assert "10:00:01" in toks


def test_fingerprint_same_shape_for_structurally_identical_lines():
    a = fingerprint("Sep  5 10:00:01 host1 sshd[111]: Accepted publickey for alice from 10.0.0.1 port 22 ssh2")
    b = fingerprint("Sep  6 11:02:03 host2 sshd[222]: Accepted publickey for bob from 10.0.0.9 port 22 ssh2")
    assert a.shape_hash == b.shape_hash
    assert a.family_hash == b.family_hash


def test_fingerprint_different_shape_for_structurally_different_lines():
    a = fingerprint("Sep  5 10:00:01 host1 sshd[111]: Accepted publickey for alice from 10.0.0.1 port 22 ssh2")
    b = fingerprint('{"a": 1, "b": "x"}')
    assert a.shape_hash != b.shape_hash


def test_fingerprint_handles_empty_and_huge_input():
    fp = fingerprint("")
    assert fp.token_count == 0
    huge = fingerprint("a " * 5000)
    assert huge.token_count <= 512  # _MAX_TOKENS cap; must not hang or blow memory


def test_shape_ngram_jaccard_identical_is_one():
    s = fingerprint("Sep  5 10:00:01 host1 sshd[111]: Accepted publickey for alice from 10.0.0.1 port 22 ssh2").shape
    assert jaccard(shape_ngrams(s), shape_ngrams(s)) == 1.0


def test_kv_detection():
    fp = fingerprint("level=error msg=\"disk full\" host=node1")
    assert fp.classes.count(TokenType.KV) >= 2
