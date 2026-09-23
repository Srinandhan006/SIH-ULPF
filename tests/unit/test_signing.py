"""Ed25519 bundle signing (docs/problem-statement.md #6/#7, docs/security.md §5)."""
from __future__ import annotations

from pathlib import Path

from uli.security.signing import generate_keypair, load_private_key, load_public_key, sign_bytes, sign_file, verify_bytes, verify_file


def test_roundtrip_valid_signature_verifies():
    priv, pub = generate_keypair()
    sk, pk = load_private_key(priv), load_public_key(pub)
    sig = sign_bytes(b"hello bundle", sk)
    assert verify_bytes(b"hello bundle", sig, pk) is True


def test_tampered_payload_fails_verification():
    priv, pub = generate_keypair()
    sk, pk = load_private_key(priv), load_public_key(pub)
    sig = sign_bytes(b"original", sk)
    assert verify_bytes(b"tampered", sig, pk) is False


def test_wrong_key_fails_verification():
    priv_a, _ = generate_keypair()
    _, pub_b = generate_keypair()
    sk_a, pk_b = load_private_key(priv_a), load_public_key(pub_b)
    sig = sign_bytes(b"payload", sk_a)
    assert verify_bytes(b"payload", sig, pk_b) is False


def test_garbage_signature_does_not_raise():
    _, pub = generate_keypair()
    pk = load_public_key(pub)
    assert verify_bytes(b"payload", "not-valid-base64!!!", pk) is False
    assert verify_bytes(b"payload", "", pk) is False


def test_sign_file_and_verify_file_roundtrip(tmp_path: Path):
    priv, pub = generate_keypair()
    (tmp_path / "priv.pem").write_bytes(priv)
    (tmp_path / "pub.pem").write_bytes(pub)
    bundle = tmp_path / "pack.yaml"
    bundle.write_text("parser_id: vendor.test\nversion: 1.0.0\n")

    sig = sign_file(bundle, tmp_path / "priv.pem")
    assert verify_file(bundle, sig, tmp_path / "pub.pem") is True

    bundle.write_text("parser_id: vendor.test\nversion: 1.0.1  # tampered after signing\n")
    assert verify_file(bundle, sig, tmp_path / "pub.pem") is False


def test_verify_file_missing_pubkey_fails_closed(tmp_path: Path):
    bundle = tmp_path / "pack.yaml"
    bundle.write_text("parser_id: vendor.test\n")
    assert verify_file(bundle, "irrelevant", tmp_path / "does-not-exist.pem") is False
