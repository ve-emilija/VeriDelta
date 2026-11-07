import time

import pytest

from veridelta import change_summary as cs


def summarize(old, new, cls="x"):
    return cs.summarize_class_change(old, new, cls)


# ── extract_class_body ───────────────────────────────────────────────────────

def test_extract_class_body_finds_the_named_class_only():
    src = "class a; int f; endclass\nclass b; int g; endclass\n"
    assert "int f" in cs.extract_class_body(src, "a")
    assert "int g" in cs.extract_class_body(src, "b")
    assert "int f" not in cs.extract_class_body(src, "b")


def test_extract_class_body_missing_file_or_class_is_none():
    assert cs.extract_class_body(None, "a") is None
    assert cs.extract_class_body("class a; endclass", "nope") is None


def test_extract_class_body_handles_parameterized_class():
    src = "class drv #(type ITEM_T = uvm_sequence_item) extends uvm_driver #(ITEM_T);\n" \
          "  int count;\nendclass"
    body = cs.extract_class_body(src, "drv")
    assert body is not None and "int count" in body


# ── members: added / removed / retyped, incl. the two real bugs found ──────

def test_member_added():
    old = "class x;\nendclass"
    new = "class x;\n  bit en;\nendclass"
    d = summarize(old, new)
    assert d["members_added"] == ["en"]


def test_member_with_initializer_is_still_caught():
    # Real bug: `bit enable_full_kmac = 1;` was invisible before the initializer
    # was added to the pattern (OpenTitan commit 5f6efdee, kmac_env_cfg.sv).
    old = "class x;\nendclass"
    new = "class x;\n  bit enable_full_kmac = 1;\nendclass"
    d = summarize(old, new)
    assert d["members_added"] == ["enable_full_kmac"]


def test_member_removed_and_retyped():
    old = "class x;\n  int a;\n  bit b;\nendclass"
    new = "class x;\n  int a[4];\nendclass"     # b removed, a retyped (array now)
    d = summarize(old, new)
    assert d["members_removed"] == ["b"]
    assert d["members_retyped"] == ["a"]


def test_local_variable_inside_a_method_is_never_a_member():
    # Real bug: a local `string default_ral_name;` declared inside build_phase()
    # was mis-read as a removed CLASS member (OpenTitan commit 2eb7256b,
    # dv_base_env.sv) because the member regex ran over the whole class body,
    # task bodies included.
    old = ("class x;\n"
           "  virtual function void build_phase(uvm_phase phase);\n"
           "    string default_ral_name;\n"
           "    default_ral_name = foo();\n"
           "  endfunction\n"
           "endclass")
    new = ("class x;\n"
           "  virtual function void build_phase(uvm_phase phase);\n"
           "    default_ral_name = foo();\n"   # the local var declaration line removed
           "  endfunction\n"
           "endclass")
    d = summarize(old, new)
    assert d["members_removed"] == []
    assert not cs.has_any_signal(d)


def test_rand_qualifier_is_part_of_identity():
    old = "class x;\n  bit [7:0] v;\nendclass"
    new = "class x;\n  rand bit [7:0] v;\nendclass"
    d = summarize(old, new)
    assert d["members_retyped"] == ["v"]


# ── methods: added / removed / signature changed vs. body-only change ──────

def test_method_added_and_removed():
    old = "class x; task a(); endtask endclass"
    new = "class x; task b(); endtask endclass"
    d = summarize(old, new)
    assert d["methods_added"] == ["b"]
    assert d["methods_removed"] == ["a"]


def test_method_signature_change_detected():
    old = "class x; task run(int a); endtask endclass"
    new = "class x; task run(int a, bit b = 0); endtask endclass"
    d = summarize(old, new)
    assert d["methods_sig_changed"] == ["run"]


def test_method_body_only_change_is_invisible_to_signature_tracking():
    # This is the documented, deliberate gap: same name, same args, different
    # body. It must NOT show up as "added"/"removed"/"sig_changed" — a body-diff
    # is future work, not something this module claims to do.
    old = "class x; task run(int a); foo(); endtask endclass"
    new = "class x; task run(int a); bar(); baz(); endtask endclass"
    d = summarize(old, new)
    assert d["methods_added"] == [] and d["methods_removed"] == [] and d["methods_sig_changed"] == []


# ── constraints ───────────────────────────────────────────────────────────────

def test_constraint_added_removed_changed():
    old = "class x; constraint a_c { a < 10; } constraint b_c { b > 0; } endclass"
    new = "class x; constraint a_c { a < 20; } constraint c_c { c == 1; } endclass"
    d = summarize(old, new)
    assert d["constraints_added"] == ["c_c"]
    assert d["constraints_removed"] == ["b_c"]
    assert d["constraints_changed"] == ["a_c"]


