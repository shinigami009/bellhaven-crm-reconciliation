from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from models import Account, Facility, Proposal
from normalization import name_similarity, normalize_name, normalize_street, normalize_zip

CARE_MAP = {
    ("Assisted Living",): "Assisted Living",
    ("Memory Support",): "Memory Care",
    ("Short-Term Rehabilitation & Nursing",): "Skilled Nursing",
}


def account_snapshot(account: Account) -> dict[str, Any]:
    return account.to_dict()


def match_evidence(facility: Facility, account: Account) -> dict[str, Any]:
    street_exact = facility.normalized_street == normalize_street(account.billing_street)
    zip_exact = normalize_zip(facility.zip) == normalize_zip(account.billing_zip)
    city_exact = facility.city.casefold() == account.billing_city.casefold()
    state_exact = facility.state.casefold() == account.billing_state.casefold()
    similarity = name_similarity(facility.name, account.name)
    score = (0.55 if street_exact else 0) + (0.2 if zip_exact else 0) + (0.1 if city_exact and state_exact else 0) + 0.15 * similarity
    return {
        "website_name": facility.name,
        "website_address_raw": facility.raw_address,
        "website_street_normalized": facility.normalized_street,
        "website_care_offerings": list(facility.care_offerings),
        "source_url": facility.source_url,
        "crm_account": account_snapshot(account),
        "crm_street_normalized": normalize_street(account.billing_street),
        "street_exact": street_exact,
        "zip_exact": zip_exact,
        "city_state_exact": city_exact and state_exact,
        "name_similarity": similarity,
        "score": round(score, 3),
    }


def plausible(evidence: dict[str, Any]) -> bool:
    return bool(
        evidence["street_exact"]
        or (evidence["zip_exact"] and evidence["city_state_exact"] and evidence["name_similarity"] >= 0.35)
        or (evidence["city_state_exact"] and evidence["name_similarity"] >= 0.82)
    )


def _stable_identity(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _stable_identity(item) for key, item in value.items() if key not in {"scraped_at", "updated_at"}}
    if isinstance(value, list):
        return [_stable_identity(item) for item in value]
    return value


def _proposal_id(proposal_type: str, target: str | None, action: str, proposed: dict[str, Any], evidence: dict[str, Any]) -> str:
    identity = {"type": proposal_type, "target": target, "action": action,
                "proposed": _stable_identity(proposed), "evidence": _stable_identity(evidence)}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    return f"{proposal_type.lower()}-{digest}"


def make_proposal(proposal_type: str, target: str | None, action: str, proposed: dict[str, Any], expected: dict[str, Any], evidence: dict[str, Any], note: str) -> Proposal:
    return Proposal(_proposal_id(proposal_type, target, action, proposed, evidence), proposal_type, target, action,
                    proposed, expected, evidence, note, datetime.now(timezone.utc).isoformat())


def _current_values(facility: Facility, parent_id: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": facility.name, "parent_id": parent_id, "billing_street": facility.street,
        "billing_city": facility.city, "billing_state": facility.state, "billing_zip": normalize_zip(facility.zip),
    }
    care = CARE_MAP.get(facility.care_offerings)
    if care:
        values["care_type"] = care
    if facility.phone:
        values["phone"] = facility.phone
    return values


def _material_changes(facility: Facility, account: Account, parent_id: str) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if normalize_name(facility.name) != normalize_name(account.name):
        changes["name"] = facility.name
    if normalize_street(facility.street) != normalize_street(account.billing_street):
        changes["billing_street"] = facility.street
    if facility.city.casefold() != account.billing_city.casefold():
        changes["billing_city"] = facility.city
    if facility.state.casefold() != account.billing_state.casefold():
        changes["billing_state"] = facility.state
    if normalize_zip(facility.zip) != normalize_zip(account.billing_zip):
        changes["billing_zip"] = normalize_zip(facility.zip)
    mapped_care = CARE_MAP.get(facility.care_offerings)
    if mapped_care and mapped_care != account.care_type:
        changes["care_type"] = mapped_care
    if facility.phone and facility.phone != account.phone:
        changes["phone"] = facility.phone
    if account.parent_id != parent_id:
        changes["parent_id"] = parent_id
    return changes


