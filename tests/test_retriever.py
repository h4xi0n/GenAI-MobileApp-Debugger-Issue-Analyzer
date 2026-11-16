"""Unit and integration tests for Android SDK Knowledge Base, Hybrid Retriever (BM25 + Dense RRF),
prompt grounding injection, and server RAG search endpoints.

Author: Arun Krishnan
"""

import math
import re
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from apps.server import app
from src.core.models import KnowledgeCard
from src.prompts.templates import build_diagnostic_prompt
from src.rag.android_kb import (
    KnowledgeCategory,
    get_canonical_knowledge_base,
)
from src.rag.hybrid_retriever import (
    BM25OkapiRetriever,
    DenseVectorRetriever,
    DeterministicHashEmbedder,
    HybridRetriever,
    android_tokenize,
)

# =============================================================================
# 1. Android Knowledge Base Integrity Tests
# =============================================================================

def test_canonical_knowledge_base_integrity():
    """Asserts that the canonical knowledge base contains all 7 core architectural issue cards."""
    cards = get_canonical_knowledge_base()
    assert len(cards) >= 7

    doc_ids = {card.doc_id for card in cards}
    expected_ids = {
        "KB-COMPOSE-001",
        "KB-COROUTINE-001",
        "KB-LIFECYCLE-001",
        "KB-LEAK-001",
        "KB-MEDIA3-001",
        "KB-CAMERAX-001",
        "KB-COMPOSE-002",
    }
    assert expected_ids.issubset(doc_ids), f"Missing doc_ids: {expected_ids - doc_ids}"

    for card in cards:
        assert isinstance(card, KnowledgeCard)
        assert card.doc_id.startswith("KB-")
        assert len(card.title) > 5
        assert len(card.symptoms) > 10
        assert len(card.root_cause_explanation) > 15
        assert len(card.recommended_fix_pattern) > 15
        assert len(card.code_example_kotlin) > 10
        assert card.remediation_snippet is not None
        assert card.category in [c.value for c in KnowledgeCategory]
        assert card.confidence_score >= 0.0


# =============================================================================
# 2. Tokenizer & BM25 Lexical Retriever Tests
# =============================================================================

def test_android_tokenizer():
    """Asserts that android_tokenize correctly splits camelCase, package paths, and identifiers."""
    # Test camelCase splitting
    tokens = android_tokenize("NullPointerException")
    assert "nullpointerexception" in tokens
    assert "null" in tokens
    assert "pointer" in tokens
    assert "exception" in tokens

    # Test package path splitting
    pkg_tokens = android_tokenize("androidx.lifecycle.compose.collectAsStateWithLifecycle")
    assert "androidx" in pkg_tokens
    assert "lifecycle" in pkg_tokens
    assert "compose" in pkg_tokens
    assert "collectasstatewithlifecycle" in pkg_tokens
    assert "state" in pkg_tokens

    # Test empty & punctuation
    assert android_tokenize("") == []
    assert android_tokenize("   ::: ---   ") == []


def test_bm25_lexical_retriever():
    """Asserts BM25OkapiRetriever correctly scores and ranks documents based on lexical terms."""
    cards = get_canonical_knowledge_base()
    retriever = BM25OkapiRetriever(cards)

    # Query targeting Compose state recomposition
    results = retriever.retrieve("remember mutableStateOf recomposition loop", top_k=3)
    assert len(results) > 0
    top_card, top_score = results[0]
    assert top_card.doc_id == "KB-COMPOSE-001"
    assert top_score > 0.0

    # Query targeting CameraX ImageProxy
    cam_results = retriever.retrieve("ImageProxy close CameraX analyzer pipeline frozen", top_k=2)
    assert len(cam_results) > 0
    assert cam_results[0][0].doc_id == "KB-CAMERAX-001"

    # Category filter
    filtered = retriever.retrieve("memory leak", top_k=5, category="MEMORY_LEAKS")
    for doc, _ in filtered:
        assert doc.category == "MEMORY_LEAKS"

    # Graceful handling of empty or disjoint queries
    empty_results = retriever.retrieve("", top_k=3)
    assert len(empty_results) <= 3
    disjoint_results = retriever.retrieve("xyznonexistentterm12345", top_k=3)
    assert len(disjoint_results) <= 3
    for _, score in disjoint_results:
        assert score == 0.0


# =============================================================================
# 3. Dense Vector Retriever & Offline Embedder Tests
# =============================================================================

def test_dense_vector_retriever_offline():
    """Asserts DeterministicHashEmbedder and DenseVectorRetriever produce stable vector geometry offline."""
    embedder = DeterministicHashEmbedder(dimension=128)
    vec1 = embedder.embed_text("Kotlin Coroutines main looper frozen ANR")
    vec2 = embedder.embed_text("Kotlin Coroutines main looper frozen ANR")
    vec3 = embedder.embed_text("Jetpack Compose Scaffold innerPadding status bar overlap")

    # Determinism assertion
    assert np.allclose(vec1, vec2)
    assert not np.allclose(vec1, vec3)
    assert vec1.shape == (128,)

    # Unit norm assertion: ||v|| == 1.0
    norm1 = np.linalg.norm(vec1)
    assert math.isclose(norm1, 1.0, rel_tol=1e-5)

    # Vector store indexing and search
    cards = get_canonical_knowledge_base()
    dense_retriever = DenseVectorRetriever(cards, embedder=embedder)

    anr_results = dense_retriever.retrieve("ANR main looper input dispatching timed out", top_k=2)
    assert len(anr_results) > 0
    top_doc, top_sim = anr_results[0]
    assert top_doc.doc_id == "KB-COROUTINE-001"
    assert 0.0 <= top_sim <= 1.0


# =============================================================================
# 4. Reciprocal Rank Fusion & Hybrid Retriever Tests
# =============================================================================

