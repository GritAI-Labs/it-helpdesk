"""IT Helpdesk L1-L2 agent — the graph.

    START -> classify -> gather(REAL API + runbook) -> draft -> critic --(pass)--> finalize -> END
                                                         ^            |
                                                         +--(revise)--+  (bounded)

Same spine as mini-ai-os, three differences:
  1. gather grounds in LIVE free public APIs (endoflife.date / CIRCL CVE / statuspage) + an internal
     runbook, so the reply cites real EOL dates, real CVSS scores, real up/down state — never guessed.
  2. AUTOMATED gate (Rob): no human in the loop. The independent critic (qwen3:30b on Spark2) decides;
     pass -> auto-resolve, revise-exhausted or policy-requires-a-human -> escalate.
  3. Two-Spark: drafter gemma4 on Spark1, critic on Spark2 (llm.py routes by model) — no load/unload.
"""
import re

from langgraph.graph import StateGraph, START, END

from state import HDState
from llm import chat, DRAFTER_MODEL, CRITIC_MODEL
from tools import (lookup_runbook, lookup_eol, lookup_cve, check_service_status,
                   extract_cve, extract_service, extract_product_cycle)

MAX_REVISIONS = 2
CATEGORIES = ["software_eol", "vulnerability", "service_status",
              "password_reset", "access_request", "other"]
NEEDS_API = {"software_eol", "vulnerability", "service_status"}

# ---- classify -------------------------------------------------------------
CLASSIFIER_SYSTEM = (
    "You triage IT helpdesk tickets. Reply with EXACTLY ONE of these category labels and nothing else:\n"
    "software_eol  (OS/software version, end-of-life, upgrade, 'still supported?')\n"
    "vulnerability (a CVE, security advisory, patch, exploit)\n"
    "service_status (is a vendor/SaaS down, outage, 'X not working')\n"
    "password_reset (password, locked out, can't log in, MFA)\n"
    "access_request (needs access/permission/role/group/shared drive)\n"
    "other (anything else)"
)
_KW = {
    "vulnerability": ["cve-", "vulnerab", "exploit", "patch", "advisory"],
    "software_eol": ["end of life", "eol", "end-of-life", "still supported", "upgrade", "outdated", "version"],
    "service_status": ["down", "outage", "not working", "unavailable", "degraded", "status"],
    "password_reset": ["password", "locked out", "lock out", "can't log in", "cannot log in", "mfa", "reset"],
    "access_request": ["access", "permission", "grant", "add me", "role", "shared drive", "group"],
}


def _keyword_guess(text):
    t = (text or "").lower()
    for label, kws in _KW.items():
        if any(k in t for k in kws):
            return label
    return None


def classify(state: HDState) -> dict:
    raw = chat([{"role": "system", "content": CLASSIFIER_SYSTEM},
                {"role": "user", "content": state["ticket"]}],
               model=DRAFTER_MODEL, temperature=0, max_tokens=16).strip().lower()
    cat = next((c for c in CATEGORIES if c in raw or c.replace("_", " ") in raw), None)
    if cat is None:
        cat = _keyword_guess(state["ticket"]) or "other"
    return {"category": cat}


# ---- gather (REAL API + runbook) -----------------------------------------
def gather(state: HDState) -> dict:
    cat = state.get("category", "other")
    ticket = state["ticket"]
    facts = {}
    if cat == "vulnerability":
        cve = extract_cve(ticket)
        facts = lookup_cve(cve) if cve else {"need_cve": True}
    elif cat == "software_eol":
        prod, cycle = extract_product_cycle(ticket)
        facts = lookup_eol(prod, cycle) if prod else {"need_product": True}
    elif cat == "service_status":
        svc = extract_service(ticket)
        facts = check_service_status(svc) if svc else {"need_service": True}
    runbook = lookup_runbook(cat, ticket)
    return {"facts": facts, "runbook": runbook}


# ---- draft (grounded in real facts + runbook) ----------------------------
DRAFTER_SYSTEM = (
    "You are an IT helpdesk L1/L2 agent. Write a concise, professional resolution (under 130 words). "
    "Ground your answer ONLY in the VERIFIED FACTS and the RUNBOOK POLICY given to you. NEVER invent an "
    "EOL date, a CVSS score/severity, a service up/down state, a patch SLA, or any policy — if a fact is "
    "missing, say what you need. State the sanctioned action and its priority/SLA from the runbook. If "
    "verified facts are missing (need_* flags), ask for the specific detail instead of guessing."
)


def _facts_block(facts: dict) -> str:
    if not facts:
        return ""
    if facts.get("need_cve"):
        return "[No CVE id found in the ticket. Ask the user for the CVE identifier before advising severity.]"
    if facts.get("need_product"):
        return "[No software/version identified. Ask the user which product and version they mean.]"
    if facts.get("need_service"):
        return "[No known service identified. Ask the user which service, or run local triage.]"
    rows = "\n".join(f"- {k}: {v}" for k, v in facts.items() if v is not None and k != "summary")
    sm = facts.get("summary")
    if sm:
        rows += f"\n- summary: {sm}"
    return "[VERIFIED FACTS from an authoritative source — ground your answer in these, invent nothing more:]\n" + rows


def _runbook_block(rb: dict) -> str:
    if not rb:
        return ""
    parts = []
    for name, sec in rb.items():
        parts.append(f"- {name} POLICY: {sec.get('policy','')}\n  SANCTIONED ACTION: {sec.get('action','')}")
    return "[RUNBOOK — state policy/SLA using these EXACT terms; take only the sanctioned action:]\n" + "\n".join(parts)


