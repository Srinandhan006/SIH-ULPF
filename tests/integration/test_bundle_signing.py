"""Signature enforcement in the actual bundle-loading path (docs/problem-statement.md #6/#7):
`ParserEngine.register_pack` -> honest `signature_ok` in storage, and `bundle_require_signature`
actually rejecting unsigned/invalid bundles rather than the old hardcoded `signature_ok=True`."""
from __future__ import annotations

from pathlib import Path

import pytest

from uli.config import Settings, reset_settings_cache
from uli.security.signing import generate_keypair, sign_bytes, load_private_key

PACK = """
parser_id: vendor.signed_test
version: 1.0.0
signatures:
  - kind: prefix
    startswith: "SIGNEDTEST"
    required: true
extract:
  - kind: regex
    field: message
    pattern: '(?P<a>\\d+)'
map:
  fields:
    src_endpoint.ip: {from: a, type: str}
"""


@pytest.fixture()
def keys(tmp_path: Path):
    priv, pub = generate_keypair()
    priv_path, pub_path = tmp_path / "priv.pem", tmp_path / "pub.pem"
    priv_path.write_bytes(priv)
    pub_path.write_bytes(pub)
    return priv_path, pub_path


def test_unsigned_pack_loads_with_honest_signature_ok_false_when_not_required(stack):
    p = stack.engine.register_pack(PACK)
    row = stack.storage.get_parser(p.id)
    assert row["signature_ok"] is False
    assert row["bundle_sha256"] is not None


def test_valid_signature_marks_signature_ok_true(tmp_settings: Settings, keys):
    priv_path, pub_path = keys
    tmp_settings.bundle_pubkey_path = pub_path
    reset_settings_cache()
    from uli.bootstrap import build_stack

    st = build_stack(tmp_settings)
    try:
        sig = sign_bytes(PACK.encode("utf-8"), load_private_key(priv_path.read_bytes()))
        p = st.engine.register_pack(PACK, signature=sig)
        assert st.storage.get_parser(p.id)["signature_ok"] is True
    finally:
        st.raw_store.close()


def test_require_signature_rejects_unsigned_bundle(tmp_settings: Settings, keys):
    _, pub_path = keys
    tmp_settings.bundle_pubkey_path = pub_path
    tmp_settings.bundle_require_signature = True
    reset_settings_cache()
    from uli.bootstrap import build_stack

    st = build_stack(tmp_settings)
    try:
        with pytest.raises(ValueError, match="signature required"):
            st.engine.register_pack(PACK)
        assert st.storage.get_parser("vendor.signed_test") is None
    finally:
        st.raw_store.close()


def test_require_signature_rejects_tampered_bundle(tmp_settings: Settings, keys):
    priv_path, pub_path = keys
    tmp_settings.bundle_pubkey_path = pub_path
    tmp_settings.bundle_require_signature = True
    reset_settings_cache()
    from uli.bootstrap import build_stack

    st = build_stack(tmp_settings)
    try:
        sig = sign_bytes(PACK.encode("utf-8"), load_private_key(priv_path.read_bytes()))
        tampered = PACK.replace("1.0.0", "1.0.1")
        with pytest.raises(ValueError, match="signature required"):
            st.engine.register_pack(tampered, signature=sig)
    finally:
        st.raw_store.close()


def test_require_signature_accepts_valid_signature(tmp_settings: Settings, keys):
    priv_path, pub_path = keys
    tmp_settings.bundle_pubkey_path = pub_path
    tmp_settings.bundle_require_signature = True
    reset_settings_cache()
    from uli.bootstrap import build_stack

    st = build_stack(tmp_settings)
    try:
        sig = sign_bytes(PACK.encode("utf-8"), load_private_key(priv_path.read_bytes()))
        p = st.engine.register_pack(PACK, signature=sig)
        assert p.id == "vendor.signed_test"
        assert st.storage.get_parser(p.id)["signature_ok"] is True
    finally:
        st.raw_store.close()


def test_load_parsers_dir_reads_sibling_sig_file(tmp_settings: Settings, keys, tmp_path: Path):
    priv_path, pub_path = keys
    tmp_settings.bundle_pubkey_path = pub_path
    reset_settings_cache()
    from uli.bootstrap import build_stack
    from uli.security.signing import sign_file

    vendors_dir = tmp_path / "vendors"
    vendors_dir.mkdir()
    pack_path = vendors_dir / "signed.yaml"
    pack_path.write_text(PACK)
    sig = sign_file(pack_path, priv_path)
    (vendors_dir / "signed.yaml.sig").write_text(sig)

    st = build_stack(tmp_settings)
    try:
        errors = st.engine.load_parsers_dir(vendors_dir)
        assert errors == []
        row = st.storage.get_parser("vendor.signed_test")
        assert row["signature_ok"] is True
    finally:
        st.raw_store.close()
