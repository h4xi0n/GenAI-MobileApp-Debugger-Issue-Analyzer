"""In-memory vector store & retrieval mechanism for semantic search."""

from typing import List, Tuple
import numpy as np

class SimpleInMemoryVectorStore:
    """Lightweight in-memory vector store with cosine similarity retrieval."""

    def __init__(self):
        self.documents: List[str] = []
        self.embeddings: List[np.ndarray] = []

    def add_documents(self, docs: List[str], embeddings: np.ndarray) -> None:
        """Add documents and their pre-computed embeddings to store."""
        for doc, emb in zip(docs, embeddings):
            self.documents.append(doc)
            self.embeddings.append(emb)

    def similarity_search(self, query_emb: np.ndarray, top_k: int = 3) -> List[Tuple[str, float]]:
        """Retrieve top_k documents most similar to query embedding."""
        if not self.embeddings:
            return []

        emb_matrix = np.array(self.embeddings)
        # Cosine similarity calculation: (A . B) / (||A|| * ||B||)
        norm_query = np.linalg.norm(query_emb)
        norm_matrix = np.linalg.norm(emb_matrix, axis=1)

        sims = np.dot(emb_matrix, query_emb) / (norm_matrix * norm_query + 1e-10)
        top_indices = np.argsort(sims)[::-1][:top_k]

        return [(self.documents[idx], float(sims[idx])) for idx in top_indices]
