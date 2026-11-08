"""
Manual, fixture-free re-check of cli.py after wiring bind_index end-to-end.
Exercises the same scenarios as tests/test_cli.py (which needs real pytest
fixtures this sandbox doesn't have) plus the new --rtl-module / bind_index
paths, using only tempfile + contextlib.redirect_stdout.
"""
import contextlib
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, "/home/claude/veridelta/src")

from veridelta.cli import main  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {name}")


def run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "dv")
    os.makedirs(src)
    os.makedirs(os.path.join(src, "sva"))
    with open(os.path.join(src, "dv_base_env_cfg.sv"), "w") as f:
        f.write("class dv_base_env_cfg extends uvm_object;\nendclass\n")
    with open(os.path.join(src, "uart_env_cfg.sv"), "w") as f:
        f.write("class uart_env_cfg #(type T = int) extends dv_base_env_cfg #(T);\nendclass\n")
    with open(os.path.join(src, "uart_agent_cfg.sv"), "w") as f:
        f.write("class uart_agent_cfg extends dv_base_env_cfg;\nendclass\n")
    # a bind file that would NOT be picked up by collect_files(scan_all=False)
    # because its name contains no UVM keyword -- this is the exact gap being
    # tested.
    with open(os.path.join(src, "aes_ctrl_bind.sv"), "w") as f:
        f.write("bind aes_ctrl aes_ctrl_assert_fpv_bind aes_ctrl_assert_fpv_bind_i (.*);\n")
    with open(os.path.join(src, "sva", "aes_ctrl_cov_bind.sv"), "w") as f:
        f.write("bind aes_ctrl aes_ctrl_cov_bind aes_ctrl_cov_bind_i (.*);\n")

    out = os.path.join(tmp, "graph")

    # 1. build-graph: still works, and now also writes bind_index.json even
    #    though neither bind file matches the UVM-keyword filename filter.
    code, text = run(["build-graph", src, "--out", out])
    check("build-graph exit 0", code == 0)
    check("build-graph reports bind index line", "bind index:" in text)
    check("bind_index.json written", os.path.isfile(os.path.join(out, "bind_index.json")))
    bind_index = json.load(open(os.path.join(out, "bind_index.json")))
    check("bind_index has aes_ctrl with 2 refs",
          sorted(bind_index.get("aes_ctrl", [])) == ["aes_ctrl_bind", "aes_ctrl_cov_bind"])

    # 2. old behavior preserved: adjacency/node_types unaffected by bind files
    adj = json.load(open(os.path.join(out, "adjacency.json")))
    check("adjacency unaffected", adj["dv_base_env_cfg"] == ["uart_agent_cfg", "uart_env_cfg"])

    # 3. blast-radius still works with no --rtl-module (back-compat default None)
    code, text = run(["blast-radius", "dv_base_env_cfg", "--graph", out])
    check("blast-radius exit 0", code == 0)
    check("blast-radius unaffected text", "2 downstream classes" in text)
    check("no bind line when --rtl-module omitted", "bind files at risk" not in text)

    # 4. blast-radius WITH --rtl-module matching aes_ctrl -> both bind files listed,
    #    cov one tagged Coverage Regression, the other Assertion Orphaning
    code, text = run(["blast-radius", "dv_base_env_cfg", "--graph", out, "--rtl-module", "aes_ctrl"])
    check("blast-radius with rtl-module exit 0", code == 0)
    check("bind files at risk line present", "bind files at risk for RTL module 'aes_ctrl' (2)" in text)
    check("coverage-regression tag shown", "aes_ctrl_cov_bind [Coverage Regression]" in text)
    check("assertion-orphaning tag shown", "aes_ctrl_bind [Assertion Orphaning]" in text)

    # 5. blast-radius with an rtl-module that has NO bind files -> explicit "no bind files" line
    code, text = run(["blast-radius", "dv_base_env_cfg", "--graph", out, "--rtl-module", "nope_ctrl"])
    check("no-bind-files line shown", "no bind files reference RTL module 'nope_ctrl'" in text)

    # 6. blast-radius --json carries bind_at_risk
    code, text = run(["blast-radius", "dv_base_env_cfg", "--graph", out, "--rtl-module", "aes_ctrl", "--json"])
    obj = json.loads(text)
    check("json bind_at_risk present", sorted(obj["bind_at_risk"]) ==
          ["aes_ctrl_bind", "aes_ctrl_cov_bind"])

    # 7. analyze --mock still works (bind_index now real {}-or-populated, not hardcoded {})
    code, text = run(["analyze", "dv_base_env_cfg", "--graph", out, "--mock", "--json"])
    check("analyze mock exit 0", code == 0)
    obj = json.loads(text)
    check("analyze predictions unaffected", set(obj["predictions"]) == {"uart_agent_cfg", "uart_env_cfg"})
    check("analyze bind_at_risk empty with no --rtl-module", obj["bind_at_risk"] == [])

    # 8. analyze --mock WITH --rtl-module surfaces bind_at_risk + the un-scored note
    code, text = run(["analyze", "dv_base_env_cfg", "--graph", out, "--mock", "--rtl-module", "aes_ctrl"])
    check("analyze text mentions bind at risk", "bind files at risk for RTL module 'aes_ctrl' (2)" in text)
    check("analyze text has the not-yet-scored note", "not yet scored" in text)

    # 9. loading an OLDER graph dir with no bind_index.json at all must not error
    old_out = os.path.join(tmp, "graph_old")
    os.makedirs(old_out)
    with open(os.path.join(old_out, "node_types.json"), "w") as f:
        json.dump({"dv_base_env_cfg": {"type": "CFG"}, "uart_env_cfg": {"type": "CFG"}}, f)
    with open(os.path.join(old_out, "adjacency.json"), "w") as f:
        json.dump({"dv_base_env_cfg": ["uart_env_cfg"], "uart_env_cfg": []}, f)
    code, text = run(["blast-radius", "dv_base_env_cfg", "--graph", old_out, "--rtl-module", "aes_ctrl"])
    check("old graph dir (no bind_index.json) doesn't crash", code == 0)
    check("old graph dir reports no bind files (empty bind_index)",
          "no bind files reference RTL module 'aes_ctrl'" in text)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
