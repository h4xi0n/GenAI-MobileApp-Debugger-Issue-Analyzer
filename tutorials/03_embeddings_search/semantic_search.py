"""Tutorial 03: Vector Embeddings and Semantic Search."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.rag.embeddings import TextEmbedder
from src.rag.retriever import SimpleInMemoryVectorStore

def run_semantic_search():
    documents = [
        "Large language models are trained on massive text corpora to predict next tokens.",
        "Retrieval-Augmented Generation injects factual external knowledge into model context.",
        "Photosynthesis is the process used by plants to convert light energy into chemical energy.",
        "Convolutional Neural Networks are primarily used in computer vision tasks."
    ]

    embedder = TextEmbedder()
    print("Computing embeddings for documents...")
    doc_embeddings = embedder.embed_texts(documents)

    vector_store = SimpleInMemoryVectorStore()
    vector_store.add_documents(documents, doc_embeddings)

    query = "How do we ground LLM responses with external data?"
    print(f"\nQuery: '{query}'")
    query_emb = embedder.embed_query(query)

    top_matches = vector_store.similarity_search(query_emb, top_k=2)
    print("\nTop Matching Documents:")
    for rank, (doc, score) in enumerate(top_matches, start=1):
        print(f"[{rank}] Score: {score:.4f} | Content: {doc}")

if __name__ == "__main__":
    run_semantic_search()
