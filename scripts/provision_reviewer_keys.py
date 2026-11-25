"""Offline Reviewer key ceremony for Muse's cryptographic independent-review gate.

Creates an Ed25519 Reviewer keypair, writes the private key file (refuses to overwrite),
adds the public key to a trust store, and prints the SHA-256 pin to configure:

  MUSE_REVIEWER_TRUST_STORE        absolute path of the trust store JSON
  MUSE_REVIEWER_TRUST_STORE_SHA256 SHA-256 of that file (pin; any edit breaks trust)

Rules enforced here:
* ``--purpose production`` private keys must live OUTSIDE the Muse repository
  (the self-evolution engine must never be able to read or rewrite them).
* Production keys prefer the ``cryptography`` backend; the pure-Python fallback is
  refused for production unless ``--allow-pure-backend`` is given explicitly.
* ``ephemeral_test`` keys are labelled as such in the trust store and are rejected by
  verifiers running with ``require_production=True`` (the default).

Examples:
  python scripts\\provision_reviewer_keys.py keygen --purpose production --key-id rk-creator-2026 ^
      --reviewer-id creator --private-out E:\\MuseKeys\\rk-creator-2026.json ^
      --trust-store E:\\MuseKeys\\reviewer-trust-store.json
  python scripts\\provision_reviewer_keys.py revoke --key-id rk-creator-2026 --trust-store E:\\MuseKeys\\reviewer-trust-store.json
  python scripts\\provision_reviewer_keys.py show --trust-store E:\\MuseKeys\\reviewer-trust-store.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from muse.reviewer_attestation import (  # noqa: E402
    PRIVATE_KEY_SCHEMA,
    ReviewAttestationError,
    active_backend,
    build_trust_record,
    generate_seed,
    parse_trust_store,
    public_key_from_seed,
    render_trust_store,
    sha256_hex,
)


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    return True


def _load_records(trust_store: Path) -> list[dict]:
    if not trust_store.exists():
        return []
    raw = trust_store.read_bytes()
    parse_trust_store(raw)
    return json.loads(raw.decode("utf-8"))["keys"]


def _write_trust_store(trust_store: Path, records: list[dict]) -> str:
    data = render_trust_store(records)
    trust_store.parent.mkdir(parents=True, exist_ok=True)
    temporary = trust_store.with_suffix(trust_store.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, trust_store)
    return sha256_hex(trust_store.read_bytes())


def _print_pin(trust_store: Path, digest: str) -> None:
    print(json.dumps({"trust_store": str(trust_store.resolve()), "trust_store_sha256": digest}, ensure_ascii=False))
    print("\n# PowerShell (current user, persistent):")
    print(f'setx MUSE_REVIEWER_TRUST_STORE "{trust_store.resolve()}"')
    print(f"setx MUSE_REVIEWER_TRUST_STORE_SHA256 {digest}")


def cmd_keygen(args) -> int:
    private_out, trust_store = Path(args.private_out), Path(args.trust_store)
    backend = active_backend()
    if args.purpose == "production":
        if _inside_repo(private_out) or _inside_repo(trust_store):
            raise ReviewAttestationError("production private keys and trust stores must live outside the Muse repo")
        if backend != "cryptography" and not args.allow_pure_backend:
            raise ReviewAttestationError("cryptography backend unavailable; pass --allow-pure-backend to accept the "
                                         "non-constant-time pure-Python signer for this ceremony")
    if not private_out.is_absolute() or not trust_store.is_absolute():
        raise ReviewAttestationError("--private-out and --trust-store must be absolute paths")
    if private_out.exists():
        raise ReviewAttestationError("refusing to overwrite an existing private key file")
    records = _load_records(trust_store)
    if any(record["key_id"] == args.key_id for record in records):
        raise ReviewAttestationError("key_id already registered; choose a new key id (rotation = new id + revoke old)")
    seed = generate_seed()
    public = public_key_from_seed(seed)
    record = build_trust_record(key_id=args.key_id, reviewer_id=args.reviewer_id, public_key=public,
                                purpose=args.purpose)
    private_out.parent.mkdir(parents=True, exist_ok=True)
    document = {"schema": PRIVATE_KEY_SCHEMA, "key_id": args.key_id, "reviewer_id": args.reviewer_id,
                "purpose": args.purpose, "seed_hex": seed.hex(), "public_key_hex": public.hex()}
    with private_out.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(document, indent=2) + "\n")
    digest = _write_trust_store(trust_store, records + [record])
    print(json.dumps({"status": "provisioned", "key_id": args.key_id, "purpose": args.purpose, "backend": backend,
                      "public_key_fingerprint_sha256": record["fingerprint_sha256"],
                      "private_key_file": str(private_out)}, ensure_ascii=False))
    print("请把私钥文件离线保存（建议 U 盘 / BitLocker 加密盘），不要放进 Muse 仓库或任何同步盘。")
    _print_pin(trust_store, digest)
    return 0


def cmd_revoke(args) -> int:
    trust_store = Path(args.trust_store)
    records = _load_records(trust_store)
    found = False
    for record in records:
        if record["key_id"] == args.key_id:
            record["status"] = "revoked"
            found = True
    if not found:
        raise ReviewAttestationError("key_id not found in trust store")
    _print_pin(trust_store, _write_trust_store(trust_store, records))
    return 0


def cmd_show(args) -> int:
    trust_store = Path(args.trust_store)
    raw = trust_store.read_bytes()
    registry = parse_trust_store(raw)
    print(json.dumps({"trust_store_sha256": sha256_hex(raw), "backend": active_backend(), "keys": [
        {"key_id": key_id, "reviewer_id": item["reviewer_id"], "purpose": item["purpose"], "status": item["status"],
         "fingerprint_sha256": item["fingerprint_sha256"]} for key_id, item in registry.items()]},
        ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse Reviewer Ed25519 key ceremony")
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser("keygen")
    keygen.add_argument("--purpose", choices=["production", "ephemeral_test"], required=True)
    keygen.add_argument("--key-id", required=True)
    keygen.add_argument("--reviewer-id", required=True)
    keygen.add_argument("--private-out", required=True)
    keygen.add_argument("--trust-store", required=True)
    keygen.add_argument("--allow-pure-backend", action="store_true")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--key-id", required=True)
    revoke.add_argument("--trust-store", required=True)
    show = sub.add_parser("show")
    show.add_argument("--trust-store", required=True)
    args = parser.parse_args(argv)
    try:
        return {"keygen": cmd_keygen, "revoke": cmd_revoke, "show": cmd_show}[args.command](args)
    except (ReviewAttestationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
