from __future__ import annotations

from dataclasses import replace

import pytest

from actions import ProposalConflict, execute_approved
from crm_client import CRMClient
from matcher import make_proposal, match_evidence, reconcile
from models import Account, Facility
from normalization import name_similarity, normalize_name, normalize_street
from storage import ProposalStore

PARENT = "PARENT"
OTHER = "OTHER"


def facility(name="Bellhaven of Maplewood", street="210 Orchard Lane", city="Maplewood", state="OH", zip="44280"):
    return Facility(name, street, city, state, zip, ("Assisted Living",), "https://example/f", "now",
                    f"{street}\n{city}, {state} {zip}", normalize_name(name), normalize_street(street))


def account(account_id="A1", name="Bellhaven of Maplewood", street="210 Orchard Ln", city="Maplewood",
            state="OH", zip="44280", parent=PARENT, revenue=0, ar=0, status="Active"):
    return Account(account_id, name, parent, "Bellhaven", street, city, state, zip, "Assisted Living", status,
                   "", revenue, ar, "", "", "", False, "2026-01-01Z")


def test_exact_address_matching():
    evidence = match_evidence(facility(), account())
    assert evidence["street_exact"] and evidence["zip_exact"]


def test_name_normalization():
    assert normalize_name("Bellhaven Health-Care & Centre") == "bellhaven healthcare and centre"


def test_fuzzy_name_fallback():
    assert name_similarity("Bellhaven Rehabilitation and Nursing", "Bellhaven Rehab and Nursing") > .8


def test_ambiguous_matches_route_to_review():
    f = facility(street="Unknown Road")
    a1 = account("A1", street="One Road")
    a2 = account("A2", street="Two Road")
    proposals, _ = reconcile([f], [a1, a2], PARENT)
    assert any(p.proposal_type == "NEEDS_REVIEW" for p in proposals)


def test_already_correct_record_has_no_proposal():
    proposals, summary = reconcile([facility(street="210 Orchard Ln")], [account()], PARENT)
    assert proposals == [] and summary["matched_correct"] == 1


def test_missing_website_facility_creates_proposal():
    proposals, _ = reconcile([facility()], [], PARENT)
    assert proposals[0].proposal_type == "MISSING_IN_CRM"


def test_stale_crm_child_is_flagged():
    stale = account("STALE", name="Old Home", street="9 Old Rd", city="Elsewhere", zip="40000")
    proposals, _ = reconcile([], [stale], PARENT)
    assert proposals[0].proposal_type == "STALE_BELLHAVEN_ACCOUNT"


def test_duplicate_detection():
    proposals, _ = reconcile([facility()], [account("A1"), account("A2")], PARENT)
    assert any(p.proposal_type == "POSSIBLE_DUPLICATE" for p in proposals)


def test_wrong_duplicate_survivor_creates_correction():
    website = replace(facility(), phone="(734) 503-1363")
    wrong = replace(account("WRONG"), phone="(330) 383-5991")
    right = replace(
        account("RIGHT"), phone="(734) 503-1363", status="Inactive",
        duplicate_of_account="WRONG", note="Duplicate of WRONG",
    )

    proposals, _ = reconcile([website], [wrong, right], PARENT)

    correction = next(p for p in proposals if p.proposal_type == "DUPLICATE_CORRECTION")
    assert correction.action == "SWAP_DUPLICATE_SURVIVOR"
    assert correction.proposed_values["survivor_id"] == "RIGHT"
    assert correction.proposed_values["loser_id"] == "WRONG"


def test_normal_reparenting():
    proposals, _ = reconcile([facility()], [account(parent=OTHER)], PARENT)
    assert any(p.action == "UPDATE_ACCOUNT" and p.proposed_values["parent_id"] == PARENT for p in proposals)


def test_chow_with_revenue_and_ar():
    proposals, _ = reconcile([facility()], [account(parent=OTHER, revenue=10, ar=1)], PARENT)
    assert any(p.action == "CHOW" for p in proposals)


def test_reparent_when_ar_zero():
    proposals, _ = reconcile([facility()], [account(parent=OTHER, revenue=10, ar=0)], PARENT)
    assert any(p.action == "UPDATE_ACCOUNT" for p in proposals)


