from __future__ import annotations

import json
import sqlite3
from dataclasses import fields
from pathlib import Path
from typing import Any

from models import Proposal


class ProposalStore:
    def __init__(self, path: str | Path = "data/reconciliation.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                proposal_id TEXT PRIMARY KEY, proposal_type TEXT NOT NULL, target_account_id TEXT,
                action TEXT NOT NULL, proposed_values TEXT NOT NULL, expected_values TEXT NOT NULL,
                evidence TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT 'PENDING', decision_at TEXT, api_result TEXT
            )
        """)
        self.connection.commit()

    def upsert(self, proposal: Proposal) -> None:
        self.connection.execute("""
            INSERT OR IGNORE INTO proposals
            (proposal_id, proposal_type, target_account_id, action, proposed_values, expected_values, evidence, note, created_at, decision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (proposal.proposal_id, proposal.proposal_type, proposal.target_account_id, proposal.action,
              json.dumps(proposal.proposed_values, sort_keys=True), json.dumps(proposal.expected_values, sort_keys=True),
              json.dumps(proposal.evidence, sort_keys=True), proposal.note, proposal.created_at, proposal.decision))
        self.connection.commit()

    def list(self, decision: str | None = None) -> list[Proposal]:
        query, params = "SELECT * FROM proposals", ()
        if decision and decision != "ALL":
            query, params = query + " WHERE decision = ?", (decision,)
        query += " ORDER BY created_at, proposal_id"
        return [self._row(row) for row in self.connection.execute(query, params)]

    def get(self, proposal_id: str) -> Proposal:
        row = self.connection.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        if not row:
            raise KeyError(proposal_id)
        return self._row(row)

    def decide(self, proposal_id: str, decision: str, decision_at: str, api_result: dict[str, Any] | None = None) -> None:
        self.connection.execute("UPDATE proposals SET decision=?, decision_at=?, api_result=? WHERE proposal_id=?",
                                (decision, decision_at, json.dumps(api_result, sort_keys=True) if api_result else None, proposal_id))
        self.connection.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> Proposal:
        value = dict(row)
        for name in ("proposed_values", "expected_values", "evidence", "api_result"):
            value[name] = json.loads(value[name]) if value[name] else None
        return Proposal(**{f.name: value[f.name] for f in fields(Proposal)})
