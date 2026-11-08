"""
VeriDelta — Git-backed change summaries
git_diff.py

The glue between change_summary.py (pure, no I/O, fully unit-tested on plain
strings) and a real git checkout: finds which file defines a changed class at
a given revision, reads both revisions of that file, and renders the compact
structural change block for Stage 3's prompt.

Kept separate from change_summary.py on purpose — change_summary.py must stay
testable with zero subprocess calls; everything that shells out to `git` lives
here, where it can be mocked or skipped (e.g. inside the API sandbox, or on a
shallow clone that does not have a given commit).
"""
from __future__ import annotations

import re
import subprocess

from .change_summary import (
    extract_class_body,
    method_line_spans,
    render_change_summary,
    summarize_class_change,
)


def _run(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))?\s\+(\d+)(?:,(\d+))?\s@@", re.M)


def parse_unified_diff_ranges(diff_text: str) -> tuple[set[int], set[int]]:
    """
    (old_lines, new_lines): the 1-indexed line numbers covered by every hunk
    in a unified diff (as produced by `git diff --unified=0 <old> <new> -- <path>`).

    A hunk header looks like `@@ -oldStart,oldCount +newStart,newCount @@`; a
    missing count defaults to 1 (a single-line hunk), and a count of 0 means
    that side is a pure insertion (old side) or deletion (new side) with no
    lines of its own to list -- range(start, start + 0) is correctly empty
    for those, so no special-casing is needed.
    """
    old_lines: set[int] = set()
    new_lines: set[int] = set()
    for m in _HUNK_HEADER.finditer(diff_text):
        old_start, old_count = int(m.group(1)), int(m.group(2) or 1)
        new_start, new_count = int(m.group(3)), int(m.group(4) or 1)
        old_lines.update(range(old_start, old_start + old_count))
        new_lines.update(range(new_start, new_start + new_count))
    return old_lines, new_lines


def method_body_diff(
    repo_dir: str,
    before_rev: str,
    after_rev: str,
    path: str,
    class_name: str,
) -> list[str]:
    """
    Names of methods of `class_name` whose BODY changed between `before_rev`
    and `after_rev`, found from a real `git diff`, independently of
    summarize_class_change()'s signature-level view.

    This is the piece that closes change_summary.py's own documented gap:
    "a method body edited without changing its name or argument list ... is
    invisible here". A method's position in the file is resolved to absolute
    line numbers on BOTH revisions (method_line_spans, from change_summary.py)
    because a method can shift lines across a commit (an earlier method
    grew/shrank) without its own body changing -- checking only one revision's
    line numbers would wrongly blame or clear the wrong method. A method
    counts as body-changed if ANY line in its span, on EITHER revision, falls
    inside a hunk; this means a line that is actually just re-indented by an
    unrelated reflow earlier in the file could falsely flag a method that
    didn't move -- same conservative, false-positive-leaning tradeoff
    change_summary.py already makes elsewhere (see its docstring).

    Returns [] if the file/class cannot be read at either revision (same
    "degrade to no signal" policy as class_change_summary), or if `git diff`
    fails (e.g. one revision not fetched locally).
    """
    old_text = read_file_at(repo_dir, before_rev, path)
    new_text = read_file_at(repo_dir, after_rev, path)
    old_spans = method_line_spans(old_text, class_name)
    new_spans = method_line_spans(new_text, class_name)
    if not old_spans and not new_spans:
        return []
    code, diff_text = _run(
        ["diff", "--unified=0", before_rev, after_rev, "--", path], repo_dir
    )
    if code != 0:
        return []
    old_lines, new_lines = parse_unified_diff_ranges(diff_text)
    touched: set[str] = set()
    for name, (s, e) in old_spans.items():
        if any(l in old_lines for l in range(s, e + 1)):
            touched.add(name)
    for name, (s, e) in new_spans.items():
        if any(l in new_lines for l in range(s, e + 1)):
            touched.add(name)
    return sorted(touched)


