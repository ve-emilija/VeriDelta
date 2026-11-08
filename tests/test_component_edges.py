from veridelta import component_edges as ce


KNOWN = {"my_env", "my_scoreboard", "my_agent", "my_monitor", "my_cfg", "base_item", "ext_item",
         "base_seq", "ext_seq"}


# ── member handles ───────────────────────────────────────────────────────────

def test_member_handle_to_known_class_is_an_edge():
    # Direction: held_type -> holder, so BFS from a change to my_scoreboard
    # reaches my_env (see ComponentEdge's docstring for the convention).
    body = "class my_env;\n  my_scoreboard scb_h;\n  int not_a_class;\nendclass"
    edges = ce.member_handle_edges("my_env", body, KNOWN)
    assert len(edges) == 1
    assert (edges[0].src, edges[0].dst, edges[0].kind) == ("my_scoreboard", "my_env", "member_handle")
    assert edges[0].via == "scb_h"


def test_member_handle_to_unknown_type_is_not_an_edge():
    body = "class my_env;\n  some_random_type x;\nendclass"
    assert ce.member_handle_edges("my_env", body, KNOWN) == []


def test_member_handle_self_reference_is_skipped():
    body = "class my_env;\n  my_env parent_h;\nendclass"
    assert ce.member_handle_edges("my_env", body, KNOWN) == []


def test_member_handle_rand_qualifier_does_not_block_resolution():
    body = "class my_env;\n  rand my_cfg cfg_h;\nendclass"
    edges = ce.member_handle_edges("my_env", body, KNOWN)
    assert [(e.src, e.via) for e in edges] == [("my_cfg", "cfg_h")]


def test_member_handle_to_library_base_class_is_filtered_out():
    # Real finding on Caliptra-rtl: auto-generated RAL register classes
    # declare a uvm_reg_field member per CSR field -- thousands of
    # near-identical, zero-signal edges without this filter.
    known = KNOWN | {"uvm_reg_field"}
    body = "class my_csr_field_holder;\n  rand uvm_reg_field NAME;\nendclass"
    assert ce.member_handle_edges("my_csr_field_holder", body, known) == []


def test_local_variable_inside_method_is_not_a_member_handle():
    # Reuses change_summary's method-body blanking: a local var must not leak in.
    body = ("class my_env;\n"
            "  virtual function void f();\n"
            "    my_scoreboard local_scb;\n"
            "  endfunction\n"
            "endclass")
    assert ce.member_handle_edges("my_env", body, KNOWN) == []


# ── config_db set/get ─────────────────────────────────────────────────────────

def test_config_db_set_and_get_extracted():
    body = 'class my_env;\n  function void f();\n' \
           '    uvm_config_db#(my_cfg)::set(this, "", "cfg", cfg);\n' \
           '  endfunction\nendclass'
    calls = ce.config_db_calls("my_env", body)
    assert len(calls) == 1
    assert calls[0] == {"class": "my_env", "verb": "set", "type": "my_cfg", "scope": "",
                         "field": "cfg", "evidence": calls[0]["evidence"]}


def test_config_db_virtual_interface_type_is_kept_verbatim():
    body = 'class my_env;\n  uvm_config_db#(virtual my_if)::get(this, "", "vif", vif);\nendclass'
    calls = ce.config_db_calls("my_env", body)
    assert calls[0]["type"] == "virtual my_if"


def test_config_db_non_literal_field_is_skipped():
    # Real OpenTitan shape: field name comes from a variable/method call, not
    # a string literal -- must not be guessed at.
    body = 'class my_env;\n  uvm_config_db#(my_cfg)::set(null, "*.env", m_util.get_name(), cfg);\nendclass'
    assert ce.config_db_calls("my_env", body) == []