def test_rrf_rank_fusion_and_hybrid_scoring():
    """Asserts HybridRetriever correctly executes RRF fusion and normalizes confidence scores."""
    hybrid = HybridRetriever(k_constant=60, weight_dense=0.6, weight_bm25=0.4)

    # Retrieve for Media3 live window drift
    results = hybrid.retrieve("ExoPlayer ERROR_CODE_BEHIND_LIVE_WINDOW surface destroyed", top_k=2)
    assert len(results) == 2
    assert results[0].doc_id == "KB-MEDIA3-001"
    assert 0.0 <= results[0].confidence_score <= 1.0
    assert results[0].match_type == "hybrid"
    assert results[0].remediation_snippet is not None

    # Retrieve for Fragment transaction onSaveInstanceState
    lifecycle_results = hybrid.search(
        "IllegalStateException Can not perform this action after onSaveInstanceState",
        top_k=1,
    )
    assert len(lifecycle_results) == 1
    assert lifecycle_results[0].doc_id == "KB-LIFECYCLE-001"
    assert lifecycle_results[0].confidence_score > 0.8

    # Category filtering
    compose_only = hybrid.retrieve("padding scaffold overlap", top_k=5, category="COMPOSE")
    for card in compose_only:
        assert card.category == "COMPOSE"


# =============================================================================
# 5. Prompt Grounding Injection Tests
# =============================================================================

def test_prompt_injection_with_grounding_cards():
    """Asserts build_diagnostic_prompt formats and injects retrieved KnowledgeCards cleanly."""
    cards = get_canonical_knowledge_base()[:2]
    cards[0].confidence_score = 0.95

    prompt = build_diagnostic_prompt(
        package_name="com.example.mobileapp",
        log_entries="11-16 12:00:00 100/100 E AndroidRuntime: FATAL EXCEPTION",
        screen_state="CRASH_DIALOG",
        grounding_cards=cards,
    )

    assert "### Grounded Android SDK Knowledge & Architectural References:" in prompt
    assert "Reference 1: [KB-COMPOSE-001]" in prompt
    assert "95%" in prompt
    assert "recomposition" in prompt.lower()
    assert "```kotlin" in prompt
    assert "androidx.lifecycle.compose" in prompt or "collectAsStateWithLifecycle" in prompt


# =============================================================================
# 6. Server Integration & RAG REST Endpoints Tests
# =============================================================================

def test_server_rag_endpoints_and_diagnostic_flow():
    """Tests GET/POST /api/rag/search endpoints and validates grounding references in /api/diagnose."""
    client = TestClient(app)

    # 1. Test GET /api/rag/search
    get_resp = client.get("/api/rag/search?query=NullPointerException+mutableStateOf&top_k=2")
    assert get_resp.status_code == 200
    cards = get_resp.json()
    assert isinstance(cards, list)
    assert len(cards) == 2
    assert cards[0]["doc_id"] == "KB-COMPOSE-001"
    assert "confidence_score" in cards[0]

    # 2. Test POST /api/rag/search
    post_resp = client.post(
        "/api/rag/search",
        json={"query": "ANR in main looper runBlocking", "top_k": 1, "category": "COROUTINES_ANR"},
    )
    assert post_resp.status_code == 200
    post_cards = post_resp.json()
    assert len(post_cards) == 1
    assert post_cards[0]["doc_id"] == "KB-COROUTINE-001"

    # 3. Test missing query 400 validation
    bad_resp = client.get("/api/rag/search")
    assert bad_resp.status_code == 400

    # 4. Test /api/diagnose returns grounding_references
    diag_resp = client.post("/api/diagnose")
    assert diag_resp.status_code == 200
    report_data = diag_resp.json()
    assert "grounding_references" in report_data
    assert isinstance(report_data["grounding_references"], list)
    assert len(report_data["grounding_references"]) > 0
    top_grounding = report_data["grounding_references"][0]
    assert "doc_id" in top_grounding
    assert "title" in top_grounding
    assert "confidence_score" in top_grounding


# =============================================================================
# 7. Strict Privacy Compliance Test
# =============================================================================

def test_strict_privacy_compliance_rag():
    """Audits RAG module files and ensures zero unauthorized personal background disclosures.

    Sole permitted author attribution: Arun Krishnan.
    Prohibited disclosures: employer names, university names, geographic locations, hardware specs.
    """
    repo_root = Path(__file__).resolve().parent.parent
    target_files = [
        repo_root / "src" / "rag" / "android_kb.py",
        repo_root / "src" / "rag" / "hybrid_retriever.py",
        repo_root / "src" / "rag" / "__init__.py",
        repo_root / "src" / "prompts" / "templates.py",
        repo_root / "apps" / "server.py",
        repo_root / "tests" / "test_retriever.py",
    ]

    # Forbidden patterns (case-insensitive)
    forbidden_tokens = [
        r"\bVMware\b",
        r"\bBroadcom\b",
        r"\bAirWatch\b",
        r"\bGeorgia Tech\b",
        r"\bBangalore\b",
        r"\bBengaluru\b",
        r"\bRTX\s*4090\b",
        r"\bRyzen\b",
        r"\bDell\b",
    ]

    for file_path in target_files:
        assert file_path.exists(), f"File {file_path} must exist"
        content = file_path.read_text(encoding="utf-8")

        for pattern in forbidden_tokens:
            matches = re.findall(pattern, content, flags=re.IGNORECASE)
            assert not matches, f"Privacy violation in {file_path.name}: found forbidden token matching '{pattern}': {matches}"

        # Assert author attribution where author line is present
        if "Author:" in content:
            assert "Author: Arun Krishnan" in content, f"Author line in {file_path.name} must strictly be 'Arun Krishnan'"
