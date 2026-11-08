"""
VeriDelta — Change summary
change_summary.py

Stage 3 currently tells the model WHICH class changed and WHAT is downstream of it,
but never WHAT changed inside the class. This module fills that gap with a compact,
structural summary built from the class body text before and after the commit — no
diff parsing, no AST: strip comments, cut out the class, regex over the two texts,
set-compare.

WHAT IT SEES vs. WHAT IT MISSES (state this in the paper)
───────────────────────────────────────────────────────────────────────────────
Seen directly:
  - members added / removed / retyped (field declarations, incl. rand/randc)
  - methods (task/function) added / removed / signature changed
  - constraint blocks added / removed / body changed
  - `uvm_*_utils` / `uvm_field_*` macro invocations added / removed
  - net change in calls to a curated set of semantically loaded APIs: RAL/CSR
    (predict/mirror/csr_rd/csr_wr/csr_update), uvm_config_db set/get, analysis
    port connect/write, factory create/override, randomize
Not seen (the honest gap, confirmed against 7 real OpenTitan commits on 2026-11-08 —
see docs/change_summary_validation.md): a method BODY edited without changing its
name or argument list — an added `if` guard, a reordered statement, a changed
constant — is invisible here unless it happens to touch one of the curated API
calls above. In the 7-commit sample this was the majority case: 5 of 7 commits had
zero member/signature/constraint changes and were visible ONLY through the curated
call counts or "methods touched". A future version that diffs method BODIES
line-by-line (not just signatures) would close this; left as future work.

USAGE
  old_body = extract_class_body(old_file_text, class_name)
  new_body = extract_class_body(new_file_text, class_name)
  summary  = summarize_class_change(old_body, new_body, class_name)
  text     = render_change_summary(summary)   # compact block for the Stage 3 prompt
"""
from __future__ import annotations

import re
from collections import Counter

from .type_annotator import strip_comments, _CLASS_HEAD, _skip_params, _skip_ws, _IDENT

# ── Locate a class body in a file ────────────────────────────────────────────

def extract_class_body(file_text: str | None, class_name: str) -> str | None:
    """
    Return the text from `class <class_name>` (after any #(...) parameter
    list) up to its matching `endclass`, or None if the class is not found or
    the file text is not available (file added/removed/unreadable — the
    caller reports this as "whole class added/removed", not as "no change").

    Matching `endclass`: UVM style classes are not nested, so this walks
    `class` / `endclass` tokens linearly after the target and returns at the
    first `endclass` that brings the nesting count back to zero. A stray
    `class` keyword inside a string or comment cannot confuse this because
    comments/strings are stripped first.
    """
    if file_text is None:
        return None
    t = strip_comments(file_text)
    for m in _CLASS_HEAD.finditer(t):
        if m.group(1) != class_name:
            continue
        i = _skip_params(t, m.end())
        if i < 0:
            continue
        start = m.start()
        depth = 1
        pos = i
        for tok in re.finditer(r"\bclass\b|\bendclass\b", t[i:]):
            if tok.group(0) == "class":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    pos = i + tok.end()
                    return t[start:pos]
        return t[start:]          # unterminated (truncated file) — best effort
    return None


# ── Structural extraction from one class body ────────────────────────────────

# A field declaration: optional rand/randc, a type (identifier, possibly scoped
# or parameterized, possibly an array/queue), then one identifier, ending at
# `;` or `=` (skip initializers). Deliberately conservative: it is better to
# miss an unusual declaration than to mis-flag a local variable inside a task
# body as a class member, so this only runs on lines at class/task top level
# is NOT enforced (regex can't do that cheaply) — instead we exclude the
# common false-positive shapes (loop variables, `local::`, cast expressions)
# via the STOP_WORDS and by requiring the line to end in `;` with no `(`
# before it on the same statement (that would be a call or a method decl).
_MEMBER_STOP = {
    "if", "else", "for", "foreach", "while", "do", "case", "begin", "end",
    "return", "break", "continue", "fork", "join", "join_any", "join_none",
    "task", "function", "endtask", "endfunction", "class", "endclass",
    "constraint", "covergroup", "endgroup", "typedef", "extends", "super",
    "this", "new", "void", "wait", "disable", "assert", "property",
}
# Matched per PHYSICAL LINE (see _members), never with re.MULTILINE over the
# whole body: an earlier version anchored on `^\s*` across the full text and a
# method body blanked to whitespace gave the engine a long run of blank lines
# to backtrack through character by character (every line start retried against
# every blank-span length) — quadratic, and it hung for ~10s on a 1700-line
# real file (kmac_scoreboard.sv) in validation. Matching single lines removes
# the cross-line whitespace run entirely, so there is nothing left to backtrack
# over.
_MEMBER_DECL = re.compile(
    r"\s*(?P<rand>rand\s+|randc\s+)?"
    r"(?P<type>(?:local\s+|protected\s+|static\s+|const\s+)*"
    r"[A-Za-z_]\w*(?:\s*#\s*\([^;]*?\))?(?:\s*::\s*[A-Za-z_]\w*)*"
    r"(?:\s*\[[^\]]*\])*)"                # packed dimension, e.g. `bit [7:0]`
    r"\s+(?P<name>[A-Za-z_]\w*)"
    r"(?P<dims>\s*(?:\[[^\]]*\])*)"       # unpacked dimension after the name, e.g. `a[4]`
    r"(?:\s*=\s*[^;]*)?\s*;\s*$"          # optional initializer, e.g. `= 1`
)


