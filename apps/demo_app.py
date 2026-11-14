"""Interactive Streamlit Demo for GenAI Exploration.

Run with:
    streamlit run apps/demo_app.py
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import streamlit as st
    from src.core.llm_client import HuggingFaceLocalLLM

    st.set_page_config(page_title="GenAI Studio", layout="centered")
    st.title("🤖 GenAI Interactive Studio")

    prompt = st.text_area("Enter your prompt:", "Artificial Intelligence in 2026 will")
    max_len = st.slider("Max Length", min_value=20, max_value=150, value=50)

    if st.button("Generate Text"):
        with st.spinner("Generating..."):
            llm = HuggingFaceLocalLLM(model_name="gpt2")
            outputs = llm.generate(prompt=prompt, max_length=max_len)
            st.subheader("Generated Output:")
            st.write(outputs[0])

except ImportError:
    print("Streamlit not installed. Install requirements with `pip install -r requirements.txt` to run web UI.")
