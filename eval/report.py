"""report.py — render the IT Helpdesk eval metrics to a demo-grade report.html.

Same GritAI design system as the mini-ai-os report; tells the story: how we built a runbook-anchored
gold set grounded in LIVE free APIs, how we verified the drafter + the automated critic gate, the
numbers, and the forward path (real-data flywheel). Called by run_eval.py, or standalone: `python report.py`.
"""
import os
import json
import html

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "report.html")

CSS = """
:root{--bg:#0e0f13;--panel:#15171d;--line:#262a33;--ink:#e7e9ee;--mut:#9aa1ad;
--accent:#ff6a1a;--accent2:#5b8cff;--ok:#39d98a;--bad:#ff5d5d;
--mono:'IBM Plex Mono',ui-monospace,Consolas,monospace;--sans:'Archivo',system-ui,Segoe UI,Roboto,sans-serif;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.55}
.wrap{max-width:920px;margin:0 auto;padding:40px 22px 80px}
header{border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:10px}
.kicker{font-family:var(--mono);font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:var(--accent)}
h1{font-size:clamp(26px,4.5vw,40px);margin:.25em 0 .1em;letter-spacing:-.02em}
.lede{color:var(--mut);font-size:17px;max-width:66ch}
.meta{font-family:var(--mono);font-size:12px;color:var(--mut);margin-top:10px}
h2{font-size:21px;margin:40px 0 12px;letter-spacing:-.01em}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:10px;overflow-x:auto}
.note{color:var(--mut);font-size:13px;font-family:var(--mono);margin-top:10px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:4px 0}
.kpi{background:#0b0c10;border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .v{font-size:30px;font-weight:700;letter-spacing:-.02em}
.kpi .l{font-family:var(--mono);font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;margin-top:4px}
.cm{border-collapse:collapse;font-family:var(--mono);font-size:13px;margin:8px 0}
.cm td,.cm th{border:1px solid var(--line);padding:10px 14px;text-align:center}
.cm th{color:var(--mut);background:#0b0c10}
.cm .tp,.cm .tn{color:var(--ok)} .cm .fn{color:var(--bad);font-weight:700} .cm .fp{color:var(--accent)}
table.cases{width:100%;border-collapse:collapse;font-size:13px}
table.cases th{text-align:left;color:var(--mut);font-family:var(--mono);font-size:11px;text-transform:uppercase;border-bottom:1px solid var(--line);padding:8px}
table.cases td{border-bottom:1px solid var(--line);padding:8px;vertical-align:top}
.tag{font-family:var(--mono);font-size:11px;padding:2px 7px;border-radius:999px;border:1px solid var(--line);color:var(--mut)}
.tag.ok{color:var(--ok);border-color:#1f5e42;background:#102019}
.tag.no{color:var(--bad);border-color:#5e1f1f;background:#201010}
.tag.soft{color:var(--accent);border-color:#5a3015;background:#1f140c}
code{font-family:var(--mono);background:#1b1e26;padding:2px 6px;border-radius:5px;font-size:.9em}
details{border:1px solid var(--line);border-radius:8px;background:#0b0c10;margin:8px 0;padding:0 14px}
summary{cursor:pointer;padding:12px 2px;font-weight:600;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"\\25B8";color:var(--accent);margin-right:8px}
details[open] summary::before{content:"\\25BE"}
.step{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--line)}
.step:last-child{border-bottom:0}
.step .n{flex:0 0 auto;font-family:var(--mono);font-size:11px;font-weight:700;color:var(--accent);border:1px solid #5a3015;background:#1f140c;border-radius:999px;padding:3px 9px}
b.accent{color:var(--accent)} .muted{color:var(--mut)}
a{color:var(--accent2);text-decoration:none} a:hover{text-decoration:underline}
footer{margin-top:50px;padding-top:20px;border-top:1px solid var(--line);color:var(--mut);font-size:13px;font-family:var(--mono)}
"""


def _pct(x):
    return "&mdash;" if x is None else f"{x*100:.0f}%"


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def _kpi(v, l, cls=""):
    return f'<div class="kpi {cls}"><div class="v">{v}</div><div class="l">{_esc(l)}</div></div>'


def _cls(x, good=0.9, warn=0.7):
    if x is None:
        return ""
    return "good" if x >= good else ("warn" if x >= warn else "bad")


def _kpi_color(x):
    return "var(--mut)" if x is None else ("var(--ok)" if x >= 0.9 else ("var(--accent)" if x >= 0.7 else "var(--bad)"))


def _kpi2(v, l, x):
    return (f'<div class="kpi"><div class="v" style="color:{_kpi_color(x)}">{v}</div>'
            f'<div class="l">{_esc(l)}</div></div>')


