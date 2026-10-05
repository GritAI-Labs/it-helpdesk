"""Tools the IT-helpdesk graph calls — grounded in REAL free public APIs (no mock, no API key).

These are the anti-hallucination layer: the drafter answers ONLY from what these return, so it can't
invent an EOL date, a CVE severity, or a service's up/down state. All three APIs are free & key-less:

  lookup_eol(product[, cycle])  -> https://endoflife.date   (OS/runtime support + EOL dates)
  lookup_cve(cve_id)            -> https://cve.circl.lu     (CVE 5.0 record incl. CVSS base score)
  check_service_status(service) -> vendor statuspage.io JSON (GitHub, Cloudflare, ... up/down)
  lookup_runbook(category)      -> runbook/runbook.json     (internal policy + the sanctioned action)

Each API tool wraps a timeout + try/except and returns {found: False, error|note: ...} on any failure,
so the agent degrades to "couldn't verify -> escalate" instead of guessing.
"""
import os
import re
import json
import datetime
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
_UA = {"User-Agent": "GritAI-IT-Helpdesk/1.0", "Accept": "application/json"}


def _get_json(url, timeout=12):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ---- runbook KB (internal policy — the ground truth the critic checks against) --------------
_RUNBOOK = None


def _load_runbook():
    global _RUNBOOK
    if _RUNBOOK is None:
        try:
            with open(os.path.join(HERE, "runbook", "runbook.json"), encoding="utf-8") as f:
                _RUNBOOK = json.load(f)
        except Exception:
            _RUNBOOK = {}
    return _RUNBOOK


def lookup_runbook(category, query=""):
    """Return the internal policy + sanctioned action for this category (and any keyword match)."""
    rb = _load_runbook()
    q = (query or "").lower()
    out = {}
    for name, sec in rb.items():
        topics = sec.get("topics", []) or []
        if name == category or name in q or any(t in q for t in topics):
            out[name] = {"policy": sec.get("text", ""), "action": sec.get("action", "")}
    return out


# ---- extractors -----------------------------------------------------------------------------
_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)
_KNOWN_SERVICES = {
    "github": "https://www.githubstatus.com",
    "cloudflare": "https://www.cloudflarestatus.com",
    "dropbox": "https://status.dropbox.com",
    "zoom": "https://status.zoom.us",
    "digitalocean": "https://status.digitalocean.com",
    "reddit": "https://www.redditstatus.com",
}
# endoflife.date product slugs we recognize from free text (extend as needed)
_EOL_PRODUCTS = ["ubuntu", "debian", "centos", "rhel", "windows", "windows-server", "macos",
                 "python", "nodejs", "node", "php", "java", "django", "ruby", "go", "dotnet",
                 "postgresql", "mysql", "mongodb", "redis", "kubernetes", "docker-engine"]


def extract_cve(text):
    m = _CVE_RE.search(text or "")
    return m.group(0).upper() if m else None


def extract_service(text):
    t = (text or "").lower()
    for name in _KNOWN_SERVICES:
        if name in t:
            return name
    return None


def extract_product_cycle(text):
    """Best-effort: find a known product slug + an optional version/cycle (e.g. 'Ubuntu 18.04')."""
    t = (text or "").lower()
    prod = next((p for p in _EOL_PRODUCTS if p in t), None)
    if prod == "node":
        prod = "nodejs"
    cycle = None
    if prod:
        m = re.search(re.escape(prod) + r"[^\d]{0,6}(\d+(?:\.\d+)?)", t)
        if m:
            cycle = m.group(1)
    return prod, cycle


