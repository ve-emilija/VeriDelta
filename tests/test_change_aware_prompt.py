"""
No fixtures needed (unlike test_pipeline.py, which needs conftest fixtures) —
build_prompt() only needs plain dicts, so this is runnable in the sandbox's
stand-in test runner as well as under real pytest.
"""
from veridelta import graphrag_pipeline as gp


def _stage1():
    return {
        "changed_node": "kmac_scoreboard",
        "changed_type": "SCBD",
        "blast_radius": [
            {"node": "kmac_env", "depth": 1, "type": "ENV", "primary": "Interface Drift"},
        ],
    }


def test_prompt_without_change_summary_has_no_new_section():
    prompt = gp.build_prompt(_stage1(), [], "kmac", {})
    assert "WHAT CHANGED IN THE CLASS ITSELF" not in prompt


def test_prompt_with_change_summary_includes_it_verbatim():
    summary = "  members added: enable_full_kmac\n  RAL/CSR calls: +2 (net)"
    prompt = gp.build_prompt(_stage1(), [], "kmac", {}, change_summary=summary)
    assert "WHAT CHANGED IN THE CLASS ITSELF:" in prompt
    assert summary in prompt
    # must come after CHANGED CLASS and before BLAST RADIUS, in that order
    assert prompt.index("CHANGED CLASS") < prompt.index("WHAT CHANGED") < prompt.index("BLAST RADIUS")


def test_prompt_with_both_message_and_summary_keeps_both():
    prompt = gp.build_prompt(_stage1(), [], "kmac", {},
                              change_message="fix the thing", change_summary="  methods added: foo")
    assert "change description: fix the thing" in prompt
    assert "methods added: foo" in prompt
