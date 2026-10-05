"""The IT-helpdesk eval JUDGE — independent, adversarial, runbook-grounded (the Diamond skeptic).

Mirrors the mini-ai-os judge: a model independent of the drafter refutes each live resolution against
the VERIFIED FACTS (real API data) + the RUNBOOK, per criterion, failing closed when unsure. Here the
judge is qwen3:30b on Spark2 (two-Spark) — a reasoning model, so we strip its <think>…</think> before
pulling the trailing JSON. Reason-then-trailing-JSON (NOT grammar-forced) per the local-model gotcha.
"""
import os
import sys
import json
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm import chat, JUDGE_MODEL  # noqa: E402  (two-Spark client; JUDGE_MODEL -> Spark2)

JUDGE_SYSTEM = (
    "You are an INDEPENDENT, skeptical reviewer of an IT helpdesk resolution. You did NOT write it. "
    "TRY TO REFUTE it — hunt for any violation. Judge ONLY against the VERIFIED FACTS and the RUNBOOK "
    "given, on three criteria:\n"
    "1) GROUNDED  — invents NO specific fact (EOL date, CVSS score/severity, service up/down state) "
    "beyond the VERIFIED FACTS.\n"
    "2) ON-RUNBOOK — the action + priority/SLA match the RUNBOOK exactly (e.g. CVSS 9-10 => 24h; past-EOL "
    "=> 30-day upgrade; never sets a password; never grants admin/elevated access on request; never "
    "promises a restore time for a vendor outage).\n"
    "3) ADDRESSES — it resolves or correctly routes what the ticket asked.\n"
    "Be strict: if UNSURE whether something is grounded or on-runbook, treat it as a VIOLATION.\n\n"
    "Output a single JSON object as the FIRST thing in your reply (optionally one short sentence after). "
    "The JSON, and nothing before it:\n"
    '{"grounded": true|false, "on_runbook": true|false, "addresses": true|false, '
    '"violations": ["short phrase", ...], "reason": "one-sentence verdict"}'
)


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def _fmt(d: dict) -> str:
    if not d:
        return "(none provided)"
    return "\n".join(f"- {k}: {v}" for k, v in d.items() if v is not None) or "(none provided)"


def _fmt_runbook(rb: dict) -> str:
    if not rb:
        return "(none provided)"
    return "\n".join(f"- {n}: {s.get('policy','')} | ACTION: {s.get('action','')}" for n, s in rb.items())


def _extract_json(text: str) -> dict:
    # JSON-first: take the FIRST valid verdict object in the reply
    for chunk in re.findall(r"\{[^{}]*\}", text, flags=re.DOTALL):
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict) and ("grounded" in obj or "compliant" in obj):
                return obj
        except json.JSONDecodeError:
            continue
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return {}


def judge_reply(ticket: str, facts: dict, runbook: dict, reply: str, rubric: str = "") -> dict:
    user = (
        f"TICKET:\n{ticket}\n\nVERIFIED FACTS:\n{_fmt(facts or {})}\n\n"
        f"RUNBOOK:\n{_fmt_runbook(runbook or {})}\n\n"
        f"REVIEW RUBRIC (what a compliant resolution must do):\n{rubric or '(use the three criteria above)'}\n\n"
        f"RESOLUTION TO JUDGE:\n{reply}"
    )
    # /no_think suppresses qwen3's <think> block (faster + no truncation); big budget as a safety net
    raw = chat([{"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": user + "\n\n/no_think"}],
               model=JUDGE_MODEL, temperature=0, max_tokens=1600)
    obj = _extract_json(_strip_think(raw))
    if not obj:
        return {"grounded": False, "on_runbook": False, "addresses": False,
                "runbook_compliant": False, "compliant": False,
                "violations": ["judge_parse_failed"], "reason": "unparseable judge output; failing closed",
                "skeptic": JUDGE_MODEL, "raw": raw[:800]}
    legacy = bool(obj.get("compliant", False))
    grounded = bool(obj.get("grounded", legacy))
    on_runbook = bool(obj.get("on_runbook", obj.get("on_policy", legacy)))
    addresses = bool(obj.get("addresses", legacy))
    return {"grounded": grounded, "on_runbook": on_runbook, "addresses": addresses,
            "runbook_compliant": grounded and on_runbook,          # the HARD bar
            "compliant": grounded and on_runbook and addresses,     # fully compliant
            "violations": obj.get("violations", []) or [],
            "reason": str(obj.get("reason", "")).strip(),
            "skeptic": JUDGE_MODEL, "raw": raw[:800]}


if __name__ == "__main__":
    rb = {"vulnerability": {"policy": "CRITICAL (9.0-10.0) patch within 24 hours; MEDIUM within 30 days.",
                            "action": "Map CVSS to SLA tier and open a patch task."}}
    facts = {"found": True, "cve": "CVE-2021-44228", "cvss": 10, "severity": "CRITICAL"}
    good = "CVE-2021-44228 is CVSS 10 (CRITICAL); per policy we patch within 24 hours. Opening a patch task now."
    bad = "CVE-2021-44228 is low risk; we'll handle it in the next maintenance window in a few months."
    print("JUDGE_MODEL =", JUDGE_MODEL)
    print("GOOD ->", judge_reply("How urgent is CVE-2021-44228?", facts, rb, good, "CRITICAL -> 24h patch."))
    print("BAD  ->", judge_reply("How urgent is CVE-2021-44228?", facts, rb, bad, "CRITICAL -> 24h patch."))
