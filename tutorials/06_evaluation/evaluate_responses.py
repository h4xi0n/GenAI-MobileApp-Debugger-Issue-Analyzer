"""Tutorial 06: Evaluation of LLM Outputs (Exact Match, Levenshtein, and Keyword Coverage)."""

from typing import List

def keyword_coverage_score(generated_text: str, expected_keywords: List[str]) -> float:
    """Calculate ratio of expected key terms present in generated answer."""
    text_lower = generated_text.lower()
    matches = sum(1 for kw in expected_keywords if kw.lower() in text_lower)
    return matches / len(expected_keywords) if expected_keywords else 0.0

def run_eval_demo():
    generated = "Retrieval-Augmented Generation helps reduce hallucinations by fetching relevant factual documents."
    expected_keywords = ["hallucinations", "factual", "retrieval"]

    score = keyword_coverage_score(generated, expected_keywords)
    print(f"Generated Text: {generated}")
    print(f"Target Keywords: {expected_keywords}")
    print(f"Coverage Score: {score * 100:.1f}%")

if __name__ == "__main__":
    run_eval_demo()