def reconcile(facilities: list[Facility], accounts: list[Account], bellhaven_parent_id: str) -> tuple[list[Proposal], dict[str, int]]:
    proposals: list[Proposal] = []
    candidate_map: dict[int, list[tuple[Account, dict[str, Any]]]] = {}
    claims: dict[str, list[int]] = defaultdict(list)
    for index, facility in enumerate(facilities):
        candidates = [(a, match_evidence(facility, a)) for a in accounts]
        candidates = [(a, e) for a, e in candidates if plausible(e)]
        candidates.sort(key=lambda item: item[1]["score"], reverse=True)
        candidate_map[index] = candidates
        exact_candidates = [(a, e) for a, e in candidates if e["street_exact"] and e["zip_exact"]]
        claimable = exact_candidates or ([(a, e) for a, e in candidates if e["score"] >= candidates[0][1]["score"] - 0.08] if candidates else [])
        for account, _ in claimable:
            claims[account.account_id].append(index)

    matched_ids: set[str] = set()
    correct = 0
    for index, facility in enumerate(facilities):
        candidates = candidate_map[index]
        exact = [(a, e) for a, e in candidates if e["street_exact"] and e["zip_exact"]]
        exact_ids = {a.account_id for a, _ in exact}
        duplicate_rank = lambda item: (
            item[0].parent_id == bellhaven_parent_id,
            normalize_name(item[0].name) == facility.normalized_name,
            bool(facility.phone) and item[0].phone == facility.phone,
            item[0].status == "Active",
            item[0].account_id,
        )

        # An earlier review can select the wrong duplicate survivor. Do not hide the
        # inactive record until we verify that the website still favors the active one.
        if len(exact) == 2:
            ordered_all = sorted(exact, key=duplicate_rank, reverse=True)
            preferred, other = ordered_all[0][0], ordered_all[1][0]
            wrong_direction = (
                preferred.status == "Inactive"
                and preferred.duplicate_of_account == other.account_id
                and other.status == "Active"
            )
            zero_history = all(
                account.lifetime_revenue == 0 and account.outstanding_ar == 0
                for account, _ in exact
            )
            if wrong_direction and zero_history:
                evidence = {
                    "website": facility.to_dict(),
                    "competing_candidates": [e for _, e in ordered_all],
                    "decision_reason": "The inactive account is the stronger website match; both accounts have zero revenue and AR.",
                }
                proposed = {
                    "survivor_id": preferred.account_id,
                    "loser_id": other.account_id,
                    "survivor_updates": {"status": "Active", "duplicate_of_account": "", "note": ""},
                    "loser_updates": {
                        "status": "Inactive",
                        "duplicate_of_account": preferred.account_id,
                        "note": f"Duplicate of {preferred.account_id}; survivor matches the current website identity and phone.",
                    },
                }
                expected = {"survivor": account_snapshot(preferred), "loser": account_snapshot(other)}
                proposals.append(make_proposal(
                    "DUPLICATE_CORRECTION", preferred.account_id, "SWAP_DUPLICATE_SURVIVOR",
                    proposed, expected, evidence,
                    "Reverse the previous duplicate decision because the inactive account is the stronger current website match.",
                ))
                matched_ids.update(exact_ids)
                continue
        actionable_exact = [
            (a, e) for a, e in exact
            if a.chow_current_account not in exact_ids
            and not (a.status == "Inactive" and a.duplicate_of_account in exact_ids)
        ]
        if len(exact) > 1 and len(actionable_exact) == 1:
            matched_ids.update(exact_ids)
            exact = actionable_exact
            candidates = actionable_exact + [(a, e) for a, e in candidates if a.account_id not in exact_ids]
        elif len(actionable_exact) > 1:
            exact = actionable_exact
        if len(exact) > 1:
            if not any(a.parent_id == bellhaven_parent_id and normalize_name(a.name) == facility.normalized_name for a, _ in exact):
                evidence = {"website": facility.to_dict(), "competing_candidates": [e for _, e in exact]}
                proposals.append(make_proposal("NEEDS_REVIEW", None, "NO_WRITE", {}, {}, evidence,
                                               "Duplicate-location collision has no account with both the current website name and Bellhaven parent; reviewer must choose a survivor and correction path."))
                matched_ids.update(a.account_id for a, _ in exact)
                continue
            ordered = sorted(exact, key=duplicate_rank, reverse=True)
            survivor = ordered[0][0]
            evidence = {"website": facility.to_dict(), "competing_candidates": [e for _, e in ordered]}
            for loser, _ in ordered[1:]:
                proposed = {"survivor_id": survivor.account_id, "loser_id": loser.account_id,
                            "loser_updates": {"duplicate_of_account": survivor.account_id, "status": "Inactive",
                            "note": f"Duplicate of {survivor.account_id}; same normalized address and ZIP for {facility.name}."}}
                expected = {"loser": account_snapshot(loser), "survivor": account_snapshot(survivor)}
                proposals.append(make_proposal("POSSIBLE_DUPLICATE", loser.account_id, "MARK_DUPLICATE", proposed, expected, evidence,
                                               "Multiple CRM accounts match the same website facility at the same address. Proposed survivor prefers correct Bellhaven parent, current name, and Active status; financial context remains visible for reviewer judgment."))
            matched_ids.update(a.account_id for a, _ in exact)
            continue
        if not candidates:
            proposed = _current_values(facility, bellhaven_parent_id)
            evidence = {"website": facility.to_dict(), "candidates": []}
            proposals.append(make_proposal("MISSING_IN_CRM", None, "CREATE_ACCOUNT", proposed, {}, evidence,
                                           "No plausible CRM account was found."))
            continue
        if len(exact) == 1:
            account, evidence = exact[0]
            close_competitors: list[tuple[Account, dict[str, Any]]] = []
        else:
            account, evidence = candidates[0]
            close_competitors = [(a, e) for a, e in candidates[1:] if e["score"] >= evidence["score"] - 0.08]
        if close_competitors or len(claims[account.account_id]) > 1:
            collision = {**evidence, "competing_candidates": [e for _, e in candidates[:5]],
                         "website_claims_for_account": [facilities[i].to_dict() for i in claims[account.account_id]]}
            proposals.append(make_proposal("NEEDS_REVIEW", account.account_id, "NO_WRITE", {}, {}, collision,
                                           "Structural collision or similarly scored candidates; no account was selected automatically."))
            continue
        matched_ids.add(account.account_id)
        desired = _current_values(facility, bellhaven_parent_id)
        changes = _material_changes(facility, account, bellhaven_parent_id)
        if not changes:
            correct += 1
            continue
        expected = {field: getattr(account, field) for field in changes}
        evidence["before"] = {field: getattr(account, field) for field in changes}
        evidence["after"] = changes
        if account.parent_id != bellhaven_parent_id and account.lifetime_revenue > 0 and account.outstanding_ar > 0:
            create_values = desired
            proposed = {"create_values": create_values, "old_account_id": account.account_id}
            # Exact invariant: the only old-account patch is added by the action executor.
            proposals.append(make_proposal("CHOW_REQUIRED", account.account_id, "CHOW", proposed,
                                           account_snapshot(account), evidence,
                                           "Revenue history and outstanding AR require preserving the old account; create a new current account and link it."))
        else:
            kind = "UPDATE_PARENT" if set(changes) == {"parent_id"} else ("UPDATE_NAME" if set(changes) == {"name"} else "UPDATE_FIELDS")
            proposals.append(make_proposal(kind, account.account_id, "UPDATE_ACCOUNT", changes, expected, evidence,
                                           "Website evidence supports these field corrections."))

    for account in accounts:
        if account.parent_id == bellhaven_parent_id and account.account_id not in matched_ids:
            evidence = {"crm_account": account_snapshot(account), "website_facility_count": len(facilities)}
            updates = {"status": "Needs Review", "note": "No matching facility found on Bellhaven's current website; review ownership before deactivation."}
            if account.status == updates["status"] and account.note == updates["note"]:
                continue
            expected = {"status": account.status, "note": account.note, "parent_id": account.parent_id}
            proposals.append(make_proposal("STALE_BELLHAVEN_ACCOUNT", account.account_id, "UPDATE_ACCOUNT", updates, expected, evidence,
                                           "Enumerated under Bellhaven but absent from the complete website portfolio. Mark for review; do not delete."))
    summary = {
        "website_facilities": len(facilities), "crm_accounts": len(accounts), "matched_correct": correct,
        "proposals": len(proposals), "missing_in_crm": sum(p.proposal_type == "MISSING_IN_CRM" for p in proposals),
        "possible_duplicates": sum(p.proposal_type == "POSSIBLE_DUPLICATE" for p in proposals),
        "stale_bellhaven": sum(p.proposal_type == "STALE_BELLHAVEN_ACCOUNT" for p in proposals),
        "chow_cases": sum(p.proposal_type == "CHOW_REQUIRED" for p in proposals),
        "needs_review": sum(p.proposal_type == "NEEDS_REVIEW" for p in proposals),
    }
    return proposals, summary
