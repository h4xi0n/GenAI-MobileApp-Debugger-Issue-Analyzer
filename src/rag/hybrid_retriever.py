"""Hybrid RAG Retrieval Engine uniting BM25 lexical token matching and Dense vector similarity.

Implements domain-aware Android tokenization (camelCase & package splitting), pure Python/NumPy
BM25Okapi, deterministic offline vector embeddings, and Reciprocal Rank Fusion (RRF).

Author: Arun Krishnan
"""

import hashlib
import math
import re

import numpy as np

from src.core.logger import setup_logger
from src.core.models import KnowledgeCard
from src.rag.android_kb import get_canonical_knowledge_base

logger = setup_logger("hybrid_retriever")


def android_tokenize(text: str) -> list[str]:
    """Domain-aware Android tokenizer splitting camelCase, package paths, and identifiers.

    Splits strings like 'NullPointerException' into ['null', 'pointer', 'exception', 'nullpointerexception']
    and 'androidx.lifecycle.compose' into individual package and symbol tokens.

    Args:
        text: Input string to tokenize.

    Returns:
        List of normalized, lowercased alphanumeric tokens.
    """
    if not text:
        return []

    # Replace punctuation, operators, and slashes with spaces
    sanitized = re.sub(r"[^\w\s]", " ", text)

    raw_tokens = sanitized.split()
    tokens: list[str] = []

    for token in raw_tokens:
        lower_token = token.lower()
        if not lower_token or len(lower_token) < 2:
            continue

        # Split camelCase and PascalCase
        # e.g., 'FeedScreen' -> 'Feed Screen', 'XMLParser' -> 'XML Parser'
        split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", token)
        split_camel = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", split_camel)
        sub_words = [w.lower() for w in split_camel.split() if len(w) > 1]

        tokens.append(lower_token)
        for sw in sub_words:
            if sw != lower_token:
                tokens.append(sw)

    return tokens


class BM25OkapiRetriever:
    """Pure-Python / NumPy implementation of the BM25Okapi lexical retrieval algorithm.

    Utilizes Robertson-Spärck Jones (RSJ) inverse document frequency smoothing and
    configurable document length normalization.
    """

    def __init__(
        self,
        documents: list[KnowledgeCard] | None = None,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        """Initializes BM25 corpus and pre-computes document statistics.

        Args:
            documents: List of KnowledgeCard models to index.
            k1: Term frequency saturation parameter (default: 1.5).
            b: Document length normalization parameter (default: 0.75).
        """
        self.k1 = k1
        self.b = b
        self.documents: list[KnowledgeCard] = documents or []
        self.corpus_size = len(self.documents)
        self.doc_token_counts: list[int] = []
        self.avg_doc_len: float = 0.0
        self.doc_term_freqs: list[dict[str, int]] = []
        self.idf: dict[str, float] = {}

        if self.documents:
            self._build_index()

    def _build_index(self) -> None:
        """Constructs term frequencies, inverted index, and smoothed IDF scores.

        Processes all corpus documents through the Android domain tokenizer, records term
        frequencies, calculates average corpus document length, and computes Robertson-Spärck
        Jones (RSJ) smoothed inverse document frequency values.
        """
        df: dict[str, int] = {}
        total_tokens = 0

        for doc in self.documents:
            # Build representative text representation
            content = (
                f"{doc.title} {doc.category} {' '.join(doc.exception_patterns)} "
                f"{doc.symptoms} {doc.root_cause_explanation} {doc.recommended_fix_pattern} "
                f"{doc.code_example_kotlin}"
            )
            tokens = android_tokenize(content)
            doc_len = len(tokens)
            self.doc_token_counts.append(doc_len)
            total_tokens += doc_len

            term_freq: dict[str, int] = {}
            for t in tokens:
                term_freq[t] = term_freq.get(t, 0) + 1
            self.doc_term_freqs.append(term_freq)

            for term in term_freq:
                df[term] = df.get(term, 0) + 1

        self.avg_doc_len = (total_tokens / self.corpus_size) if self.corpus_size > 0 else 0.0

        # Calculate smoothed IDF: ln(1 + (N - n + 0.5) / (n + 0.5))
        for term, freq in df.items():
            self.idf[term] = math.log(1.0 + (self.corpus_size - freq + 0.5) / (freq + 0.5))

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        category: str | None = None,
    ) -> list[tuple[KnowledgeCard, float]]:
        """Scores all indexed documents against query terms using BM25Okapi.

        Args:
            query: Free-form query text or exception message.
            top_k: Maximum number of candidate results to return.
            category: Optional category filter.

        Returns:
            Ranked list of tuples containing (KnowledgeCard, bm25_score).
        """
        if not self.documents:
            return []

        query_tokens = android_tokenize(query)
        if not query_tokens:
            candidates = [
                (doc, 0.0) for doc in self.documents
                if category is None or doc.category.upper() == category.upper()
            ]
            return candidates[:top_k]

        scores: list[tuple[KnowledgeCard, float]] = []

        for idx, doc in enumerate(self.documents):
            if category and doc.category.upper() != category.upper():
                continue

            doc_len = self.doc_token_counts[idx]
            term_freqs = self.doc_term_freqs[idx]
            score = 0.0

            for q_term in query_tokens:
                if q_term not in term_freqs:
                    continue

                f = term_freqs[q_term]
                idf = self.idf.get(q_term, 0.0)
                len_norm = 1.0 - self.b + self.b * (doc_len / (self.avg_doc_len + 1e-9))
                tf_component = (f * (self.k1 + 1.0)) / (f + self.k1 * len_norm)
                score += idf * tf_component

            scores.append((doc, score))

        # Sort descending by score
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


