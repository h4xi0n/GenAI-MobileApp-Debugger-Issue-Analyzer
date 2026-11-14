"""Tutorial 04: End-to-End Retrieval Augmented Generation (RAG) Pipeline."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.prompts.templates import SYSTEM_PROMPTS
from src.rag.embeddings import TextEmbedder
from src.rag.retriever import SimpleInMemoryVectorStore

def run_rag_pipeline():
    knowledge_base = [
        "Project Polaris is an enterprise GenAI platform launched in Q3 2026.",
        "Polaris uses hybrid dense and sparse retrieval with an average latency of 45ms.",
        "Team members of Polaris can authenticate using OAuth2.0 and API keys."
    ]

    embedder = TextEmbedder()
    store = SimpleInMemoryVectorStore()
    doc_embs = embedder.embed_texts(knowledge_base)
    store.add_documents(knowledge_base, doc_embs)

    question = "What is the latency of Project Polaris?"
    q_emb = embedder.embed_query(question)
    retrieved_docs = store.similarity_search(q_emb, top_k=1)

    context_str = "\n".join([doc for doc, _ in retrieved_docs])
    rag_prompt = SYSTEM_PROMPTS["rag_context"].format(
        context=context_str,
        question=question
    )

    print("=== ASSEMBLED RAG PROMPT FOR LLM ===")
    print(rag_prompt)
    print("====================================")

if __name__ == "__main__":
    run_rag_pipeline()
