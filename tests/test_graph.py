import pytest

from veridelta import type_annotator as ta


@pytest.mark.parametrize("name,expected", [
    ("uart_env_cfg", "CFG"),          # CFG is evaluated before ENV
    ("uart_env", "ENV"),
    ("uart_agent", "AGENT"),
    ("uart_scoreboard", "SCBD"),
    ("uart_base_vseq", "SEQ"),
    ("uart_env_cov", "COV"),
    ("dv_base_reg_block", "RAL"),
    ("something_else", "OTHER"),
])
def test_type_function(name, expected):
    assert ta.classify_type(name) == expected


def test_type_function_is_case_insensitive():
    assert ta.classify_type("UART_ENV_CFG") == "CFG"


def test_plain_inheritance_edge():
    assert ta.extract_class_edges("class a_cfg extends b_cfg;\nendclass") == [("b_cfg", "a_cfg")]


def test_parameterized_inheritance_is_not_dropped():
    src = ("class cip_base_env_cfg #(type RAL_T = dv_base_reg_block) extends dv_base_env_cfg #(RAL_T);\n"
           "endclass")
    assert ("dv_base_env_cfg", "cip_base_env_cfg") in ta.extract_class_edges(src)


def test_extractor_handles_nested_parentheses_in_the_parameter_block():
    # the earlier regex stopped at the first ")" of the parameter block and dropped such declarations
    src = "class x_cfg #(int W = $clog2(8)) extends y_cfg #(W);\nendclass"
    assert ta.legacy_class_edges(src) == []
    assert ta.extract_class_edges(src) == [("y_cfg", "x_cfg")]


def test_comments_and_strings_are_ignored():
    src = '// class fake_cfg extends other_cfg;\n/* class f2 extends g2; */\nstring s = "class f3 extends g3;";\n'
    assert ta.extract_class_edges(src) == []


def test_virtual_and_package_scoped_extends():
    src = "virtual class base_seq extends uvm_sequence;\nclass a_seq extends pkg::base_seq;\n"
    edges = ta.extract_class_edges(src)
    assert ("uvm_sequence", "base_seq") in edges
    assert any(child == "a_seq" for _, child in edges)


def test_annotate_graph_sets_types():
    nx = pytest.importorskip("networkx")
    G = nx.DiGraph([("dv_base_env_cfg", "uart_env_cfg"), ("dv_base_env_cfg", "weird_name")])
    ta.annotate_graph(G)
    assert G.nodes["uart_env_cfg"]["type"] == "CFG"
    assert G.nodes["weird_name"]["type"] == "OTHER"


def test_shipped_graphs_match_the_paper(paper_data):
    ot = paper_data("data", "opentitan", "adjacency.json")
    cal = paper_data("data", "caliptra-rtl", "adjacency.json")
    assert (len(ot), sum(map(len, ot.values()))) == (1312, 1287)
    assert (len(cal), sum(map(len, cal.values()))) == (1043, 1021)


# ── UVM base-class typing (--uvm-types) ──────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("uvm_sequence", "SEQ"),
    ("uvm_scoreboard", "SCBD"),
    ("uvm_reg_block", "RAL"),
    ("uvm_mem", "RAL"),
    ("uvmf_environment_base", "ENV"),
    ("uvmf_environment_configuration_base", "CFG"),
    ("uvm_object", None),            # too generic to say anything
    ("uvm_component", None),
    ("my_env", None),                # only library classes count
])
def test_uvm_base_type(name, expected):
    assert ta.uvm_base_type(name) == expected


def _chain_graph():
    nx = pytest.importorskip("networkx")
    G = nx.DiGraph()
    G.add_edges_from([("uvm_sequence", "block_gen"), ("block_gen", "block_gen2"),
                      ("uvm_env", "top_scoreboard")])
    return G


def test_default_annotation_is_name_only():
    G = ta.annotate_graph(_chain_graph())
    assert G.nodes["block_gen"]["type"] == "OTHER"
    assert G.nodes["block_gen2"]["type"] == "OTHER"


def test_uvm_typing_fills_untyped_classes_through_the_chain():
    G = ta.annotate_graph(_chain_graph(), uvm=True)
    assert G.nodes["block_gen"]["type"] == "SEQ"
    assert G.nodes["block_gen2"]["type"] == "SEQ"
    assert G.nodes["block_gen2"]["inherited_from"] == "uvm_sequence"


def test_name_based_type_wins_over_uvm_ancestor():
    G = ta.annotate_graph(_chain_graph(), uvm=True)
    assert G.nodes["top_scoreboard"]["type"] == "SCBD"      # name says SCBD, ancestor says ENV
    assert "inherited_from" not in G.nodes["top_scoreboard"]
