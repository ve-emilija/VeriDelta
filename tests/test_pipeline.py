import json

import pytest

from veridelta import graphrag_pipeline as gp
from veridelta.mock import MockClient


def test_blast_radius_is_bfs_ordered_and_excludes_the_changed_class(adjacency):
    radius = gp.get_blast_radius("dv_base_env_cfg", adjacency)
    assert radius == [("cip_base_env_cfg", 1), ("dv_base_agent_cfg", 1),
                      ("spi_env_cfg", 2), ("uart_agent_cfg", 2), ("uart_env_cfg", 2)]
    assert "dv_base_env_cfg" not in [n for n, _ in radius]


def test_blast_radius_visits_each_class_once():
    adj = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}      # diamond
    assert sorted(n for n, _ in gp.get_blast_radius("a", adj)) == ["b", "c", "d"]


def test_blast_radius_cap_keeps_the_nearest_classes(adjacency):
    radius, meta = gp.get_blast_radius("dv_base_env_cfg", adjacency, max_nodes=2, return_meta=True)
    assert [d for _, d in radius] == [1, 1]
    assert meta == {"full_size": 5, "truncated": True}


def test_unknown_or_leaf_class_has_empty_radius(adjacency):
    assert gp.get_blast_radius("nope", adjacency) == []
    assert gp.get_blast_radius("uart_env_cfg", adjacency) == []


def test_stage1_context_attaches_type_and_prior(adjacency, node_types):
    radius = gp.get_blast_radius("cip_base_env_cfg", adjacency)
    ctx = gp.build_stage1_context("cip_base_env_cfg", radius, node_types, [])
    assert {it["primary"] for it in ctx["blast_radius"]} == {"Interface Drift"}
    assert ctx["changed_type"] == "CFG"


def test_retrieval_scores_ip_class_and_type(history, node_types):
    got = gp.retrieve_similar_commits("uart_env_cfg", "CFG", "uart", history, node_types)
    assert got[0]["hash"] == "aaaaaaaa"               # same IP (+3) + same class (+3) + same type (+2)
    assert "dddddddd" not in [c["hash"] for c in got]  # score 0: not retrieved


def test_retrieval_only_uses_older_commits(history, node_types):
    got = gp.retrieve_similar_commits("uart_env_cfg", "CFG", "uart", history, node_types,
                                      before="2024-04-01T00:00:00")
    assert {c["hash"] for c in got} == {"aaaaaaaa", "bbbbbbbb"}


def test_retrieval_never_returns_excluded_commits(history, node_types):
    got = gp.retrieve_similar_commits("uart_env_cfg", "CFG", "uart", history, node_types,
                                      exclude={"aaaaaaaa11"})
    assert "aaaaaaaa" not in [c["hash"] for c in got]


def test_retrieval_breaks_ties_by_recency(node_types):
    hist = {f"h{i}": {"date": f"2024-0{i}-01T00:00:00", "ip_block": "x", "changed_nodes": ["spi_env_cfg"],
                      "subject": ""} for i in (1, 2, 3)}
    got = gp.retrieve_similar_commits("uart_env_cfg", "CFG", "y", hist, node_types, k=2)
    assert [c["hash"] for c in got] == ["h3", "h2"]


def test_prompt_has_all_parts_and_no_failure_labels_in_history(adjacency, node_types, history):
    radius = gp.get_blast_radius("dv_base_env_cfg", adjacency)
    stage1 = gp.build_stage1_context("dv_base_env_cfg", radius, node_types, [])
    sim = gp.retrieve_similar_commits("dv_base_env_cfg", "CFG", "dv", history, node_types)
    prompt = gp.build_prompt(stage1, sim, "dv", node_types)
    for part in ("CHANGED CLASS: dv_base_env_cfg", "BLAST RADIUS (5", "HISTORICAL EXAMPLES", "Valid categories"):
        assert part in prompt
    history_block = prompt.split("HISTORICAL EXAMPLES")[1].split("Valid categories")[0]
    assert "Interface Drift" not in history_block     # history carries no failure categories


def test_change_message_is_added_only_when_given(adjacency, node_types):
    stage1 = gp.build_stage1_context("dv_base_env_cfg", gp.get_blast_radius("dv_base_env_cfg", adjacency), node_types, [])
    assert "change description" not in gp.build_prompt(stage1, [], "dv", node_types)
    assert "change description: add RAL type" in gp.build_prompt(stage1, [], "dv", node_types, change_message="add RAL type")


def test_predict_with_mock_model_covers_every_class(adjacency, node_types, history):
    data = {"adjacency": adjacency, "node_types": node_types, "bind_index": {}, "commit_index": history}
    res = gp.predict("dv_base_env_cfg", "dv", data, MockClient())
    assert set(res["predictions"]) == set(res["blast_radius"])
    assert res["few_shot_commits_used"] > 0


class SilentClient:
    """A model that returns an empty JSON object: every class must fall back to its prior."""
    class messages:
        @staticmethod
        def create(**kw):
            class R: pass
            r, b = R(), R()
            b.text = "{}"
            r.content = [b]
            r.stop_reason = "end_turn"
            return r


def test_missing_model_answers_fall_back_to_the_structural_prior(adjacency, node_types):
    data = {"adjacency": adjacency, "node_types": node_types, "bind_index": {}, "commit_index": {}}
    res = gp.predict("dv_base_env_cfg", "dv", data, SilentClient())
    for pred in res["predictions"].values():
        assert pred["source"] == "structural_prior"
        assert pred["confidence"] == pytest.approx(0.40)
        assert pred["categories"] == ["Interface Drift"]


def test_out_of_vocabulary_categories_are_dropped():
    parsed, _ = gp.parse_llm_json('{"a_cfg": {"categories": ["Interface Drift", "Made Up"], "confidence": 0.9}}')
    assert gp.clean_predictions(parsed)["a_cfg"]["categories"] == ["Interface Drift"]


def test_truncated_model_output_is_salvaged():
    parsed, complete = gp.parse_llm_json('{"a_cfg": {"categories": ["Interface Drift"], "confidence": 0.9}, "b_cf')
    assert "a_cfg" in parsed and not complete


def test_json_output_is_serialisable(adjacency, node_types):
    data = {"adjacency": adjacency, "node_types": node_types, "bind_index": {}, "commit_index": {}}
    json.dumps(gp.predict("dv_base_env_cfg", "dv", data, MockClient()))
