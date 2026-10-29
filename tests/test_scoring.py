import pytest

from veridelta import evaluation as ev


def rec(pred, actual, node="n"):
    return {"commit": "c", "changed_node": "x", "node": node, "node_type": "CFG",
            "predicted": pred, "actual": actual, "ip_block": "i", "source": "llm"}


def test_micro_scores_on_a_toy_set():
    recs = [rec(["Interface Drift"], ["Interface Drift"]),                   # TP
            rec(["Interface Drift", "Checker Desync"], ["Interface Drift"]),  # TP + FP
            rec([], ["RAL Desync"])]                                          # FN
    m = ev.compute_metrics(recs)["micro"]
    assert (m["tp"], m["fp"], m["fn"]) == (2, 1, 1)
    assert m["precision"] == pytest.approx(0.6667, abs=1e-4)
    assert m["f1"] == pytest.approx(0.6667, abs=1e-4)


def test_empty_prediction_and_empty_truth_score_zero_not_crash():
    assert ev.compute_metrics([])["micro"]["f1"] == 0.0


def test_duplicate_points_are_counted_once():
    recs = [rec(["Interface Drift"], ["Interface Drift"], node="a"),
            rec(["Interface Drift"], ["Interface Drift"], node="a"),
            rec(["Interface Drift"], ["Interface Drift"], node="b")]
    assert len(ev.dedupe_records(recs)) == 2


def test_reported_table_iv_numbers_are_reproduced_from_the_stored_run(paper_data):
    """Regression guard: re-scoring the stored predictions must give the paper's Table IV."""
    run = paper_data("results", "run1", "results.json")
    scored = {s: ev.compute_metrics(ev.dedupe_records(r))["micro"]["f1"]
              for s, r in run["records"]["filtered"].items()}
    assert scored["graphrag"] == pytest.approx(0.95, abs=0.005)
    assert scored["llm_no_history"] == pytest.approx(0.90, abs=0.005)
    assert scored["llm_no_graph"] == pytest.approx(0.51, abs=0.005)
    assert scored["structural_prior"] == pytest.approx(0.99, abs=0.005)
    assert {len(ev.dedupe_records(r)) for r in run["records"]["filtered"].values()} == {1673}


def test_evaluation_sample_is_the_one_in_the_paper(paper_data):
    sample = paper_data("data", "sample400.json")
    assert len(sample) == 400
    assert sum(c["repo"] == "opentitan" for c in sample) == 374
    assert min(c["date"] for c in sample)[:10] == "2024-08-25"