def write_report(metrics, rows):
    crit = metrics.get("critic")
    draft = metrics.get("drafter")
    models = metrics.get("models", {})
    srcs = metrics.get("data_sources", [])
    crows = [r for r in rows if r.get("kind") == "critic"]
    drows = [r for r in rows if r.get("kind") == "drafter"]
    n_viol = sum(1 for r in crows if r.get("expect_verdict") == "revise")
    n_ctrl = sum(1 for r in crows if r.get("expect_verdict") == "pass")

    h = [f"<!doctype html><html lang=en><head><meta charset=utf-8>"
         f"<meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>IT Helpdesk agent — Evaluation</title><style>{CSS}</style></head><body><div class=wrap>"]
    h.append("<header><div class=kicker>GritAI Labs · IT helpdesk agent · evaluation</div>"
             "<h1>Did the gate earn its automation?</h1>"
             "<p class=lede>This L1/L2 helpdesk agent runs <b>unattended</b> — so before trusting it we prove "
             "both halves against the runbook and <b>live</b> external facts: the <b>drafter</b> writes "
             "grounded, on-runbook resolutions, and the <b>critic</b> gate reliably catches violations. "
             "Grounded in real free APIs, 100% local models, $0.</p>"
             f"<div class=meta>generated {_esc(metrics.get('generated_at'))} · drafter "
             f"<code>{_esc(models.get('drafter'))}</code> · gate/critic <code>{_esc(models.get('critic'))}</code> · "
             f"judge <code>{_esc(models.get('judge'))}</code> · live data: "
             f"{_esc(', '.join(srcs))} · {metrics.get('n_cases','?')} cases</div></header>")

    h.append("<h2>Headline</h2><div class=panel><div class=kpis>")
    if crit:
        h.append(_kpi2(_pct(crit.get("recall")), "gate recall (violations caught)", crit.get("recall")))
        h.append(_kpi2(_pct(crit.get("precision")), "gate precision", crit.get("precision")))
    if draft:
        h.append(_kpi2(_pct(draft.get("runbook_compliant_pct")), "drafter runbook-compliant", draft.get("runbook_compliant_pct")))
        h.append(_kpi2(_pct(draft.get("category_accuracy")), "triage accuracy", draft.get("category_accuracy")))
    h.append("</div><div class=note>Recall is the safety number: of all planted runbook violations, what "
             "fraction did the automated gate catch? A miss is the costly error.</div></div>")

    h.append("<h2>1 · How we built the test</h2><div class=panel>"
             "<div class=step><div class=n>A</div><div><b>Runbook-anchored gold set.</b> Every case traces to a "
             "section of the internal IT <code>runbook</code> (EOL upgrade SLA, CVSS patch tiers, outage comms, "
             "password self-service, least-privilege access).</div></div>"
             f"<div class=step><div class=n>B</div><div><b>{len(drows)} drafter cases</b> run LIVE: they pull real "
             "EOL dates (endoflife.date), real CVSS scores (CIRCL), real service status (statuspage) at eval "
             "time — no mock.</div></div>"
             f"<div class=step><div class=n>C</div><div><b>{len(crows)} critic cases</b> = {n_ctrl} known-good "
             f"controls + {n_viol} <b class=accent>planted runbook violations</b>: wrong CVSS SLA tier, invented "
             "EOL date, severity-without-lookup, setting a password, granting admin, inventing an outage + "
             "promising a restore time, answering the wrong question.</div></div></div>")

    h.append("<h2>2 · How we verified</h2><div class=panel>"
             "<div class=step><div class=n>Gate</div><div><b>Deterministic confusion matrix.</b> Each hand-labeled "
             "resolution goes to the real critic node; its verdict is compared to the ground-truth label. No LLM "
             "grading an LLM — this is the strongest proof of the automated gate.</div></div>"
             "<div class=step><div class=n>Drafter</div><div><b>Independent Diamond judge</b> "
             f"(<code>{_esc(models.get('judge'))}</code> on a separate node) refutes each live resolution per "
             "criterion (grounded / on-runbook / addresses), failing closed when unsure, plus deterministic "
             "grounding checks.</div></div></div>")

    if crit:
        h.append("<h2>3 · The automated gate (critic)</h2><div class=panel>"
                 "<table class=cm><tr><th></th><th>predicted REVISE</th><th>predicted PASS</th></tr>"
                 f"<tr><th>actual VIOLATION</th><td class=tp>{crit['tp']} ✓ caught</td><td class=fn>{crit['fn']} ✗ missed</td></tr>"
                 f"<tr><th>actual GOOD</th><td class=fp>{crit['fp']} false alarm</td><td class=tn>{crit['tn']} ✓ passed</td></tr></table>"
                 "<div class=kpis>"
                 + _kpi2(_pct(crit.get("recall")), "recall", crit.get("recall"))
                 + _kpi2(_pct(crit.get("precision")), "precision", crit.get("precision"))
                 + _kpi2(_pct(crit.get("accuracy")), "accuracy", crit.get("accuracy")) + "</div>")
        h.append("<details><summary>Per-case detail (" + str(len(crows)) + " critic cases)</summary><table class=cases>"
                 "<tr><th>id</th><th>ticket</th><th>planted violation</th><th>expected</th><th>got</th><th></th></tr>")
        for r in crows:
            tag = '<span class="tag ok">correct</span>' if r.get("correct") else '<span class="tag no">WRONG</span>'
            h.append(f"<tr><td><code>{_esc(r['id'])}</code></td><td>{_esc(r['ticket'])}</td>"
                     f"<td class=muted>{_esc(r.get('violation'))}</td><td>{_esc(r.get('expect_verdict'))}</td>"
                     f"<td>{_esc(r.get('got_verdict'))}</td><td>{tag}</td></tr>")
        h.append("</table></details></div>")

    if draft:
        h.append("<h2>4 · The drafter (grounded in live data)</h2><div class=panel><div class=kpis>"
                 + _kpi2(_pct(draft.get("runbook_compliant_pct")), "runbook-compliant (grounded & on-runbook)", draft.get("runbook_compliant_pct"))
                 + _kpi2(_pct(draft.get("grounded_pct")), "grounded (invents no fact)", draft.get("grounded_pct"))
                 + _kpi2(_pct(draft.get("on_runbook_pct")), "on-runbook (right action/SLA)", draft.get("on_runbook_pct"))
                 + _kpi2(_pct(draft.get("addresses_pct")), "addresses", draft.get("addresses_pct"))
                 + _kpi2(_pct(draft.get("category_accuracy")), "triage accuracy", draft.get("category_accuracy"))
                 + _kpi2(_pct(draft.get("fully_correct_pct")), "fully correct (strict)", draft.get("fully_correct_pct"))
                 + "</div>")
        pc = draft.get("per_category") or {}
        if pc:
            h.append("<div class=note>runbook-compliance by category: " + " · ".join(
                f"{_esc(k)} {d['runbook_compliant']}/{d['n']}" for k, d in sorted(pc.items())) + "</div>")
        h.append("<details><summary>Per-case detail (" + str(len(drows)) + " drafter cases)</summary><table class=cases>"
                 "<tr><th>id</th><th>ticket</th><th>triage</th><th>grounded</th><th>on-runbook</th><th>addresses</th><th>judge note</th></tr>")
        for r in drows:
            j = r.get("judge", {})
            def _t(v):
                return '<span class="tag ok">Y</span>' if v else '<span class="tag no">n</span>'
            cok = ('<span class="tag ok">' if r["checks"]["cat_ok"] else '<span class="tag no">') + _esc(r.get("category")) + '</span>'
            addr = '<span class="tag ok">Y</span>' if j.get("addresses") else '<span class="tag soft">soft</span>'
            h.append(f"<tr><td><code>{_esc(r['id'])}</code></td><td>{_esc(r['ticket'])}</td><td>{cok}</td>"
                     f"<td>{_t(j.get('grounded'))}</td><td>{_t(j.get('on_runbook'))}</td><td>{addr}</td>"
                     f"<td class=muted>{_esc(j.get('reason'))}</td></tr>")
        h.append("</table></details></div>")

    h.append("<h2>5 · What it shows &amp; where it goes</h2><div class=panel>"
             "<p>The gate is <b>measured, not asserted</b>, against a runbook and live external truth — and the "
             "drafter's answers are checked by an independent skeptic. The same harness A/B's models and will "
             "<b>eval-gate any fine-tune</b>.</p>"
             "<p><b class=accent>Forward: the data flywheel on real data.</b> Every row of <code>dataset.jsonl</code> "
             "— real tickets, live facts, graded resolutions — is the bootstrap to fine-tune the local models on "
             "<i>how to resolve</i>, while the free APIs keep the <i>facts</i> current. Eval-gated by this page.</p></div>")
    h.append("<footer>Built by <b>GritAI&nbsp;Labs</b> · runbook-grounded eval over live free APIs, 100% local · "
             "<a href='/'>&larr; back to the live agent</a></footer></div></body></html>")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("".join(h))
    return OUT


if __name__ == "__main__":
    with open(os.path.join(HERE, "metrics.json"), encoding="utf-8") as f:
        m = json.load(f)
    rows = []
    dp = os.path.join(HERE, "dataset.jsonl")
    if os.path.exists(dp):
        rows = [json.loads(ln) for ln in open(dp, encoding="utf-8") if ln.strip()]
    print("wrote", write_report(m, rows))
