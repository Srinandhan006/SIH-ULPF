"""Ed25519 bundle signing/verification (docs/security.md §5, docs/problem-statement.md #6/#7).

A "bundle" here is just a byte sequence — a parser-pack YAML file's contents, or an air-gap
tarball. Signatures are detached (base64-encoded, carried alongside the bundle as a `.sig` file
or an API field), never embedded/executed, matching the project's "declarative, no code
execution" stance for anything vendor-supplied (docs/security.md §3).
"""
from __future__ import annotations

import base64
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def generate_keypair() -> tuple[bytes, bytes]:
    """Returns (private_key_pem, public_key_pem)."""
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8,
                             encryption_algorithm=serialization.NoEncryption())
    pub = sk.public_key().public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv, pub


def load_private_key(pem_bytes: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem_bytes, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("key is not an Ed25519 private key")
    return key


def load_public_key(pem_bytes: bytes) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem_bytes)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("key is not an Ed25519 public key")
    return key


def sign_bytes(data: bytes, private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(private_key.sign(data)).decode("ascii")


def verify_bytes(data: bytes, signature_b64: str, public_key: Ed25519PublicKey) -> bool:
    """Fail-closed: any malformed input or verification failure returns False, never raises —
    a bundle-loading path must be able to treat this as a plain reject, not a crash (P1 ethos)."""
    try:
        public_key.verify(base64.b64decode(signature_b64), data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def sign_file(path: Path, private_key_path: Path) -> str:
    sk = load_private_key(Path(private_key_path).read_bytes())
    return sign_bytes(Path(path).read_bytes(), sk)


def verify_file(path: Path, signature_b64: str, public_key_path: Path) -> bool:
    try:
        pk = load_public_key(Path(public_key_path).read_bytes())
    except (ValueError, FileNotFoundError):
        return False
    return verify_bytes(Path(path).read_bytes(), signature_b64, pk)
