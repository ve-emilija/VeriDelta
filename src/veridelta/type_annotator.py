"""
VeriDelta — Type Annotator
type_annotator.py

Assigns component type τ(v) to every node in the UVM dependency graph.
Seven types map directly to the six DV failure categories established by
the companion empirical study.

τ → DV failure category
───────────────────────────────────────────────────────────────
CFG    _env_cfg, _cfg, _env_pkg            Interface Drift
ENV    _env, _base_env                     Interface Drift
AGENT  _agent, _driver, _monitor, _if      Interface Drift
SEQ    _vseq, _seq, _seq_item, _sequencer  Sequence Invalidation
SCBD   _scoreboard, _scb, _checker         Checker Desync
RAL    _reg_block, _ral_pkg, _reg          RAL Desync
COV    _cov_if, _cov_bind, _cov            Coverage Regression
SVA    _bind, _sva_if, _sva                Assertion Orphaning
OTHER  (no match)                          —
───────────────────────────────────────────────────────────────

Rules are evaluated top-to-bottom; first match wins.
More specific patterns must come before general ones (e.g. CFG before ENV,
RAL before CFG, COV before SVA) to avoid false matches.

CHANGES vs. the version used for the submitted numbers
───────────────────────────────────────────────────────────────
1. Class-declaration parsing is now a small scanner instead of one regex.
   The old regex  `extends\\s+(\\w+)`  captured the PACKAGE for package-scoped
   bases (`class x extends my_pkg::base`) and so created an edge
   my_pkg -> x.  That is why `scoreboard_pkg` and `racl_ctrl_base_env_pkg`
   exist as graph roots, and why tl_agent_scoreboard, xbar_scoreboard and
   racl_ctrl_env_cfg were orphaned from their real parents.  The old
   `#\\([^)]*\\)` parameter skipper also silently dropped any class whose
   parameter list contains a nested ')' (e.g. `$clog2(N)`).  Comments and
   string literals are now stripped before parsing, so commented-out classes
   no longer create edges.
2. `--audit` prints exactly which edges the old and new parsers disagree on,
   so the effect on Table I can be measured on the real OpenTitan checkout.
3. Bind parsing strips comments, no longer records the testbench root `tb`
   as an RTL module for hierarchical targets, and uses os.path.splitext.
4. Opt-in flags (defaults reproduce the old behaviour):
     --scan-all        parse every .sv/.svh instead of the filename keyword filter
     --inherit-types   type OTHER nodes by their nearest typed ancestor
                       (fixes the 25 jtag_dmi_reg_* / jtag_dtm_reg_* register
                       classes and core_ibex_vseqr; it does NOT fix classes that
                       are wrongly typed by a suffix rule, e.g. timed_reg)

Usage as module:
    from type_annotator import classify_type, annotate_graph, CATEGORY

Usage standalone:
    python type_annotator.py --opentitan /path/to/opentitan/hw [--audit]
    → runs unit tests, annotates the live graph, prints distribution,
      saves node_types.json, graph_edges.json, bind_index.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, deque

try:                                   # networkx is only needed to build/annotate a graph
    import networkx as nx
except ImportError:                    # pragma: no cover
    nx = None


# ── Taxonomy: τ type → DV failure category ───────────────────────────────────
# Directly mirrors Table 3 of the companion empirical study.
# OTHER nodes carry no primary category prediction; they still appear
# in the blast radius but are not labelled in category-level evaluation.

CATEGORY: dict[str, str | None] = {
    "CFG":   "Interface Drift",
    "ENV":   "Interface Drift",
    "AGENT": "Interface Drift",
    "SEQ":   "Sequence Invalidation",
    "SCBD":  "Checker Desync",
    "RAL":   "RAL Desync",
    "COV":   "Coverage Regression",
    "SVA":   "Assertion Orphaning",
    "OTHER": None,
}

# Ordered list (used for consistent display/iteration)
ALL_TYPES: list[str] = list(CATEGORY.keys())

# Reverse mapping: category string → list of τ types that produce it
CATEGORY_TO_TYPES: dict[str, list[str]] = {}
for _t, _c in CATEGORY.items():
    if _c is not None:
        CATEGORY_TO_TYPES.setdefault(_c, []).append(_t)


# ── Classification rules ──────────────────────────────────────────────────────
# Each rule is a tuple: (type_str, suffixes, contains, prefixes)
#   suffix   — name.endswith(s)     (checked first, most reliable signal)
#   contains — s in name            (substring match, use sparingly)
#   prefix   — name.startswith(s)   (for bind_ files and ral_ packages)
#
# ORDER MATTERS — first match wins:
#   RAL   before CFG/ENV  (catches _ral_pkg, _reg_block before _env_cfg)
#   CFG   before ENV      (catches _env_cfg before plain _env)
#   COV   before SVA      (catches _cov_bind before plain _bind)
#   SVA   before AGENT    (catches _sva_if before plain _if)
#   SEQ   before ENV      (seq rules are specific, but listed before ENV)
#   ENV   before AGENT    (catches _env before _if or loose patterns)
#
# NOTE: SVA has 0 nodes in the class graph by construction — assertion bind
# files are modules/interfaces, not classes, so they never appear as
# `class X extends Y` nodes.  Assertion Orphaning is therefore predicted only
# through bind_index.json, never through τ.

_RULES: list[tuple[str, list[str], list[str], list[str]]] = [
    (
        "RAL",
        ["_reg_block", "_ral_pkg", "_reg_model", "_reg"],
        ["_ral_"],
        [],
    ),
    (
        "CFG",
        ["_env_cfg", "_env_pkg", "_cfg"],
        [],
        [],
    ),
    (
        "SCBD",
        ["_scoreboard", "_scb", "_checker"],
        [],
        [],
    ),
    (
        "COV",
        ["_cov_if", "_cov_bind", "_coverage", "_cov"],
        ["cov_if", "cov_bind"],
        [],
    ),
    (
        "SVA",
        ["_sva_if", "_assert_if", "_sva", "_bind"],
        ["sva_"],
        ["bind_"],
    ),
    (
        "SEQ",
        ["_vseq", "_base_vseq", "_seq_item", "_seq", "_sequencer",
         "_sequence", "_item"],
        [],
        [],
    ),
    (
        "ENV",
        ["_env", "_base_env"],
        [],
        [],
    ),
    (
        "AGENT",
        ["_agent", "_agt", "_driver", "_monitor", "_if"],
        [],
        [],
    ),
]


# ── Core classification function ──────────────────────────────────────────────

def classify_type(name: str) -> str:
    """
    Return the UVM component type τ for a class name.

    Matching is case-insensitive and checks (in order):
      1. suffix patterns  (name ends with pattern)
      2. contains patterns (pattern appears anywhere in name)
      3. prefix patterns  (name starts with pattern)

    Args:
        name: UVM class name string, e.g. 'cip_base_env_cfg'

    Returns:
        One of: CFG | ENV | AGENT | SEQ | SCBD | RAL | COV | SVA | OTHER
    """
    n = name.lower()
    for type_str, suffixes, contains, prefixes in _RULES:
        if any(n.endswith(s) for s in suffixes):
            return type_str
        if any(c in n for c in contains):
            return type_str
        if any(n.startswith(p) for p in prefixes):
            return type_str
    return "OTHER"


# ── Source parsing: comments, class declarations ─────────────────────────────

_STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*.*?\*/', re.S)


def strip_comments(text: str) -> str:
    """
    Remove // and /* */ comments and blank out string literals, preserving
    newlines so that offsets stay roughly line-aligned.
    """
    def repl(m: re.Match) -> str:
        s = m.group(0)
        if s.startswith('"'):
            return '""'
        if s.startswith("/*"):
            return " " + "\n" * s.count("\n")
        return ""
    return _STRING_OR_COMMENT.sub(repl, text)


_CLASS_HEAD = re.compile(r"\bclass\s+(?:(?:automatic|static)\s+)?([A-Za-z_]\w*)")
_IDENT      = re.compile(r"[A-Za-z_]\w*")


def _skip_ws(t: str, i: int) -> int:
    n = len(t)
    while i < n and t[i].isspace():
        i += 1
    return i


def _skip_balanced(t: str, i: int) -> int:
    """t[i] == '(' → index just past the matching ')', or -1 if unbalanced."""
    depth = 0
    for j in range(i, len(t)):
        c = t[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
    return -1


def _skip_params(t: str, i: int) -> int:
    """If a `#( ... )` block starts at i (after whitespace), skip it. -1 on error."""
    j = _skip_ws(t, i)
    if j < len(t) and t[j] == "#":
        k = _skip_ws(t, j + 1)
        if k < len(t) and t[k] == "(":
            return _skip_balanced(t, k)
        return -1
    return i


def _parse_extends_target(t: str, i: int) -> str | None:
    """
    Parse   [scope[#(..)]::]* base [#(..)]   and return the BASE class name
    (the last identifier of a `::` chain), or None.
    """
    last = None
    while True:
        i = _skip_ws(t, i)
        m = _IDENT.match(t, i)
        if not m:
            return last
        last, i = m.group(0), m.end()
        e = _skip_params(t, i)
        if e < 0:
            return None
        j = _skip_ws(t, e)
        if t.startswith("::", j):
            i = j + 2
            continue
        return last


def extract_class_edges(text: str) -> list[tuple[str, str]]:
    """
    Return [(parent, child), ...] for every `class child [#(...)] extends
    [pkg::]parent [#(...)]` declaration in `text`.

    Handles: virtual/automatic/static classes, nested parentheses in parameter
    lists, package-scoped and parameterised base classes, comments and string
    literals.  Forward declarations (`typedef class x;`) and classes with no
    `extends` produce no edge.
    """
    t = strip_comments(text)
    edges: list[tuple[str, str]] = []
    for m in _CLASS_HEAD.finditer(t):
        child = m.group(1)
        i = _skip_params(t, m.end())
        if i < 0:
            continue
        i = _skip_ws(t, i)
        if not t.startswith("extends", i):
            continue
        after = i + len("extends")
        if after < len(t) and (t[after].isalnum() or t[after] == "_"):
            continue
        parent = _parse_extends_target(t, after)
        if parent and parent != child:
            edges.append((parent, child))
    return edges


# The regex used for the submitted numbers — kept ONLY so --audit can diff it.
_LEGACY_CLASS_PATTERN = re.compile(
    r"\bclass\s+(\w+)\s*(?:#\s*\([^)]*\))?\s*extends\s+(\w+)",
    re.IGNORECASE | re.DOTALL,
)


def legacy_class_edges(text: str) -> list[tuple[str, str]]:
    return [(m.group(2), m.group(1)) for m in _LEGACY_CLASS_PATTERN.finditer(text)]


# ── File discovery ────────────────────────────────────────────────────────────

EXTENSIONS   = (".sv", ".svh")
UVM_KEYWORDS = (
    # core UVM components — from lowRISC DVCodingStyle.md spec
    "agent", "env", "driver", "monitor", "scoreboard",
    "sequencer", "sequence", "interface", "checker",
    # additional — confirmed in OpenTitan commit history
    "vseq",   # virtual sequences (_vseq) — 'vseq' != 'sequence'
    "cov",    # coverage interfaces (_cov_if, _env_cov, _agent_cov)
    "sva",    # SVA interface files (_sva_if, _sva)
    "bind",   # assertion bind files (_bind, _ctrl_bind, _cov_bind)
    "reg",    # RAL register block files (_reg_block, _reg)
    "ral",    # RAL package files (_ral_pkg)
    "item",   # sequence items (_item, _seq_item)
)


def collect_files(root: str, scan_all: bool = False) -> list[str]:
    """
    Default (scan_all=False) reproduces the submitted behaviour: only files
    whose NAME contains a UVM keyword are parsed, so any class living in a
    file with an unrelated name is invisible to the graph.
    """
    files = []
    for dirpath, _, filenames in os.walk(root):
        for f in sorted(filenames):
            if not f.endswith(EXTENSIONS):
                continue
            if scan_all or any(k in f.lower() for k in UVM_KEYWORDS):
                files.append(os.path.join(dirpath, f))
    return sorted(files)


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="ignore") as fh:
        return fh.read()


def build_graph(files: list[str]):
    if nx is None:
        raise RuntimeError("networkx is required: pip install networkx")
    G = nx.DiGraph()
    for f in files:
        try:
            for parent, child in extract_class_edges(_read(f)):
                G.add_edge(parent, child)
        except OSError:
            pass
    return G


def audit_edges(files: list[str], max_show: int = 25) -> dict:
    """
    Compare the submitted regex against the scanner over `files`.
    Returns counts and examples of edges that only one of them finds.
    """
    old: dict[tuple[str, str], str] = {}
    new: dict[tuple[str, str], str] = {}
    for f in files:
        try:
            text = _read(f)
        except OSError:
            continue
        for e in legacy_class_edges(text):
            old.setdefault(e, f)
        for e in extract_class_edges(text):
            new.setdefault(e, f)
    only_old = sorted(set(old) - set(new))
    only_new = sorted(set(new) - set(old))
    return {
        "legacy_edges": len(old), "scanner_edges": len(new),
        "only_legacy": [(p, c, os.path.basename(old[(p, c)])) for p, c in only_old],
        "only_scanner": [(p, c, os.path.basename(new[(p, c)])) for p, c in only_new],
        "max_show": max_show,
    }


def print_audit_edges(a: dict) -> None:
    print(f"  legacy regex edges : {a['legacy_edges']}")
    print(f"  scanner edges      : {a['scanner_edges']}")
    print(f"  only in legacy  ({len(a['only_legacy'])}): wrong-parent / phantom edges")
    for p, c, f in a["only_legacy"][: a["max_show"]]:
        print(f"      {p} -> {c}    [{f}]")
    print(f"  only in scanner ({len(a['only_scanner'])}): edges the legacy regex missed or mis-parented")
    for p, c, f in a["only_scanner"][: a["max_show"]]:
        print(f"      {p} -> {c}    [{f}]")


# ── Graph annotation ──────────────────────────────────────────────────────────

def _nearest_typed_ancestor(G, node: str) -> str | None:
    """Nearest ancestor whose NAME-based type is not OTHER (ties → alphabetical)."""
    seen = {node}
    frontier = [node]
    while frontier:
        nxt = []
        for n in frontier:
            for p in G.predecessors(n):
                if p not in seen:
                    seen.add(p)
                    nxt.append(p)
        typed = sorted(p for p in nxt if classify_type(p) != "OTHER")
        if typed:
            return typed[0]
        frontier = nxt
    return None


def annotate_graph(G, inherit: bool = False):
    """
    Add 'type' and 'category' node attributes to every node in G.

    inherit=False (default) → pure name-based τ, as in the submitted paper.
    inherit=True            → a node that is OTHER by name takes the type of its
                              nearest name-typed ancestor and records
                              G.nodes[n]['inherited_from'].
    """
    for node in G.nodes():
        t = classify_type(node)
        if inherit and t == "OTHER":
            anc = _nearest_typed_ancestor(G, node)
            if anc:
                t = classify_type(anc)
                G.nodes[node]["inherited_from"] = anc
        G.nodes[node]["type"]     = t
        G.nodes[node]["category"] = CATEGORY[t]
    return G


# ── Query helpers ─────────────────────────────────────────────────────────────

def type_distribution(G) -> dict[str, int]:
    """Count nodes per type. Graph must already be annotated."""
    return dict(Counter(G.nodes[n].get("type", "OTHER") for n in G.nodes()))


def nodes_by_type(G, type_str: str) -> list[str]:
    """Return all node names with a given type."""
    return [n for n in G.nodes() if G.nodes[n].get("type") == type_str]


def predict_categories_for_blast_radius(
    blast_radius: set[str], G
) -> dict[str, list[str]]:
    """
    For each node in the blast radius, return its predicted DV failure
    categories (may be empty list for OTHER nodes).
    """
    result: dict[str, list[str]] = {}
    for node in blast_radius:
        cat = G.nodes[node].get("category") if node in G else None
        result[node] = [cat] if cat else []
    return result


# ── Output utilities ──────────────────────────────────────────────────────────

def print_distribution(G) -> None:
    """Pretty-print type distribution with category mapping."""
    dist  = type_distribution(G)
    total = sum(dist.values())

    print(f"\nVeriDelta τ type distribution  ({total} nodes total)")
    print("─" * 58)
    print(f"  {'Type':<8} {'Count':>5}  {'%':>5}  Category")
    print("─" * 58)
    for t in ALL_TYPES:
        count = dist.get(t, 0)
        pct   = 100 * count / total if total else 0.0
        cat   = CATEGORY[t] or "—"
        print(f"  {t:<8} {count:>5}  {pct:>4.1f}%  {cat}")
    print("─" * 58)
    interface_drift_n = sum(dist.get(t, 0) for t in ["CFG", "ENV", "AGENT"])
    print(f"\n  Interface Drift pool (CFG + ENV + AGENT): {interface_drift_n} nodes")
    print(f"  Unlabelled (OTHER):                       {dist.get('OTHER', 0)} nodes")


def save_annotations(G, output_path: str = "node_types.json") -> None:
    """
    Persist node type annotations to JSON.

        { "cip_base_env_cfg": {"type": "CFG", "category": "Interface Drift"}, ... }

    Nodes typed via --inherit-types additionally carry "inherited_from".
    Downstream code only reads "type"/"category", so the schema is backward
    compatible.
    """
    annotations = {}
    for node in G.nodes():
        a = {
            "type":     G.nodes[node].get("type", "OTHER"),
            "category": G.nodes[node].get("category"),
        }
        if "inherited_from" in G.nodes[node]:
            a["inherited_from"] = G.nodes[node]["inherited_from"]
        annotations[node] = a
    with open(output_path, "w") as fh:
        json.dump(annotations, fh, indent=2)
    print(f"\nAnnotations saved → {output_path}  ({len(annotations)} nodes)")


# ── Bind index ────────────────────────────────────────────────────────────────

_BIND_STMT = re.compile(r"\bbind\s+([A-Za-z_][\w.]*)")
_BIND_NOT_TARGETS = {"module", "interface", "class", "function", "task",
                     "begin", "end", "this"}
_BIND_ROOTS = {"tb", "dut"}          # testbench roots are not RTL module names


def parse_bind_targets(text: str) -> list[str]:
    """
    Return the RTL target of every `bind <target> ...` statement in `text`.

    Comments are stripped first, which replaces the old stop-word hack for
    'bind the …' prose.  For a hierarchical target (tb.dut.u_x) only the LAST
    component is kept — it is the closest thing to a module/instance name —
    and bare testbench roots (tb, dut) are dropped.  Instance paths can only be
    resolved to module types by elaboration; they will usually not match an RTL
    file stem, which is the honest outcome.
    """
    out = []
    for m in _BIND_STMT.finditer(strip_comments(text)):
        target = m.group(1)
        if "." in target:
            target = target.rsplit(".", 1)[-1]
        if not target or target.lower() in _BIND_NOT_TARGETS | _BIND_ROOTS:
            continue
        out.append(target)
    return out


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def is_bind_candidate(path: str) -> bool:
    stem = _stem(path)
    in_sva_dir = (os.sep + "sva" + os.sep) in path or path.endswith(os.sep + "sva")
    return stem.endswith("_bind") or in_sva_dir


def build_bind_index(files: list[str]) -> dict[str, list[str]]:
    """
    Parse _bind.sv files and sva/ directory files for SystemVerilog
    'bind <rtl_module_name>' statements.

    Returns dict: { rtl_module_name: [bind_file_stem, ...] }

    WHY THIS EXISTS
    ───────────────
    Assertion Orphaning occurs when RTL module names or signal hierarchies
    change and the bind statements referencing them become stale.  Unlike
    class inheritance — which the dependency graph captures — bind statements
    are MODULE-level constructs.

    The bind statement form is:
      bind <rtl_module_or_instance> <checker_module> <instance_name> (ports);

    USAGE BY graphrag_pipeline.py (Stage 1 extension)
    ──────────────────────────────────────────────────
    When a commit changes an RTL file, extract the RTL module name from the
    file stem and look it up in bind_index.  Matching bind files are flagged;
    `*_cov_bind` files are routed to Coverage Regression, all others to
    Assertion Orphaning (see graphrag_pipeline.bind_category).
    """
    bind_index: dict[str, list[str]] = {}
    for f in files:
        if not is_bind_candidate(f):
            continue
        stem = _stem(f)
        try:
            targets = parse_bind_targets(_read(f))
        except OSError:
            continue
        for tgt in targets:
            bind_index.setdefault(tgt, [])
            if stem not in bind_index[tgt]:
                bind_index[tgt].append(stem)
    return bind_index


def save_graph_edges(G, output_path: str = "graph_edges.json") -> None:
    """Save the dependency graph as a successor adjacency list {parent: [children]}."""
    adjacency = {node: list(G.successors(node)) for node in G.nodes()}
    with open(output_path, "w") as fh:
        json.dump(adjacency, fh, indent=2)
    print(f"\nGraph edges saved → {output_path}  ({G.number_of_edges()} edges)")


def save_bind_index(
    bind_index: dict[str, list[str]],
    output_path: str = "bind_index.json",
) -> None:
    """Persist the bind index: { "sysrst_ctrl": ["sysrst_ctrl_bind"], ... }"""
    with open(output_path, "w") as fh:
        json.dump(bind_index, fh, indent=2)
    total_refs = sum(len(v) for v in bind_index.values())
    print(
        f"\nBind index saved → {output_path}  "
        f"({len(bind_index)} RTL modules, {total_refs} bind-file references)"
    )


# ── Unit tests ────────────────────────────────────────────────────────────────

def _run_unit_tests() -> bool:
    """Validate classify_type(), the class scanner and the bind parser."""
    cases = [
        # CFG — more specific than ENV, must win
        ("dv_base_env_cfg",        "CFG"),
        ("cip_base_env_cfg",       "CFG"),
        ("aes_env_cfg",            "CFG"),
        ("tl_agent_env_cfg",       "CFG"),   # ends _env_cfg, not _env
        ("xbar_env_cfg",           "CFG"),
        ("chip_env_cfg",           "CFG"),
        # ENV
        ("cip_base_env",           "ENV"),
        ("dv_base_env",            "ENV"),
        ("aes_env",                "ENV"),
        ("kmac_env",               "ENV"),
        # AGENT / DRIVER / MONITOR
        ("tl_agent",               "AGENT"),
        ("dv_base_driver",         "AGENT"),
        ("dv_base_monitor",        "AGENT"),
        ("i2c_driver",             "AGENT"),
        ("uart_if",                "AGENT"),
        # SEQ
        ("rv_dm_smoke_vseq",       "SEQ"),
        ("dv_base_vseq",           "SEQ"),
        ("cip_base_vseq",          "SEQ"),
        ("dv_base_seq_item",       "SEQ"),
        ("aes_base_seq",           "SEQ"),
        # SCBD
        ("uart_scoreboard",        "SCBD"),
        ("aes_scb",                "SCBD"),
        ("dv_base_checker",        "SCBD"),
        # RAL — must beat CFG/ENV
        ("aes_reg_block",          "RAL"),
        ("cip_base_reg",           "RAL"),
        ("aes_ral_pkg",            "RAL"),
        # COV — must beat SVA (cov_bind before _bind)
        ("aes_cov_if",             "COV"),
        ("aes_cov_bind",           "COV"),
        ("aes_cov",                "COV"),
        # SVA
        ("bind_aes_sec_cm",        "SVA"),
        ("aes_sva",                "SVA"),
        ("aes_sva_if",             "SVA"),
        # SEQ — _item suffix
        ("csrng_item",             "SEQ"),
        ("aes_message_item",       "SEQ"),
        ("dma_seq_item",           "SEQ"),
        # OTHER
        ("dv_base_vif_proxy",      "OTHER"),
        ("dv_base_object",         "OTHER"),
        ("dv_base_component",      "OTHER"),
    ]

    failed = 0
    for name, expected in cases:
        got = classify_type(name)
        if got != expected:
            print(f"  FAIL  classify {name:<38} expected={expected:<6}  got={got}")
            failed += 1

    edge_cases = [
        ("plain",                "class a extends b;",                              [("b", "a")]),
        ("virtual",              "virtual class a extends b;",                      [("b", "a")]),
        ("params",               "class a #(type T = c) extends b;",                [("b", "a")]),
        ("nested paren params",  "class a #(int W = $clog2(N)) extends b #(W);",    [("b", "a")]),
        ("multi-line params",    "class a #(\n type T = c\n) extends\n  b;",        [("b", "a")]),
        ("pkg scoped",           "class a extends my_pkg::b;",                      [("b", "a")]),
        ("pkg scoped + params",  "class a extends my_pkg::b #(.X(1));",             [("b", "a")]),
        ("param pkg scope",      "class a extends p#(3)::b;",                       [("b", "a")]),
        ("line comment",         "// class a extends b;\nclass c extends d;",       [("d", "c")]),
        ("block comment",        "/* class a extends b; */ class c extends d;",     [("d", "c")]),
        ("string literal",       'x = "class a extends b"; class c extends d;',     [("d", "c")]),
        ("no extends",           "class a;\nendclass",                              []),
        ("typedef fwd",          "typedef class a;\nclass b extends c;",            [("c", "b")]),
        ("automatic",            "class automatic a extends b;",                    [("b", "a")]),
        ("extends prefix word",  "class a extendsfoo;",                             []),
        ("two classes",          "class a extends b; endclass\nclass c extends a;", [("b", "a"), ("a", "c")]),
        ("self edge",            "class a extends a;",                              []),
    ]
    for label, src, expected in edge_cases:
        got = extract_class_edges(src)
        if got != expected:
            print(f"  FAIL  class-scan [{label}] expected={expected} got={got}")
            failed += 1

    bind_cases = [
        ("simple",        "bind uart uart_sva_if u_sva (.*);",                     ["uart"]),
        ("params",        "bind aes aes_cov_if #(.W(1)) u_cov (.*);",              ["aes"]),
        ("instance list", "bind aes : u_a, u_b aes_chk u_chk (.*);",               ["aes"]),
        ("hier target",   "bind tb.dut.u_rom rom_cov u_c (.*);",                   ["u_rom"]),
        ("tb root only",  "bind tb x y (.*);",                                     []),
        ("comment prose", "// bind the assertion to dut\nbind gpio gpio_chk u (.*);", ["gpio"]),
        ("block comment", "/* bind foo x y; */ bind bar x y (.*);",                ["bar"]),
        ("identifier",    "rebind x; bindings y;",                                 []),
    ]
    for label, src, expected in bind_cases:
        got = parse_bind_targets(src)
        if got != expected:
            print(f"  FAIL  bind [{label}] expected={expected} got={got}")
            failed += 1

    total = len(cases) + len(edge_cases) + len(bind_cases)
    print(f"  {total - failed} passed, {failed} failed out of {total} cases")
    return failed == 0


# ── Standalone entrypoint ─────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VeriDelta type annotator")
    ap.add_argument("--opentitan", default=os.environ.get(
        "OPENTITAN_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", "opentitan", "hw")),
        help="path to opentitan/hw (default: $OPENTITAN_PATH)")
    ap.add_argument("--out-dir", default=".", help="where to write the three JSON files")
    ap.add_argument("--scan-all", action="store_true",
                    help="parse every .sv/.svh instead of the filename keyword filter")
    ap.add_argument("--inherit-types", action="store_true",
                    help="type OTHER nodes by nearest typed ancestor (sensitivity analysis)")
    ap.add_argument("--audit", action="store_true",
                    help="diff the old regex against the scanner and exit without writing")
    ap.add_argument("--self-test", action="store_true", help="run unit tests and exit")
    args = ap.parse_args(argv)

    print("=" * 60)
    print("VeriDelta  —  type_annotator.py")
    print("=" * 60)

    print("\nStep 1: Unit tests")
    print("─" * 40)
    if not _run_unit_tests():
        print("\nFix failing cases before running on the full graph.")
        return 1
    print("All unit tests passed.")
    if args.self_test:
        return 0

    if not os.path.isdir(args.opentitan):
        print(f"\nOpenTitan path not found: {args.opentitan}\n"
              f"  set --opentitan or $OPENTITAN_PATH")
        return 1
    if nx is None:
        print("\nnetworkx is required: pip install networkx")
        return 1

    print("\nStep 2: Building dependency graph")
    print("─" * 40)
    files = collect_files(args.opentitan, args.scan_all)
    print(f"  Found {len(files)} source files "
          f"({'all .sv/.svh' if args.scan_all else 'keyword-filtered'})")

    if args.audit:
        print("\nAudit: legacy regex vs scanner (keyword-filtered file set)")
        print_audit_edges(audit_edges(collect_files(args.opentitan, False)))
        if not args.scan_all:
            kw   = {e for f in collect_files(args.opentitan, False)
                    for e in extract_class_edges(_read(f))}
            full = {e for f in collect_files(args.opentitan, True)
                    for e in extract_class_edges(_read(f))}
            print(f"\n  Edges invisible to the filename keyword filter: {len(full - kw)}")
            for p, c in sorted(full - kw)[:25]:
                print(f"      {p} -> {c}")
        return 0

    G = build_graph(files)
    print(f"  Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    print("\nStep 3: Annotating types")
    print("─" * 40)
    annotate_graph(G, inherit=args.inherit_types)
    print_distribution(G)
    if args.inherit_types:
        n_inh = sum(1 for n in G if "inherited_from" in G.nodes[n])
        print(f"\n  Nodes typed by inheritance: {n_inh}")

    print("\nStep 4: Sample nodes per type")
    print("─" * 40)
    for t in ALL_TYPES:
        examples = sorted(nodes_by_type(G, t))[:5]
        if examples:
            print(f"  {t:<8}  {examples}")

    print("\nStep 5: Blast radius spot-check — cip_base_env_cfg")
    print("─" * 40)
    ROOT = "cip_base_env_cfg"
    if ROOT in G:
        br = nx.descendants(G, ROOT)
        preds = predict_categories_for_blast_radius(br, G)
        by_cat: dict[str, list[str]] = {}
        for node, cats in preds.items():
            for c in cats:
                by_cat.setdefault(c, []).append(node)
        print(f"  Blast radius: {len(br)} nodes")
        for cat, nodes in sorted(by_cat.items()):
            print(f"  {cat:<25}  {len(nodes)} nodes")
    else:
        print(f"  Node '{ROOT}' not found in graph — check OpenTitan path.")

    roots = sorted(n for n in G if G.in_degree(n) == 0)
    print(f"\n  Graph roots: {len(roots)}")
    pkg_roots = [r for r in roots if r.endswith("_pkg")]
    if pkg_roots:
        print(f"  WARNING: *_pkg roots usually mean a package-scoped `extends` was "
              f"mis-parsed: {pkg_roots}")

    print("\nStep 6: Building bind index (Assertion Orphaning)")
    print("─" * 40)
    bind_index = build_bind_index(files)
    total_refs = sum(len(v) for v in bind_index.values())
    print(f"  RTL modules referenced by bind statements: {len(bind_index)}")
    print(f"  Total bind-file references:                {total_refs}")
    for rtl_mod, bfiles in list(bind_index.items())[:8]:
        print(f"    {rtl_mod:<35} {bfiles}")

    os.makedirs(args.out_dir, exist_ok=True)
    save_bind_index(bind_index, os.path.join(args.out_dir, "bind_index.json"))
    save_graph_edges(G,         os.path.join(args.out_dir, "graph_edges.json"))
    save_annotations(G,         os.path.join(args.out_dir, "node_types.json"))
    print("\nDone. Re-run ground_truth_mapper.py next (node set may have changed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
