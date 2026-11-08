"""
VeriDelta — Component-coupling edges
component_edges.py

Item 2 of the November 2026 roadmap: "Add the missing edges — member handles,
uvm_config_db set/get, analysis-port connections, factory overrides and
bind. Slang becomes useful here, because resolving types and macros is what
it does well."

`bind` is already handled (type_annotator.build_bind_index /
graphrag_pipeline.get_bind_at_risk, wired into the CLI in the commit before
this one). This module adds the other four. All four extend the blast
radius with real UVM coupling that pure class *inheritance* (the only edge
kind the graph had before this) cannot see at all:

  1. member handles       `scoreboard scoreboard_h;` inside an env — a change
                           to `scoreboard`'s public interface can break any
                           class that HOLDS a handle to it, independent of
                           inheritance.
  2. config_db set/get     `uvm_config_db#(T)::set(...)` in one class and
                           `uvm_config_db#(T)::get(...)` in another, matched
                           on (type, field) — these two classes are coupled
                           through the config DB even with no direct
                           reference to each other in either file.
  3. analysis-port connect `producer.ap.connect(consumer.export)` — couples
                           the producer's and consumer's declared types
                           directly, not just both of them to the env that
                           wires them together.
  4. factory overrides     `X::type_id::set_type_override(Y::get_type())` —
                           changing X can silently mean Y runs instead; the
                           blast radius of a change to X must include Y.

ON SLANG, AND WHY THIS IS REGEX INSTEAD
────────────────────────────────────────────────────────────────────────────
The roadmap item names slang as the natural tool for this step ("resolving
types and macros is what it does well"). That is correct for signal-level
work (parameter resolution, macro expansion, elaboration) — but every edge
kind here is resolved from SOURCE TEXT ONLY, against SYMBOL TABLES already
built by this same regex-based pipeline (the member-handle table from
`_members()`, the class/RTL-module name sets from node_types.json /
bind_index.json). None of the four needs elaboration or macro expansion to
produce a *first-approximation* edge — they need exactly what change_summary.py
already proved out: strip comments, scope to one class body, regex, cross-
reference against a known-name set. Pulling in slang here would buy
preprocessor-correct parsing (real value for e.g. `` `celled parameters) at
the cost of maintaining a C++ toolchain path on top of SOURCE_FILES this
project does not otherwise need yet. Documented as a real limitation below
and in docs/change_summary_validation.md: every edge this module finds is a
textual, non-elaborated approximation, and the FAILURE MODE in each case is
documented at the function that can produce it.

USAGE
  table   = build_corpus(files)              # {class_name: class_body_text}
  edges   = all_component_edges(table, known_classes)
  # edges: list[ComponentEdge] — see below. Each carries `kind` and `evidence`
  # (the literal source snippet it was read from) so a prediction built on
  # top of it can CITE that evidence (item 4), not just assert a relationship.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .change_summary import extract_class_body, _members
from .type_annotator import strip_comments, _CLASS_HEAD, _skip_params


# ── A single typed, evidenced edge ───────────────────────────────────────────

@dataclass(frozen=True)
class ComponentEdge:
    """
    src/dst follow the SAME convention as the existing inheritance adjacency
    (adjacency[parent] = [children]): BFS from a changed class walks src ->
    dst, so `dst` is what should appear in the blast radius when `src`
    changes. Concretely, per kind:
      member_handle    held_type   -> holder     (held's interface changes -> holder breaks)
      config_db        setter      -> getter     (what's set changes -> what reads it breaks)
      analysis_port    producer    -> consumer   (producer's item/port changes -> consumer breaks)
      factory_override orig        -> new        (orig changes, but `new` is what actually runs)
    """
    src: str
    dst: str
    kind: str                # "member_handle" | "config_db" | "analysis_port" | "factory_override"
    evidence: str            # the literal source snippet this edge was read from (for citations)
    via: str = ""            # the member/field/port name involved, when there is one


# UVM/OVM base-library classes that can legitimately appear in `known_classes`
# (because a project class `extends` one of them) but that are never a
# project's own "changed_node": the methodology library itself is not what
# this tool's commits touch. Validated finding (Caliptra-rtl, 2026-11-08):
# auto-generated RAL register classes (e.g. `aes_clp_reg__AES_NAME extends
# uvm_reg`) declare a `uvm_reg_field` member for every single CSR field, so
# WITHOUT this filter uvm_reg_field alone picks up thousands of
# near-identical, zero-discriminative member_handle edges (one per register
# field in the whole design) — real count before filtering: see
# docs/change_summary_validation.md, "component_edges validation". This list
# is deliberately short and only the highest-volume/most-generic UVM base
# classes; it is a heuristic, not a complete UVM class list, and is applied
# as a target-side filter only (a project class legitimately named like a
# library class is not expected and not specially handled).
_LIBRARY_BASE_CLASSES = frozenset({
    "uvm_object", "uvm_component", "uvm_transaction", "uvm_sequence_item",
    "uvm_sequence", "uvm_sequencer", "uvm_driver", "uvm_monitor",
    "uvm_scoreboard", "uvm_env", "uvm_agent", "uvm_test", "uvm_subscriber",
    "uvm_reg", "uvm_reg_field", "uvm_reg_block", "uvm_reg_map", "uvm_reg_file",
    "uvm_mem", "uvm_reg_predictor", "uvm_reg_adapter", "uvm_analysis_port",
    "uvm_analysis_imp", "uvm_analysis_export", "uvm_tlm_analysis_fifo",
    "uvm_event", "uvm_object_wrapper", "uvm_config_db", "uvm_report_object",
    "uvm_phase", "uvm_domain", "uvm_pool", "uvm_queue", "uvm_barrier",
    "uvm_event_pool", "uvm_resource_db",
})


# ── 1. Member handles ────────────────────────────────────────────────────────

def member_handle_edges(class_name: str, body: str, known_classes: set) -> list[ComponentEdge]:
    """
    Every declared member of `class_name` whose TYPE is itself a known class
    (from `known_classes`, normally node_types.json's keys, minus
    `_LIBRARY_BASE_CLASSES`) becomes an edge member_type -> class_name:
    `class_name` holds a handle to `member_type`, so a change to
    `member_type`'s interface is downstream of blast radius started AT
    `member_type`, independent of inheritance.

    MISSES (by design, same conservative stance as change_summary._members):
      - parameterized/templated member types (`push_pull_agent#(.W(N)) h;`,
        `uvm_analysis_imp #(T, this_type) h;`) — real case found validating
        against OpenTitan (otp_ctrl_env.sv: `push_pull_agent#(.DeviceDataWidth(
        OTBN_DATA_SIZE)) m_otbn_pull_agent;`): the nested `(...)` inside the
        `#(...)` parameter list breaks `_MEMBER_DECL`'s own non-greedy
        paren-matching, so the ENTIRE line fails to match, not just the type.
        change_summary.py has the identical limitation (it is the same
        regex) — documented there and here rather than fixed, since a
        correct nested-paren parameterized-type parser is most of the way to
        writing a real SV type grammar, which is exactly the "resolving
        types ... is what slang does well" case the roadmap item points at.
      - members declared via a `typedef` alias to a class — the alias name,
        not the real class name, is what appears in the type text.
      - INHERITED members: this only sees `class_name`'s OWN declarations,
        not its ancestors'. Real case found validating against OpenTitan:
        `otp_ctrl_env` reads `scoreboard.otbn_fifo...` in `connect_phase`,
        but `scoreboard` is declared on its base class `cip_base_env`
        (parameterized as `SCOREBOARD_T`), not on `otp_ctrl_env` itself — so
        the connect call's `scoreboard` side cannot resolve from this body
        alone. `all_component_edges`'s caller is expected to pass each
        class's OWN body only (ancestor members are NOT merged in): the
        existing inheritance BFS already reaches `otp_ctrl_env` FROM
        `cip_base_env` if `cip_base_env`'s own member_handle edge fires, so
        the miss is partial (the base class's edge still exists) rather than
        total, but a change to the member's type is not attributed to the
        correct concrete env. Left as future work; see
        docs/change_summary_validation.md.
    """
    edges = []
    for name, type_text in _members(body).items():
        # type_text may carry rand/randc, array dims, or `local`/`static` —
        # take the first bare identifier, which is where the base type lives.
        m = re.match(r"(?:rand\s+|randc\s+)?(?:local\s+|protected\s+|static\s+|const\s+)*"
                     r"([A-Za-z_]\w*)", type_text)
        if not m:
            continue
        base_type = m.group(1)
        if base_type == class_name:
            continue                                   # self-reference (linked list node, etc.)
        if base_type in _LIBRARY_BASE_CLASSES:
            continue
        if base_type in known_classes:
            edges.append(ComponentEdge(base_type, class_name, "member_handle",
                                        evidence=f"{type_text} {name};", via=name))
    return edges


# ── 2. uvm_config_db set/get ─────────────────────────────────────────────────

# `uvm_config_db#(TYPE)::set(context, "scope", "field", value)` /  ::get(...)
# TYPE may carry `virtual` (interface handles) and a scope (`pkg::type`).
# Only a LITERAL string field name is matched — `::set(..., field_var, ...)`
# (OpenTitan's flash_ctrl_bkdr_util.sv uses `m_mem_bkdr_util.get_name()` as
# the field argument) is skipped rather than guessed, since matching on an
# unresolved variable would be matching on nothing.
_CONFIG_DB_CALL = re.compile(
    r"\buvm_config_db\s*#\s*\(\s*(?P<type>(?:virtual\s+)?[\w:]+)\s*\)\s*::\s*(?P<verb>set|get)\s*\(\s*"
    r"(?P<ctx>[^,]*),\s*\"(?P<scope>[^\"]*)\"\s*,\s*\"(?P<field>[^\"]*)\"",
)


def config_db_calls(class_name: str, body: str) -> list[dict]:
    """Every `::set`/`::get` call in `body`, as a flat record. Pure extraction,
    no cross-class matching here (that needs the whole corpus — see
    `config_db_edges`)."""
    out = []
    for m in _CONFIG_DB_CALL.finditer(body):
        out.append({
            "class": class_name,
            "verb": m.group("verb"),
            "type": m.group("type").strip(),
            "scope": m.group("scope"),
            "field": m.group("field"),
            "evidence": m.group(0).split("(")[0] + f'(..., "{m.group("field")}", ...)',
        })
    return out


def config_db_edges(table: dict) -> list[ComponentEdge]:
    """
    Cross-reference every `::set` against every `::get` with the SAME (type,
    field) key, across the whole corpus, and emit setter_class -> getter_class.

    HONEST LIMITATION (state in the paper): this ignores the `scope` string
    entirely. UVM config_db resolution is hierarchical path matching
    ("*.env.agent*" style wildcards against the actual instance tree), which
    this text-only pass cannot elaborate. Matching on (type, field) alone is
    a conservative OVER-approximation: it can create an edge between a setter
    and a getter that, with real instance paths, never actually resolve to
    each other (false positive edge) — but a type+field collision across
    unrelated subsystems is rare in the OpenTitan/Caliptra naming conventions
    actually observed (validated below), and the cost of a spurious edge in
    a change-impact tool is low (a few extra classes to review) compared to
    the cost of a missed one (silently never flagged).
    """
    sets = defaultdict(list)     # (type, field) -> [(class, evidence), ...]
    gets = defaultdict(list)
    for class_name, body in table.items():
        for rec in config_db_calls(class_name, body):
            key = (rec["type"], rec["field"])
            (sets if rec["verb"] == "set" else gets)[key].append(rec)

    edges = []
    for key, setters in sets.items():
        for getter in gets.get(key, ()):
            for setter in setters:
                if setter["class"] == getter["class"]:
                    continue                            # same class setting then reading its own key
                edges.append(ComponentEdge(
                    setter["class"], getter["class"], "config_db",
                    evidence=f"{setter['evidence']}  ~>  {getter['evidence']}",
                    via=f"{key[0]} \"{key[1]}\"",
                ))
    return edges


# ── 3. Analysis-port connect ─────────────────────────────────────────────────

# `<lhs-chain>.connect(<rhs-chain>)`. Only the FIRST identifier of each chain
# is resolved (see module docstring: deeper chains like `.monitor.ap` need a
# member-of-a-member lookup this pass does not attempt), via the enclosing
# class's own member-handle table.
_CONNECT_CALL = re.compile(
    r"(?P<lhs>[A-Za-z_]\w*)(?:\s*\[[^\]]*\])?(?:\s*\.\s*[A-Za-z_]\w*(?:\s*\[[^\]]*\])?)*"
    r"\s*\.\s*connect\s*\(\s*"
    r"(?P<rhs>[A-Za-z_]\w*)(?:\s*\[[^\]]*\])?(?:\s*\.\s*[A-Za-z_]\w*(?:\s*\[[^\]]*\])?)*\s*\)",
)
_SELF_REFS = {"this", "super"}


def analysis_port_edges(class_name: str, body: str, known_classes: set) -> list[ComponentEdge]:
    """
    Resolve each side of every `.connect(...)` call in `body` to a member's
    declared type (falling back to the enclosing class itself for a bare
    `this.x.connect(...)`), and emit an edge between the two resolved types
    when both are known classes and they differ.

    MISSES (documented, not silently dropped): `.connect()` calls inside a
    `foreach`/generate loop over an array of agents resolve to the ARRAY
    member's declared type, which is usually right (`m_tl_agents[i].monitor...`
    still resolves via `m_tl_agents`) — but a connect whose LHS is a local
    variable (declared inside the method, not a class member) cannot be
    resolved at all and is silently skipped, same "miss rather than mis-flag"
    policy as the rest of this module.
    """
    members = _members(body)

    def resolve(ident: str) -> str | None:
        if ident in _SELF_REFS:
            return class_name
        type_text = members.get(ident)
        if type_text is None:
            return None
        m = re.match(r"(?:rand\s+|randc\s+)?(?:local\s+|protected\s+|static\s+|const\s+)*"
                     r"([A-Za-z_]\w*)", type_text)
        return m.group(1) if m else None

    edges = []
    for m in _CONNECT_CALL.finditer(body):
        src_type = resolve(m.group("lhs"))
        dst_type = resolve(m.group("rhs"))
        if not src_type or not dst_type or src_type == dst_type:
            continue
        if src_type not in known_classes or dst_type not in known_classes:
            continue
        if src_type in _LIBRARY_BASE_CLASSES or dst_type in _LIBRARY_BASE_CLASSES:
            continue
        snippet = re.sub(r"\s+", " ", m.group(0)).strip()
        edges.append(ComponentEdge(src_type, dst_type, "analysis_port", evidence=snippet))
    return edges


# ── 4. Factory overrides ─────────────────────────────────────────────────────

# Form A: `[pkg::]ORIG::type_id::set_(type|inst)_override[_by_type](NEW::get_type[()]);`
_FACTORY_TYPEID = re.compile(
    r"(?:[A-Za-z_]\w*::)*(?P<orig>[A-Za-z_]\w*)::type_id::set_(?:type|inst)_override(?:_by_type)?\s*\(\s*"
    r"(?:[A-Za-z_]\w*::)*(?P<new>[A-Za-z_]\w*)::get_type\s*\(?\s*\)?",
)
# Form B: `set_(type|inst)_override_by_type([pkg::]ORIG::get_type[()], [pkg::]NEW::get_type[()]);`
_FACTORY_BY_TYPE = re.compile(
    r"\bset_(?:type|inst)_override_by_type\s*\(\s*"
    r"(?:[A-Za-z_]\w*::)*(?P<orig>[A-Za-z_]\w*)::get_type\s*\(?\s*\)?\s*,\s*"
    r"(?:[A-Za-z_]\w*::)*(?P<new>[A-Za-z_]\w*)::get_type\s*\(?\s*\)?",
)


def factory_override_edges(class_name: str, body: str, known_classes: set) -> list[ComponentEdge]:
    """
    Both call forms used across OpenTitan/Caliptra are matched (see the two
    regexes above). Edge direction is orig -> new: a change to ORIG's
    interface is at risk of being silently masked or broken by NEW, which is
    what will actually run.

    `class_name` is the class the override statement was FOUND in (almost
    always a `*_env`/`*_test` build_phase, not orig or new themselves) — kept
    on the edge only as evidence context, since the edge itself is orig->new
    regardless of which env class wired the override.

    BUG FOUND IN VALIDATION, FIXED HERE: an earlier version did not check
    `known_classes` at all (unlike the other three edge kinds), so orig/new
    tokens that are not themselves nodes in the graph (e.g. `csr_base_seq`,
    found on the real OpenTitan tree in a file matched into the corpus only
    because it shares a file with a UVM-keyword-matching class) produced
    edges whose endpoints `blast-radius`/`analyze` cannot look up at all —
    association_edges.json must stay self-consistent with node_types.json's
    node universe for `--show-associations` to be usable on every src it
    lists.
    """
    edges = []
    for pat in (_FACTORY_TYPEID, _FACTORY_BY_TYPE):
        for m in pat.finditer(body):
            orig, new = m.group("orig"), m.group("new")
            if orig == new:
                continue
            if orig not in known_classes or new not in known_classes:
                continue
            if orig in _LIBRARY_BASE_CLASSES or new in _LIBRARY_BASE_CLASSES:
                continue
            snippet = re.sub(r"\s+", " ", m.group(0)).strip()
            edges.append(ComponentEdge(orig, new, "factory_override",
                                        evidence=f"[{class_name}] {snippet}"))
    return edges


# ── Corpus-level driver ──────────────────────────────────────────────────────

def build_corpus(files: list[str], known_classes: set) -> dict[str, str]:
    """
    One pass over `files`: for every class declared anywhere in the corpus
    that is also in `known_classes` (normally node_types.json's keys, i.e.
    already in the inheritance graph), extract its body text ONCE.

    A class is only looked up by name via `extract_class_body`, which is
    O(file size) per call — calling it once per (file, known class) pair
    would be O(files * classes), so instead this scans each file's own
    `class <name>` headers directly and keeps only the ones already known,
    which is the same single linear pass `extract_class_body` already does
    internally, just not repeated per class.
    """
    corpus: dict[str, str] = {}
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        t = strip_comments(text)
        for m in _CLASS_HEAD.finditer(t):
            name = m.group(1)
            if name not in known_classes or name in corpus:
                continue
            body = extract_class_body(text, name)
            if body is not None:
                corpus[name] = body
    return corpus


def all_component_edges(table: dict[str, str], known_classes: set) -> list[ComponentEdge]:
    """All four edge kinds (member handles, config_db, analysis-port, factory
    overrides) over a corpus built by `build_corpus`. `bind` edges are NOT
    included here — they are a separate RTL-module-keyed structure
    (type_annotator.build_bind_index / bind_index.json), not a class-to-class
    edge, and stay on their own existing path."""
    edges: list[ComponentEdge] = []
    for class_name, body in table.items():
        edges += member_handle_edges(class_name, body, known_classes)
        edges += analysis_port_edges(class_name, body, known_classes)
        edges += factory_override_edges(class_name, body, known_classes)
    edges += config_db_edges(table)
    return edges


def edges_to_adjacency(edges: list[ComponentEdge]) -> dict[str, list[dict]]:
    """
    Group edges by source class for storage/loading (association_edges.json):
    { src: [{"dst":, "kind":, "via":, "evidence":}, ...] }. Multiple edges
    between the same (src, dst, kind) are kept distinct (different `via`/
    `evidence`) rather than collapsed, since the evidence is exactly what
    item 4 (citations) needs.
    """
    out: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        out[e.src].append({"dst": e.dst, "kind": e.kind, "via": e.via, "evidence": e.evidence})
    return dict(sorted(out.items()))
