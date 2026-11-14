# Generative AI Tutorial & Development Framework

A modular, production-ready Generative AI project architecture designed for hands-on experimentation, learning, and enterprise scalability.

---

## 🏛️ Architecture Overview

The repository is structured following industry-standard GenAI design patterns, separating configuration, data pipelines, reusable core services, tutorials, and applications:

```text
GenAI tutorial/
├── .env.example             # Template for API keys (OpenAI, HuggingFace, Anthropic, Gemini)
├── .gitignore               # GenAI & ML ignores (weights, vector DBs, caches, keys)
├── README.md                # Project documentation and architecture guide
├── requirements.txt         # Pinned and categorized Python dependencies
├── text_gen.py              # Root convenience entry point (backwards compatible)
│
├── config/                  # Centralized configuration
│   ├── config.yaml          # Hyperparameters, model selections, and RAG chunk sizes
│   └── settings.py          # Environment loader and settings accessor
│
├── data/                    # Data storage and indexing
│   ├── raw/                 # Unprocessed text datasets and documents (.gitkeep)
│   ├── processed/           # Cleaned, chunked, and tokenized data (.gitkeep)
│   └── vector_db/           # Local vector indices (ChromaDB, FAISS) (.gitkeep)
│
├── src/                     # Reusable Core Source Code
│   ├── __init__.py
│   ├── core/                # LLM pipeline wrappers, logger, inference providers
│   │   ├── llm_client.py    # Standardized HuggingFace & provider interface
│   │   └── logger.py        # Centralized logging format
│   ├── prompts/             # Prompt engineering and templates
│   │   └── templates.py     # System instructions and dynamic prompt classes
│   ├── rag/                 # Retrieval-Augmented Generation components
│   │   ├── embeddings.py    # Dense vector representations
│   │   └── retriever.py     # In-memory and persistent similarity search
│   ├── agents/              # Agent reasoning loops and tool execution
│   │   └── tools.py         # Extensible tool interfaces
│   ├── finetuning/          # Model fine-tuning (LoRA, QLoRA, PEFT)
│   └── evaluation/          # Evaluation benchmarks (metrics, ground truth testing)
│
├── tutorials/               # Step-by-Step Hands-on Learning Path
│   ├── 01_foundations/      # Text generation & sampling parameters (temperature, top-k/p)
│   ├── 02_prompt_engineering/ # Zero-shot, few-shot, and structured outputs
│   ├── 03_embeddings_search/ # Dense vectors and cosine similarity search
│   ├── 04_rag_pipeline/     # End-to-end RAG with context injection
│   ├── 05_agents_and_tools/ # Autonomous agent reasoning and tool usage
│   └── 06_evaluation/       # Response verification and metric scoring
│
├── apps/                    # Interactive Applications & UIs
│   └── demo_app.py          # Streamlit web interface for model interaction
│
└── tests/                   # Automated unit and integration tests
    └── test_text_gen.py     # Test suite
```

---

## 🚀 Quickstart Guide

### 1. Environment Setup

Create and activate a virtual environment:

```bash
# Windows (PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1

# Linux / macOS
python -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Credentials

Copy the environment template and insert your API keys if needed:

```bash
cp .env.example .env
```

---

## 📚 Running Tutorials

Each module in `tutorials/` is completely runnable:

```bash
# 01. Foundations (Transformers Text Generation)
python tutorials/01_foundations/text_generation.py

# 02. Prompt Engineering
python tutorials/02_prompt_engineering/prompt_engineering.py

# 03. Embeddings & Semantic Search
python tutorials/03_embeddings_search/semantic_search.py

# 04. RAG Pipeline
python tutorials/04_rag_pipeline/simple_rag.py

# 05. Agents & Tools
python tutorials/05_agents_and_tools/basic_agent.py

# 06. Output Evaluation
python tutorials/06_evaluation/evaluate_responses.py
```

---

## 🖥️ Launching the Interactive UI

Launch the Streamlit interface:

```bash
streamlit run apps/demo_app.py
```

---

## 🧪 Running Tests

Execute automated unit tests:

```bash
pytest tests/
```
