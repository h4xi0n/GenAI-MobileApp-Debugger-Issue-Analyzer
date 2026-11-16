"""Retrieval-Augmented Generation (RAG) and Android SDK Knowledge Base module.

Provides canonical Android SDK architectural issue indices, BM25Okapi lexical retrieval,
deterministic offline embeddings, and Reciprocal Rank Fusion (RRF) hybrid search.

Author: Arun Krishnan
"""

from .android_kb import (
    CANONICAL_KNOWLEDGE_DOCUMENTS,
    KnowledgeCategory,
    get_canonical_knowledge_base,
)
from .embeddings import TextEmbedder
from .hybrid_retriever import (
    BM25OkapiRetriever,
    BM25Retriever,
    DenseVectorRetriever,
    DeterministicHashEmbedder,
    HybridRetriever,
    android_tokenize,
)
from .retriever import SimpleInMemoryVectorStore

__all__ = [
    "CANONICAL_KNOWLEDGE_DOCUMENTS",
    "BM25OkapiRetriever",
    "BM25Retriever",
    "DenseVectorRetriever",
    "DeterministicHashEmbedder",
    "HybridRetriever",
    "KnowledgeCategory",
    "SimpleInMemoryVectorStore",
    "TextEmbedder",
    "android_tokenize",
    "get_canonical_knowledge_base",
]
