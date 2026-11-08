"""
Fixture-free manual verification of git_diff.py's new method-body-diff feature
(method_body_diff, parse_unified_diff_ranges, and class_change_summary's new
methods_body_changed wiring), run against a REAL, synthetic git repo built
with real `git` subprocess calls -- not the stub pytest fixtures, since this
sandbox has no real pytest/pip install (see tests/test_git_diff.py's own
docstring, and docs/change_summary_validation.md, for why this script exists
alongside the real pytest-style tests rather than instead of them: this is
the verification record for a sandbox that cannot run `pytest` for real).

Exits 0 and prints "ALL CHECKS PASSED" iff every assertion below holds.
"""
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, "/home/claude/veridelta/src")
from veridelta import git_diff as gd  # noqa: E402

checks_run = 0
checks_failed = 0


def check(desc, cond):
    global checks_run, checks_failed
    checks_run += 1
    status = "OK  " if cond else "FAIL"
    print(f"[{status}] {desc}")
    if not cond:
        checks_failed += 1


def git(repo_dir, *args):
    r = subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout


def commit(repo_dir, path, text, msg):
    with open(f"{repo_dir}/{path}", "w") as f:
        f.write(text)
    git(repo_dir, "add", path)
    git(repo_dir, "commit", "-q", "-m", msg)
    return git(repo_dir, "rev-parse", "HEAD").strip()


def fresh_repo(tmpdir):
    git(tmpdir, "init", "-q")
    git(tmpdir, "config", "user.email", "t@example.com")
    git(tmpdir, "config", "user.name", "t")
    return tmpdir


# ── 1. parse_unified_diff_ranges ────────────────────────────────────────────

diff1 = "@@ -3 +3 @@\n-old\n+new\n"
old_lines, new_lines = gd.parse_unified_diff_ranges(diff1)
check("single-line hunk -> old={3}", old_lines == {3})
check("single-line hunk -> new={3}", new_lines == {3})

diff2 = "@@ -5,2 +5,2 @@\n-a\n-b\n+A\n+B\n@@ -10,0 +11,3 @@\n+x\n+y\n+z\n"
old_lines, new_lines = gd.parse_unified_diff_ranges(diff2)
check("multi-hunk old lines", old_lines == {5, 6})
check("multi-hunk new lines incl. pure insertion", new_lines == {5, 6, 11, 12, 13})

diff3 = "@@ -8,3 +7,0 @@\n-a\n-b\n-c\n"
old_lines, new_lines = gd.parse_unified_diff_ranges(diff3)
check("pure deletion -> old={8,9,10}", old_lines == {8, 9, 10})
check("pure deletion -> new=empty", new_lines == set())

check("no hunks -> empty/empty", gd.parse_unified_diff_ranges("") == (set(), set()))

# ── 2. method_body_diff against a real synthetic git repo ──────────────────

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

with tempfile.TemporaryDirectory() as tmp:
    repo = fresh_repo(tmp)
    sha1 = commit(repo, "drv.sv", CLASS_V1, "v1")
    sha2 = commit(repo, "drv.sv", CLASS_V2, "v2")
    touched = gd.method_body_diff(repo, sha1, sha2, "drv.sv", "drv")
    check("only run_phase's body changed -> touched == ['run_phase']", touched == ["run_phase"])

with tempfile.TemporaryDirectory() as tmp:
    repo = fresh_repo(tmp)
    sha1 = commit(repo, "drv.sv", CLASS_V1, "v1")
    sha2 = commit(repo, "other.sv", "// unrelated", "unrelated")
    touched = gd.method_body_diff(repo, sha1, sha2, "drv.sv", "drv")
    check("unrelated file changed -> drv.sv untouched -> []", touched == [])

with tempfile.TemporaryDirectory() as tmp:
    repo = fresh_repo(tmp)
    sha1 = commit(repo, "drv.sv", CLASS_V1, "v1")
    check("missing file -> []", gd.method_body_diff(repo, sha1, sha1, "nope.sv", "drv") == [])
    check("missing class -> []", gd.method_body_diff(repo, sha1, sha1, "drv.sv", "nope_class") == [])

