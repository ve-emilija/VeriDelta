"""
Tests for git_diff.py against a REAL, synthetic git repo (created fresh per
test in a tmp dir) rather than the OpenTitan/Caliptra-rtl checkouts used for
qualitative validation elsewhere -- a synthetic repo is fully deterministic
and needs no network access, so these tests run anywhere git itself runs.
"""
import subprocess
import time

from veridelta import git_diff as gd

# The `repo` fixture (a fresh, real, empty git repo per test) lives in
# tests/conftest.py, shared with any other test module that wants one.


def _git(repo_dir, *args):
    r = subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout


def _commit(repo_dir, path, text, msg):
    with open(f"{repo_dir}/{path}", "w") as f:
        f.write(text)
    _git(repo_dir, "add", path)
    _git(repo_dir, "commit", "-q", "-m", msg)
    return _git(repo_dir, "rev-parse", "HEAD").strip()


# ── parse_unified_diff_ranges ────────────────────────────────────────────────

def test_parse_unified_diff_ranges_single_line_change():
    diff = "@@ -3 +3 @@\n-old\n+new\n"
    old_lines, new_lines = gd.parse_unified_diff_ranges(diff)
    assert old_lines == {3}
    assert new_lines == {3}


def test_parse_unified_diff_ranges_multi_line_and_pure_insertion():
    diff = (
        "@@ -5,2 +5,2 @@\n-a\n-b\n+A\n+B\n"
        "@@ -10,0 +11,3 @@\n+x\n+y\n+z\n"
    )
    old_lines, new_lines = gd.parse_unified_diff_ranges(diff)
    assert old_lines == {5, 6}
    assert new_lines == {5, 6, 11, 12, 13}


def test_parse_unified_diff_ranges_pure_deletion():
    diff = "@@ -8,3 +7,0 @@\n-a\n-b\n-c\n"
    old_lines, new_lines = gd.parse_unified_diff_ranges(diff)
    assert old_lines == {8, 9, 10}
    assert new_lines == set()


def test_parse_unified_diff_ranges_no_hunks_is_empty():
    assert gd.parse_unified_diff_ranges("") == (set(), set())


# ── method_body_diff: the real feature, against a real git repo ────────────

CLASS_V1 = (
    "class drv extends uvm_driver;\n"
    "  task run_phase(uvm_phase phase);\n"
    "    foo();\n"
    "  endtask\n"
    "  function void build_phase(uvm_phase phase);\n"
    "    bar();\n"
    "  endfunction\n"
    "endclass\n"
)

# Only run_phase's body changes (foo() -> foo(); baz();); build_phase untouched.
CLASS_V2 = (
    "class drv extends uvm_driver;\n"
    "  task run_phase(uvm_phase phase);\n"
    "    foo();\n"
    "    baz();\n"
    "  endtask\n"
    "  function void build_phase(uvm_phase phase);\n"
    "    bar();\n"
    "  endfunction\n"
    "endclass\n"
)


def test_method_body_diff_finds_only_the_touched_method(repo):
    sha1 = _commit(repo, "drv.sv", CLASS_V1, "v1")
    sha2 = _commit(repo, "drv.sv", CLASS_V2, "v2")
    touched = gd.method_body_diff(repo, sha1, sha2, "drv.sv", "drv")
    assert touched == ["run_phase"]


def test_method_body_diff_empty_when_nothing_in_the_class_changed(repo):
    sha1 = _commit(repo, "drv.sv", CLASS_V1, "v1")
    sha2 = _commit(repo, "other.sv", "// unrelated file, no change to drv.sv", "unrelated")
    # drv.sv itself did not change between sha1 and sha2 (sha2's commit only
    # added a different file), so nothing should be reported as body-changed.
    touched = gd.method_body_diff(repo, sha1, sha2, "drv.sv", "drv")
    assert touched == []


def test_method_body_diff_missing_file_or_class_is_empty(repo):
    sha1 = _commit(repo, "drv.sv", CLASS_V1, "v1")
    touched = gd.method_body_diff(repo, sha1, sha1, "nope.sv", "drv")
    assert touched == []
    touched2 = gd.method_body_diff(repo, sha1, sha1, "drv.sv", "nope_class")
    assert touched2 == []


# This is the exact shape of the documented, previously-accepted gap: same
# method name, same argument list, only the body differs -- summarize_class_change()
# alone reports zero signal for it, but class_change_summary() (which calls
# method_body_diff() internally) must now catch it.
def test_class_change_summary_fills_in_methods_body_changed_from_real_diff(repo):
    sha1 = _commit(repo, "drv.sv", CLASS_V1, "v1")
    sha2 = _commit(repo, "drv.sv", CLASS_V2, "v2")
    d = gd.class_change_summary(repo, sha1, sha2, "drv", path_hint="drv.sv")
    assert d is not None
    assert d["methods_body_changed"] == ["run_phase"]
    assert d["methods_added"] == [] and d["methods_sig_changed"] == []
    # And the rendered text now surfaces it, where it previously would have
    # fallen through to the "no signal" message.
    text = gd.render_change_summary(d)
    assert "run_phase" in text
    assert "method bodies touched" in text


def test_method_body_diff_does_not_double_count_a_renamed_method(repo):
    # method_body_diff() itself is purely name-based per revision; verifying
    # that class_change_summary() does not list a method in BOTH
    # methods_added/removed AND methods_body_changed when it was actually
    # renamed (git diff still reports line-level churn on both the old and
    # new name's lines, since a rename is a delete+add from git's point of
    # view with --unified=0).
    old = (
        "class drv extends uvm_driver;\n"
        "  task run_phase(uvm_phase phase);\n"
        "    foo();\n"
        "  endtask\n"
        "endclass\n"
    )
    new = (
        "class drv extends uvm_driver;\n"
        "  task run_phase_new(uvm_phase phase);\n"
        "    foo();\n"
        "  endtask\n"
        "endclass\n"
    )
    sha1 = _commit(repo, "drv.sv", old, "v1")
    sha2 = _commit(repo, "drv.sv", new, "v2")
    d = gd.class_change_summary(repo, sha1, sha2, "drv", path_hint="drv.sv")
    assert d["methods_added"] == ["run_phase_new"]
    assert d["methods_removed"] == ["run_phase"]
    # Neither name should ALSO show up in methods_body_changed -- they're
    # already explained by the add/remove, so class_change_summary() filters
    # them out of body_touched before assigning.
    assert d["methods_body_changed"] == []


def test_method_body_diff_is_reasonably_fast_on_a_bigger_class(repo):
    # Not a real perf cliff like change_summary.py's documented regex-hang bug
    # -- just a sanity check that method_body_diff doesn't do anything
    # quadratic in the number of methods/hunks for a class of realistic size.
    methods_v1 = "\n".join(
        f"  task m{i}(); foo(); endtask" for i in range(60)
    )
    methods_v2 = "\n".join(
        f"  task m{i}(); foo(); bar(); endtask" if i == 30 else f"  task m{i}(); foo(); endtask"
        for i in range(60)
    )
    v1 = f"class big extends uvm_component;\n{methods_v1}\nendclass\n"
    v2 = f"class big extends uvm_component;\n{methods_v2}\nendclass\n"
    sha1 = _commit(repo, "big.sv", v1, "v1")
    sha2 = _commit(repo, "big.sv", v2, "v2")
    t0 = time.time()
    touched = gd.method_body_diff(repo, sha1, sha2, "big.sv", "big")
    assert time.time() - t0 < 2.0
    assert touched == ["m30"]
