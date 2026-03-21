# Multimodal RAG Project

A Retrieval-Augmented Generation (RAG) system that uses **Gemini Embedding 2** for multimodal document embeddings and **GPT-5.4** for answer generation.

## Features

- **Multimodal Embeddings**: Combines text and visual information from PDF pages into unified 1536-dimensional vectors
- **Visual Understanding**: Renders PDF pages as images to capture tables, diagrams, and layouts
- **Semantic Search**: Uses ChromaDB with HNSW index for fast cosine similarity search
- **Cited Answers**: GPT-5.4 generates answers with page number references
- **Web Interface**: Clean Streamlit UI for document management and chat

## Architecture

```
PDF Document
    |
    v
[PyMuPDF] --> Extract Text + Render Page Images
    |
    v
[Gemini Embedding 2] --> Multimodal 1536-dim Vectors
    |
    v
[ChromaDB] --> HNSW Index with Cosine Similarity
    |
    v
[Query] --> [Gemini Embedding 2] --> Search Top-5 Pages
    |
    v
[GPT-5.4] --> Answer with Citations
```

## Setup

### 1. Prerequisites

- Python 3.10 or higher
- Google API key (for Gemini Embedding 2)
- OpenAI API key (for GPT-5.4)

### 2. Installation

```bash
# Create virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

```bash
# Copy example environment file
cp .env.example .env

# Edit .env and add your API keys
# GOOGLE_API_KEY=your_google_api_key_here
# OPENAI_API_KEY=your_openai_api_key_here
```

## Usage

### Option 1: Web Interface (Recommended)

```bash
# Start the Streamlit app
streamlit run app.py
```

Then:
1. Open the URL shown in the terminal (usually http://localhost:8501)
2. Upload a PDF using the sidebar
3. Click "Index PDF" to process the document
4. Ask questions in the chat interface

### Option 2: Command Line

#### Index a PDF

```bash
python ingest.py path/to/your/document.pdf
```

#### Query the Index

```bash
python rag.py "What is the main topic of the document?"
```

### Option 3: Python API

```python
from ingest import ingest_pdf
from rag import rag_query

# Index a document
stats = ingest_pdf("document.pdf")
print(f"Processed {stats['pages_processed']} pages")

# Query the indexed content
result = rag_query("What are the key findings?")
print(result["answer"])
print(f"Sources: {result['sources']}")
```

## Project Structure

```
New_Rag_Project/
|-- ingest.py          # PDF ingestion and embedding generation
|-- rag.py             # RAG query pipeline
|-- app.py             # Streamlit web interface
|-- requirements.txt   # Python dependencies
|-- .env.example       # Environment configuration template
|-- README.md          # This file
|-- integration_proposal.md  # Universal Agent integration ideas
|-- chroma_db/         # ChromaDB storage (created automatically)
|-- image_cache/       # Rendered page images (created automatically)
```

## Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_API_KEY` | Required | API key for Gemini Embedding 2 |
| `OPENAI_API_KEY` | Required | API key for GPT-5.4 |
| `CHROMA_DB_PATH` | `./chroma_db` | Path to ChromaDB storage |
| `COLLECTION_NAME` | `pdf_pages` | ChromaDB collection name |
| `BATCH_SIZE` | `10` | Embeddings per batch during ingestion |
| `TOP_K` | `5` | Number of pages to retrieve per query |
| `IMAGE_CACHE_DIR` | `./image_cache` | Directory for rendered page images |

## Technical Details

### Embedding Model
- **Model**: Gemini Embedding 2 (`gemini-embedding-002`)
- **Output**: 1536-dimensional vectors
- **Task Types**: `retrieval_document` (ingestion), `retrieval_query` (search)

### Generation Model
- **Model**: GPT-5.4
- **Temperature**: 0.3 (for consistent, factual responses)
- **Max Tokens**: 1024

### Vector Database
- **Engine**: ChromaDB with persistent storage
- **Index**: HNSW (Hierarchical Navigable Small World)
- **Distance**: Cosine similarity

## Troubleshooting

### "Collection not found" error
Make sure you've indexed at least one PDF before querying:
```bash
python ingest.py your_document.pdf
```

### "API key not found" error
Ensure your `.env` file contains valid API keys:
```bash
source .env  # or restart your terminal
```

### Slow ingestion
Large PDFs with many pages can take time. The batch upsert mechanism helps maintain stability. Adjust `BATCH_SIZE` in `.env` if needed.

## License

This project is part of the Universal Agent repository.

## References

- [Gemini Embedding 2 Documentation](https://ai.google.dev/gemini-api/docs/embeddings)
- [OpenAI GPT-5.4 Documentation](https://platform.openai.com/docs)
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [PyMuPDF Documentation](https://pymupdf.readthedocs.io/)