# ---- THE TOOLS (real APIs) ------------------------------------------------------------------
def lookup_eol(product, cycle=None):
    """OS/runtime support status from endoflife.date. Returns support + the authoritative EOL date."""
    if not product:
        return {"found": False, "note": "no product identified"}
    slug = product.strip().lower()
    try:
        data = _get_json(f"https://endoflife.date/api/{slug}.json")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"found": False, "product": slug, "note": "product not tracked by endoflife.date"}
        return {"found": False, "product": slug, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"found": False, "product": slug, "error": str(e)[:160]}
    if not isinstance(data, list) or not data:
        return {"found": False, "product": slug, "note": "no release data"}
    rec = None
    if cycle:
        rec = next((r for r in data if str(r.get("cycle")) == str(cycle)
                    or str(r.get("cycle")).startswith(str(cycle))), None)
    rec = rec or data[0]  # fall back to the newest cycle
    eol = rec.get("eol")
    today = datetime.date.today()
    eol_passed, days_to_eol = None, None
    if isinstance(eol, str) and re.match(r"\d{4}-\d{2}-\d{2}", eol):
        d = datetime.date.fromisoformat(eol)
        days_to_eol = (d - today).days
        eol_passed = days_to_eol < 0
    elif eol is True:
        eol_passed = True
    elif eol is False:
        eol_passed = False
    return {"found": True, "product": slug, "cycle": rec.get("cycle"),
            "eol": eol, "eol_passed": eol_passed, "days_to_eol": days_to_eol,
            "latest": rec.get("latest"), "lts": rec.get("lts")}


def _find_cvss(obj):
    """Recursively pull the first (baseScore, baseSeverity) out of a CVE 5.0 metrics tree."""
    if isinstance(obj, dict):
        if "baseScore" in obj:
            return obj.get("baseScore"), obj.get("baseSeverity")
        for v in obj.values():
            r = _find_cvss(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_cvss(v)
            if r:
                return r
    return None


def lookup_cve(cve_id):
    """CVE severity from CIRCL (cve.circl.lu), CVE 5.0 schema. Returns CVSS base score + severity."""
    if not cve_id:
        return {"found": False, "note": "no CVE id identified"}
    cid = cve_id.strip().upper()
    try:
        data = _get_json(f"https://cve.circl.lu/api/cve/{cid}", timeout=15)
    except Exception as e:
        return {"found": False, "cve": cid, "error": str(e)[:160]}
    if not data or not isinstance(data, dict):
        return {"found": False, "cve": cid, "note": "not found"}
    cvss = _find_cvss(data.get("containers", {})) or _find_cvss(data)
    score, severity = (cvss if cvss else (None, None))
    summary = ""
    try:
        for d in data["containers"]["cna"]["descriptions"]:
            if d.get("lang", "en").startswith("en"):
                summary = (d.get("value") or "")[:300]
                break
    except Exception:
        pass
    if score is None:
        return {"found": True, "cve": cid, "cvss": None, "severity": None,
                "summary": summary, "note": "no CVSS score published"}
    return {"found": True, "cve": cid, "cvss": score, "severity": severity, "summary": summary}


def check_service_status(service):
    """Up/down from the vendor's official statuspage.io JSON (key-less /api/v2/status.json)."""
    if not service:
        return {"found": False, "note": "no known service identified", "known": sorted(_KNOWN_SERVICES)}
    key = service.strip().lower()
    base = _KNOWN_SERVICES.get(key)
    if not base:
        return {"found": False, "service": key, "note": "service not in known status pages",
                "known": sorted(_KNOWN_SERVICES)}
    try:
        data = _get_json(f"{base}/api/v2/status.json", timeout=10)
    except Exception as e:
        return {"found": False, "service": key, "error": str(e)[:160]}
    st = data.get("status", {})
    return {"found": True, "service": key,
            "indicator": st.get("indicator"),           # none | minor | major | critical
            "description": st.get("description"),         # human text
            "operational": st.get("indicator") == "none",
            "updated": data.get("page", {}).get("updated_at")}


if __name__ == "__main__":
    print("EOL ubuntu 18.04 ->", lookup_eol("ubuntu", "18.04"))
    print("EOL python 3.8   ->", lookup_eol("python", "3.8"))
    print("CVE Log4Shell    ->", lookup_cve("CVE-2021-44228"))
    print("STATUS github    ->", check_service_status("github"))
    print("RUNBOOK vuln     ->", list(lookup_runbook("vulnerability").keys()))