# task...endtask / function...endfunction are not nested in UVM-style code, so a
# non-greedy span from the keyword to its own matching end-keyword is enough to
# blank out method BODIES before member extraction runs — a local variable such
# as `string default_ral_name;` declared inside build_phase() must never be read
# as a class member. (Found as a real false positive on OpenTitan commit
# 2eb7256b — see docs/change_summary_validation.md.) Method signatures and the
# curated API-call counts are extracted from the UN-blanked body elsewhere, since
# those calls legitimately live inside method bodies.
_METHOD_BODY_BLOCK = re.compile(r"\b(task|function)\b.*?\bend\1\b", re.S)


def _blank_method_bodies(body: str) -> str:
    """Replace each matched task/function span with same-length whitespace
    (newlines kept as newlines) so character OFFSETS into the blanked text
    still line up with the original text — needed because `_constraints`
    slices the original body using positions found in the blanked one."""
    return _METHOD_BODY_BLOCK.sub(lambda m: "".join(c if c == "\n" else " " for c in m.group(0)), body)


def _members(body: str) -> dict:
    """Line-by-line on purpose — see the comment on _MEMBER_DECL. A
    declaration split across physical lines (rare in the UVM style this was
    built against) is missed; documented as a known gap, not silently wrong."""
    out = {}
    for line in _blank_method_bodies(body).splitlines():
        m = _MEMBER_DECL.match(line)
        if not m:
            continue
        type_, name = m.group("type").strip(), m.group("name")
        first_word = type_.split()[-1] if type_.split() else type_
        if first_word in _MEMBER_STOP or name in _MEMBER_STOP:
            continue
        if "(" in type_ and ")" not in type_:   # unbalanced -> not a simple decl
            continue
        dims = re.sub(r"\s+", "", m.group("dims"))   # part of identity: scalar->array is a real retype
        out[name] = ("rand " if m.group("rand") else "") + re.sub(r"\s+", " ", type_) + dims
    return out


_METHOD_DECL = re.compile(
    r"\b(?:virtual\s+|protected\s+|local\s+|static\s+|pure\s+virtual\s+)*"
    r"(?P<kind>task|function)\b"
    r"(?:\s*automatic)?"
    r"(?:\s+[A-Za-z_]\w*(?:\s*#\s*\([^)]*\))?)?"   # return type for a function
    r"\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<args>[^)]*)\)",
)


def _methods(body: str) -> dict:
    out = {}
    for m in _METHOD_DECL.finditer(body):
        args = re.sub(r"\s+", " ", m.group("args")).strip()
        out[m.group("name")] = (m.group("kind"), args)
    return out


def method_line_spans(file_text: str | None, class_name: str) -> dict[str, tuple[int, int]]:
    """
    name -> (start_line, end_line), 1-indexed and inclusive, of every
    task/function in `class_name` within `file_text`. Comments are stripped
    only to locate the class/method boundaries; line numbers are into
    `file_text` AS GIVEN, because `strip_comments()` preserves every newline
    (blanks a `/* */` block to the same number of lines, drops `//` to
    nothing on its own line).

    THIS IS WHAT LETS A REAL GIT DIFF BE CORRELATED TO A METHOD (see
    git_diff.method_body_diff): a unified diff's hunk headers are line
    numbers, not class-relative offsets, so closing the module's own
    documented gap — "a method body edited without changing its name or
    argument list ... is invisible here" — needs a method's ABSOLUTE line
    range in the file, not just its name and signature.
    """
    if file_text is None:
        return {}
    t = strip_comments(file_text)
    body = extract_class_body(file_text, class_name)
    if body is None:
        return {}
    start_offset = None
    for m in _CLASS_HEAD.finditer(t):
        if m.group(1) != class_name:
            continue
        i = _skip_params(t, m.end())
        if i < 0:
            continue
        start_offset = m.start()
        break
    if start_offset is None:
        return {}
    spans: dict[str, tuple[int, int]] = {}
    for m in _METHOD_BODY_BLOCK.finditer(body):
        decl = _METHOD_DECL.search(body, m.start(), m.end())
        if not decl:
            continue
        abs_start = start_offset + m.start()
        abs_end = start_offset + m.end()
        start_line = t.count("\n", 0, abs_start) + 1
        end_line = t.count("\n", 0, abs_end) + 1
        spans[decl.group("name")] = (start_line, end_line)
    return spans


