"""
label_commits.py -- failure-category labels for the 400 evaluation commits.

Labeler: Claude Sonnet (a stronger model than the Haiku used by VeriDelta), temperature 0, shown the
same information the human annotator saw: commit message, changed UVM classes with their type, and
the changed files.  It never sees the dependency graph, the blast radius or any VeriDelta output.
Usage:  python label_commits.py [--mock] [--limit N]      (needs ANTHROPIC_API_KEY unless --mock)
Output: data/labels_llm.json   {hash: {"categories": [...], "none": bool, "reason": str}}
"""
import csv, json, os, sys, argparse, collections
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # repository root (data/ and results/ live here)
from veridelta import graphrag_pipeline as gp

LABELER_MODEL = os.environ.get("VD_LABELER_MODEL", "claude-sonnet-5-5")
DEFS = """Interface Drift: signal layouts, widths or protocol parameters change, invalidating virtual interface handles or transaction field assumptions in agents, drivers and monitors.
Sequence Invalidation: stimulus generation assumptions become invalid, so sequences produce illegal or incomplete stimulus.
Checker Desync: scoreboard reference models diverge from the updated behavior.
RAL Desync: the register abstraction layer drifts from the actual register map after register additions, removals or address changes.
Coverage Regression: coverage collection points become unreachable or insufficient after structural changes."""

def prompt(c, msg, files):
    cl = ", ".join(f"{k} ({v})" for k, v in c["types"].items())
    return f"""You are labeling a commit to a UVM verification environment.

Question: which failure categories could this change cause in the components that depend on the changed classes?
Choose one or two categories from the list below. If the change is a pure refactor, rename, comment or tooling change that would cause none of them, answer "none".

{DEFS}

COMMIT MESSAGE:
{msg[:1500]}

CHANGED UVM CLASSES: {cl}
CHANGED FILES:
""" + "\n".join(files[:12]) + """

Answer with ONLY a JSON object: {"categories": ["<category>", ...], "none": false, "reason": "<at most 15 words>"}"""

class Mock:                       # offline plumbing test only
    class messages:
        @staticmethod
        def create(**kw):
            class R: pass
            r = R(); t = R(); t.text = '{"categories": ["Sequence Invalidation"], "none": false, "reason": "mock"}'
            r.content = [t]; r.stop_reason = "end_turn"; return r

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--mock", action="store_true"); ap.add_argument("--limit", type=int)
    ap.add_argument("--cache", default=os.path.join(ROOT, "results", "cache_labels")); a = ap.parse_args()
    gp.MODEL = LABELER_MODEL; gp.configure_llm(a.cache, None)   # Sonnet 5.5 has no temperature parameter
    client = Mock() if a.mock else gp.make_client()
    sample = json.load(open(os.path.join(ROOT, "data", "sample400.json")))[: a.limit]
    body = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "data", "corpus", "commits.csv"), encoding="utf-8", errors="ignore")):
        body[(r["repo"], r["hash"])] = (r["subject"] + "\n\n" + r["body"].strip())
    files = collections.defaultdict(list)
    for r in csv.DictReader(open(os.path.join(ROOT, "data", "corpus", "corpus_files.csv"))):
        files[(r["repo"], r["hash"])].append(r["path"])
    from concurrent.futures import ThreadPoolExecutor
    def one(c):
        k = (c["repo"], c["hash"])
        base = prompt(c, body.get(k, c["subject"]), files[k]); obj = None
        for attempt in range(3):
            p_ = base if attempt == 0 else base + "\n\nYour previous reply was not a JSON object. Reply with ONLY the JSON object, nothing else."
            raw, stop = gp.llm_text(p_, client, max_tokens=1000)
            obj, ok = gp.parse_llm_json(raw)
            if stop != "max_tokens" and ok and isinstance(obj, dict) and "categories" in obj:
                break
            gp.LLM_STATS["label_reply_retry"] += 1; obj = None
        if obj is None:                      # never turn a failure into 'none'
            gp.LLM_STATS["label_unparsed"] += 1
            return c["hash"], {"categories": [], "none": None, "reason": "UNPARSED"}
        cats = [x for x in obj.get("categories", []) if x in gp.VALID_CATEGORIES]
        return c["hash"], {"categories": cats[:2], "none": bool(obj.get("none")) or not cats, "reason": obj.get("reason", "")}
    with ThreadPoolExecutor(8) as ex:
        out = dict(ex.map(one, sample))
    json.dump(out, open(os.path.join(ROOT, "data", "labels_llm_mock.json" if a.mock else "labels_llm.json"), "w"), indent=1)
    print("done", len(out), "| distribution:", dict(collections.Counter(c for v in out.values() for c in v["categories"])),
          "| none:", sum(v["none"] for v in out.values()), "| stats", dict(gp.LLM_STATS))
