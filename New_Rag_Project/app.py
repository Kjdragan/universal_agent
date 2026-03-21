"""
Streamlit Web Interface for Multimodal RAG

A web application for uploading PDFs, indexing them with multimodal
embeddings, and querying the indexed content.

Usage:
    streamlit run app.py
"""

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import streamlit as st

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from ingest import ingest_pdf, setup_chroma
from rag import rag_query

# Configuration
CHROMA_DB_PATH = os.environ.get("CHROMA_DB_PATH", "./chroma_db")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "pdf_pages")

# Page config
st.set_page_config(
    page_title="Multimodal RAG Assistant",
    page_icon=":book:",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
CUSTOM_CSS = """
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1E3A5F;
        margin-bottom: 1rem;
    }
    .sub-header {
        font-size: 1.2rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .chat-container {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 1rem;
        margin: 0.5rem 0;
    }
    .user-message {
        background-color: #0078FF;
        color: white;
        padding: 0.8rem 1rem;
        border-radius: 15px 15px 5px 15px;
        margin: 0.5rem 0;
        display: inline-block;
        max-width: 80%;
        float: right;
        clear: both;
    }
    .assistant-message {
        background-color: #E8ECEF;
        color: #1E3A5F;
        padding: 0.8rem 1rem;
        border-radius: 15px 15px 15px 5px;
        margin: 0.5rem 0;
        display: inline-block;
        max-width: 80%;
        float: left;
        clear: both;
    }
    .source-tag {
        background-color: #28A745;
        color: white;
        padding: 0.2rem 0.5rem;
        border-radius: 5px;
        font-size: 0.8rem;
        margin-right: 0.3rem;
    }
    .error-message {
        background-color: #DC3545;
        color: white;
        padding: 1rem;
        border-radius: 10px;
        margin: 1rem 0;
    }
    .success-message {
        background-color: #28A745;
        color: white;
        padding: 1rem;
        border-radius: 10px;
        margin: 1rem 0;
    }
    .info-box {
        background-color: #E3F2FD;
        border-left: 4px solid #2196F3;
        padding: 1rem;
        margin: 1rem 0;
    }
</style>
"""


def apply_custom_css() -> None:
    """Apply custom CSS styling to the Streamlit app."""
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def init_session_state() -> None:
    """Initialize Streamlit session state variables."""
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "indexed_files" not in st.session_state:
        st.session_state.indexed_files = []
    if "is_indexing" not in st.session_state:
        st.session_state.is_indexing = False


def get_collection_info() -> dict:
    """Get information about the current ChromaDB collection."""
    try:
        _, collection = setup_chroma()
        count = collection.count()
        return {
            "exists": True,
            "document_count": count,
            "collection_name": COLLECTION_NAME
        }
    except Exception as e:
        return {
            "exists": False,
            "error": str(e),
            "document_count": 0
        }


def handle_pdf_upload(uploaded_file) -> None:
    """
    Handle PDF upload and indexing.

    Args:
        uploaded_file: Streamlit UploadedFile object.
    """
    if uploaded_file is None:
        return

    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(uploaded_file.getbuffer())
        tmp_path = tmp_file.name

    try:
        st.session_state.is_indexing = True

        with st.spinner(f"Indexing {uploaded_file.name}... This may take a few minutes."):
            # Create progress bar
            progress_bar = st.progress(0)
            status_text = st.empty()

            status_text.text("Extracting pages and generating embeddings...")

            # Run ingestion
            stats = ingest_pdf(tmp_path, verbose=False)

            progress_bar.progress(100)
            status_text.text("Indexing complete!")

            # Update session state
            if uploaded_file.name not in st.session_state.indexed_files:
                st.session_state.indexed_files.append(uploaded_file.name)

            st.success(
                f"Successfully indexed **{uploaded_file.name}**\n\n"
                f"- Pages processed: {stats['pages_processed']}\n"
                f"- Embeddings generated: {stats['embeddings_generated']}"
            )

            if stats["errors"]:
                st.warning(f"Some pages had errors: {len(stats['errors'])} issues")

    except Exception as e:
        st.error(f"Error indexing PDF: {str(e)}")

    finally:
        st.session_state.is_indexing = False
        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)