def draft(state: HDState) -> dict:
    cat = state.get("category", "other")
    user = f"[category: {cat}]\n\nTICKET:\n{state['ticket']}"
    fb = _facts_block(state.get("facts") or {})
    if fb:
        user += "\n\n" + fb
    rbb = _runbook_block(state.get("runbook") or {})
    if rbb:
        user += "\n\n" + rbb
    if state.get("verdict") == "revise" and state.get("critique"):
        user += (f"\n\n[Revise your previous draft. Reviewer said: {state['critique']}]"
                 f"\nPrevious draft:\n{state.get('draft', '')}")
    reply = chat([{"role": "system", "content": DRAFTER_SYSTEM},
                  {"role": "user", "content": user}], model=DRAFTER_MODEL)
    return {"draft": reply}


# ---- critic (INDEPENDENT gate — different model, on Spark2) ---------------
CRITIC_SYSTEM = (
    "You are an INDEPENDENT reviewer of an IT helpdesk resolution. You did NOT write it. Judge it "
    "strictly on three criteria:\n"
    "1) GROUNDED  — it invents NO specific fact (EOL date, CVSS score/severity, service up/down state) "
    "beyond the VERIFIED FACTS it was given.\n"
    "2) ON-RUNBOOK — the action + priority/SLA match the RUNBOOK policy exactly (e.g. CVSS 9-10 => patch "
    "in 24h; past-EOL => 30-day upgrade; never sets a password or grants admin access on request).\n"
    "3) ADDRESSES — it resolves or correctly routes what the ticket asked.\n"
    "On the FIRST line write exactly one word: PASS or REVISE. If REVISE, line two = the single most "
    "important fix. Do not output anything else."
)


def _strip_think(text: str) -> str:
    """qwen3 is a thinking model; drop any <think>…</think> before parsing the verdict."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def critic(state: HDState) -> dict:
    facts = state.get("facts") or {}
    rb = state.get("runbook") or {}
    ctx = ""
    if facts:
        ctx += "\n\nVERIFIED FACTS the reply may use:\n" + "\n".join(
            f"- {k}: {v}" for k, v in facts.items() if v is not None)
    if rb:
        ctx += "\n\nRUNBOOK the reply MUST match:\n" + "\n".join(
            f"- {n}: {s.get('policy','')} | ACTION: {s.get('action','')}" for n, s in rb.items())
    raw = chat([{"role": "system", "content": CRITIC_SYSTEM},
                {"role": "user", "content": f"TICKET:\n{state['ticket']}{ctx}\n\n"
                                            f"RESOLUTION TO JUDGE:\n{state['draft']}\n\n/no_think"}],
               model=CRITIC_MODEL, temperature=0, max_tokens=700)
    cleaned = _strip_think(raw)
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    # first explicit verdict token wins; default pass only if neither appears
    verdict, reason = "pass", ""
    low = cleaned.lower()
    pi, ri = low.find("pass"), low.find("revise")
    if ri != -1 and (pi == -1 or ri < pi):
        verdict = "revise"
        reason = next((lines[i + 1] for i, ln in enumerate(lines)
                       if "revise" in ln.lower() and i + 1 < len(lines)), "") or (lines[-1] if lines else "")
    return {"verdict": verdict, "critique": reason, "revisions": state.get("revisions", 0) + 1}


def route_after_critic(state: HDState) -> str:
    if state.get("verdict") == "pass":
        return "ok"
    if state.get("revisions", 0) >= MAX_REVISIONS:
        return "ok"   # exhausted -> finalize will escalate
    return "revise"


# ---- finalize (the AUTOMATED gate) ----------------------------------------
_ADMIN_RE = re.compile(r"\b(admin|administrator|root|elevated|sudo|domain admin|privileged)\b", re.IGNORECASE)


def finalize(state: HDState) -> dict:
    cat = state.get("category", "other")
    # policy-required human: elevated-access requests never auto-grant (runbook: security sign-off)
    if cat == "access_request" and _ADMIN_RE.search(state.get("ticket", "")):
        return {"status": "escalated",
                "escalate_reason": "elevated/admin access requires security sign-off (runbook)"}
    if state.get("verdict") == "pass":
        return {"status": "resolved"}
    return {"status": "escalated",
            "escalate_reason": state.get("critique") or "critic could not clear the draft within the revise budget"}


def build_graph():
    b = StateGraph(HDState)
    b.add_node("classify", classify)
    b.add_node("gather", gather)
    b.add_node("draft", draft)
    b.add_node("critic", critic)
    b.add_node("finalize", finalize)
    b.add_edge(START, "classify")
    b.add_edge("classify", "gather")
    b.add_edge("gather", "draft")
    b.add_edge("draft", "critic")
    b.add_conditional_edges("critic", route_after_critic, {"ok": "finalize", "revise": "draft"})
    b.add_edge("finalize", END)
    return b.compile()


app = build_graph()


def run(ticket: str) -> dict:
    """Run one ticket end-to-end (automated; no human gate)."""
    return app.invoke({"ticket": ticket})


if __name__ == "__main__":
    import sys
    t = " ".join(sys.argv[1:]) or "We're still on Ubuntu 18.04 on a few servers — is that a problem?"
    out = run(t)
    print("category :", out.get("category"))
    print("facts    :", out.get("facts"))
    print("verdict  :", out.get("verdict"), "| status:", out.get("status"),
          ("| " + out.get("escalate_reason", "") if out.get("status") == "escalated" else ""))
    print("draft    :\n" + (out.get("draft") or ""))