def test_deterministic_proposal_ids_and_store_rerun(tmp_path):
    p1, _ = reconcile([facility()], [], PARENT)
    p2, _ = reconcile([replace(facility(), scraped_at="later")], [], PARENT)
    assert p1[0].proposal_id == p2[0].proposal_id
    store = ProposalStore(tmp_path / "db.sqlite")
    store.upsert(p1[0]); store.upsert(p2[0])
    assert len(store.list()) == 1


class FakeClient:
    def __init__(self, accounts):
        self.accounts = {a.account_id: a for a in accounts}
        self.patches = []
        self.created = []

    def get_account(self, account_id): return self.accounts[account_id]
    def list_accounts(self, **filters): return list(self.accounts.values())
    def create_account(self, values):
        created = replace(account("NEW"), **values)
        self.accounts[created.account_id] = created; self.created.append(values); return created
    def update_account(self, account_id, values):
        self.patches.append((account_id, values.copy()))
        self.accounts[account_id] = replace(self.accounts[account_id], **values)
        return self.accounts[account_id]


def test_chow_invariant_only_patches_link(tmp_path):
    old = account(parent=OTHER, revenue=10, ar=1)
    proposal = reconcile([facility()], [old], PARENT)[0][0]
    store = ProposalStore(tmp_path / "db.sqlite"); store.upsert(proposal)
    client = FakeClient([old])
    execute_approved(client, store, proposal)
    assert client.patches == [(old.account_id, {"chow_current_account": "NEW"})]
    after = client.get_account(old.account_id)
    for field, value in old.to_dict().items():
        if field != "chow_current_account": assert getattr(after, field) == value


def test_structural_collision_multiple_websites_one_account():
    f1 = facility(name="Bellhaven Maplewood")
    f2 = facility(name="Bellhaven of Maplewood")
    proposals, _ = reconcile([f1, f2], [account()], PARENT)
    assert sum(p.proposal_type == "NEEDS_REVIEW" for p in proposals) >= 1


def test_optimistic_write_conflict(tmp_path):
    original = account()
    evidence = {"test": True}
    proposal = make_proposal("UPDATE_FIELDS", original.account_id, "UPDATE_ACCOUNT",
                             {"billing_zip": "44281"}, {"billing_zip": "44280"}, evidence, "test")
    store = ProposalStore(tmp_path / "db.sqlite"); store.upsert(proposal)
    changed = replace(original, billing_zip="99999")
    with pytest.raises(ProposalConflict):
        execute_approved(FakeClient([changed]), store, proposal)
    assert store.get(proposal.proposal_id).decision == "CONFLICTED"


def test_partial_create_response_is_refetched():
    expected = account("NEW")

    class PartialResponseClient(CRMClient):
        def __init__(self): pass
        def _request(self, method, path, **kwargs): return {"account_id": "NEW"}
        def get_account(self, account_id): return expected

    assert PartialResponseClient().create_account({"name": "Example"}) == expected


def test_duplicate_survivor_correction_updates_both_accounts(tmp_path):
    correct = replace(account("RIGHT"), status="Inactive", duplicate_of_account="WRONG", note="wrong duplicate")
    wrong = account("WRONG")
    proposed = {
        "survivor_id": "RIGHT", "loser_id": "WRONG",
        "survivor_updates": {"status": "Active", "duplicate_of_account": "", "note": ""},
        "loser_updates": {"status": "Inactive", "duplicate_of_account": "RIGHT", "note": "Duplicate of RIGHT"},
    }
    expected = {"survivor": correct.to_dict(), "loser": wrong.to_dict()}
    proposal = make_proposal("DUPLICATE_CORRECTION", "RIGHT", "SWAP_DUPLICATE_SURVIVOR",
                             proposed, expected, {"reason": "test"}, "test")
    store = ProposalStore(tmp_path / "db.sqlite"); store.upsert(proposal)
    client = FakeClient([correct, wrong])
    execute_approved(client, store, proposal)
    assert client.get_account("RIGHT").status == "Active"
    assert client.get_account("RIGHT").duplicate_of_account == ""
    assert client.get_account("WRONG").status == "Inactive"
    assert client.get_account("WRONG").duplicate_of_account == "RIGHT"
