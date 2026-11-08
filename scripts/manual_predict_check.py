"""
Fixture-free re-run of tests/test_pipeline.py's fixture-dependent cases
(inlining conftest.py's fixtures as plain values) to verify the `evidence`
citation addition to predict()/merge_predictions doesn't change any
existing, asserted behavior.
"""
import json
import sys

sys.path.insert(0, "/home/claude/veridelta/src")

from veridelta import graphrag_pipeline as gp
from veridelta.mock import MockClient

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {name}")


adjacency = {
    "dv_base_env_cfg": ["cip_base_env_cfg", "dv_base_agent_cfg"],
    "cip_base_env_cfg": ["spi_env_cfg", "uart_env_cfg"],
    "dv_base_agent_cfg": ["uart_agent_cfg"],
    "uart_env_cfg": [], "spi_env_cfg": [], "uart_agent_cfg": [],
}
node_types = {n: {"type": "CFG"} for n in
              ("dv_base_env_cfg", "cip_base_env_cfg", "dv_base_agent_cfg",
               "uart_env_cfg", "spi_env_cfg", "uart_agent_cfg")}
history = {
    "aaaaaaaa11": {"date": "2024-01-01T00:00:00", "subject": "uart cfg", "ip_block": "uart",
                   "changed_nodes": ["uart_env_cfg"]},
    "bbbbbbbb22": {"date": "2024-03-01T00:00:00", "subject": "spi cfg", "ip_block": "spi",
                   "changed_nodes": ["spi_env_cfg"]},
    "cccccccc33": {"date": "2024-05-01T00:00:00", "subject": "base cfg", "ip_block": "dv",
                   "changed_nodes": ["dv_base_env_cfg"]},
    "dddddddd44": {"date": "2024-07-01T00:00:00", "subject": "unrelated", "ip_block": "xxx",
                   "changed_nodes": ["zzz_unknown"]},
}

# test_predict_with_mock_model_covers_every_class
data = {"adjacency": adjacency, "node_types": node_types, "bind_index": {}, "commit_index": history}
res = gp.predict("dv_base_env_cfg", "dv", data, MockClient())
check("predictions keys == blast_radius", set(res["predictions"]) == set(res["blast_radius"]))
check("few_shot_commits_used > 0", res["few_shot_commits_used"] > 0)

# NEW: evidence citation checks
for node, pred in res["predictions"].items():
    check(f"{node} has evidence.graph_path", "graph_path" in pred["evidence"])
    check(f"{node} has evidence.cited_commits (list)", isinstance(pred["evidence"]["cited_commits"], list))
    check(f"{node} no longer carries raw depth key", "depth" not in pred)
# uart_env_cfg's commit aaaaaaaa11 should be cited on uart_env_cfg specifically
check("uart_env_cfg cites its own real commit",
      "aaaaaaaa" in res["predictions"]["uart_env_cfg"]["evidence"]["cited_commits"])
check("spi_env_cfg does not cite uart's commit",
      "aaaaaaaa" not in res["predictions"]["spi_env_cfg"]["evidence"]["cited_commits"])
check("depth is correct for direct children", res["predictions"]["cip_base_env_cfg"]["evidence"]["graph_path"]
      == "inheritance, depth=1")
check("depth is correct for grandchildren", res["predictions"]["uart_env_cfg"]["evidence"]["graph_path"]
      == "inheritance, depth=2")


# test_missing_model_answers_fall_back_to_the_structural_prior
class SilentClient:
    class messages:
        @staticmethod
        def create(**kw):
            class R: pass
            r, b = R(), R()
            b.text = "{}"
            r.content = [b]
            r.stop_reason = "end_turn"
            return r


data2 = {"adjacency": adjacency, "node_types": node_types, "bind_index": {}, "commit_index": {}}
res2 = gp.predict("dv_base_env_cfg", "dv", data2, SilentClient())
for pred in res2["predictions"].values():
    check("source == structural_prior", pred["source"] == "structural_prior")
    check("confidence == 0.40", abs(pred["confidence"] - 0.40) < 1e-9)
    check("categories == ['Interface Drift']", pred["categories"] == ["Interface Drift"])
    check("evidence present even with no commits", pred["evidence"]["cited_commits"] == [])

# test_json_output_is_serialisable
try:
    json.dumps(res)
    json.dumps(res2)
    check("json serialisable", True)
except TypeError:
    check("json serialisable", False)

# bind_at_risk evidence path (NEW: confirm bind_index predictions get graph_path="bind_index")
bind_data = dict(data)
bind_data["bind_index"] = {"my_rtl": ["my_rtl_bind"]}
res3 = gp.predict("dv_base_env_cfg", "dv", bind_data, MockClient(), rtl_module="my_rtl")
check("bind-at-risk entry present", "my_rtl_bind" in res3["predictions"])
check("bind-at-risk evidence.graph_path == bind_index",
      res3["predictions"]["my_rtl_bind"]["evidence"]["graph_path"] == "bind_index")
check("bind-at-risk evidence.cited_commits == []",
      res3["predictions"]["my_rtl_bind"]["evidence"]["cited_commits"] == [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
