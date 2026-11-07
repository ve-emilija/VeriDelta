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

import subprocess

from .change_summary import extract_class_body, summarize_class_change, render_change_summary


def _run(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout


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
    return summarize_class_change(old_body, new_body, class_name)


def render_for_commit(repo_dir: str, sha: str, class_name: str, path_hint: str | None = None) -> str | None:
    """Convenience: change_summary text for `class_name` as changed BY commit
    `sha` (compares sha~1 -> sha). Returns None if the class can't be
    located (commit not fetched locally, or git-grep found nothing)."""
    d = class_change_summary(repo_dir, f"{sha}~1", sha, class_name, path_hint)
    return render_change_summary(d) if d is not None else None
