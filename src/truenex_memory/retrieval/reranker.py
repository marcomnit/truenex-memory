"""Second-stage reranking with a cross-encoder.

The first stage (FTS5/BM25 plus dense cosine) decides which candidates exist;
it cannot decide which one answers the question, because it never looks at the
query and a candidate together. A cross-encoder does exactly that: it scores
each (query, chunk) pair jointly.

READ THIS BEFORE TRUSTING IT: the improvement did NOT replicate on a frozen
set. Measured 2026-08-21 on two independent sets of 30 questions, each written
by a different agent from real documents with the explicit constraint of
avoiding the document's own words — the case a lexical ranker cannot serve:

                                     development set   FROZEN set
    stage 1 only (current pipeline)      2/30             2/30
    + cross-encoder, fused               3/30             2/30
    + cross-encoder, replacing           6/30             2/30

The candidates are not the problem: the union of the top-200 lexical and
top-200 dense candidates holds the target in 15/30 cases on the development
set and 13/30 on the frozen one. On the development set the cross-encoder
converted 4 of those 15; on the frozen set it converted 0 of 13. So the gain
seen while building this was development-set-specific, and the honest reading
today is that this module does not yet earn its cost.

It is kept, off by default, because the code is small and the diagnosis it
enables is worth having: the gap between "the target is among the candidates"
(43%) and "the system returns it" (7%) is real and large, and something has to
close it. A cross-encoder over these candidates is not that something.

Also measured, and worth not rediscovering: reranking memory nodes as if they
were passages collapsed `bug-report` from 6/6 to 2/6, and letting the
reranker's order REPLACE the first stage cost 5 cases on the committed set
(docs-it 10/14 -> 5/14) — hence the memory exemption and the "fuse" default.

OFF BY DEFAULT. The model is ~568M parameters: loading it is not something a
CLI invocation should pay for silently. Set TRUENEX_RERANKER=on to enable, and
TRUENEX_RERANKER_MODEL to override the model.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)

# Multilingual, handles Italian and English, and behaves acceptably on code
# where the answer sits in a docstring or a comment. Recommended
# independently by two of the three agents consulted on 2026-08-21.
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

# How many candidates the cross-encoder scores. Every candidate is a forward
# pass, so this is the latency knob: the union of 200+200 first-stage
# candidates averaged 307 unique chunks per query, which is more than the
# ranking needs and enough to matter on a CPU-only machine.
RERANK_CANDIDATE_LIMIT = 120

# Truncation for the candidate text. The model's window is 512 tokens; longer
# chunks are cut rather than dropped, because a chunk whose beginning answers
# the question should still win.
RERANK_MAX_LENGTH = 512


@dataclass(frozen=True)
class RerankerConfig:
    """Resolved reranker settings for one search."""

    enabled: bool
    model_name: str
    candidate_limit: int
    # "fuse" merges the reranker's order with the first stage's (safe: the
    # only configuration measured positive on BOTH eval sets). "replace"
    # takes the reranker's order outright — much stronger on questions
    # phrased away from the document's words, but it cost 5 cases on the
    # lexically-worded committed set.
    mode: str = "fuse"
    # How many contiguous chunks to place beside each candidate when scoring
    # it. 0 = the chunk alone. Changes neither the index nor the hit returned:
    # only the text the score is computed on.
    context_span: int = 1


def reranker_config_from_env() -> RerankerConfig:
    """Read the reranker settings, defaulting to disabled."""

    enabled = os.environ.get("TRUENEX_RERANKER", "off").strip().lower() == "on"
    model_name = os.environ.get("TRUENEX_RERANKER_MODEL", DEFAULT_RERANKER_MODEL).strip()
    raw_limit = os.environ.get("TRUENEX_RERANKER_CANDIDATES", "").strip()
    try:
        limit = int(raw_limit) if raw_limit else RERANK_CANDIDATE_LIMIT
    except ValueError:
        limit = RERANK_CANDIDATE_LIMIT
    try:
        span = int(os.environ.get("TRUENEX_RERANKER_CONTEXT", "1"))
    except ValueError:
        span = 1
    span = max(0, min(span, 4))
    mode = os.environ.get("TRUENEX_RERANKER_MODE", "fuse").strip().lower()
    if mode not in ("fuse", "replace"):
        mode = "fuse"
    return RerankerConfig(
        enabled=enabled,
        model_name=model_name or DEFAULT_RERANKER_MODEL,
        candidate_limit=max(1, limit),
        mode=mode,
        context_span=span,
    )


class CrossEncoderReranker:
    """Lazily loaded cross-encoder, reused across searches in one process."""

    _cache: dict[str, object] = {}

    def __init__(self, model_name: str = DEFAULT_RERANKER_MODEL) -> None:
        self.model_name = model_name

    def _model(self):
        """Load once per model name per process, or return None if unusable.

        Returning None rather than raising is deliberate: a missing model or a
        machine without the optional dependency must degrade to first-stage
        ranking, never break a search.
        """

        if self.model_name in self._cache:
            return self._cache[self.model_name]
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            logger.warning(
                "reranking requested but sentence-transformers is missing: "
                "install truenex-memory[semantic]"
            )
            self._cache[self.model_name] = None
            return None
        device = None
        try:
            import torch

            if torch.cuda.is_available():
                device = "cuda"
        except Exception:  # pragma: no cover - torch always present with s-t
            device = None
        try:
            # A cross-encoder is one forward pass per candidate, so the device
            # is the difference between an interactive search and an
            # unusable one: measured 3.4 s per query on CPU against 0.4 s on
            # a consumer GPU for the same 120 candidates.
            model = CrossEncoder(
                self.model_name, max_length=RERANK_MAX_LENGTH, device=device
            )
        except Exception as error:  # pragma: no cover - model/IO failure
            logger.warning("reranker %s unavailable: %s", self.model_name, error)
            model = None
        self._cache[self.model_name] = model
        return model

    def score(self, query: str, texts: Sequence[str]) -> list[float] | None:
        """Joint (query, text) scores, or None when the model is unusable."""

        if not texts:
            return []
        model = self._model()
        if model is None:
            return None
        try:
            scores = model.predict(
                [[query, text] for text in texts], show_progress_bar=False
            )
        except Exception as error:  # pragma: no cover - runtime failure
            logger.warning("reranking failed, keeping first-stage order: %s", error)
            return None
        return [float(value) for value in scores]
