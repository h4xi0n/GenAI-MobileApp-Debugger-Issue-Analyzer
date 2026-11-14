"""Retrieval-Augmented Generation (RAG) module."""

from .embeddings import TextEmbedder
from .retriever import SimpleInMemoryVectorStore

__all__ = ["TextEmbedder", "SimpleInMemoryVectorStore"]
