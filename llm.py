"""Thin OpenAI-compatible chat client with TWO-SPARK model routing — stdlib only, no SDK.

Why two endpoints: to avoid Ollama load/unload thrash we keep ONE model resident per node —
the drafter (gemma4) on Spark1, the critic/judge (qwen3:30b) on Spark2 — so a run never has to
swap a 20GB model in and out between nodes. chat(model=...) picks the endpoint from the model name.

Config via env (or a local .env): SPARK1_URL, SPARK2_URL, DRAFTER_MODEL, CRITIC_MODEL, JUDGE_MODEL.
Code defaults are localhost (no LAN IPs in source — the real fleet IPs live in .env, gitignored).
"""
import os
import json
import urllib.request
import urllib.error


def _load_dotenv(path=None):
    """Tiny .env loader so we don't need python-dotenv. Looks beside this file."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

# Two endpoints (localhost defaults; real fleet IPs in .env)
SPARK1 = os.environ.get("SPARK1_URL", "http://localhost:11434/v1")   # drafter node
SPARK2 = os.environ.get("SPARK2_URL", "http://localhost:11434/v1")   # critic/judge node

DRAFTER_MODEL = os.environ.get("DRAFTER_MODEL", "gemma4:12b-it-qat")  # generator (classify + draft)
CRITIC_MODEL = os.environ.get("CRITIC_MODEL", "qwen3:30b")            # independent gate
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "qwen3:30b")              # eval judge (Diamond)

# model -> endpoint routing (one model resident per node, no load/unload)
ENDPOINTS = {
    DRAFTER_MODEL: SPARK1,
    CRITIC_MODEL: SPARK2,
    JUDGE_MODEL: SPARK2,
}


def base_for(model):
    """Which endpoint hosts this model (default Spark1)."""
    return ENDPOINTS.get(model, SPARK1)


def chat(messages, model=None, temperature=0.3, max_tokens=512, timeout=180):
    """Send chat messages to the endpoint that hosts `model`, return the assistant's text.

    Retries on an EMPTY response with more token headroom — reasoning-heavy models (qwen3)
    can spend tokens before emitting content, so a tight max_tokens can truncate the answer.
    """
    mdl = model or DRAFTER_MODEL
    base = base_for(mdl)
    mt = max_tokens
    text = ""
    for _ in range(3):
        body = {"model": mdl, "messages": messages,
                "temperature": temperature, "max_tokens": mt, "stream": False}
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = json.loads(r.read())["choices"][0]["message"]["content"].strip()
        if text:
            return text
        mt = max(mt * 2, 1024)
    return text


if __name__ == "__main__":
    # connectivity smoke test: ping the drafter (Spark1) and the gate (Spark2)
    for label, mdl in [("drafter", DRAFTER_MODEL), ("gate", CRITIC_MODEL)]:
        try:
            out = chat([{"role": "user", "content": "Reply with exactly: OK"}], model=mdl, max_tokens=8)
            print(f"[{label:<7}] {mdl:<18} @ {base_for(mdl)} -> {out!r}")
        except Exception as e:
            print(f"[{label:<7}] {mdl:<18} @ {base_for(mdl)} -> ERROR {e}")
