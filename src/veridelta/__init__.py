"""VeriDelta: GraphRAG-grounded change impact analysis for UVM verification environments.

Given a changed UVM class, VeriDelta finds the downstream classes in the typed inheritance
graph (Stage 1), retrieves similar older commits (Stage 2) and asks a language model which
failure categories may affect each downstream class (Stage 3).
"""
__version__ = "0.1.0"

from .graphrag_pipeline import (          # noqa: E402  (convenience re-exports)
    MAX_BLAST_NODES,
    MAX_FEW_SHOT,
    MODEL,
    TYPE_PRIMARY_CATEGORY,
    VALID_CATEGORIES,
    build_prompt,
    build_stage1_context,
    get_blast_radius,
    predict,
    retrieve_similar_commits,
)

__all__ = [
    "__version__", "MAX_BLAST_NODES", "MAX_FEW_SHOT", "MODEL", "TYPE_PRIMARY_CATEGORY",
    "VALID_CATEGORIES", "build_prompt", "build_stage1_context", "get_blast_radius", "predict",
    "retrieve_similar_commits",
]
