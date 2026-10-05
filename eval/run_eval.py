"""run_eval.py — policy(runbook)-grounded eval + data collection for the IT Helpdesk agent.

Same shape as the mini-ai-os harness, proving BOTH halves against the runbook + the LIVE API facts:
  CRITIC  (the automated gate) -> DETERMINISTIC confusion matrix over hand-labeled resolutions incl.
                                  planted runbook violations. precision / recall / accuracy.
  DRAFTER (free text)          -> independent qwen3:30b Diamond judge (judge.py) refutes each LIVE
                                  resolution + deterministic grounding checks.

Reuses the REAL agent nodes from graph.py (classify/gather/draft/critic) — no re-implementation; the
drafter path pulls LIVE data from the free APIs. Outputs dataset.jsonl + metrics.json (+ report.html).

Run (from eval/):  D:\Studio\venvs\langgraph_env\Scripts\python.exe run_eval.py
  --only {all,critic,drafter}   --cases HD-C07,HD-D01   --limit N   --no-report
"""
import os
import sys
import json
import time
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import graph as G  # noqa: E402  (real agent nodes; no side-effects to neutralize — no lessons KB)
from judge import judge_reply, JUDGE_MODEL  # noqa: E402
from llm import DRAFTER_MODEL, CRITIC_MODEL  # noqa: E402
from tools import lookup_runbook  # noqa: E402

GOLD = os.path.join(HERE, "gold_cases.jsonl")
DATASET = os.path.join(HERE, "dataset.jsonl")
METRICS = os.path.join(HERE, "metrics.json")
_ASK_HINTS = ("which product", "which version", "what version", "which os", "cve id", "cve identifier",
              "which service", "could you", "can you tell", "can you share", "please provide",
              "let me know which", "clarify", "what is the")


def load_gold():
    with open(GOLD, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def resolve_runbook(section_names):
    """Build the {name:{policy,action}} dict the critic/judge expect, from section names."""
    out = {}
    for n in (section_names or []):
        rb = lookup_runbook(n)
        if n in rb:
            out[n] = rb[n]
    return out


# ------------------------------------------------------------------ CRITIC PATH
def eval_critic(cases):
    rows, tp = [], 0
    fp = fn = tn = 0
    for c in cases:
        runbook = resolve_runbook(c.get("runbook_sections"))
        state = {"ticket": c["ticket"], "facts": c.get("facts") or {},
                 "runbook": runbook, "draft": c["draft"], "category": c.get("runbook_ref", "other")}
        t0 = time.time()
        try:
            out = G.critic(state)
            got, critique, err = out.get("verdict", "pass"), out.get("critique", ""), None
        except Exception as e:
            got, critique, err = "error", "", str(e)[:200]
        dt = round(time.time() - t0, 1)
        exp = c["expect_verdict"]
        correct = (got == exp)
        if exp == "revise" and got == "revise":
            tp += 1
        elif exp == "revise" and got == "pass":
            fn += 1
        elif exp == "pass" and got == "revise":
            fp += 1
        elif exp == "pass" and got == "pass":
            tn += 1
        rows.append({"id": c["id"], "kind": "critic", "ticket": c["ticket"], "facts": state["facts"],
                     "runbook_sections": c.get("runbook_sections"), "runbook_ref": c.get("runbook_ref"),
                     "draft": c["draft"], "expect_verdict": exp, "got_verdict": got, "critique": critique,
                     "correct": correct, "violation": c.get("violation"), "error": err, "secs": dt})
        print(f"  [{'ok ' if correct else 'XX '}] {c['id']:<7} exp={exp:<6} got={got:<6} ({dt}s)  {c.get('violation')}")
    total = tp + fp + fn + tn
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "total": total,
            "precision": tp / (tp + fp) if (tp + fp) else None,
            "recall": tp / (tp + fn) if (tp + fn) else None,
            "accuracy": (tp + tn) / total if total else None,
            "positive_class": "revise (a runbook violation to catch)"}, rows


# ----------------------------------------------------------------- DRAFTER PATH
def _run_drafter(ticket):
    st = {"ticket": ticket}
    st.update(G.classify(st))
    st.update(G.gather(st))
    st.update(G.draft(st))
    return st.get("category"), st.get("facts") or {}, st.get("runbook") or {}, st.get("draft", "")