def test_config_db_edges_match_setter_to_getter_on_type_and_field():
    table = {
        "setter_cls": 'class setter_cls;\n  function void f();\n'
                      '    uvm_config_db#(my_cfg)::set(this, "", "cfg", cfg);\n  endfunction\nendclass',
        "getter_cls": 'class getter_cls;\n  function void f();\n'
                      '    uvm_config_db#(my_cfg)::get(this, "", "cfg", cfg);\n  endfunction\nendclass',
        "unrelated_cls": 'class unrelated_cls;\n  function void f();\n'
                          '    uvm_config_db#(int)::get(this, "", "other_key", x);\n  endfunction\nendclass',
    }
    edges = ce.config_db_edges(table)
    assert len(edges) == 1
    e = edges[0]
    assert (e.src, e.dst, e.kind) == ("setter_cls", "getter_cls", "config_db")
    assert e.via == 'my_cfg "cfg"'


def test_config_db_same_class_set_and_get_is_not_an_edge():
    table = {
        "self_cls": 'class self_cls;\n  function void f();\n'
                    '    uvm_config_db#(my_cfg)::set(this, "", "cfg", cfg);\n'
                    '    uvm_config_db#(my_cfg)::get(this, "", "cfg", cfg2);\n  endfunction\nendclass',
    }
    assert ce.config_db_edges(table) == []


# ── analysis-port connect ────────────────────────────────────────────────────

def test_connect_resolves_both_sides_via_member_table():
    body = ("class my_env;\n"
            "  my_agent agt_h;\n"
            "  my_scoreboard scb_h;\n"
            "  function void connect_phase(uvm_phase phase);\n"
            "    agt_h.monitor.ap.connect(scb_h.analysis_export);\n"
            "  endfunction\n"
            "endclass")
    edges = ce.analysis_port_edges("my_env", body, KNOWN)
    assert len(edges) == 1
    assert (edges[0].src, edges[0].dst, edges[0].kind) == ("my_agent", "my_scoreboard", "analysis_port")


def test_connect_with_this_prefix_resolves_to_enclosing_class():
    body = ("class my_scoreboard;\n"
            "  my_agent agt_h;\n"
            "  function void connect_phase(uvm_phase phase);\n"
            "    agt_h.ap.connect(this.imp);\n"
            "  endfunction\n"
            "endclass")
    edges = ce.analysis_port_edges("my_scoreboard", body, KNOWN)
    assert (edges[0].src, edges[0].dst) == ("my_agent", "my_scoreboard")


def test_connect_with_array_index_still_resolves_the_member():
    body = ("class my_env;\n"
            "  my_agent agents[string];\n"
            "  my_scoreboard scb_h;\n"
            "  function void connect_phase(uvm_phase phase);\n"
            "    agents[\"x\"].monitor.ap.connect(scb_h.analysis_export);\n"
            "  endfunction\n"
            "endclass")
    edges = ce.analysis_port_edges("my_env", body, KNOWN)
    assert (edges[0].src, edges[0].dst) == ("my_agent", "my_scoreboard")


def test_connect_to_unresolvable_local_variable_is_skipped():
    body = ("class my_env;\n"
            "  function void connect_phase(uvm_phase phase);\n"
            "    my_scoreboard local_scb = get_scb();\n"
            "    agt_h.ap.connect(local_scb.analysis_export);\n"
            "  endfunction\n"
            "endclass")
    # `agt_h` isn't a declared member here either -> nothing resolvable.
    assert ce.analysis_port_edges("my_env", body, KNOWN) == []


def test_connect_same_type_both_sides_is_not_an_edge():
    body = ("class my_env;\n"
            "  my_agent a_h;\n"
            "  my_agent b_h;\n"
            "  function void connect_phase(uvm_phase phase);\n"
            "    a_h.ap.connect(b_h.export);\n"
            "  endfunction\n"
            "endclass")
    assert ce.analysis_port_edges("my_env", body, KNOWN) == []


# ── factory overrides ─────────────────────────────────────────────────────────

def test_factory_override_type_id_form():
    body = 'class my_env;\n  function void f();\n' \
           '    base_item::type_id::set_type_override(ext_item::get_type());\n  endfunction\nendclass'
    edges = ce.factory_override_edges("my_env", body, KNOWN)
    assert len(edges) == 1
    assert (edges[0].src, edges[0].dst, edges[0].kind) == ("base_item", "ext_item", "factory_override")