# ── 3. class_change_summary wiring: this is the actual paper-facing gap ────

with tempfile.TemporaryDirectory() as tmp:
    repo = fresh_repo(tmp)
    sha1 = commit(repo, "drv.sv", CLASS_V1, "v1")
    sha2 = commit(repo, "drv.sv", CLASS_V2, "v2")
    d = gd.class_change_summary(repo, sha1, sha2, "drv", path_hint="drv.sv")
    check("class_change_summary() found the class", d is not None)
    check(
        "d['methods_body_changed'] == ['run_phase'] (THE closed gap)",
        d is not None and d["methods_body_changed"] == ["run_phase"],
    )
    check("no false positive on methods_added", d is not None and d["methods_added"] == [])
    check("no false positive on methods_sig_changed", d is not None and d["methods_sig_changed"] == [])
    if d is not None:
        text = gd.render_change_summary(d)
        check("rendered text names run_phase", "run_phase" in text)
        check("rendered text has the new label", "method bodies touched" in text)

# ── 4. renamed method: no double-report between added/removed and body_changed

with tempfile.TemporaryDirectory() as tmp:
    repo = fresh_repo(tmp)
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
    sha1 = commit(repo, "drv.sv", old, "v1")
    sha2 = commit(repo, "drv.sv", new, "v2")
    d = gd.class_change_summary(repo, sha1, sha2, "drv", path_hint="drv.sv")
    check("rename -> methods_added == ['run_phase_new']", d["methods_added"] == ["run_phase_new"])
    check("rename -> methods_removed == ['run_phase']", d["methods_removed"] == ["run_phase"])
    check(
        "rename -> methods_body_changed excludes both (already explained) == []",
        d["methods_body_changed"] == [],
    )

# ── 5. a class with NO method touched at all -> methods_body_changed == []

with tempfile.TemporaryDirectory() as tmp:
    repo = fresh_repo(tmp)
    sha1 = commit(repo, "drv.sv", CLASS_V1, "v1")
    # drv.sv itself is untouched between sha1 and sha2 -- only an unrelated
    # file changes -- so method_body_diff's underlying `git diff -- drv.sv`
    # is empty and no method should be flagged.
    sha2 = commit(repo, "other2.sv", "// also unrelated", "unrelated2")
    d = gd.class_change_summary(repo, sha1, sha2, "drv", path_hint="drv.sv")
    check("unrelated-file-only commit -> methods_body_changed == []", d["methods_body_changed"] == [])

# ── 6. not a hang on a class with many methods ──────────────────────────────

with tempfile.TemporaryDirectory() as tmp:
    repo = fresh_repo(tmp)
    methods_v1 = "\n".join(f"  task m{i}(); foo(); endtask" for i in range(60))
    methods_v2 = "\n".join(
        f"  task m{i}(); foo(); bar(); endtask" if i == 30 else f"  task m{i}(); foo(); endtask"
        for i in range(60)
    )
    v1 = f"class big extends uvm_component;\n{methods_v1}\nendclass\n"
    v2 = f"class big extends uvm_component;\n{methods_v2}\nendclass\n"
    sha1 = commit(repo, "big.sv", v1, "v1")
    sha2 = commit(repo, "big.sv", v2, "v2")
    t0 = time.time()
    touched = gd.method_body_diff(repo, sha1, sha2, "big.sv", "big")
    elapsed = time.time() - t0
    check(f"60-method class diff is fast ({elapsed:.3f}s < 2.0s)", elapsed < 2.0)
    check("60-method class -> only m30 flagged", touched == ["m30"])

print()
if checks_failed:
    print(f"{checks_failed}/{checks_run} CHECKS FAILED")
    sys.exit(1)
print(f"ALL CHECKS PASSED ({checks_run}/{checks_run})")