# Alias for compatibility
BM25Retriever = BM25OkapiRetriever


class DeterministicHashEmbedder:
    """100% offline, deterministic pseudo-embedding vectorizer.

    Projects text into a fixed-dimensional unit vector using word tokens and character
    n-grams hashed deterministically via SHA-256. Guarantees consistent vector geometry
    across different platforms and processes with zero external model weights.
    """

    def __init__(self, dimension: int = 128):
        """Initializes embedder with target vector dimensionality."""
        self.dimension = dimension

    def _hash_token(self, token: str) -> tuple[int, float]:
        """Hashes token into a fixed dimension slot index and sign (+1.0 or -1.0).

        Args:
            token: Substring or token to hash.

        Returns:
            Tuple of (slot_index, sign_multiplier) for vector projection.
        """
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        # Use first 4 bytes for index, 5th byte for sign
        slot = int.from_bytes(digest[:4], byteorder="big") % self.dimension
        sign = 1.0 if (digest[4] % 2 == 0) else -1.0
        return slot, sign

    def embed_text(self, text: str) -> np.ndarray:
        """Converts text into an L2-normalized dense embedding vector.

        Combines hashed word tokens and character trigrams for subword morphology,
        projected into a unit-normalized vector space.

        Args:
            text: Input string to embed.

        Returns:
            L2-normalized float32 NumPy array of shape (dimension,).
        """
        vec = np.zeros(self.dimension, dtype=np.float32)
        tokens = android_tokenize(text)
        if not tokens:
            return vec

        # Incorporate word tokens
        for token in tokens:
            slot, sign = self._hash_token(token)
            vec[slot] += sign * 1.5

            # Incorporate character tri-grams for subword morphology
            if len(token) >= 3:
                for i in range(len(token) - 2):
                    trigram = token[i : i + 3]
                    tg_slot, tg_sign = self._hash_token(trigram)
                    vec[tg_slot] += tg_sign * 0.5

        # L2-normalize vector: v / ||v||
        norm = np.linalg.norm(vec)
        if norm > 1e-9:
            vec = vec / norm
        return vec