def test_factory_override_by_type_form():
    body = 'class my_env;\n  function void f();\n' \
           '    set_type_override_by_type(base_seq::get_type(), ext_seq::get_type());\n  endfunction\nendclass'
    edges = ce.factory_override_edges("my_env", body, KNOWN)
    assert (edges[0].src, edges[0].dst) == ("base_seq", "ext_seq")


def test_factory_override_get_type_without_parens():
    # Real OpenTitan shape (adc_ctrl): `::get_type` with no trailing `()`.
    body = 'class my_env;\n  function void f();\n' \
           '    set_type_override_by_type(base_seq::get_type, ext_seq::get_type);\n  endfunction\nendclass'
    edges = ce.factory_override_edges("my_env", body, KNOWN)
    assert (edges[0].src, edges[0].dst) == ("base_seq", "ext_seq")


def test_factory_override_package_scoped_orig_is_stripped_to_base_name():
    body = 'class my_env;\n  function void f();\n' \
           '    cip_base_pkg::base_item::type_id::set_type_override(ext_item::get_type());\n' \
           '  endfunction\nendclass'
    edges = ce.factory_override_edges("my_env", body, KNOWN)
    assert (edges[0].src, edges[0].dst) == ("base_item", "ext_item")


def test_factory_override_same_orig_and_new_is_not_an_edge():
    body = 'class my_env;\n  function void f();\n' \
           '    base_item::type_id::set_type_override(base_item::get_type());\n  endfunction\nendclass'
    assert ce.factory_override_edges("my_env", body, KNOWN) == []


def test_factory_override_to_a_class_outside_known_classes_is_not_an_edge():
    # Real bug found validating against OpenTitan: an earlier version had no
    # known_classes check at all (unlike the other three edge kinds), so a
    # token like `csr_base_seq` -- present in the corpus as a regex match but
    # NOT a node in node_types.json/adjacency.json -- produced an edge whose
    # endpoint `blast-radius --show-associations` could never look up.
    body = 'class my_env;\n  function void f();\n' \
           '    csr_base_seq::type_id::set_type_override(csr_hw_reset_seq::get_type());\n' \
           '  endfunction\nendclass'
    assert ce.factory_override_edges("my_env", body, KNOWN) == []
    known_with_csr = KNOWN | {"csr_base_seq", "csr_hw_reset_seq"}
    edges = ce.factory_override_edges("my_env", body, known_with_csr)
    assert (edges[0].src, edges[0].dst) == ("csr_base_seq", "csr_hw_reset_seq")


# ── corpus + end-to-end ────────────────────────────────────────────────────────

def test_edges_to_adjacency_groups_by_source_and_keeps_evidence():
    edges = [
        ce.ComponentEdge("a", "b", "member_handle", "evidence1", "h"),
        ce.ComponentEdge("a", "c", "factory_override", "evidence2"),
    ]
    adj = ce.edges_to_adjacency(edges)
    assert set(adj["a"][0]) == {"dst", "kind", "via", "evidence"}
    assert {d["dst"] for d in adj["a"]} == {"b", "c"}


def test_all_component_edges_end_to_end_on_a_small_corpus():
    table = {
        "my_env": ("class my_env;\n"
                   "  my_agent agt_h;\n"
                   "  my_scoreboard scb_h;\n"
                   "  function void connect_phase(uvm_phase phase);\n"
                   "    agt_h.monitor.ap.connect(scb_h.analysis_export);\n"
                   "  endfunction\n"
                   "endclass"),
        "my_scoreboard": "class my_scoreboard;\nendclass",
        "my_agent": "class my_agent;\nendclass",
    }
    edges = ce.all_component_edges(table, KNOWN)
    kinds = {e.kind for e in edges}
    assert "member_handle" in kinds and "analysis_port" in kinds
