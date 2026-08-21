from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from crm_client import CRMClient
from models import Account, Proposal
from normalization import normalize_name, normalize_street, normalize_zip
from storage import ProposalStore


class ProposalConflict(RuntimeError):
    pass


def _assert_fields(account: Account, expected: dict[str, Any]) -> None:
    conflicts = {field: {"expected": value, "actual": getattr(account, field)}
                 for field, value in expected.items() if getattr(account, field) != value}
    if conflicts:
        raise ProposalConflict(f"CRM account changed since proposal generation: {conflicts}")


def _equivalent(accounts: list[Account], values: dict[str, Any]) -> Account | None:
    for account in accounts:
        if (normalize_name(account.name) == normalize_name(values["name"])
                and normalize_street(account.billing_street) == normalize_street(values["billing_street"])
                and normalize_zip(account.billing_zip) == normalize_zip(values["billing_zip"])
                and account.parent_id == values["parent_id"]
                and account.status != "Inactive"):
            return account
    return None


def execute_approved(client: CRMClient, store: ProposalStore, proposal: Proposal) -> dict[str, Any]:
    if proposal.decision != "PENDING":
        raise ValueError("Only pending proposals may be approved")
    if proposal.action == "NO_WRITE":
        raise ValueError("This proposal is informational and cannot be approved as a write")
    result: dict[str, Any]
    try:
        if proposal.action == "UPDATE_ACCOUNT":
            assert proposal.target_account_id
            current = client.get_account(proposal.target_account_id)
            _assert_fields(current, proposal.expected_values)
            updated = client.update_account(current.account_id, proposal.proposed_values)
            _assert_fields(updated, proposal.proposed_values)
            result = {"updated_account": updated.to_dict()}
        elif proposal.action == "CREATE_ACCOUNT":
            existing = _equivalent(client.list_accounts(), proposal.proposed_values)
            created = existing or client.create_account(proposal.proposed_values)
            result = {"account": created.to_dict(), "created": existing is None}
        elif proposal.action == "CHOW":
            assert proposal.target_account_id
            old = client.get_account(proposal.target_account_id)
            _assert_fields(old, proposal.expected_values)
            values = proposal.proposed_values["create_values"]
            existing = _equivalent(client.list_accounts(), values)
            current = existing or client.create_account(values)
            # CHOW invariant: this is deliberately the only patch to the old account.
            linked = client.update_account(old.account_id, {"chow_current_account": current.account_id})
            for field, expected in proposal.expected_values.items():
                if field not in {"chow_current_account", "updated_at"} and getattr(linked, field) != expected:
                    raise ProposalConflict(f"CHOW invariant failed for old-account field {field}")
            if linked.chow_current_account != current.account_id:
                raise ProposalConflict("CHOW link was not persisted")
            result = {"old_account": linked.to_dict(), "current_account": current.to_dict(), "created": existing is None}
        elif proposal.action == "MARK_DUPLICATE":
            loser_id = proposal.proposed_values["loser_id"]
            survivor_id = proposal.proposed_values["survivor_id"]
            loser = client.get_account(loser_id)
            survivor = client.get_account(survivor_id)
            _assert_fields(loser, proposal.expected_values["loser"])
            _assert_fields(survivor, proposal.expected_values["survivor"])
            updated = client.update_account(loser_id, proposal.proposed_values["loser_updates"])
            _assert_fields(updated, proposal.proposed_values["loser_updates"])
            result = {"loser": updated.to_dict(), "survivor": survivor.to_dict()}
        elif proposal.action == "SWAP_DUPLICATE_SURVIVOR":
            survivor_id = proposal.proposed_values["survivor_id"]
            loser_id = proposal.proposed_values["loser_id"]
            survivor = client.get_account(survivor_id)
            loser = client.get_account(loser_id)
            _assert_fields(survivor, proposal.expected_values["survivor"])
            _assert_fields(loser, proposal.expected_values["loser"])
            restored = client.update_account(survivor_id, proposal.proposed_values["survivor_updates"])
            _assert_fields(restored, proposal.proposed_values["survivor_updates"])
            retired = client.update_account(loser_id, proposal.proposed_values["loser_updates"])
            _assert_fields(retired, proposal.proposed_values["loser_updates"])
            result = {"survivor": restored.to_dict(), "loser": retired.to_dict(), "correction": True}
        elif proposal.action == "RESOLVE_DUPLICATE_GROUP":
            survivor_id = proposal.proposed_values["survivor_id"]
            survivor = client.get_account(survivor_id)
            _assert_fields(survivor, proposal.expected_values["survivor"])
            current_losers = {}
            for item in proposal.proposed_values["losers"]:
                current_losers[item["account_id"]] = client.get_account(item["account_id"])
                _assert_fields(current_losers[item["account_id"]], proposal.expected_values["losers"][item["account_id"]])
            restored = client.update_account(survivor_id, proposal.proposed_values["survivor_updates"])
            _assert_fields(restored, proposal.proposed_values["survivor_updates"])
            retired_accounts = []
            for item in proposal.proposed_values["losers"]:
                retired = client.update_account(item["account_id"], item["updates"])
                _assert_fields(retired, item["updates"])
                retired_accounts.append(retired.to_dict())
            result = {"survivor": restored.to_dict(), "losers": retired_accounts, "correction": True}
        else:
            raise ValueError(f"Unknown proposal action: {proposal.action}")
    except ProposalConflict as exc:
        store.decide(proposal.proposal_id, "CONFLICTED", datetime.now(timezone.utc).isoformat(), {"error": str(exc)})
        raise
    store.decide(proposal.proposal_id, "APPROVED", datetime.now(timezone.utc).isoformat(), result)
    return result


def reject(store: ProposalStore, proposal: Proposal) -> None:
    if proposal.decision != "PENDING":
        raise ValueError("Only pending proposals may be rejected")
    store.decide(proposal.proposal_id, "REJECTED", datetime.now(timezone.utc).isoformat(), {"message": "Rejected by reviewer"})