_CONSTRAINT_DECL = re.compile(r"\bconstraint\s+([A-Za-z_]\w*)\s*\{", re.MULTILINE)


def _constraints(body: str) -> dict:
    """name -> normalized body text (brace-balanced), for equality comparison."""
    out = {}
    for m in _CONSTRAINT_DECL.finditer(_blank_method_bodies(body)):
        name = m.group(1)
        i, depth = m.end(), 1
        for j in range(i, len(body)):
            if body[j] == "{":
                depth += 1
            elif body[j] == "}":
                depth -= 1
                if depth == 0:
                    out[name] = re.sub(r"\s+", " ", body[i:j]).strip()
                    break
    return out


_MACRO = re.compile(r"`(uvm_\w*_utils\w*|uvm_field_\w+|uvm_component_utils\w*|uvm_object_utils\w*)\b")


def _macros(body: str) -> Counter:
    return Counter(m.group(1) for m in _MACRO.finditer(body))


# Curated API groups: each maps to one category of "something that matters even
# when it is just a call added inside an existing method body". Patterns are
# matched after stripping comments; order doesn't matter, groups don't overlap
# by design (checked in tests).
API_GROUPS: dict[str, re.Pattern] = {
    "ral_csr":     re.compile(r"\.(?:predict|set|get|mirror|get_mirrored_value)\s*\(|"
                               r"\bcsr_(?:rd|wr|update|spinwait)\s*\("),
    "config_db":   re.compile(r"\buvm_config_db\s*#"),
    "analysis_port": re.compile(r"\.(?:connect|write)\s*\("),
    "factory":     re.compile(r"\b(?:type_id::create|create_type_override|"
                               r"set_type_override_by_type|set_inst_override_by_type|"
                               r"set_type_override|set_inst_override)\s*\("),
    "randomize":   re.compile(r"\brandomize\s*\("),
}


def _api_calls(body: str) -> Counter:
    return Counter({g: len(p.findall(body)) for g, p in API_GROUPS.items()})


def extract_structure(body: str | None) -> dict:
    """All structural facts pulled from one class body (or an empty shell if
    the class does not exist on this side — "the whole class was added/removed")."""
    if body is None:
        return {"members": {}, "methods": {}, "constraints": {}, "macros": Counter(), "calls": Counter()}
    return {
        "members": _members(body),
        "methods": _methods(body),
        "constraints": _constraints(body),
        "macros": _macros(body),
        "calls": _api_calls(body),
    }


# ── Diff two structures ───────────────────────────────────────────────────────

def diff_structure(old: dict, new: dict) -> dict:
    """Set/dict-compare two extract_structure() results. Pure function, no I/O —
    this is what the unit tests exercise directly."""
    o_mem, n_mem = old["members"], new["members"]
    members_added = sorted(set(n_mem) - set(o_mem))
    members_removed = sorted(set(o_mem) - set(n_mem))
    members_retyped = sorted(k for k in set(o_mem) & set(n_mem) if o_mem[k] != n_mem[k])

    o_meth, n_meth = old["methods"], new["methods"]
    methods_added = sorted(set(n_meth) - set(o_meth))
    methods_removed = sorted(set(o_meth) - set(n_meth))
    common_meth = set(o_meth) & set(n_meth)
    methods_sig_changed = sorted(k for k in common_meth if o_meth[k] != n_meth[k])

    o_con, n_con = old["constraints"], new["constraints"]
    constraints_added = sorted(set(n_con) - set(o_con))
    constraints_removed = sorted(set(o_con) - set(n_con))
    constraints_changed = sorted(k for k in set(o_con) & set(n_con) if o_con[k] != n_con[k])

    macros_added = sorted((new["macros"] - old["macros"]).elements())
    macros_removed = sorted((old["macros"] - new["macros"]).elements())

    calls_delta = {g: new["calls"].get(g, 0) - old["calls"].get(g, 0)
                   for g in API_GROUPS if new["calls"].get(g, 0) != old["calls"].get(g, 0)}

    return {
        "members_added": members_added, "members_removed": members_removed,
        "members_retyped": members_retyped,
        "methods_added": methods_added, "methods_removed": methods_removed,
        "methods_sig_changed": methods_sig_changed,
        "constraints_added": constraints_added, "constraints_removed": constraints_removed,
        "constraints_changed": constraints_changed,
        "macros_added": macros_added, "macros_removed": macros_removed,
        "calls_delta": calls_delta,
    }


