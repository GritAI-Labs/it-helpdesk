"""The IT-helpdesk graph's shared state.

Same TypedDict-as-tape pattern as mini-ai-os: every node reads this and returns a partial update.
LangGraph silently DROPS any key a node returns that isn't declared here — so every field a node
writes must live in this dict (that bug cost us a day on mini-ai-os; it's declared, not learned-again).
"""
from typing import TypedDict


class HDState(TypedDict, total=False):
    ticket: str      # inbound IT support ticket (input)
    category: str    # classification: software_eol/vulnerability/service_status/password_reset/access_request/other
    facts: dict      # REAL data from a free API (endoflife.date / CIRCL CVE / statuspage) — set by gather
    runbook: dict    # internal policy + sanctioned action for the category — set by gather
    draft: str       # the drafted resolution/reply (set by draft)
    verdict: str     # "pass" | "revise"  (independent critic on Spark2)
    critique: str    # reviewer's reason when verdict == "revise"
    revisions: int   # revise-loop counter (bounded)
    status: str      # "resolved" (auto) | "escalated"  — the automated gate's outcome
    escalate_reason: str  # why it escalated (critic couldn't clear it, or policy requires a human)
