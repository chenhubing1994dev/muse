"""Ironclad settled-revenue and net-contribution ledger with signed payment webhooks (WS7).

Accounting invariants (enforced in code and SQLite, covered by tests):
* Attention / funnel signals (view, like, lead, inquiry, cart_checkout,
  pending_order) are stored as counts and NEVER contribute to revenue.
* ``settled_revenue`` only increases from a settlement backed by a verified
  receipt: an HMAC-verified provider webhook or a creator-imported provider
  statement file whose SHA-256 matches. Unverified money is rejected.
* ``net_contribution = settled_revenue - refunds - chargebacks
  - platform_and_payment_fees - measured_compute_and_tool_costs``.
* Rows are append-only and hash-chained; ``(source, external_id)`` is unique,
  so webhook replays are idempotent no-ops.
* With no imported settlement the ledger reports exactly ``US$0.00``.

Live provider secrets stay behind gates G5/G9: the secret is read from an
environment variable at runtime and never stored in the ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SIGNAL_KINDS = frozenset({"view", "like", "lead", "inquiry", "cart_checkout", "pending_order"})
MONEY_IN_KINDS = frozenset({"settlement"})
MONEY_OUT_KINDS = frozenset({"refund", "chargeback", "platform_fee", "payment_fee", "compute_cost", "tool_cost"})
MONEY_KINDS = MONEY_IN_KINDS | MONEY_OUT_KINDS
RECEIPT_KINDS = frozenset({"hmac_webhook", "provider_statement"})
_CURRENCY = re.compile(r"[A-Z]{3}")
_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,159}")
_GENESIS = "0" * 64
CURRENCY_SYMBOLS = {"USD": "US$", "HKD": "HK$", "CNY": "¥", "EUR": "€", "GBP": "£"}

# Default provider event mapping. A completed checkout is only a pending order: money is
# recognised when the provider reports it as settled/available, never at checkout time.
DEFAULT_EVENT_MAP = {
    "checkout.session.completed": "pending_order",
    "order_created": "pending_order",
    "charge.succeeded": "pending_order",
    "balance.available": "settlement",
    "payout.paid": "settlement",
    "sale.settled": "settlement",
    "charge.refunded": "refund",
    "refund.created": "refund",
    "order_refunded": "refund",
    "charge.dispute.created": "chargeback",
    "dispute.created": "chargeback",
}


class LedgerError(ValueError):
    """Raised when an entry would violate an accounting invariant."""


class WebhookRejected(LedgerError):
    """Raised when a provider webhook fails signature, freshness, schema or replay checks."""


class VerifiedReceipt:
    """Evidence that money actually moved. Only created by the verifiers in this module."""

    __slots__ = ("kind", "provider", "external_id", "sha256", "_seal")
    _SEAL = object()

    def __init__(self, kind: str, provider: str, external_id: str, sha256: str, seal: object):
        if seal is not VerifiedReceipt._SEAL:
            raise LedgerError("VerifiedReceipt can only be produced by a receipt verifier")
        if kind not in RECEIPT_KINDS:
            raise LedgerError("unknown receipt kind")
        self.kind, self.provider, self.external_id, self.sha256 = kind, provider, external_id, sha256
        self._seal = seal

    def is_genuine(self) -> bool:
        return self._seal is VerifiedReceipt._SEAL


def _ident(value: Any, name: str) -> str:
    if not isinstance(value, str) or _IDENT.fullmatch(value) is None:
        raise LedgerError(f"{name} must be a safe identifier")
    return value


def _minor_units(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10 ** 12:
        raise LedgerError("amounts are non-negative integer minor units (e.g. cents)")
    return value


def format_money(minor: int, currency: str) -> str:
    sign = "-" if minor < 0 else ""
    whole, cents = divmod(abs(int(minor)), 100)
    return f"{sign}{CURRENCY_SYMBOLS.get(currency, currency + ' ')}{whole:,}.{cents:02d}"


def verify_provider_statement(path: str | Path, *, expected_sha256: str, provider: str,
                              external_id: str) -> VerifiedReceipt:
    """A creator-exported settlement statement (CSV/PDF) whose SHA-256 the creator confirmed."""
    candidate = Path(path)
    if not candidate.is_file() or candidate.is_symlink():
        raise LedgerError("provider statement must be a regular file")
    if candidate.stat().st_size > 50 * 1024 * 1024:
        raise LedgerError("provider statement exceeds 50 MiB")
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if not isinstance(expected_sha256, str) or not hmac.compare_digest(digest, expected_sha256.lower()):
        raise LedgerError("provider statement SHA-256 does not match the confirmed value")
    return VerifiedReceipt("provider_statement", _ident(provider, "provider"), _ident(external_id, "external_id"),
                           digest, VerifiedReceipt._SEAL)


class WebhookVerifier:
    """Stripe-style ``t=<unix>,v1=<hex hmac-sha256(t + '.' + body)>`` verification."""

    def __init__(self, provider: str, secret: bytes, *, tolerance_seconds: int = 300, clock=time.time,
                 event_map: dict[str, str] | None = None):
        if not isinstance(secret, (bytes, bytearray)) or len(secret) < 16:
            raise LedgerError("webhook secret must be at least 16 bytes")
        self.provider = _ident(provider, "provider")
        self._secret = bytes(secret)
        self.tolerance = int(tolerance_seconds)
        self.clock = clock
        self.event_map = dict(DEFAULT_EVENT_MAP if event_map is None else event_map)

    @staticmethod
    def sign(secret: bytes, body: bytes, timestamp: int) -> str:
        digest = hmac.new(secret, str(timestamp).encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={digest}"

    def verify(self, body: bytes, signature_header: str) -> tuple[dict[str, Any], VerifiedReceipt | None, str]:
        if not isinstance(body, (bytes, bytearray)) or len(body) > 512 * 1024:
            raise WebhookRejected("webhook body missing or larger than 512 KiB")
        if not isinstance(signature_header, str) or len(signature_header) > 1024:
            raise WebhookRejected("webhook signature header missing")
        parts: dict[str, list[str]] = {}
        for item in signature_header.split(","):
            key, _, value = item.strip().partition("=")
            parts.setdefault(key, []).append(value)
        try:
            timestamp = int(parts.get("t", [""])[0])
        except ValueError as exc:
            raise WebhookRejected("webhook timestamp is malformed") from exc
        if abs(int(self.clock()) - timestamp) > self.tolerance:
            raise WebhookRejected("webhook timestamp outside tolerance (possible replay)")
        expected = self.sign(self._secret, bytes(body), timestamp).split("v1=", 1)[1]
        if not any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", [])):
            raise WebhookRejected("webhook signature verification failed")
        try:
            event = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookRejected("webhook body is not valid UTF-8 JSON") from exc
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not isinstance(event.get("type"), str):
            raise WebhookRejected("webhook event must include string id and type")
        kind = self.event_map.get(event["type"])
        if kind is None:
            raise WebhookRejected(f"unmapped webhook event type: {event['type'][:80]}")
        receipt = None
        if kind in MONEY_KINDS:
            receipt = VerifiedReceipt("hmac_webhook", self.provider, _ident(event["id"], "event id"),
                                      hashlib.sha256(bytes(body)).hexdigest(), VerifiedReceipt._SEAL)
        return event, receipt, kind


class RevenueLedger:
    def __init__(self, database: str | Path):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        kinds = ",".join(f"'{kind}'" for kind in sorted(SIGNAL_KINDS | MONEY_KINDS))
        self.connection.executescript(f"""
            CREATE TABLE IF NOT EXISTS ledger_entries(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ({kinds})),
                amount_minor INTEGER NOT NULL DEFAULT 0 CHECK(amount_minor >= 0),
                quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
                currency TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                product_ref TEXT NOT NULL DEFAULT '',
                receipt_kind TEXT NOT NULL DEFAULT '',
                receipt_sha256 TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL UNIQUE,
                UNIQUE(source, external_id),
                CHECK(kind NOT IN ('view','like','lead','inquiry','cart_checkout','pending_order') OR amount_minor = 0),
                CHECK(kind <> 'settlement' OR (receipt_kind IN ('hmac_webhook','provider_statement')
                                               AND length(receipt_sha256) = 64))
            );
            CREATE INDEX IF NOT EXISTS idx_ledger_kind ON ledger_entries(kind, currency);
            CREATE TRIGGER IF NOT EXISTS ledger_entries_no_update BEFORE UPDATE ON ledger_entries
                BEGIN SELECT RAISE(ABORT,'ledger entries are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS ledger_entries_no_delete BEFORE DELETE ON ledger_entries
                BEGIN SELECT RAISE(ABORT,'ledger entries are append-only'); END;
        """)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _append(self, **fields: Any) -> dict[str, Any]:
        fields.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                "SELECT * FROM ledger_entries WHERE source=? AND external_id=?",
                (fields["source"], fields["external_id"]),
            ).fetchone()
            if existing is not None:
                self.connection.execute("ROLLBACK")
                same = (existing["kind"] == fields["kind"] and existing["amount_minor"] == fields.get("amount_minor", 0)
                        and existing["currency"] == fields.get("currency", ""))
                if not same:
                    raise LedgerError("idempotency conflict: external_id reused with different content")
                return {**dict(existing), "duplicate": True}
            last = self.connection.execute("SELECT row_hash FROM ledger_entries ORDER BY sequence DESC LIMIT 1").fetchone()
            prev_hash = last["row_hash"] if last else _GENESIS
            body = {key: fields.get(key, "" if key not in ("amount_minor", "quantity") else 0) for key in (
                "kind", "amount_minor", "quantity", "currency", "source", "external_id", "product_ref",
                "receipt_kind", "receipt_sha256", "note", "created_at")}
            row_hash = hashlib.sha256((prev_hash + json.dumps(body, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
            self.connection.execute(
                "INSERT INTO ledger_entries(kind,amount_minor,quantity,currency,source,external_id,product_ref,"
                "receipt_kind,receipt_sha256,note,created_at,prev_hash,row_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (body["kind"], body["amount_minor"], body["quantity"], body["currency"], body["source"],
                 body["external_id"], body["product_ref"], body["receipt_kind"], body["receipt_sha256"],
                 body["note"], body["created_at"], prev_hash, row_hash),
            )
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        return {**body, "prev_hash": prev_hash, "row_hash": row_hash, "duplicate": False}

    def record_signal(self, kind: str, *, source: str, external_id: str, quantity: int = 1,
                      product_ref: str = "") -> dict[str, Any]:
        if kind not in SIGNAL_KINDS:
            raise LedgerError(f"{kind!r} is not an attention/funnel signal")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 <= quantity <= 10 ** 9:
            raise LedgerError("quantity must be a non-negative integer")
        return self._append(kind=kind, quantity=quantity, source=_ident(source, "source"),
                            external_id=_ident(external_id, "external_id"), product_ref=str(product_ref)[:200])

    def record_money(self, kind: str, *, amount_minor: int, currency: str, source: str, external_id: str,
                     receipt: VerifiedReceipt | None = None, product_ref: str = "", note: str = "") -> dict[str, Any]:
        if kind not in MONEY_KINDS:
            raise LedgerError(f"{kind!r} is not a money movement")
        if not isinstance(currency, str) or _CURRENCY.fullmatch(currency) is None:
            raise LedgerError("currency must be an ISO-4217 code")
        receipt_kind = receipt_sha = ""
        if kind == "settlement":
            if not isinstance(receipt, VerifiedReceipt) or not receipt.is_genuine():
                raise LedgerError("settled revenue requires a verified settlement receipt")
            if receipt.external_id != external_id or receipt.provider != source:
                raise LedgerError("receipt does not belong to this settlement")
            receipt_kind, receipt_sha = receipt.kind, receipt.sha256
        elif receipt is not None and isinstance(receipt, VerifiedReceipt) and receipt.is_genuine():
            receipt_kind, receipt_sha = receipt.kind, receipt.sha256
        return self._append(kind=kind, amount_minor=_minor_units(amount_minor), currency=currency,
                            source=_ident(source, "source"), external_id=_ident(external_id, "external_id"),
                            receipt_kind=receipt_kind, receipt_sha256=receipt_sha,
                            product_ref=str(product_ref)[:200], note=str(note)[:500])

    def ingest_webhook(self, verifier: WebhookVerifier, body: bytes, signature_header: str) -> dict[str, Any]:
        event, receipt, kind = verifier.verify(body, signature_header)
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if kind in SIGNAL_KINDS:
            return self.record_signal(kind, source=verifier.provider, external_id=event["id"],
                                      product_ref=str(data.get("product_ref", ""))[:200])
        amount, currency = data.get("amount_minor"), str(data.get("currency", "")).upper()
        return self.record_money(kind, amount_minor=amount, currency=currency, source=verifier.provider,
                                 external_id=event["id"], receipt=receipt,
                                 product_ref=str(data.get("product_ref", ""))[:200])

    def verify_chain(self) -> bool:
        previous = _GENESIS
        for row in self.connection.execute("SELECT * FROM ledger_entries ORDER BY sequence"):
            body = {key: row[key] for key in ("kind", "amount_minor", "quantity", "currency", "source", "external_id",
                                               "product_ref", "receipt_kind", "receipt_sha256", "note", "created_at")}
            expected = hashlib.sha256((previous + json.dumps(body, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
            if row["prev_hash"] != previous or row["row_hash"] != expected:
                return False
            previous = row["row_hash"]
        return True

    def summary(self, currency: str = "USD") -> dict[str, Any]:
        totals = {kind: 0 for kind in MONEY_KINDS}
        for row in self.connection.execute(
                "SELECT kind, SUM(amount_minor) AS total FROM ledger_entries WHERE currency=? GROUP BY kind", (currency,)):
            if row["kind"] in totals:
                totals[row["kind"]] = int(row["total"] or 0)
        signals = {kind: 0 for kind in SIGNAL_KINDS}
        for row in self.connection.execute("SELECT kind, SUM(quantity) AS total FROM ledger_entries GROUP BY kind"):
            if row["kind"] in signals:
                signals[row["kind"]] = int(row["total"] or 0)
        settled = totals["settlement"]
        fees = totals["platform_fee"] + totals["payment_fee"]
        costs = totals["compute_cost"] + totals["tool_cost"]
        net = settled - totals["refund"] - totals["chargeback"] - fees - costs
        settled_count = self.connection.execute(
            "SELECT count(*) FROM ledger_entries WHERE kind='settlement' AND currency=?", (currency,)).fetchone()[0]
        return {
            "currency": currency,
            "settled_revenue_minor": settled,
            "refunds_minor": totals["refund"],
            "chargebacks_minor": totals["chargeback"],
            "platform_and_payment_fees_minor": fees,
            "measured_compute_and_tool_costs_minor": costs,
            "net_contribution_minor": net,
            "settled_revenue_display": format_money(settled, currency),
            "net_contribution_display": format_money(net, currency),
            "verified_settlements": int(settled_count),
            "signals_not_revenue": signals,
            "chain_intact": self.verify_chain(),
            "status_label": "COMMERCIALLY_VALIDATED" if settled_count else "NO_SETTLED_REVENUE_YET",
        }


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(prog="muse-revenue-ledger", description="Settled-revenue & net-contribution ledger.")
    parser.add_argument("--db", default=str(root / "data" / "commerce" / "revenue-ledger.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    summary = sub.add_parser("summary")
    summary.add_argument("--currency", default="USD")
    statement = sub.add_parser("import-statement", help="record a settlement backed by a provider statement file")
    statement.add_argument("--file", required=True)
    statement.add_argument("--sha256", required=True, help="SHA-256 you confirmed for the statement file")
    statement.add_argument("--provider", required=True)
    statement.add_argument("--external-id", required=True)
    statement.add_argument("--amount-minor", type=int, required=True)
    statement.add_argument("--currency", default="USD")
    webhook = sub.add_parser("ingest-webhook", help="verify and record one webhook body (secret from env)")
    webhook.add_argument("--provider", required=True)
    webhook.add_argument("--body-file", required=True)
    webhook.add_argument("--signature", required=True)
    webhook.add_argument("--secret-env", default="MUSE_PAYMENT_WEBHOOK_SECRET")
    args = parser.parse_args(argv)
    try:
        with RevenueLedger(args.db) as ledger:
            if args.command == "summary":
                result = ledger.summary(args.currency.upper())
            elif args.command == "import-statement":
                receipt = verify_provider_statement(args.file, expected_sha256=args.sha256, provider=args.provider,
                                                    external_id=args.external_id)
                result = ledger.record_money("settlement", amount_minor=args.amount_minor, currency=args.currency.upper(),
                                             source=args.provider, external_id=args.external_id, receipt=receipt)
            else:
                secret = os.environ.get(args.secret_env, "")
                if not secret:
                    raise LedgerError(f"{args.secret_env} is not set (live webhook secrets stay behind gate G9)")
                verifier = WebhookVerifier(args.provider, secret.encode("utf-8"))
                result = ledger.ingest_webhook(verifier, Path(args.body_file).read_bytes(), args.signature)
    except (LedgerError, OSError, sqlite3.DatabaseError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
