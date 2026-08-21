from __future__ import annotations

import os

import streamlit as st

from actions import ProposalConflict, execute_approved, reject
from crm_client import CRMClient
from pipeline import BASE_URL
from storage import ProposalStore

st.set_page_config(page_title="Bellhaven Reconciliation", layout="wide")
st.title("Bellhaven CRM Reconciliation")
st.caption("Every CRM write requires an explicit approval and a fresh optimistic-state check.")

store = ProposalStore()
status = st.selectbox("Queue", ["PENDING", "APPROVED", "REJECTED", "CONFLICTED", "ALL"])
proposals = store.list(status)
all_proposals = store.list()
cols = st.columns(4)
for col, label in zip(cols, ["PENDING", "APPROVED", "REJECTED", "CONFLICTED"]):
    col.metric(label.title(), sum(p.decision == label for p in all_proposals))
st.progress(0 if not all_proposals else sum(p.decision != "PENDING" for p in all_proposals) / len(all_proposals))

if not proposals:
    st.info("No proposals in this view. Run `python pipeline.py --dry-run` first.")

for proposal in proposals:
    high_impact = proposal.proposal_type in {"CHOW_REQUIRED", "POSSIBLE_DUPLICATE", "DUPLICATE_CORRECTION", "STALE_BELLHAVEN_ACCOUNT"}
    icon = "⚠️ " if high_impact else ""
    with st.expander(f"{icon}{proposal.proposal_type} · {proposal.proposal_id} · {proposal.decision}"):
        st.write(proposal.note)
        if proposal.proposal_type == "CHOW_REQUIRED":
            st.error("CHOW SOP: preserve the old account; create a new current account; only set the old chow_current_account link.")
        left, right = st.columns(2)
        with left:
            st.subheader("Evidence / before")
            st.json(proposal.evidence)
        with right:
            st.subheader("Proposed change")
            st.json({"action": proposal.action, "values": proposal.proposed_values, "expected_at_approval": proposal.expected_values})
        if proposal.decision == "PENDING":
            approve_col, reject_col = st.columns(2)
            if approve_col.button("APPROVE", key=f"approve-{proposal.proposal_id}", type="primary",
                                  disabled=proposal.action == "NO_WRITE"):
                token = os.getenv("BELLHAVEN_API_TOKEN", "")
                if not token:
                    st.error("BELLHAVEN_API_TOKEN is not set in this app process.")
                else:
                    try:
                        result = execute_approved(CRMClient(BASE_URL, token), store, proposal)
                        st.success("CRM action succeeded and was verified.")
                        st.json(result)
                        st.rerun()
                    except ProposalConflict as exc:
                        st.error(f"Proposal is stale/conflicted; nothing was overwritten. {exc}")
                    except Exception as exc:
                        st.exception(exc)
            if reject_col.button("REJECT", key=f"reject-{proposal.proposal_id}"):
                reject(store, proposal)
                st.success("Proposal rejected and recorded.")
                st.rerun()
        elif proposal.api_result:
            st.subheader("Audit result")
            st.json(proposal.api_result)
