"""
Re-validates the honest gap documented in docs/change_summary_validation.md
section 1 ("5 of 7 commits had zero member/signature/constraint changes and
were visible only through call-count deltas or 'methods touched'") against
the SAME 7 real OpenTitan commits, now that git_diff.py's method_body_diff()
closes that gap for real. Prints, per class, whether has_any_signal() is True
BEFORE vs AFTER this session's change, so the improvement is a real,
re-run number rather than an assertion.

Requires the OpenTitan checkout already present at /home/claude/sources/opentitan
(same commits used for the original 9-case validation).
"""
import sys

sys.path.insert(0, "/home/claude/veridelta/src")
from veridelta import change_summary as cs  # noqa: E402
from veridelta import git_diff as gd  # noqa: E402

REPO = "/home/claude/sources/opentitan"

# (commit, class_name, path) -- same classes named in the original validation
# doc's 9-case set, each commit compared against its own parent (sha~1).
CASES = [
    ("a62413272", "dv_base_driver", "hw/dv/sv/dv_lib/dv_base_driver.sv"),
    ("5f6efdee7", "kmac_scoreboard", "hw/ip/kmac/dv/env/kmac_scoreboard.sv"),
    ("5f6efdee7", "kmac_env_cfg", "hw/ip/kmac/dv/env/kmac_env_cfg.sv"),
    ("5f6efdee7", "kmac_base_vseq", "hw/ip/kmac/dv/env/seq_lib/kmac_base_vseq.sv"),
    ("0cd8865c3", "aon_timer_base_vseq", "hw/ip/aon_timer/dv/env/seq_lib/aon_timer_base_vseq.sv"),
    ("0cd8865c3", "aon_timer_env_cov", "hw/ip/aon_timer/dv/env/aon_timer_env_cov.sv"),
    ("06367c424", "aes_base_vseq", "hw/ip/aes/dv/env/seq_lib/aes_base_vseq.sv"),
    ("2eb7256b1", "dv_base_env", "hw/dv/sv/dv_lib/dv_base_env.sv"),
    ("da7fc3571", "lc_ctrl_base_vseq", "hw/ip/lc_ctrl/dv/env/seq_lib/lc_ctrl_base_vseq.sv"),
]

before_zero_signal = 0
after_zero_signal = 0
newly_caught = []

for sha, cls, path in CASES:
    before_rev, after_rev = f"{sha}~1", sha
    # BEFORE: what summarize_class_change() alone sees (no git-diff pass) --
    # this is exactly the code path that existed before this session's change.
    old_text = gd.read_file_at(REPO, before_rev, path)
    new_text = gd.read_file_at(REPO, after_rev, path)
    old_body = cs.extract_class_body(old_text, cls)
    new_body = cs.extract_class_body(new_text, cls)
    d_before = cs.summarize_class_change(old_body, new_body, cls)
    had_signal_before = cs.has_any_signal(d_before)

    # AFTER: the real, now-wired class_change_summary(), which additionally
    # runs method_body_diff() against the real git diff.
    d_after = gd.class_change_summary(REPO, before_rev, after_rev, cls, path_hint=path)
    had_signal_after = d_after is not None and cs.has_any_signal(d_after)

    tag = "SAME" if had_signal_before == had_signal_after else "CHANGED"
    print(f"{sha} {cls:22s} before_signal={had_signal_before!s:5s} after_signal={had_signal_after!s:5s} [{tag}]")
    if not had_signal_before:
        before_zero_signal += 1
    if not had_signal_after:
        after_zero_signal += 1
    if not had_signal_before and had_signal_after:
        newly_caught.append((sha, cls, d_after["methods_body_changed"]))

print()
print(f"zero-signal cases BEFORE this session's change: {before_zero_signal}/{len(CASES)}")
print(f"zero-signal cases AFTER  this session's change: {after_zero_signal}/{len(CASES)}")
print()
print("Cases newly given signal by method_body_diff():")
for sha, cls, methods in newly_caught:
    print(f"  {sha} {cls}: methods_body_changed = {methods}")
