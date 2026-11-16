"""Embeddings generator using Sentence Transformers or compatible models."""


import numpy as np

from config.settings import DEFAULT_EMBEDDING_MODEL
from src.core.logger import setup_logger

logger = setup_logger(__name__)

class TextEmbedder:
    """Generates dense vector embeddings for text passages and queries."""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        """Lazy load sentence_transformers model on first use."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Encode list of strings into embedding vectors."""
        return self.model.encode(texts, convert_to_numpy=True)

    def embed_query(self, query: str) -> np.ndarray:
        """Encode a single query string into an embedding vector."""
        return self.model.encode([query], convert_to_numpy=True)[0]
