"""Muse's proactive idea and product initiative incubator.

Research and creator-interest signals can become concrete proposals without
waiting for a direct task. Proposals are durable and can progress into prototypes.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
import re
import math
import time

from datetime import datetime, timezone
from pathlib import Path

STATUSES = {"proposed", "prioritized", "building", "testing", "shipped", "parked", "rejected"}
KINDS = {"feature", "tool", "product", "research", "workflow", "creative_output"}
CONTENT_PROFILE_FIELDS = frozenset({"topic", "hook_style", "narrative_structure", "editing_pace", "visual_style", "voice_language", "cta_style", "format"})


class InitiativeStore:
    def __init__(self, database: str | Path):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS creator_initiatives(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                creator_value TEXT NOT NULL,
                concept TEXT NOT NULL,
                first_step TEXT NOT NULL,
                expected_output TEXT NOT NULL,
                interest_signals_json TEXT NOT NULL DEFAULT '[]',
                inspiration_refs_json TEXT NOT NULL DEFAULT '[]',
                capability_gap TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 50,
                autonomy_level TEXT NOT NULL DEFAULT 'prototype',
                status TEXT NOT NULL DEFAULT 'proposed',
                outcome TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(title,kind)
            );
            CREATE INDEX IF NOT EXISTS idx_creator_initiatives_queue
            ON creator_initiatives(status,priority DESC,created_at);
            CREATE TABLE IF NOT EXISTS initiative_feedback(
                id TEXT PRIMARY KEY,
                initiative_id TEXT NOT NULL REFERENCES creator_initiatives(id) ON DELETE CASCADE,
                feedback TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('accept','revise','reject')),
                reviewer TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                evolution_cycle_id TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_initiative_feedback_timeline
            ON initiative_feedback(initiative_id,created_at,id);
            CREATE TABLE IF NOT EXISTS initiative_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                initiative_id TEXT NOT NULL REFERENCES creator_initiatives(id) ON DELETE RESTRICT,
                event_type TEXT NOT NULL CHECK(event_type IN (
                    'created','status_changed','feedback_recorded','product_built'
                )),
                from_status TEXT,
                to_status TEXT,
                actor TEXT NOT NULL,
                source TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                event_key TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(initiative_id,event_key)
            );
            CREATE INDEX IF NOT EXISTS idx_initiative_events_timeline
            ON initiative_events(initiative_id,sequence);
            CREATE TRIGGER IF NOT EXISTS initiative_events_no_update
            BEFORE UPDATE ON initiative_events
            BEGIN SELECT RAISE(ABORT,'initiative events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS initiative_events_no_delete
            BEFORE DELETE ON initiative_events
            BEGIN SELECT RAISE(ABORT,'initiative events are append-only'); END;
            CREATE TABLE IF NOT EXISTS initiative_knowledge_links(
                id TEXT PRIMARY KEY,
                initiative_id TEXT NOT NULL REFERENCES creator_initiatives(id) ON DELETE RESTRICT,
                knowledge_node_id TEXT NOT NULL,
                node_type TEXT NOT NULL CHECK(node_type IN (
                    'source','idea','experiment','claim','decision','failure','feedback','improvement_proposal'
                )),
                relationship TEXT NOT NULL CHECK(relationship IN (
                    'related','inspired_by','supports','refutes','counter_evidence',
                    'tested_by','improvement_candidate','addresses_gap'
                )),
                evolution_cycle_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(initiative_id,knowledge_node_id,relationship,evolution_cycle_id)
            );
            CREATE INDEX IF NOT EXISTS idx_initiative_knowledge_links_timeline
            ON initiative_knowledge_links(initiative_id,created_at,id);
            CREATE TRIGGER IF NOT EXISTS initiative_knowledge_links_no_update
            BEFORE UPDATE ON initiative_knowledge_links
            BEGIN SELECT RAISE(ABORT,'initiative knowledge links are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS initiative_knowledge_links_no_delete
            BEFORE DELETE ON initiative_knowledge_links
            BEGIN SELECT RAISE(ABORT,'initiative knowledge links are append-only'); END;
        """)
        feedback_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(initiative_feedback)")}
        if "evolution_cycle_id" not in feedback_columns:
            self.connection.execute("ALTER TABLE initiative_feedback ADD COLUMN evolution_cycle_id TEXT NOT NULL DEFAULT ''")
        self.connection.commit()

        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS initiative_failures(
                id TEXT PRIMARY KEY,
                initiative_id TEXT NOT NULL REFERENCES creator_initiatives(id) ON DELETE RESTRICT,
                failure_code TEXT NOT NULL,
                error_type TEXT NOT NULL,
                evolution_cycle_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_initiative_failures_timeline
                ON initiative_failures(initiative_id,created_at,id);
            CREATE TRIGGER IF NOT EXISTS initiative_failures_no_update
                BEFORE UPDATE ON initiative_failures
                BEGIN SELECT RAISE(ABORT,'initiative failures are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS initiative_failures_no_delete
                BEFORE DELETE ON initiative_failures
                BEGIN SELECT RAISE(ABORT,'initiative failures are append-only'); END;
        """)

        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS initiative_video_attempts(
                attempt_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL CHECK(mode IN ('prompt_to_video','local_assets')),
                status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
                initiative_id TEXT REFERENCES creator_initiatives(id) ON DELETE RESTRICT,
                product_id TEXT,
                version_id TEXT,
                mp4_sha256 TEXT,
                package_sha256 TEXT,
                manifest_sha256 TEXT,
                failure_code TEXT,
                error_type TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_video_attempts_status
                ON initiative_video_attempts(status,created_at);
            CREATE INDEX IF NOT EXISTS idx_video_attempts_initiative
                ON initiative_video_attempts(initiative_id,created_at);
            CREATE TABLE IF NOT EXISTS initiative_video_attempt_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL REFERENCES initiative_video_attempts(attempt_id) ON DELETE RESTRICT,
                event_type TEXT NOT NULL CHECK(event_type IN ('queued','started','succeeded','failed','cancelled')),
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS initiative_video_attempt_events_no_update
                BEFORE UPDATE ON initiative_video_attempt_events
                BEGIN SELECT RAISE(ABORT,'video attempt events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS initiative_video_attempt_events_no_delete
                BEFORE DELETE ON initiative_video_attempt_events
                BEGIN SELECT RAISE(ABORT,'video attempt events are append-only'); END;
        """)


        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS initiative_performance_snapshots(
                id TEXT PRIMARY KEY,
                initiative_id TEXT NOT NULL REFERENCES creator_initiatives(id) ON DELETE RESTRICT,
                idempotency_key TEXT NOT NULL,
                platform TEXT NOT NULL CHECK(platform IN ('youtube','tiktok','douyin','instagram','facebook','bilibili','other')),
                source TEXT NOT NULL CHECK(source IN ('official_analytics','manual_entry')),
                currency TEXT NOT NULL CHECK(length(currency)=3),
                published_at TEXT,
                captured_at TEXT NOT NULL,
                source_reference TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                views INTEGER NOT NULL DEFAULT 0 CHECK(views>=0),
                likes INTEGER NOT NULL DEFAULT 0 CHECK(likes>=0),
                comments INTEGER NOT NULL DEFAULT 0 CHECK(comments>=0),
                saves INTEGER NOT NULL DEFAULT 0 CHECK(saves>=0),
                shares INTEGER NOT NULL DEFAULT 0 CHECK(shares>=0),
                cta_clicks INTEGER NOT NULL DEFAULT 0 CHECK(cta_clicks>=0),
                qualified_leads INTEGER NOT NULL DEFAULT 0 CHECK(qualified_leads>=0),
                orders INTEGER NOT NULL DEFAULT 0 CHECK(orders>=0),
                reported_revenue_minor INTEGER NOT NULL DEFAULT 0 CHECK(reported_revenue_minor>=0),
                settled_revenue_minor INTEGER NOT NULL DEFAULT 0 CHECK(settled_revenue_minor>=0),
                production_cost_minor INTEGER NOT NULL DEFAULT 0 CHECK(production_cost_minor>=0),
                three_second_retention_pct REAL CHECK(three_second_retention_pct IS NULL OR (three_second_retention_pct>=0 AND three_second_retention_pct<=100)),
                completion_rate_pct REAL CHECK(completion_rate_pct IS NULL OR (completion_rate_pct>=0 AND completion_rate_pct<=100)),
                avg_watch_seconds REAL CHECK(avg_watch_seconds IS NULL OR avg_watch_seconds>=0),
                UNIQUE(initiative_id,idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_initiative_performance_timeline
                ON initiative_performance_snapshots(initiative_id,captured_at,id);
            CREATE TRIGGER IF NOT EXISTS initiative_performance_no_update
                BEFORE UPDATE ON initiative_performance_snapshots
                BEGIN SELECT RAISE(ABORT,'initiative performance snapshots are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS initiative_performance_no_delete
                BEFORE DELETE ON initiative_performance_snapshots
                BEGIN SELECT RAISE(ABORT,'initiative performance snapshots are append-only'); END;
        """)

        attempt_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(initiative_video_attempts)")}
        performance_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(initiative_performance_snapshots)")}
        if "content_profile_json" not in performance_columns:
            self.connection.execute("ALTER TABLE initiative_performance_snapshots ADD COLUMN content_profile_json TEXT NOT NULL DEFAULT '{}'")
        self.connection.commit()


        if "evolution_cycle_id" not in attempt_columns:
            self.connection.execute("ALTER TABLE initiative_video_attempts ADD COLUMN evolution_cycle_id TEXT NOT NULL DEFAULT ''")
        for column, declaration in (
            ("worker_id", "TEXT"), ("lease_expires_at", "REAL"),
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
            ("approval_task_id", "TEXT"), ("manifest_sha256", "TEXT"),
            ("recipe_sha256", "TEXT"),
        ):
            if column not in attempt_columns:
                self.connection.execute(f"ALTER TABLE initiative_video_attempts ADD COLUMN {column} {declaration}")
        self.connection.commit()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()
    def propose(self, title, *, kind, creator_value, concept, first_step,                expected_output, interest_signals=(), inspiration_refs=(),
                capability_gap="", priority=50, autonomy_level="prototype"):
        fields = (title, creator_value, concept, first_step, expected_output, capability_gap)
        if any(not isinstance(x, str) or not x.strip() or len(x) > 4000
               for x in fields[:5]) or len(capability_gap) > 2000:
            raise ValueError("initiative text fields must be non-empty and bounded")
        if kind not in KINDS:
            raise ValueError("unsupported initiative kind")
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100:
            raise ValueError("priority must be 0..100")
        if autonomy_level not in {"draft", "prototype", "local_safe_implementation"}:
            raise ValueError("unsupported autonomy level")
        for values in (interest_signals, inspiration_refs):
            if not isinstance(values, (list, tuple)) or len(values) > 64 or any(
                not isinstance(x, str) or len(x) > 2000 for x in values
            ):
                raise ValueError("invalid initiative references")
        now = self._now()
        item_id = uuid.uuid4().hex
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO creator_initiatives
            (id,title,kind,creator_value,concept,first_step,expected_output,
             interest_signals_json,inspiration_refs_json,capability_gap,priority,
             autonomy_level,status,outcome,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'proposed','',?,?)""",
            (item_id, title.strip(), kind, creator_value.strip(), concept.strip(),
             first_step.strip(), expected_output.strip(),
             json.dumps(list(dict.fromkeys(interest_signals)), ensure_ascii=False),
             json.dumps(list(dict.fromkeys(inspiration_refs)), ensure_ascii=False),
             capability_gap.strip(), priority, autonomy_level, now, now),
        )
        if cursor.rowcount == 1:
            self._append_event(
                item_id, "created", from_status=None, to_status="proposed",
                actor="system", source="initiative_store", details={},
                event_key="created",
            )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM creator_initiatives WHERE title=? AND kind=?",
            (title.strip(), kind)
        ).fetchone()
        return self._decode(row)

    @staticmethod
    def _decode(row):
        item = dict(row)
        item["interest_signals"] = json.loads(item.pop("interest_signals_json"))
        item["inspiration_refs"] = json.loads(item.pop("inspiration_refs_json"))
        return item

    def get_by_id(self, item_id):
        if not isinstance(item_id, str) or not re.fullmatch(r"[0-9a-f]{32}", item_id):
            raise ValueError("initiative ID must be a 32-character lowercase UUID hex string")
        row = self.connection.execute(
            "SELECT * FROM creator_initiatives WHERE id=?", (item_id,)
        ).fetchone()
        return self._decode(row) if row is not None else None

    def list(self, *, status=None, limit=50):
        if status is not None and status not in STATUSES:
            raise ValueError("unsupported initiative status")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be 1..200")
        if status is None:
            rows = self.connection.execute(
                "SELECT * FROM creator_initiatives ORDER BY priority DESC,created_at LIMIT ?",
                (limit,)
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM creator_initiatives WHERE status=? ORDER BY priority DESC,created_at LIMIT ?",
                (status, limit)
            ).fetchall()
        return [self._decode(row) for row in rows]

    def update(self, item_id, *, status, outcome="", actor="system", source="initiative_store"):
        if status not in STATUSES or not isinstance(outcome, str) or len(outcome) > 4000:
            raise ValueError("invalid initiative update")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 200:
            raise ValueError("actor must be non-empty bounded text")
        if not isinstance(source, str) or not source.strip() or len(source) > 200:
            raise ValueError("source must be non-empty bounded text")
        current = self.connection.execute(
            "SELECT status FROM creator_initiatives WHERE id=?", (item_id,)
        ).fetchone()
        if current is None:
            raise KeyError(item_id)
        previous_status = current["status"]
        self.connection.execute(
            "UPDATE creator_initiatives SET status=?,outcome=?,updated_at=? WHERE id=?",
            (status, outcome.strip(), self._now(), item_id)
        )
        if previous_status != status:
            self._append_event(
                item_id, "status_changed", from_status=previous_status,
                to_status=status, actor=actor.strip(), source=source.strip(),
                details={"outcome_recorded": bool(outcome.strip())},
            )
        self.connection.commit()
        updated = self.connection.execute(
            "SELECT * FROM creator_initiatives WHERE id=?", (item_id,)
        ).fetchone()
        return self._decode(updated)

    def record_feedback(self, initiative_id, feedback, *, decision, reviewer="creator",
                        source="manual", evolution_cycle_id=None):
        """Persist an auditable review event without changing initiative status."""
        if not isinstance(initiative_id, str) or not initiative_id.strip() or len(initiative_id) > 128:
            raise ValueError("initiative_id must be a non-empty bounded string")
        if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 8000:
            raise ValueError("feedback must be a non-empty string of at most 8000 characters")
        if not isinstance(decision, str) or decision not in {"accept", "revise", "reject"}:
            raise ValueError("decision must be accept, revise, or reject")
        for value, field in ((reviewer, "reviewer"), (source, "source")):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{field} must be a non-empty string of at most 200 characters")
        if evolution_cycle_id is not None and (
            not isinstance(evolution_cycle_id, str)
            or not re.fullmatch(r"cycle_[0-9a-f]{32}", evolution_cycle_id)
        ):
            raise ValueError("evolution_cycle_id must be a valid Muse Evolution Cycle ID")
        exists = self.connection.execute(
            "SELECT 1 FROM creator_initiatives WHERE id=?", (initiative_id,)
        ).fetchone()
        if exists is None:
            raise KeyError(initiative_id)
        feedback_id = uuid.uuid4().hex
        created_at = self._now()
        self.connection.execute(
            """INSERT INTO initiative_feedback
            (id,initiative_id,feedback,decision,reviewer,source,created_at,evolution_cycle_id)
            VALUES(?,?,?,?,?,?,?,?)""",
            (feedback_id, initiative_id, feedback.strip(), decision,
             reviewer.strip(), source.strip(), created_at, evolution_cycle_id or ""),
        )
        current = self.connection.execute(
            "SELECT status FROM creator_initiatives WHERE id=?", (initiative_id,)
        ).fetchone()
        self._append_event(
            initiative_id, "feedback_recorded",
            from_status=current["status"], to_status=current["status"],
            actor=reviewer.strip(), source=source.strip(),
            details={"feedback_id": feedback_id, "decision": decision,
                     "evolution_cycle_id": evolution_cycle_id},
            event_key=f"feedback:{feedback_id}",
        )
        self.connection.commit()
        return self._decode_feedback(self.connection.execute(
            "SELECT * FROM initiative_feedback WHERE id=?", (feedback_id,)
        ).fetchone())

    @staticmethod
    def _decode_feedback(row):
        item = dict(row)
        item["evolution_cycle_id"] = item.get("evolution_cycle_id") or None
        return item

    def list_feedback(self, *, initiative_id=None, limit=50):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be 1..200")
        if initiative_id is not None and (
            not isinstance(initiative_id, str) or not initiative_id.strip()
            or len(initiative_id) > 128
        ):
            raise ValueError("initiative_id must be a non-empty bounded string")
        if initiative_id is None:
            rows = self.connection.execute(
                "SELECT * FROM initiative_feedback ORDER BY created_at,id LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """SELECT * FROM initiative_feedback WHERE initiative_id=?
                ORDER BY created_at,id LIMIT ?""",
                (initiative_id, limit),
            ).fetchall()
        return [self._decode_feedback(row) for row in rows]

    @staticmethod
    def _decode_performance_snapshot(row):
        item = dict(row)
        item["content_profile"] = json.loads(item.pop("content_profile_json", "{}") or "{}")
        return item

    def record_performance(
        self, initiative_id, *, idempotency_key, platform, source, currency="SGD",
        metrics, published_at=None, source_reference="", notes="", content_profile=None,
    ):
        """Append one validated platform-performance snapshot; identical replays are idempotent."""
        if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
            raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
            raise ValueError("idempotency_key must contain 1-128 safe characters")
        if not isinstance(platform, str) or platform not in {"youtube", "tiktok", "douyin", "instagram", "facebook", "bilibili", "other"}:
            raise ValueError("unsupported platform")
        if not isinstance(source, str) or source not in {"official_analytics", "manual_entry"}:
            raise ValueError("source must be official_analytics or manual_entry")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("currency must be a three-letter uppercase ISO currency code")
        if published_at is not None:
            if not isinstance(published_at, str) or len(published_at) > 64:
                raise ValueError("published_at must be an ISO-8601 timestamp")
            try:
                parsed_published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("published_at must be an ISO-8601 timestamp") from exc
            if parsed_published.tzinfo is None or parsed_published.utcoffset() is None:
                raise ValueError("published_at must include a timezone")
            published_at = parsed_published.isoformat()
        for value, field, maximum in (
            (source_reference, "source_reference", 500), (notes, "notes", 1000),
        ):
            if not isinstance(value, str) or len(value) > maximum:
                raise ValueError(f"{field} must be a string of at most {maximum} characters")
        if not isinstance(metrics, dict) or not metrics:
            raise ValueError("metrics must be a non-empty object")
        if content_profile is None:
            content_profile = {}
        if not isinstance(content_profile, dict) or set(content_profile) - CONTENT_PROFILE_FIELDS:
            raise ValueError("content_profile must contain only documented creative dimensions")
        normalized_profile = {}
        for field, value in content_profile.items():
            if not isinstance(value, str) or not value.strip() or len(value) > 80 or any(ord(char) < 32 for char in value):
                raise ValueError(f"content_profile.{field} must be non-empty text of at most 80 characters")
            normalized_profile[field] = value.strip()
        content_profile_json = json.dumps(normalized_profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        count_fields = {
            "views", "likes", "comments", "saves", "shares", "cta_clicks",
            "qualified_leads", "orders", "reported_revenue_minor",
            "settled_revenue_minor", "production_cost_minor",
        }
        rate_fields = {"three_second_retention_pct", "completion_rate_pct", "avg_watch_seconds"}
        if set(metrics) - count_fields - rate_fields:
            raise ValueError("metrics contains unsupported fields")
        normalized = {}
        for field in sorted(count_fields):
            value = metrics.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
                raise ValueError(f"{field} must be a non-negative integer")
            normalized[field] = value
        for field in sorted(rate_fields):
            if field not in metrics:
                normalized[field] = None
                continue
            value = metrics[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field} must be a finite non-negative number")
            value = float(value)
            if not math.isfinite(value) or value < 0 or (field != "avg_watch_seconds" and value > 100):
                raise ValueError(f"{field} is outside its allowed range")
            normalized[field] = value
        exists = self.connection.execute(
            "SELECT 1 FROM creator_initiatives WHERE id=?", (initiative_id,)
        ).fetchone()
        if exists is None:
            raise KeyError(initiative_id)
        columns = [
            "views", "likes", "comments", "saves", "shares", "cta_clicks",
            "qualified_leads", "orders", "reported_revenue_minor", "settled_revenue_minor",
            "production_cost_minor", "three_second_retention_pct", "completion_rate_pct", "avg_watch_seconds",
        ]
        values = [normalized[field] for field in columns]
        def same_snapshot(row):
            return (
                json.loads(row["content_profile_json"] or "{}") == normalized_profile
                and row["platform"] == platform and row["source"] == source
                and row["currency"] == currency and row["published_at"] == published_at
                and row["source_reference"] == source_reference and row["notes"] == notes
                and all(row[field] == value for field, value in zip(columns, values))
            )
        existing = self.connection.execute(
            "SELECT * FROM initiative_performance_snapshots WHERE initiative_id=? AND idempotency_key=?",
            (initiative_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if not same_snapshot(existing):
                raise ValueError("idempotency_key was already used with different performance data")
            return self._decode_performance_snapshot(existing)
        snapshot_id = uuid.uuid4().hex
        captured_at = self._now()
        insert_columns = [
            "id", "initiative_id", "idempotency_key", "platform", "source", "currency",
            "published_at", "captured_at", "source_reference", "notes", *columns, "content_profile_json",
        ]
        cursor = self.connection.execute(
            f"INSERT OR IGNORE INTO initiative_performance_snapshots({','.join(insert_columns)}) VALUES({','.join('?' for _ in insert_columns)})",
            [snapshot_id, initiative_id, idempotency_key, platform, source, currency,
             published_at, captured_at, source_reference, notes, *values, content_profile_json],
        )
        if cursor.rowcount == 0:
            existing = self.connection.execute(
                "SELECT * FROM initiative_performance_snapshots WHERE initiative_id=? AND idempotency_key=?",
                (initiative_id, idempotency_key),
            ).fetchone()
            if existing is None or not same_snapshot(existing):
                raise ValueError("idempotency_key was already used with different performance data")
            return self._decode_performance_snapshot(existing)
        self.connection.commit()
        return self._decode_performance_snapshot(self.connection.execute(
            "SELECT * FROM initiative_performance_snapshots WHERE id=?", (snapshot_id,)
        ).fetchone())

    def list_performance(self, *, initiative_id, limit=50):
        if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
            raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        exists = self.connection.execute(
            "SELECT 1 FROM creator_initiatives WHERE id=?", (initiative_id,)
        ).fetchone()
        if exists is None:
            raise KeyError(initiative_id)
        rows = self.connection.execute(
            "SELECT * FROM initiative_performance_snapshots WHERE initiative_id=? ORDER BY captured_at DESC,id DESC LIMIT ?",
            (initiative_id, limit),
        ).fetchall()
        return [self._decode_performance_snapshot(row) for row in rows]
    def aggregate_performance(self, *, initiative_id):
        if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
            raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
        rows = self.connection.execute(
            """SELECT currency,COUNT(*) AS snapshots,
            SUM(views) AS views,SUM(likes) AS likes,SUM(comments) AS comments,
            SUM(saves) AS saves,SUM(shares) AS shares,SUM(cta_clicks) AS cta_clicks,
            SUM(qualified_leads) AS qualified_leads,SUM(orders) AS orders,
            SUM(reported_revenue_minor) AS reported_revenue_minor,
            SUM(settled_revenue_minor) AS settled_revenue_minor,
            SUM(production_cost_minor) AS production_cost_minor,
            AVG(three_second_retention_pct) AS avg_three_second_retention_pct,
            AVG(completion_rate_pct) AS avg_completion_rate_pct,
            AVG(avg_watch_seconds) AS avg_watch_seconds
            FROM initiative_performance_snapshots WHERE initiative_id=? GROUP BY currency ORDER BY currency""",
            (initiative_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def analyze_performance(self, *, initiative_id, min_sample_count=3, min_views=1000):
        """Return cautious per-platform cohorts using only each identified item's latest snapshot.

        Snapshots are repeated measurements, not additive events. Empty source references
        cannot identify a content item and are excluded from comparisons.
        """
        if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
            raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
        if isinstance(min_sample_count, bool) or not isinstance(min_sample_count, int) or not 2 <= min_sample_count <= 100:
            raise ValueError("min_sample_count must be between 2 and 100")
        if isinstance(min_views, bool) or not isinstance(min_views, int) or not 0 <= min_views <= 2**63 - 1:
            raise ValueError("min_views must be a non-negative integer")
        if self.connection.execute("SELECT 1 FROM creator_initiatives WHERE id=?", (initiative_id,)).fetchone() is None:
            raise KeyError(initiative_id)

        rows = self.connection.execute(
            "SELECT * FROM initiative_performance_snapshots WHERE initiative_id=? ORDER BY captured_at,id",
            (initiative_id,),
        ).fetchall()
        latest = {}
        unidentified = 0
        for row in rows:
            reference = row["source_reference"].strip()
            if not reference:
                unidentified += 1
                continue
            key = (row["platform"], row["source"], row["currency"], reference)
            previous = latest.get(key)
            if previous is None or (row["captured_at"], row["id"]) > (previous["captured_at"], previous["id"]):
                latest[key] = dict(row)

        cohorts = {}
        count_fields = (
            "likes", "comments", "saves", "shares", "cta_clicks",
            "qualified_leads", "orders", "reported_revenue_minor",
            "settled_revenue_minor", "production_cost_minor",
        )
        for (platform, source, currency, _reference), snapshot in latest.items():
            key = (platform, source, currency)
            cohort = cohorts.setdefault(key, {
                "platform": platform, "source": source, "currency": currency,
                "sample_count": 0, "total_views": 0,
                **{field: 0 for field in count_fields},
                "_retention_sum": 0.0, "_retention_views": 0,
                "_completion_sum": 0.0, "_completion_views": 0,
                "_watch_sum": 0.0, "_watch_views": 0,
                "_items": [],
            })
            cohort["sample_count"] += 1
            views = int(snapshot["views"] or 0)
            item_rates = {
                "engagement_per_view_pct": 100.0 * sum(int(snapshot[field] or 0) for field in ("likes", "comments", "saves", "shares")) / views if views else None,
                "cta_clicks_per_view_pct": 100.0 * int(snapshot["cta_clicks"] or 0) / views if views else None,
                "qualified_leads_per_view_pct": 100.0 * int(snapshot["qualified_leads"] or 0) / views if views else None,
                "orders_per_view_pct": 100.0 * int(snapshot["orders"] or 0) / views if views else None,
                "three_second_retention_pct": snapshot["three_second_retention_pct"],
                "completion_rate_pct": snapshot["completion_rate_pct"],
                "avg_watch_seconds": snapshot["avg_watch_seconds"],
            }
            cohort["_items"].append({
                "source_reference": _reference, "views": views, "rates": item_rates,
                "content_profile": json.loads(snapshot["content_profile_json"] or "{}"),
                "revenue_minor": {
                    "reported": int(snapshot["reported_revenue_minor"] or 0),
                    "settled": int(snapshot["settled_revenue_minor"] or 0),
                    "production_cost": int(snapshot["production_cost_minor"] or 0),
                },
            })
            cohort["total_views"] += views
            for field in count_fields:
                cohort[field] += int(snapshot[field] or 0)
            if views:
                for field, total_key, weight_key in (
                    ("three_second_retention_pct", "_retention_sum", "_retention_views"),
                    ("completion_rate_pct", "_completion_sum", "_completion_views"),
                    ("avg_watch_seconds", "_watch_sum", "_watch_views"),
                ):
                    value = snapshot[field]
                    if value is not None:
                        cohort[total_key] += float(value) * views
                        cohort[weight_key] += views

        results = []
        for cohort in cohorts.values():
            views = cohort["total_views"]
            enough_samples = cohort["sample_count"] >= min_sample_count
            enough_views = views >= min_views
            warnings = []
            if not enough_samples:
                warnings.append("insufficient_content_samples")
            if not enough_views:
                warnings.append("insufficient_views")
            if unidentified:
                warnings.append("unidentified_snapshots_excluded")
            rates = {
                "engagement_per_view_pct": 100.0 * sum(cohort[f] for f in ("likes", "comments", "saves", "shares")) / views if views else None,
                "cta_clicks_per_view_pct": 100.0 * cohort["cta_clicks"] / views if views else None,
                "qualified_leads_per_view_pct": 100.0 * cohort["qualified_leads"] / views if views else None,
                "orders_per_view_pct": 100.0 * cohort["orders"] / views if views else None,
                "three_second_retention_pct": cohort["_retention_sum"] / cohort["_retention_views"] if cohort["_retention_views"] else None,
                "completion_rate_pct": cohort["_completion_sum"] / cohort["_completion_views"] if cohort["_completion_views"] else None,
                "avg_watch_seconds": cohort["_watch_sum"] / cohort["_watch_views"] if cohort["_watch_views"] else None,
            }
            ranked_metrics = {}
            for metric in ("three_second_retention_pct", "completion_rate_pct", "cta_clicks_per_view_pct", "orders_per_view_pct"):
                eligible = [item for item in cohort["_items"] if item["views"] >= 100 and item["rates"][metric] is not None]
                eligible.sort(key=lambda item: (float(item["rates"][metric]), item["source_reference"]))
                comparable = enough_samples and enough_views and len(eligible) >= 3
                ranked_metrics[metric] = {
                    "status": "descriptive_comparison" if comparable else "insufficient_item_samples",
                    "eligible_item_count": len(eligible),
                    "top": (eligible[-3:][::-1] if len(eligible) >= 6 else ([eligible[-1]] if comparable else [])),
                    "bottom": (eligible[:3] if len(eligible) >= 6 else ([eligible[0]] if comparable else [])),
                }
            results.append({
                "platform": cohort["platform"], "source": cohort["source"], "currency": cohort["currency"],
                "sample_count": cohort["sample_count"], "min_sample_count": min_sample_count,
                "total_views": views, "min_views": min_views,
                "status": "ready_for_review" if enough_samples and enough_views else "insufficient_sample",
                "rates": rates,
                "item_comparisons": ranked_metrics,
                "item_comparison_min_views": 100,
                "comparison_note": "Metric-specific descriptive ranking only; not an overall success score or causal evidence.",
                "counts": {field: cohort[field] for field in count_fields[:7]},
                "revenue_minor": {
                    "reported": cohort["reported_revenue_minor"],
                    "settled": cohort["settled_revenue_minor"],
                    "production_cost": cohort["production_cost_minor"],
                },
                "warnings": warnings,
                "interpretation": "descriptive_observation_not_causal_evidence",
            })
        results.sort(key=lambda item: (item["platform"], item["source"], item["currency"]))
        return {
            "initiative_id": initiative_id, "status": "ready" if results else "insufficient_data",
            "cohorts": results, "unidentified_snapshots_excluded": unidentified,
            "deduplication": "latest_snapshot_per_platform_source_currency_source_reference",
        }

    def record_product_failure(
        self, initiative_id, *, failure_code="video.product_job_failed",
        error_type="RuntimeError", evolution_cycle_id=None,
    ):
        """Persist a redacted video-job failure; raw exception text is never accepted."""
        if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
            raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
        if not isinstance(failure_code, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", failure_code):
            raise ValueError("failure_code must be a stable lowercase classification")
        if not isinstance(error_type, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_type):
            raise ValueError("error_type must be a bounded exception class name")
        if evolution_cycle_id is not None and (
            not isinstance(evolution_cycle_id, str)
            or not re.fullmatch(r"cycle_[0-9a-f]{32}", evolution_cycle_id)
        ):
            raise ValueError("evolution_cycle_id must be a valid Muse Evolution Cycle ID")
        if self.connection.execute(
            "SELECT 1 FROM creator_initiatives WHERE id=?", (initiative_id,)
        ).fetchone() is None:
            raise KeyError(initiative_id)
        failure_id, created_at = uuid.uuid4().hex, self._now()
        self.connection.execute(
            "INSERT INTO initiative_failures(id,initiative_id,failure_code,error_type,evolution_cycle_id,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (failure_id, initiative_id, failure_code, error_type, evolution_cycle_id or "", created_at),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM initiative_failures WHERE id=?", (failure_id,)
        ).fetchone()
        item = dict(row)
        item["evolution_cycle_id"] = item["evolution_cycle_id"] or None
        return item

    def list_failures(self, *, initiative_id=None, limit=100):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if initiative_id is not None and (
            not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id)
        ):
            raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
        if initiative_id is None:
            rows = self.connection.execute(
                "SELECT * FROM initiative_failures ORDER BY created_at,id LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM initiative_failures WHERE initiative_id=? ORDER BY created_at,id LIMIT ?",
                (initiative_id, limit),
            ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["evolution_cycle_id"] = item["evolution_cycle_id"] or None
        return items

    def aggregate_failure_metrics(self, *, since):
        if not hasattr(since, "isoformat"):
            raise ValueError("since must be a datetime")
        rows = self.connection.execute(
            "SELECT failure_code,COUNT(*) AS count FROM initiative_failures "
            "WHERE created_at>=? GROUP BY failure_code ORDER BY failure_code",
            (since.isoformat(),),
        ).fetchall()
        by_code = {row["failure_code"]: row["count"] for row in rows}
        return {"total": sum(by_code.values()), "by_code": by_code}


    @staticmethod
    def _validate_video_attempt_id(attempt_id):
        if not isinstance(attempt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", attempt_id):
            raise ValueError("attempt_id must be 1-64 safe identifier characters")

    def start_video_attempt(self, attempt_id, *, mode, initiative_id=None, evolution_cycle_id=None, approval_task_id=None, recipe_sha256=None):
        self._validate_video_attempt_id(attempt_id)
        if approval_task_id is not None and (
            not isinstance(approval_task_id, str) or not approval_task_id.strip() or len(approval_task_id) > 128
        ):
            raise ValueError("approval_task_id must be non-empty bounded text")
        if mode not in {"prompt_to_video", "local_assets"}:
            raise ValueError("unsupported video attempt mode")
        if recipe_sha256 is not None and (
            not isinstance(recipe_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", recipe_sha256)
        ):
            raise ValueError("recipe_sha256 must be a SHA-256 digest")
        if initiative_id is not None:
            if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
                raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
            if self.connection.execute("SELECT 1 FROM creator_initiatives WHERE id=?", (initiative_id,)).fetchone() is None:
                raise KeyError(initiative_id)
        if evolution_cycle_id is not None and (
            not isinstance(evolution_cycle_id, str)
            or not re.fullmatch(r"cycle_[0-9a-f]{32}", evolution_cycle_id)
        ):
            raise ValueError("evolution_cycle_id must be a valid Muse Evolution Cycle ID")
        now = self._now()
        try:
            self.connection.execute(
                """INSERT INTO initiative_video_attempts
                (attempt_id,mode,status,initiative_id,evolution_cycle_id,approval_task_id,recipe_sha256,created_at)
                VALUES(?,?,'queued',?,?,?,?,?)""",
                (attempt_id, mode, initiative_id, evolution_cycle_id or "", approval_task_id, recipe_sha256, now),
            )
            self.connection.execute(
                "INSERT INTO initiative_video_attempt_events(attempt_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (attempt_id, "queued", json.dumps({"recipe_sha256": recipe_sha256} if recipe_sha256 else {}, separators=(",", ":")), now),
            )
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError("video attempt already exists") from exc
        return dict(self.connection.execute(
            "SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone())
    def mark_video_attempt_running(self, attempt_id):
        self._validate_video_attempt_id(attempt_id)
        row = self.connection.execute(
            "SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        if row["status"] == "running":
            return dict(row)
        if row["status"] != "queued":
            raise ValueError("only queued video attempts can start")
        now = self._now()
        self.connection.execute(
            "UPDATE initiative_video_attempts SET status='running',started_at=?,worker_id='status-api',lease_expires_at=?,attempt_count=attempt_count+1 WHERE attempt_id=? AND status='queued'",
            (now, time.time() + 3600, attempt_id),
        )
        self.connection.execute(
            "INSERT INTO initiative_video_attempt_events(attempt_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
            (attempt_id, "started", json.dumps({"worker_id": "status-api"}), now),
        )
        self.connection.commit()
        return dict(self.connection.execute(            "SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone())

    def complete_video_attempt(
        self, attempt_id, *, initiative_id=None, product_id, version_id,
        mp4_sha256, package_sha256, manifest_sha256=None,
    ):
        self._validate_video_attempt_id(attempt_id)
        for value, field in ((product_id, "product_id"), (version_id, "version_id")):
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"{field} must be non-empty bounded text")
        for value, field in ((mp4_sha256, "mp4_sha256"), (package_sha256, "package_sha256")):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{field} must be a SHA-256 digest")
        if manifest_sha256 is not None and (
            not isinstance(manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256)
        ):
            raise ValueError("manifest_sha256 must be a SHA-256 digest")
        if initiative_id is not None:
            if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
                raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
            if self.connection.execute("SELECT 1 FROM creator_initiatives WHERE id=?", (initiative_id,)).fetchone() is None:
                raise KeyError(initiative_id)
        row = self.connection.execute(
            "SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        if initiative_id is not None and row["initiative_id"] not in {None, initiative_id}:
            raise ValueError("video attempt cannot be reassigned to another initiative")
        resolved_initiative_id = initiative_id or row["initiative_id"]
        expected = (resolved_initiative_id, product_id, version_id, mp4_sha256, package_sha256, manifest_sha256)
        actual = (
            row["initiative_id"], row["product_id"], row["version_id"],
            row["mp4_sha256"], row["package_sha256"], row["manifest_sha256"],
        )
        if row["status"] == "succeeded":
            if actual == expected:
                return dict(row)
            raise ValueError("video attempt already completed with different artifact data")
        if row["status"] not in {"queued", "running"}:
            raise ValueError("video attempt is already terminal")
        now = self._now()
        self.connection.execute(
            """UPDATE initiative_video_attempts SET status='succeeded',initiative_id=?,
            product_id=?,version_id=?,mp4_sha256=?,package_sha256=?,manifest_sha256=?,finished_at=?,
            worker_id=NULL,lease_expires_at=NULL
            WHERE attempt_id=? AND status IN ('queued','running')""",
            (*expected, now, attempt_id),
        )
        self.connection.execute(
            "INSERT INTO initiative_video_attempt_events(attempt_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
            (attempt_id, "succeeded", json.dumps({
                "product_id": product_id, "version_id": version_id,
                "manifest_sha256": manifest_sha256,
                "recipe_sha256": row["recipe_sha256"],
            }, separators=(",", ":")), now),
        )
        self.connection.commit()
        return dict(self.connection.execute(
            "SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone())

    def fail_video_attempt(
        self, attempt_id, *, error_type="RuntimeError",
        failure_code="video.product_job_failed", initiative_id=None,
    ):
        self._validate_video_attempt_id(attempt_id)
        if not isinstance(error_type, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_type):
            raise ValueError("error_type must be a bounded exception class name")
        if not isinstance(failure_code, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", failure_code):
            raise ValueError("failure_code must be a stable lowercase classification")
        if initiative_id is not None:
            if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
                raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
            if self.connection.execute("SELECT 1 FROM creator_initiatives WHERE id=?", (initiative_id,)).fetchone() is None:
                raise KeyError(initiative_id)
        row = self.connection.execute(
            "SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        if initiative_id is not None and row["initiative_id"] not in {None, initiative_id}:
            raise ValueError("video attempt cannot be reassigned to another initiative")
        resolved_initiative_id = initiative_id or row["initiative_id"]
        if row["status"] == "failed":
            if row["failure_code"] == failure_code and row["error_type"] == error_type and row["initiative_id"] == resolved_initiative_id:
                return dict(row)
            raise ValueError("video attempt already failed with different failure data")
        if row["status"] not in {"queued", "running"}:
            raise ValueError("video attempt is already terminal")
        now = self._now()
        self.connection.execute(
            """UPDATE initiative_video_attempts SET status='failed',failure_code=?,
            error_type=?,finished_at=?,worker_id=NULL,lease_expires_at=NULL
            WHERE attempt_id=? AND status IN ('queued','running')""",
            (failure_code, error_type, now, attempt_id),
        )
        self.connection.execute(
            "INSERT INTO initiative_video_attempt_events(attempt_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
            (attempt_id, "failed", json.dumps({"failure_code": failure_code, "error_type": error_type}, separators=(",", ":")), now),
        )
        self.connection.commit()
        return dict(self.connection.execute(
            "SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone())

    def claim_next_video_attempt(self, worker_id, *, lease_seconds=3600, now=None, attempt_id=None):
        """Atomically claim one queued attempt; recover only expired worker leases."""
        if not isinstance(worker_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", worker_id):
            raise ValueError("worker_id must be 1-64 safe identifier characters")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 15 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 15 and 3600")
        if attempt_id is not None:
            self._validate_video_attempt_id(attempt_id)
        current = time.time() if now is None else float(now)
        if current < 0:
            raise ValueError("now must be non-negative")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            expired = self.connection.execute(
                "SELECT attempt_id FROM initiative_video_attempts WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?",
                (current,),
            ).fetchall()
            for item in expired:
                self.connection.execute(
                    "UPDATE initiative_video_attempts SET status='queued',worker_id=NULL,lease_expires_at=NULL,started_at=NULL WHERE attempt_id=? AND status='running'",
                    (item["attempt_id"],),
                )
                self.connection.execute(
                    "INSERT INTO initiative_video_attempt_events(attempt_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                    (item["attempt_id"], "queued", json.dumps({"reason": "expired_worker_lease_recovered"}), self._now()),
                )
            if attempt_id is None:
                row = self.connection.execute(
                    "SELECT * FROM initiative_video_attempts WHERE status='queued' AND cancel_requested=0 ORDER BY created_at,attempt_id LIMIT 1"
                ).fetchone()
            else:
                row = self.connection.execute(
                    "SELECT * FROM initiative_video_attempts WHERE attempt_id=? AND status='queued' AND cancel_requested=0 AND approval_task_id IS NOT NULL",
                    (attempt_id,),
                ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            cursor = self.connection.execute(
                "UPDATE initiative_video_attempts SET status='running',worker_id=?,lease_expires_at=?,attempt_count=attempt_count+1,started_at=? WHERE attempt_id=? AND status='queued'",
                (worker_id, current + lease_seconds, self._now(), row["attempt_id"]),
            )
            if cursor.rowcount != 1:
                self.connection.rollback()
                return None
            self.connection.execute(
                "INSERT INTO initiative_video_attempt_events(attempt_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (row["attempt_id"], "started", json.dumps({"worker_id": worker_id}), self._now()),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_video_attempt(row["attempt_id"])

    def release_video_attempt(self, attempt_id, *, worker_id, reason="worker_yielded"):
        self._validate_video_attempt_id(attempt_id)
        if not isinstance(worker_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", worker_id):
            raise ValueError("worker_id must be 1-64 safe identifier characters")
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", reason):
            raise ValueError("reason must be a stable lowercase release code")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            if row["status"] != "running" or row["worker_id"] != worker_id:
                raise ValueError("only the current owning worker can release a running attempt")
            now = self._now()
            self.connection.execute(
                "UPDATE initiative_video_attempts SET status='queued',worker_id=NULL,lease_expires_at=NULL,started_at=NULL WHERE attempt_id=? AND status='running' AND worker_id=?",
                (attempt_id, worker_id),
            )
            self.connection.execute(
                "INSERT INTO initiative_video_attempt_events(attempt_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (attempt_id, "queued", json.dumps({"reason": reason, "worker_id": worker_id}), now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_video_attempt(attempt_id)

    def cancel_video_attempt(self, attempt_id, *, reason="creator_cancelled"):
        self._validate_video_attempt_id(attempt_id)
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", reason):
            raise ValueError("reason must be a stable lowercase cancellation code")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            if row["status"] == "cancelled":
                self.connection.commit()
                return dict(row)
            if row["status"] != "queued":
                raise ValueError("only queued video attempts can be cancelled safely")
            now = self._now()
            self.connection.execute(
                "UPDATE initiative_video_attempts SET status='cancelled',finished_at=? WHERE attempt_id=? AND status='queued'",
                (now, attempt_id),
            )
            self.connection.execute(
                "INSERT INTO initiative_video_attempt_events(attempt_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (attempt_id, "cancelled", json.dumps({"reason": reason}), now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_video_attempt(attempt_id)

    def retry_failed_video_attempt(self, attempt_id, *, max_attempts=2):
        self._validate_video_attempt_id(attempt_id)
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            if row["status"] != "failed":
                raise ValueError("only failed video attempts can be retried")
            if int(row["attempt_count"] or 0) >= max_attempts:
                raise ValueError("video attempt retry budget exhausted")
            now = self._now()
            self.connection.execute(
                "UPDATE initiative_video_attempts SET status='queued',failure_code=NULL,error_type=NULL,finished_at=NULL,worker_id=NULL,lease_expires_at=NULL,cancel_requested=0 WHERE attempt_id=? AND status='failed'",
                (attempt_id,),
            )
            self.connection.execute(
                "INSERT INTO initiative_video_attempt_events(attempt_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (attempt_id, "queued", json.dumps({"reason": "explicit_retry", "previous_attempt_count": int(row["attempt_count"] or 0)}), now),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.get_video_attempt(attempt_id)

    def list_video_attempt_events(self, attempt_id):
        self._validate_video_attempt_id(attempt_id)
        rows = self.connection.execute(
            "SELECT event_type,details_json,created_at FROM initiative_video_attempt_events WHERE attempt_id=? ORDER BY sequence",
            (attempt_id,),
        ).fetchall()
        return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]

    def get_video_attempt(self, attempt_id):
        """Return one durable video-attempt record by its opaque job identifier."""
        self._validate_video_attempt_id(attempt_id)
        row = self.connection.execute(
            "SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def list_queued_approval_video_attempts(self, *, limit=200):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.connection.execute(
            "SELECT * FROM initiative_video_attempts WHERE status='queued' AND cancel_requested=0 "
            "AND approval_task_id IS NOT NULL ORDER BY created_at,attempt_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_video_attempts(self, *, limit=100):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.connection.execute(
            "SELECT * FROM initiative_video_attempts ORDER BY created_at DESC,attempt_id DESC LIMIT ?",            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def aggregate_video_attempt_metrics(self):
        row = self.connection.execute(
            """SELECT COUNT(*) AS total,
            SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status IN ('queued','running') THEN 1 ELSE 0 END) AS pending
            FROM initiative_video_attempts"""
        ).fetchone()
        total = int(row["total"] or 0)
        succeeded = int(row["succeeded"] or 0)
        failed = int(row["failed"] or 0)
        pending = int(row["pending"] or 0)
        terminal = succeeded + failed
        return {
            "total": total, "succeeded": succeeded, "failed": failed, "pending": pending,
            "terminal": terminal, "success_rate": succeeded / terminal if terminal else None,
            "scope": "persisted_video_attempts",
        }

    def _append_event(        self, initiative_id, event_type, *, from_status, to_status,
        actor, source, details, event_key=None,
    ):
        if event_type not in {"created", "status_changed", "feedback_recorded", "product_built"}:
            raise ValueError("unsupported initiative event type")
        for value, field in ((actor, "actor"), (source, "source")):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{field} must be non-empty bounded text")
        if not isinstance(details, dict):
            raise ValueError("event details must be an object")
        details_json = json.dumps(
            details, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        if len(details_json.encode("utf-8")) > 4096:
            raise ValueError("event details exceed 4096 bytes")
        event_id = uuid.uuid4().hex
        self.connection.execute(
            """INSERT OR IGNORE INTO initiative_events
            (id,initiative_id,event_type,from_status,to_status,actor,source,
             details_json,event_key,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (event_id, initiative_id, event_type, from_status, to_status,
             actor.strip(), source.strip(), details_json, event_key, self._now()),
        )
        return event_id

    def record_product_artifact(
        self, initiative_id, *, relative_path, package_sha256, validated,
        artifact_metadata=None,
    ):
        if not isinstance(initiative_id, str) or not initiative_id.strip() or len(initiative_id) > 128:
            raise ValueError("initiative_id must be non-empty bounded text")
        if not isinstance(relative_path, str) or len(relative_path) > 1000:
            raise ValueError("product artifact path must be inside data/products")
        normalized_path = relative_path.replace("\\", "/")
        parts = normalized_path.split("/")
        if (
            parts[:2] != ["data", "products"]
            or normalized_path.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("product artifact path must be inside data/products")
        relative_path = normalized_path
        if not isinstance(package_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", package_sha256):
            raise ValueError("package_sha256 must be a lowercase SHA-256 digest")
        if validated is not True:
            raise ValueError("only validated product artifacts can be recorded")
        current = self.connection.execute(
            "SELECT status FROM creator_initiatives WHERE id=?", (initiative_id,)
        ).fetchone()
        if current is None:
            raise KeyError(initiative_id)
        details = {
            "relative_path": relative_path,
            "package_sha256": package_sha256,
            "validated": True,
        }
        if artifact_metadata is None:
            artifact_metadata = {}
        if not isinstance(artifact_metadata, dict):
            raise ValueError("artifact_metadata must be an object")
        try:
            metadata_json = json.dumps(
                artifact_metadata, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("artifact_metadata must contain finite JSON values") from exc
        if len(metadata_json.encode("utf-8")) > 3072:
            raise ValueError("artifact_metadata exceeds 3072 bytes")
        cycle_id = artifact_metadata.get("evolution_cycle_id")
        if cycle_id is not None and (
            not isinstance(cycle_id, str) or not re.fullmatch(r"cycle_[0-9a-f]{32}", cycle_id)
        ):
            raise ValueError("artifact_metadata evolution_cycle_id is invalid")
        details["artifact_metadata"] = artifact_metadata
        event_key = f"product:{package_sha256}:{cycle_id or 'unscoped'}"
        existing = self.connection.execute(
            "SELECT details_json FROM initiative_events WHERE initiative_id=? AND event_key=?",
            (initiative_id, event_key),
        ).fetchone()
        if existing is not None:
            if json.loads(existing["details_json"]) != details:
                raise ValueError("product artifact idempotency key conflicts with existing event")
            return self._decode_event(self.connection.execute(
                "SELECT * FROM initiative_events WHERE initiative_id=? AND event_key=?",
                (initiative_id, event_key),
            ).fetchone())
        self._append_event(
            initiative_id, "product_built",
            from_status=current["status"], to_status=current["status"],
            actor="system", source="local_product_builder",
            details=details, event_key=event_key,
        )
        self.connection.commit()
        return self._decode_event(self.connection.execute(
            "SELECT * FROM initiative_events WHERE initiative_id=? AND event_key=?",
            (initiative_id, event_key),
        ).fetchone())

    def record_knowledge_link(
        self, initiative_id, knowledge_node_id, *, node_type, relationship="related",
        evolution_cycle_id=None,
    ):
        """Append an idempotent link from an initiative to a research/evolution entity."""
        if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
            raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
        if not isinstance(knowledge_node_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", knowledge_node_id):
            raise ValueError("knowledge_node_id must be a safe bounded reference")
        if node_type not in {"source", "idea", "experiment", "claim", "decision", "failure", "feedback", "improvement_proposal"}:
            raise ValueError("unsupported linked knowledge node type")
        if relationship not in {"related", "inspired_by", "supports", "refutes", "counter_evidence", "tested_by", "improvement_candidate", "addresses_gap"}:
            raise ValueError("unsupported initiative knowledge relationship")
        if evolution_cycle_id is not None and (
            not isinstance(evolution_cycle_id, str)
            or not re.fullmatch(r"cycle_[0-9a-f]{32}", evolution_cycle_id)
        ):
            raise ValueError("evolution_cycle_id must be a valid Muse Evolution Cycle ID")
        if self.connection.execute("SELECT 1 FROM creator_initiatives WHERE id=?", (initiative_id,)).fetchone() is None:
            raise KeyError(initiative_id)
        link_id = uuid.uuid5(uuid.NAMESPACE_URL, f"muse:{initiative_id}:{knowledge_node_id}:{relationship}:{evolution_cycle_id or 'unscoped'}").hex
        created_at = self._now()
        self.connection.execute(
            "INSERT OR IGNORE INTO initiative_knowledge_links"
            "(id,initiative_id,knowledge_node_id,node_type,relationship,evolution_cycle_id,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (link_id, initiative_id, knowledge_node_id, node_type, relationship, evolution_cycle_id or "", created_at),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM initiative_knowledge_links WHERE initiative_id=? AND knowledge_node_id=? AND relationship=? AND evolution_cycle_id=?",
            (initiative_id, knowledge_node_id, relationship, evolution_cycle_id or ""),
        ).fetchone()
        return dict(row)

    @staticmethod
    def _decode_event(row):
        event = dict(row)
        event["details"] = json.loads(event.pop("details_json"))
        return event

    def list_events(self, *, initiative_id, limit=100):
        if not isinstance(initiative_id, str) or not initiative_id.strip() or len(initiative_id) > 128:
            raise ValueError("initiative_id must be non-empty bounded text")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self.connection.execute(
            """SELECT * FROM (
                SELECT * FROM initiative_events WHERE initiative_id=?
                ORDER BY sequence DESC LIMIT ?
            ) ORDER BY sequence ASC""",
            (initiative_id, limit),
        ).fetchall()
        return [self._decode_event(row) for row in rows]

    def summary(self):
        rows = self.connection.execute(
            "SELECT status,COUNT(*) n FROM creator_initiatives GROUP BY status"
        ).fetchall()
        counts = {row["status"]: row["n"] for row in rows}
        return {"total": sum(counts.values()), "by_status": counts,
                "ready_to_prioritize": counts.get("proposed", 0)}

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
