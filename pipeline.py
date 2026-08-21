from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crm_client import CRMClient
from matcher import reconcile
from scraper import scrape_facilities
from storage import ProposalStore

BASE_URL = os.getenv("BELLHAVEN_BASE_URL", "https://analyst-assessment-production.up.railway.app")
PARENT_NAME = "Bellhaven Senior Living (Parent Account)"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def run(dry_run: bool = True, token: str | None = None, data_dir: str | Path = "data") -> dict[str, Any]:
    # Reconciliation itself is always read-only. Writes exist only in actions.execute_approved.
    token = token or os.getenv("BELLHAVEN_API_TOKEN", "")
    client = CRMClient(BASE_URL, token)
    facilities, source_pages = scrape_facilities(BASE_URL)
    accounts = client.list_accounts()
    parents = [account for account in accounts if account.name == PARENT_NAME and not account.parent_id]
    if len(parents) != 1:
        raise RuntimeError(f"Expected one Bellhaven parent, found {len(parents)}")
    parent = parents[0]
    # This explicit call proves stale detection uses the complete parent-filtered set.
    children = client.list_accounts(parent_id=parent.account_id)
    proposals, summary = reconcile(facilities, accounts, parent.account_id)
    data_path = Path(data_dir)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_path = data_path / "runs" / run_id
    run_path.mkdir(parents=True, exist_ok=False)
    summary.update({"bellhaven_children": len(children), "run_id": run_id, "source_pages": source_pages,
                    "pending": len(proposals), "approved": 0, "rejected": 0, "dry_run": dry_run})
    _write_json(run_path / "website_facilities.json", [f.to_dict() for f in facilities])
    _write_json(run_path / "crm_accounts.json", [a.to_dict() for a in accounts])
    _write_json(run_path / "summary.json", summary)
    store = ProposalStore(data_path / "reconciliation.db")
    for proposal in proposals:
        store.upsert(proposal)
    decisions = store.list()
    summary["pending"] = sum(p.decision == "PENDING" for p in decisions)
    summary["approved"] = sum(p.decision == "APPROVED" for p in decisions)
    summary["rejected"] = sum(p.decision == "REJECTED" for p in decisions)
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bellhaven website/CRM reconciliation")
    parser.add_argument("--dry-run", action="store_true", help="Generate proposals and snapshots; never write CRM data")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(dry_run=True, data_dir=args.data_dir)