def test_constraint_whitespace_only_change_is_not_flagged():
    old = "class x; constraint a_c { a < 10; } endclass"
    new = "class x; constraint a_c {\n  a < 10;\n} endclass"
    d = summarize(old, new)
    assert d["constraints_changed"] == []


# ── macros ────────────────────────────────────────────────────────────────────

def test_macro_added():
    old = "class x; endclass"
    new = "class x; `uvm_object_utils(x) endclass"
    d = summarize(old, new)
    assert d["macros_added"] == ["uvm_object_utils"]


# ── curated API-call deltas ───────────────────────────────────────────────────

@pytest.mark.parametrize("old_call,new_call,group", [
    ("", "ral.foo.predict(1);", "ral_csr"),
    ("", "csr_rd(.ptr(ral.x), .value(v));", "ral_csr"),
    ("", "uvm_config_db#(int)::set(this, \"\", \"k\", 1);", "config_db"),
    ("", "mon.ap.connect(sb.analysis_export);", "analysis_port"),
    ("", "foo = foo_type::type_id::create(\"foo\", this);", "factory"),
    ("", "item.randomize();", "randomize"),
])
def test_each_api_group_is_detected(old_call, new_call, group):
    old = f"class x; task t(); {old_call} endtask endclass"
    new = f"class x; task t(); {new_call} endtask endclass"
    d = summarize(old, new)
    assert d["calls_delta"].get(group, 0) == 1, d["calls_delta"]


def test_call_delta_is_net_not_absolute():
    # Same call count before and after (even if the surrounding guard changed)
    # must show NO delta — this is the real aon_timer/kmac case where a call
    # already existed and only its guarding `if` changed.
    old = "class x; task t(); if (a) ral.x.predict(1); endtask endclass"
    new = "class x; task t(); if (a && b) ral.x.predict(1); endtask endclass"
    d = summarize(old, new)
    assert d["calls_delta"] == {}


def test_api_groups_do_not_overlap():
    seen = set()
    for g, p in cs.API_GROUPS.items():
        assert g not in seen
        seen.add(g)


# ── class added / removed (no diff to compute) ──────────────────────────────

def test_class_added():
    d = summarize(None, "class x; int a; endclass")
    assert d["class_added"] and not d["class_removed"]
    assert "NEW class" in cs.render_change_summary(d)


def test_class_removed():
    d = summarize("class x; int a; endclass", None)
    assert d["class_removed"] and not d["class_added"]
    assert "REMOVED" in cs.render_change_summary(d)


# ── render: deterministic, truncates long lists ─────────────────────────────

def test_render_is_deterministic_for_the_llm_cache():
    d = summarize("class x; endclass",
                   "class x; bit a; bit b; task t1(); endtask task t2(); endtask endclass")
    r1, r2 = cs.render_change_summary(d), cs.render_change_summary(d)
    assert r1 == r2


def test_render_truncates_long_lists():
    names = [f"m{i}" for i in range(10)]
    old = "class x;\nendclass"
    new = "class x;\n" + "\n".join(f"  bit {n};" for n in names) + "\nendclass"
    d = summarize(old, new)
    text = cs.render_change_summary(d, max_names=3)
    assert "+7 more" in text


def test_no_signal_message_when_nothing_changed():
    old = "class x;\n  task t();\n    foo();\n  endtask\nendclass"
    new = "class x;\n  task t();\n    bar();\n  endtask\nendclass"
    d = summarize(old, new)
    assert not cs.has_any_signal(d)
    assert "not visible to this summary" in cs.render_change_summary(d)


# ── performance regression: must stay fast on a large blanked body ─────────

def test_large_method_body_does_not_hang():
    # Real bug: `^\s*` over a whole-body MULTILINE scan went quadratic once a
    # method body was blanked to ~1500 lines of whitespace (kmac_scoreboard.sv,
    # ~10s to hang). This body is built to reproduce that shape synthetically
    # so the regression is caught without needing the real OpenTitan checkout.
    big_body = "\n".join(f"    x_{i} = y_{i} + z_{i};" for i in range(3000))
    src = f"class x;\n  task big();\n{big_body}\n  endtask\n  bit after_flag;\nendclass"
    t0 = time.time()
    body = cs.extract_class_body(src, "x")
    d = cs.summarize_class_change(body, body, "x")
    assert time.time() - t0 < 2.0
    assert "after_flag" in cs.extract_structure(body)["members"]
