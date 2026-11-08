"""Stand-in test runner for this sandbox (no pip/pytest). Discovers tests/test_*.py
in the given repo, imports each with the stub `pytest` shim on sys.path, runs every
test_* function (handling pytest.mark.parametrize via the shim's ._params), and
prints a pass/fail summary with full tracebacks for failures."""
import sys, os, glob, importlib.util, traceback

REPO = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/veridelta"
STUB = os.path.dirname(os.path.abspath(__file__))  # this directory, wherever the repo is checked out
sys.path.insert(0, STUB)
sys.path.insert(0, os.path.join(REPO, "src"))

only = sys.argv[2] if len(sys.argv) > 2 else None
total_ok = total_bad = 0
for path in sorted(glob.glob(os.path.join(REPO, "tests", "test_*.py"))):
    modname = os.path.basename(path)[:-3]
    if only and only not in modname:
        continue
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
    except Exception:
        print(f"IMPORT FAILED: {modname}")
        traceback.print_exc()
        total_bad += 1
        continue
    ok = bad = 0
    for name, fn in vars(m).items():
        if not name.startswith("test_") or not callable(fn):
            continue
        cases = [()] if not hasattr(fn, "_params") else \
            [tuple(v) if isinstance(v, (tuple, list)) else (v,) for v in fn._params[1]]
        for c in cases:
            try:
                fn(*c)
                ok += 1
            except Exception:
                bad += 1
                print(f"FAIL {modname}.{name}{c}")
                traceback.print_exc()
    print(f"{modname}: {ok} passed, {bad} failed")
    total_ok += ok
    total_bad += bad
print(f"\nTOTAL: {total_ok} passed, {total_bad} failed")
sys.exit(1 if total_bad else 0)