def render_sidebar() -> None:
    """Render the sidebar with upload and controls."""
    with st.sidebar:
        st.header("Document Management")

        # Collection info
        info = get_collection_info()
        if info["exists"]:
            st.metric("Indexed Pages", info["document_count"])
        else:
            st.info("No documents indexed yet.")

        st.divider()

        # File upload
        st.subheader("Upload PDF")
        uploaded_file = st.file_uploader(
            "Choose a PDF file",
            type="pdf",
            help="Upload a PDF document to index"
        )

        if uploaded_file and st.button("Index PDF", type="primary", disabled=st.session_state.is_indexing):
            handle_pdf_upload(uploaded_file)

        st.divider()

        # Indexed files list
        if st.session_state.indexed_files:
            st.subheader("Indexed Files")
            for filename in st.session_state.indexed_files:
                st.text(f"- {filename}")

        st.divider()

        # Clear chat button
        if st.button("Clear Chat History"):
            st.session_state.chat_history = []
            st.rerun()

        # Environment info
        st.divider()
        st.caption("Configuration")
        st.caption(f"DB Path: `{CHROMA_DB_PATH}`")
        st.caption(f"Collection: `{COLLECTION_NAME}`")


def render_chat_message(role: str, content: str, sources: Optional[list] = None) -> None:
    """
    Render a chat message with appropriate styling.

    Args:
        role: 'user' or 'assistant'
        content: The message content
        sources: Optional list of source page numbers
    """
    if role == "user":
        st.markdown(f'<div class="user-message">{content}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="assistant-message">{content}</div>', unsafe_allow_html=True)
        if sources:
            source_tags = " ".join([f'<span class="source-tag">Page {s}</span>' for s in sources])
            st.markdown(f'<div style="clear: both; margin-top: 0.5rem;">{source_tags}</div>', unsafe_allow_html=True)
    st.markdown('<div style="clear: both;"></div>', unsafe_allow_html=True)


def render_chat_interface() -> None:
    """Render the main chat interface."""
    st.markdown('<h1 class="main-header">Multimodal RAG Assistant</h1>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Ask questions about your indexed PDF documents</p>', unsafe_allow_html=True)

    # Display chat history
    chat_container = st.container()
    with chat_container:
        if not st.session_state.chat_history:
            st.markdown(
                '<div class="info-box">'
                '<strong>Getting Started:</strong><br>'
                '1. Upload a PDF using the sidebar<br>'
                '2. Click "Index PDF" to process the document<br>'
                '3. Ask questions about your document below'
                '</div>',
                unsafe_allow_html=True
            )
        else:
            for message in st.session_state.chat_history:
                render_chat_message(
                    message["role"],
                    message["content"],
                    message.get("sources")
                )

    # Chat input
    st.divider()
    col1, col2 = st.columns([6, 1])

    with col1:
        user_question = st.text_input(
            "Ask a question about your documents:",
            key="question_input",
            placeholder="What would you like to know?"
        )

    with col2:
        send_button = st.button("Send", type="primary")

    # Handle question submission
    if (send_button or st.session_state.get("question_input")) and user_question:
        # Check if documents are indexed
        info = get_collection_info()
        if not info["exists"] or info["document_count"] == 0:
            st.error("Please index a PDF document first before asking questions.")
            return

        # Add user message to history
        st.session_state.chat_history.append({
            "role": "user",
            "content": user_question
        })

        # Get RAG response
        with st.spinner("Searching documents and generating answer..."):
            try:
                result = rag_query(user_question)

                # Add assistant response to history
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": result["answer"],
                    "sources": result["sources"]
                })

                # Rerun to update the display
                st.rerun()

            except Exception as e:
                st.error(f"Error processing your question: {str(e)}")


def main() -> None:
    """Main entry point for the Streamlit application."""
    # Validate environment
    if "GOOGLE_API_KEY" not in os.environ:
        st.error("GOOGLE_API_KEY environment variable not set. Please set it and restart the app.")
        st.stop()

    if "OPENAI_API_KEY" not in os.environ:
        st.error("OPENAI_API_KEY environment variable not set. Please set it and restart the app.")
        st.stop()

    # Initialize
    apply_custom_css()
    init_session_state()

    # Render UI
    render_sidebar()
    render_chat_interface()


if __name__ == "__main__":
    main()
