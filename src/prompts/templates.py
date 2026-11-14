"""Reusable prompt templates and system prompts for GenAI tasks."""

from typing import Dict, Any

class PromptTemplate:
    """Simple template formatter for dynamic prompts."""

    def __init__(self, template: str):
        self.template = template

    def format(self, **kwargs: Any) -> str:
        """Format template with keyword arguments."""
        return self.template.format(**kwargs)


SYSTEM_PROMPTS = {
    "qa_assistant": "You are a helpful, precise AI assistant answering user questions based strictly on context.",
    "summarizer": "You are an expert summarizer. Condense the text into concise key points without losing fidelity.",
    "code_assistant": "You are an expert software engineer. Provide clean, well-documented, idiomatic code.",
    "rag_context": (
        "Context:\n{context}\n\n"
        "Question: {question}\n\n"
        "Answer the question based only on the context provided above. If unknown, state that you do not know."
    )
}
