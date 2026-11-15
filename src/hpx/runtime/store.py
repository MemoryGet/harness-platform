"""SQLite run log with tenant-scoped keys and append-only events."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                  tenant_id TEXT NOT NULL, run_id TEXT NOT NULL,
                  pack_id TEXT NOT NULL, pack_version TEXT NOT NULL,
                  pack_checksum TEXT NOT NULL, status TEXT NOT NULL,
                  ir_json TEXT NOT NULL, inputs_json TEXT NOT NULL,
                  started_at TEXT NOT NULL, finished_at TEXT,
                  triggered_by TEXT NOT NULL,
                  PRIMARY KEY (tenant_id, run_id)
                );
                CREATE TABLE IF NOT EXISTS run_events (
                  tenant_id TEXT NOT NULL, run_id TEXT NOT NULL,
                  seq INTEGER NOT NULL, event_type TEXT NOT NULL,
                  payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
                  PRIMARY KEY (tenant_id, run_id, seq),
                  FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, run_id)
                );
                CREATE TABLE IF NOT EXISTS gate_decisions (
                  tenant_id TEXT NOT NULL, run_id TEXT NOT NULL,
                  criterion_id TEXT NOT NULL, approver TEXT NOT NULL,
                  decision TEXT NOT NULL, note TEXT NOT NULL,
                  decided_at TEXT NOT NULL,
                  PRIMARY KEY (tenant_id, run_id, criterion_id),
                  FOREIGN KEY (tenant_id, run_id) REFERENCES runs(tenant_id, run_id)
                );
            """)

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def create_run(self, tenant_id: str, ir: dict, inputs: dict, triggered_by: str) -> str:
        run_id = str(uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (tenant_id, run_id, ir["pack"]["id"], ir["pack"]["version"],
                        ir["pack"]["checksum"], "pending", json.dumps(ir, ensure_ascii=False),
                        json.dumps(inputs, ensure_ascii=False), _now(), None, triggered_by))
            db.execute("INSERT INTO run_events VALUES (?, ?, ?, ?, ?, ?)",
                       (tenant_id, run_id, 1, "run_created", "{}", _now()))
        return run_id

    def get_run(self, tenant_id: str, run_id: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE tenant_id=? AND run_id=?", (tenant_id, run_id)).fetchone()
        if row is None:
            raise KeyError(f"找不到 run：{run_id}")
        result = dict(row)
        result["ir"] = json.loads(result.pop("ir_json"))
        result["inputs"] = json.loads(result.pop("inputs_json"))
        return result

    def events(self, tenant_id: str, run_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM run_events WHERE tenant_id=? AND run_id=? ORDER BY seq",
                              (tenant_id, run_id)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def append_event(self, tenant_id: str, run_id: str, event_type: str, payload: dict,
                     status: str | None = None) -> int:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE tenant_id=? AND run_id=?",
                             (tenant_id, run_id)).fetchone()
            seq = row[0]
            db.execute("INSERT INTO run_events VALUES (?, ?, ?, ?, ?, ?)",
                       (tenant_id, run_id, seq, event_type, json.dumps(payload, ensure_ascii=False), _now()))
            if status:
                finished = _now() if status in ("done", "failed", "aborted") else None
                db.execute("UPDATE runs SET status=?, finished_at=? WHERE tenant_id=? AND run_id=?",
                           (status, finished, tenant_id, run_id))
        return seq

    def decide_gate(self, tenant_id: str, run_id: str, criterion_id: str,
                    approver: str, decision: str, note: str) -> bool:
        if decision not in ("approve", "reject"):
            raise ValueError("decision 必须是 approve 或 reject")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT decision FROM gate_decisions WHERE tenant_id=? AND run_id=? AND criterion_id=?",
                                  (tenant_id, run_id, criterion_id)).fetchone()
            if existing:
                if existing[0] != decision:
                    raise ValueError("人工闸门已作出不同决定")
                return False
            db.execute("INSERT INTO gate_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (tenant_id, run_id, criterion_id, approver, decision, note, _now()))
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM run_events WHERE tenant_id=? AND run_id=?",
                             (tenant_id, run_id)).fetchone()[0]
            payload = {"criterion_id": criterion_id, "approver": approver,
                       "decision": decision, "note": note}
            db.execute("INSERT INTO run_events VALUES (?, ?, ?, ?, ?, ?)",
                       (tenant_id, run_id, seq, "gate_decided", json.dumps(payload, ensure_ascii=False), _now()))
        return True