def read_file_at(repo_dir: str, rev: str, path: str) -> str | None:
    """`git show <rev>:<path>`, or None if the revision/path does not exist
    (file added/removed, or the revision is not fetched locally)."""
    code, out = _run(["show", f"{rev}:{path}"], repo_dir)
    return out if code == 0 else None


def find_file_defining_class(repo_dir: str, rev: str, class_name: str) -> str | None:
    """
    `git grep` the tree at `rev` for a `class <class_name>` declaration and
    return the path of the (first) file that has one. This is how a changed
    class name is turned into a file path when the caller doesn't already
    know it (the stored evaluation data keeps class names, not file paths —
    see docs/change_summary_validation.md, "Not wired into the 400-commit
    evaluation").

    Not reliable when two files in the same tree declare a class of the same
    name (rare in this style of codebase, but possible); the first match
    wins. Returns None if git grep finds nothing or the revision isn't local.
    """
    code, out = _run(
        ["grep", "-l", "-E", rf"\bclass\s+(automatic\s+|static\s+)?{class_name}\b", rev,
         "--", "*.sv", "*.svh"],
        repo_dir,
    )
    if code != 0 or not out.strip():
        return None
    first = out.splitlines()[0]
    return first.split(":", 1)[1] if ":" in first else None


def class_change_summary(
    repo_dir: str,
    before_rev: str,
    after_rev: str,
    class_name: str,
    path_hint: str | None = None,
) -> dict | None:
    """
    Resolve the file defining `class_name` at `after_rev` (and the same path
    at `before_rev`, which is right for the common case of an edit; a rename
    falls back to a fresh git-grep at `before_rev` so a renamed class is still
    compared against its real old body rather than reported as "added").
    Returns the change_summary.py dict, or None if the class cannot be
    located at `after_rev` at all (e.g. the class was fully removed and
    before_rev's grep is also the only way to find it — the caller should
    try `class_change_summary` with before/after swapped in that case, or
    accept the "class not found" None and fall back to no summary).
    """
    path = path_hint or find_file_defining_class(repo_dir, after_rev, class_name)
    if path is None:
        return None
    new_text = read_file_at(repo_dir, after_rev, path)
    new_body = extract_class_body(new_text, class_name)
    old_text = read_file_at(repo_dir, before_rev, path)
    old_body = extract_class_body(old_text, class_name)
    if old_body is None and old_text is not None:
        # same path, different revision, class not there: maybe it moved files
        old_path = find_file_defining_class(repo_dir, before_rev, class_name)
        if old_path and old_path != path:
            old_body = extract_class_body(read_file_at(repo_dir, before_rev, old_path), class_name)
    if old_body is None and new_body is None:
        return None
    d = summarize_class_change(old_body, new_body, class_name)
    if not (d["class_added"] or d["class_removed"]):
        # Only meaningful for an edit of an existing class -- a brand-new or
        # fully-removed class has no "body changed, signature didn't" case.
        # Uses `path` (the file at after_rev) for BOTH revisions, same as the
        # old_body/new_body read above; if the class moved files between
        # before_rev and after_rev, read_file_at(before_rev, path) returns
        # None and method_body_diff degrades to [] for that one commit rather
        # than raising -- the rename case already loses signature-level
        # tracking too (see the docstring above), so this is consistent.
        body_touched = method_body_diff(repo_dir, before_rev, after_rev, path, class_name)
        already = set(d["methods_added"]) | set(d["methods_removed"]) | set(d["methods_sig_changed"])
        d["methods_body_changed"] = sorted(m for m in body_touched if m not in already)
    return d


def render_for_commit(repo_dir: str, sha: str, class_name: str, path_hint: str | None = None) -> str | None:
    """Convenience: change_summary text for `class_name` as changed BY commit
    `sha` (compares sha~1 -> sha). Returns None if the class can't be
    located (commit not fetched locally, or git-grep found nothing)."""
    d = class_change_summary(repo_dir, f"{sha}~1", sha, class_name, path_hint)
    return render_change_summary(d) if d is not None else None