class DenseVectorRetriever:
    """Semantic vector search retriever utilizing cosine similarity over document embeddings."""

    def __init__(
        self,
        documents: list[KnowledgeCard] | None = None,
        embedder: DeterministicHashEmbedder | None = None,
    ):
        """Initializes vector store and generates document embeddings."""
        self.documents: list[KnowledgeCard] = documents or []
        self.embedder = embedder or DeterministicHashEmbedder(dimension=128)
        self.doc_embeddings: np.ndarray | None = None

        if self.documents:
            self._index_documents()

    def _index_documents(self) -> None:
        """Computes and stores normalized dense vectors for all corpus documents.

        Concatenates document metadata (title, category, exceptions, symptoms, root cause,
        and remediation) and projects each document into the dense vector space.
        """
        vectors = []
        for doc in self.documents:
            text = (
                f"{doc.title} {doc.category} {' '.join(doc.exception_patterns)} "
                f"{doc.symptoms} {doc.root_cause_explanation} {doc.recommended_fix_pattern}"
            )
            vec = self.embedder.embed_text(text)
            vectors.append(vec)
        self.doc_embeddings = np.array(vectors, dtype=np.float32)

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        category: str | None = None,
    ) -> list[tuple[KnowledgeCard, float]]:
        """Performs cosine similarity search against query vector.

        Args:
            query: Input search text.
            top_k: Maximum candidate count.
            category: Optional category filter.

        Returns:
            Ranked list of tuples (KnowledgeCard, cosine_similarity).
        """
        if not self.documents or self.doc_embeddings is None:
            return []

        query_vec = self.embedder.embed_text(query)
        q_norm = np.linalg.norm(query_vec)
        if q_norm < 1e-9:
            candidates = [
                (doc, 0.0) for doc in self.documents
                if category is None or doc.category.upper() == category.upper()
            ]
            return candidates[:top_k]

        # Cosine similarity: (A . B) / (||A|| * ||B||)
        # Note: doc_embeddings and query_vec are already unit normalized
        dot_products = np.dot(self.doc_embeddings, query_vec)

        results: list[tuple[KnowledgeCard, float]] = []
        for idx, doc in enumerate(self.documents):
            if category and doc.category.upper() != category.upper():
                continue
            sim = float(dot_products[idx])
            results.append((doc, max(sim, 0.0)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]


class HybridRetriever:
    """Production-grade Hybrid RAG retriever uniting BM25 and Dense Search with Reciprocal Rank Fusion.

    Uses RRF formula: RRF(d) = sum(w_m / (k + rank_m(d))) for m in {bm25, dense}.
    Normalizes candidate scores to [0.0, 1.0] confidence ratings.
    """

    def __init__(
        self,
        documents: list[KnowledgeCard] | None = None,
        k_constant: int = 60,
        weight_dense: float = 0.6,
        weight_bm25: float = 0.4,
    ):
        """Initializes HybridRetriever with BM25 and Dense indexes.

        Args:
            documents: KnowledgeCard corpus (defaults to canonical Android knowledge base).
            k_constant: Reciprocal Rank Fusion smoothing parameter (default: 60).
            weight_dense: Weight assigned to semantic vector rankings (default: 0.6).
            weight_bm25: Weight assigned to lexical BM25 rankings (default: 0.4).
        """
        self.documents = documents if documents is not None else get_canonical_knowledge_base()
        self.k_constant = k_constant
        self.weight_dense = weight_dense
        self.weight_bm25 = weight_bm25

        self.bm25_retriever = BM25OkapiRetriever(self.documents)
        self.dense_retriever = DenseVectorRetriever(self.documents)
        logger.info(f"HybridRetriever initialized with {len(self.documents)} canonical documents.")

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        category: str | None = None,
    ) -> list[KnowledgeCard]:
        """Executes hybrid retrieval uniting lexical and dense search via RRF.

        Args:
            query: Search query, exception message, or logcat window context.
            top_k: Number of grounded cards to return.
            category: Optional category filter.

        Returns:
            Ranked list of KnowledgeCard instances with normalized confidence scores.
        """
        if not self.documents:
            return []

        # Retrieve full candidate rankings for both modalities
        bm25_results = self.bm25_retriever.retrieve(
            query=query,
            top_k=len(self.documents),
            category=category,
        )
        dense_results = self.dense_retriever.retrieve(
            query=query,
            top_k=len(self.documents),
            category=category,
        )

        # Build rank maps (1-based ranking)
        bm25_ranks: dict[str, int] = {
            doc.doc_id: rank for rank, (doc, _) in enumerate(bm25_results, 1)
        }
        dense_ranks: dict[str, int] = {
            doc.doc_id: rank for rank, (doc, _) in enumerate(dense_results, 1)
        }

        # Theoretical max RRF score when a document is ranked #1 in both modalities:
        # max_score = (w_dense + w_bm25) / (k + 1)
        max_theoretical_score = (self.weight_dense + self.weight_bm25) / (self.k_constant + 1.0)

        # Merge via Reciprocal Rank Fusion
        fused_scores: dict[str, float] = {}
        doc_map: dict[str, KnowledgeCard] = {d.doc_id: d for d in self.documents}

        candidate_ids = set(bm25_ranks.keys()) | set(dense_ranks.keys())
        for doc_id in candidate_ids:
            score = 0.0
            if doc_id in bm25_ranks:
                score += self.weight_bm25 / (self.k_constant + bm25_ranks[doc_id])
            if doc_id in dense_ranks:
                score += self.weight_dense / (self.k_constant + dense_ranks[doc_id])

            fused_scores[doc_id] = score

        # Sort candidate documents by RRF score descending
        sorted_candidates = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)

        grounded_cards: list[KnowledgeCard] = []
        for doc_id, rrf_score in sorted_candidates[:top_k]:
            original_doc = doc_map[doc_id]
            # Normalize confidence score to [0.0, 1.0]
            normalized_conf = min(round(rrf_score / max_theoretical_score, 4), 1.0)

            # Construct output card with updated confidence and metadata
            card = KnowledgeCard(
                doc_id=original_doc.doc_id,
                category=original_doc.category,
                title=original_doc.title,
                exception_patterns=original_doc.exception_patterns,
                symptoms=original_doc.symptoms,
                root_cause_explanation=original_doc.root_cause_explanation,
                recommended_fix_pattern=original_doc.recommended_fix_pattern,
                code_example_kotlin=original_doc.code_example_kotlin,
                doc_references=original_doc.doc_references,
                confidence_score=normalized_conf,
                doc_url=original_doc.doc_url or (original_doc.doc_references[0] if original_doc.doc_references else None),
                match_type="hybrid",
                remediation_snippet=original_doc.code_example_kotlin,
            )
            grounded_cards.append(card)

        return grounded_cards

    def search(
        self,
        query: str,
        top_k: int = 3,
        category: str | None = None,
    ) -> list[KnowledgeCard]:
        """Convenience alias for retrieve() executing hybrid BM25 + Dense RRF search.

        Args:
            query: Free-form search query, exception message, or logcat context.
            top_k: Maximum number of grounded knowledge cards to return.
            category: Optional category filter.

        Returns:
            Ranked list of KnowledgeCard instances with normalized confidence scores.
        """
        return self.retrieve(query=query, top_k=top_k, category=category)
