# IT Helpdesk Agent

An automated **L1/L2 IT helpdesk agent** (LangGraph) that triages a ticket, grounds its answer in
**live external truth**, and lets an **independent critic gate** the resolution before it auto-resolves
or escalates — proven against a runbook with an evaluation harness. 100% local models, $0 to run.

Sibling to [mini-ai-os](https://github.com/GritAI-Labs/mini-ai-os): same spine, grounded in real data.

```
ticket → classify → gather (lookup_eol · lookup_cve · service_status + runbook) → draft → critic ─(pass)→ finalize
                                              ^                                              │
                                              └───────────────(revise, bounded)─────────────┘
                                                                     pass → RESOLVED (automated)
                                                       revise-exhausted / policy → ESCALATED
```

## Grounded in real, free, key-less APIs
No mock data — the agent pulls the live fact at request time and cites it (and refuses to guess):

| Ticket type | Live source | Example |
|---|---|---|
| "Is my OS/runtime still supported?" | [endoflife.date](https://endoflife.date) | Ubuntu 18.04 → EOL 2023-05-31 → P2 upgrade, 30-day SLA |
| "How urgent is CVE-X?" | [CIRCL CVE](https://cve.circl.lu) | CVE-2021-44228 → CVSS 10 CRITICAL → patch within 24h |
| "Is service X down?" | vendor `statuspage.io` | GitHub operational → local L1 triage |
| password / access | internal `runbook/` | self-service SSO+MFA · least-privilege, manager approval |

## Design
- **Automated gate (no human in the loop):** an independent critic decides `pass → auto-resolve`;
  it escalates what it can't clear or what policy reserves for a human (e.g. admin-access requests).
- **Two-Spark model routing** (`llm.py`): drafter `gemma4:12b` on one node, critic/judge `qwen3:30b`
  on another — one model resident per node, no load/unload thrash.
- **Anti-hallucination:** the drafter answers ONLY from the verified facts + runbook; it cannot invent
  an EOL date, a CVSS score, or a service's up/down state.

## Proven, not asserted — `eval/`
A runbook-anchored gold set (hand-curated, incl. **planted policy violations**) proves both halves
before we trust the automation:
- **Critic gate** — deterministic confusion matrix over labeled resolutions: **precision 100%, recall
  88%** (catches wrong-SLA, invented-EOL/CVSS, password-setting, admin-granting, invented-outage).
- **Drafter** — an independent `qwen3:30b` Diamond judge refutes each live reply: **triage 100%,
  grounded 92%, runbook-compliant 75%**.

Run it: `python eval/run_eval.py` → `eval/report.html` (served at `/eval`). Every row of
`eval/dataset.jsonl` is the bootstrap for an eval-gated fine-tune (the data flywheel).

## Run
```bash
cp .env.example .env     # point SPARK1_URL/SPARK2_URL at your Ollama endpoint(s)
python server.py         # http://127.0.0.1:8013   (paste a ticket)
python graph.py "Is CVE-2021-44228 urgent?"   # or one-shot from the CLI
```
Needs any OpenAI-compatible endpoint(s) (Ollama / vLLM) hosting the drafter + critic models.

---
Built by [GritAI Labs](https://github.com/GritAI-Labs).
