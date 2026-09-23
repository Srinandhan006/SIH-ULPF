#!/usr/bin/env python3
"""CLI for Ed25519 bundle signing (docs/security.md §5). Used for both vendor parser packs
(`parsers/vendors/*.yaml`) and air-gap tarballs (`deployment/airgap/build_bundle.sh`) — one key
format, one signature format, one verification path either way.

    python scripts/sign_bundle.py keygen --out-dir deployment/keys
    python scripts/sign_bundle.py sign parsers/vendors/acme.yaml --key deployment/keys/bundle_private.pem
    python scripts/sign_bundle.py verify parsers/vendors/acme.yaml --sig parsers/vendors/acme.yaml.sig --pubkey deployment/keys/bundle_public.pem
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uli.security.signing import generate_keypair, sign_file, verify_file  # noqa: E402


def cmd_keygen(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    priv_path, pub_path = out_dir / "bundle_private.pem", out_dir / "bundle_public.pem"
    if priv_path.exists() and not args.force:
        print(f"refusing to overwrite existing {priv_path} (pass --force to regenerate)", file=sys.stderr)
        return 1
    priv, pub = generate_keypair()
    priv_path.write_bytes(priv)
    priv_path.chmod(0o600)
    pub_path.write_bytes(pub)
    print(f"private key: {priv_path} (keep offline / out of version control)")
    print(f"public key:  {pub_path} (distribute to ULI_BUNDLE_PUBKEY_PATH on verifying nodes)")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    sig = sign_file(Path(args.file), Path(args.key))
    out = Path(args.out) if args.out else Path(args.file).with_suffix(Path(args.file).suffix + ".sig")
    out.write_text(sig + "\n", encoding="ascii")
    print(f"signed {args.file} -> {out}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    sig = Path(args.sig).read_text(encoding="ascii").strip()
    ok = verify_file(Path(args.file), sig, Path(args.pubkey))
    print("OK: signature valid" if ok else "FAIL: signature invalid or does not match")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pk = sub.add_parser("keygen", help="generate a new Ed25519 keypair")
    pk.add_argument("--out-dir", default="deployment/keys")
    pk.add_argument("--force", action="store_true")
    pk.set_defaults(func=cmd_keygen)

    ps = sub.add_parser("sign", help="sign a file, writing a detached base64 signature")
    ps.add_argument("file")
    ps.add_argument("--key", required=True, help="Ed25519 private key PEM")
    ps.add_argument("--out", default=None, help="signature output path (default: <file>.sig)")
    ps.set_defaults(func=cmd_sign)

    pv = sub.add_parser("verify", help="verify a file against a detached signature")
    pv.add_argument("file")
    pv.add_argument("--sig", required=True)
    pv.add_argument("--pubkey", required=True, help="Ed25519 public key PEM")
    pv.set_defaults(func=cmd_verify)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