def eval_drafter(cases):
    rows = []
    for c in cases:
        exp = c.get("expect", {}) or {}
        t0 = time.time()
        try:
            cat, facts, runbook, draft = _run_drafter(c["ticket"])
            err = None
        except Exception as e:
            cat, facts, runbook, draft, err = None, {}, {}, "", str(e)[:200]
        dl = (draft or "").lower()
        cat_ok = (cat == c.get("expect_category"))
        cite_ok = all(t.lower() in dl for t in exp.get("must_cite", []))
        noinvent_ok = all(t.lower() not in dl for t in exp.get("must_not_invent", []))
        ask_ok = (any(h in dl for h in _ASK_HINTS)) if exp.get("must_ask") else True
        if draft and err is None:
            jr = judge_reply(c["ticket"], facts, runbook, draft, exp.get("rubric", ""))
        else:
            jr = {"grounded": False, "on_runbook": False, "addresses": False, "runbook_compliant": False,
                  "compliant": False, "violations": ["drafter_error"], "reason": err or "no draft",
                  "skeptic": JUDGE_MODEL, "raw": ""}
        checks = {"cat_ok": cat_ok, "cite_ok": cite_ok, "noinvent_ok": noinvent_ok, "ask_ok": ask_ok}
        passed = all(checks.values()) and bool(jr.get("compliant"))
        dt = round(time.time() - t0, 1)
        rows.append({"id": c["id"], "kind": "drafter", "ticket": c["ticket"],
                     "expect_category": c.get("expect_category"), "category": cat,
                     "runbook_ref": c.get("runbook_ref"), "facts": facts, "runbook": runbook,
                     "draft": draft, "rubric": exp.get("rubric", ""), "checks": checks,
                     "judge": jr, "passed": passed, "error": err, "secs": dt})
        rc = "Y" if jr.get("runbook_compliant") else "n"
        ad = "Y" if jr.get("addresses") else "n"
        print(f"  [{'ok ' if passed else 'XX '}] {c['id']:<7} cat={str(cat):<15} runbook_ok={rc} addr={ad} "
              f"cite={int(cite_ok)} inv={int(noinvent_ok)} ask={int(ask_ok)} ({dt}s)")
    total = len(rows)
    ok = [r for r in rows if r["error"] is None]
    n = len(ok)

    def frac(p):
        return (sum(1 for r in ok if p(r)) / n) if n else None

    per_cat = {}
    for r in ok:
        d = per_cat.setdefault(r["expect_category"] or "?", {"n": 0, "runbook_compliant": 0})
        d["n"] += 1
        d["runbook_compliant"] += 1 if r["judge"].get("runbook_compliant") else 0
    return {"total": total, "category_accuracy": frac(lambda r: r["checks"]["cat_ok"]),
            "grounded_pct": frac(lambda r: r["judge"].get("grounded")),
            "on_runbook_pct": frac(lambda r: r["judge"].get("on_runbook")),
            "addresses_pct": frac(lambda r: r["judge"].get("addresses")),
            "runbook_compliant_pct": frac(lambda r: r["judge"].get("runbook_compliant")),
            "fully_compliant_pct": frac(lambda r: r["judge"].get("compliant")),
            "fully_correct_pct": (sum(1 for r in rows if r["passed"]) / total) if total else None,
            "per_category": per_cat}, rows


def _pct(x):
    return "--" if x is None else f"{x*100:.0f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["all", "critic", "drafter"], default="all")
    ap.add_argument("--cases", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args()

    gold = load_gold()
    if args.cases:
        want = {x.strip() for x in args.cases.split(",") if x.strip()}
        gold = [g for g in gold if g["id"] in want]
    crit = [g for g in gold if g["kind"] == "critic"]
    draf = [g for g in gold if g["kind"] == "drafter"]
    if args.limit:
        crit, draf = crit[:args.limit], draf[:args.limit]

    t0 = time.time()
    metrics = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "agent": "it-helpdesk",
               "models": {"drafter": DRAFTER_MODEL, "critic": CRITIC_MODEL, "judge": JUDGE_MODEL},
               "data_sources": ["endoflife.date", "cve.circl.lu", "statuspage.io", "runbook/runbook.json"]}
    all_rows = []

    if args.only in ("all", "critic") and crit:
        print(f"\n=== CRITIC PATH — {len(crit)} cases (automated gate; positive=revise) ===")
        csum, crows = eval_critic(crit)
        metrics["critic"] = csum
        all_rows += crows
        print(f"  -> precision={_pct(csum['precision'])} recall={_pct(csum['recall'])} "
              f"accuracy={_pct(csum['accuracy'])}  (TP{csum['tp']} FN{csum['fn']} FP{csum['fp']} TN{csum['tn']})")

    if args.only in ("all", "drafter") and draf:
        print(f"\n=== DRAFTER PATH — {len(draf)} cases (runbook compliance via independent judge, LIVE APIs) ===")
        dsum, drows = eval_drafter(draf)
        metrics["drafter"] = dsum
        all_rows += drows
        print(f"  -> category_acc={_pct(dsum['category_accuracy'])} "
              f"runbook_compliant={_pct(dsum['runbook_compliant_pct'])} "
              f"(grounded={_pct(dsum['grounded_pct'])} on_runbook={_pct(dsum['on_runbook_pct'])} "
              f"addresses={_pct(dsum['addresses_pct'])}) fully_correct={_pct(dsum['fully_correct_pct'])}")

    metrics["elapsed_secs"] = round(time.time() - t0, 1)
    metrics["n_cases"] = len(all_rows)
    with open(DATASET, "w", encoding="utf-8") as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(METRICS, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {DATASET} ({len(all_rows)} rows)\nwrote {METRICS} ({metrics['elapsed_secs']}s)")

    if not args.no_report:
        try:
            import report
            report.write_report(metrics, all_rows)
            print(f"wrote {os.path.join(HERE, 'report.html')}")
        except ImportError:
            print("(report.py not present — skipping report.html)")


if __name__ == "__main__":
    main()