_SIGNAL_LIST_FIELDS = (
    "members_added", "members_removed", "members_retyped",
    "methods_added", "methods_removed", "methods_sig_changed",
    "methods_body_changed",
    "constraints_added", "constraints_removed", "constraints_changed",
    "macros_added", "macros_removed",
)


def has_any_signal(d: dict) -> bool:
    """True if diff_structure()/summarize_class_change() found anything to
    report. Checks an explicit field whitelist, NOT every key in `d` — `d`
    also carries metadata (class_name, class_added, class_removed), and an
    earlier version that did `any(d[k] for k in d if k != "calls_delta")`
    was always True because class_name is a non-empty string."""
    return any(d[k] for k in _SIGNAL_LIST_FIELDS) or any(d["calls_delta"].values())


def summarize_class_change(old_body: str | None, new_body: str | None, class_name: str) -> dict:
    """
    old_body is None  -> class did not exist before (class added by this commit)
    new_body is None  -> class does not exist after (class removed by this commit)
    both None         -> caller error (nothing to summarize)

    `methods_body_changed` is always present but always `[]` here: this
    function has no git access (that is the whole point of keeping it pure
    and dependency-free — see the module docstring), and detecting a
    method-BODY change needs a real diff against the file's line numbers,
    not just the two bodies' text. `git_diff.class_change_summary()` is the
    one caller that can fill it in for real, from `method_body_diff()`; the
    key is initialized here so the dict shape — and `render_change_summary`/
    `has_any_signal`, which both read it — is identical whether or not a
    git-aware caller supplied it.
    """
    old, new = extract_structure(old_body), extract_structure(new_body)
    d = diff_structure(old, new)
    d["methods_body_changed"] = []
    d["class_added"] = old_body is None and new_body is not None
    d["class_removed"] = new_body is None and old_body is not None
    d["class_name"] = class_name
    return d


# ── Render for the Stage 3 prompt ────────────────────────────────────────────

_CALL_LABEL = {"ral_csr": "RAL/CSR calls", "config_db": "uvm_config_db calls",
               "analysis_port": "analysis-port connect/write calls",
               "factory": "factory create/override calls", "randomize": "randomize() calls"}


def render_change_summary(d: dict, max_names: int = 6) -> str:
    """One compact block, the format the Stage 3 prompt injects verbatim.
    Deterministic order (declaration order above) so the same diff always
    renders to the same text — needed for the on-disk LLM response cache."""
    if d["class_added"]:
        return f"  {d['class_name']} is a NEW class added by this commit."
    if d["class_removed"]:
        return f"  {d['class_name']} was REMOVED by this commit."

    def names(lst):
        if not lst:
            return ""
        shown = ", ".join(lst[:max_names])
        more = f" (+{len(lst) - max_names} more)" if len(lst) > max_names else ""
        return shown + more

    lines = []
    for label, key in (("members added", "members_added"), ("members removed", "members_removed"),
                        ("members retyped", "members_retyped"),
                        ("methods added", "methods_added"), ("methods removed", "methods_removed"),
                        ("method signatures changed", "methods_sig_changed"),
                        ("method bodies touched (diff, signature unchanged)", "methods_body_changed"),
                        ("constraints added", "constraints_added"),
                        ("constraints removed", "constraints_removed"),
                        ("constraint bodies changed", "constraints_changed"),
                        ("UVM automation macros added", "macros_added"),
                        ("UVM automation macros removed", "macros_removed")):
        if d[key]:
            lines.append(f"  {label}: {names(d[key])}")
    for g, delta in d["calls_delta"].items():
        if delta:
            sign = "+" if delta > 0 else ""
            lines.append(f"  {_CALL_LABEL[g]}: {sign}{delta} (net)")
    if not lines:
        return (f"  No added/removed members, methods, constraints or curated API calls detected "
                 f"in {d['class_name']}, and no diff-detected method body changes either "
                 f"(or this summary was built without git history, so method bodies were not checked). "
                 f"If there is still a real change, it is something this summary's regexes do not model "
                 f"(e.g. a change outside any method/member/constraint, such as a `typedef` or a class-level "
                 f"`parameter`).")
    return "\n".join(lines)
