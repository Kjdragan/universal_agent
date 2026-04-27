"""
PDF Ingestion Pipeline with Multimodal Embeddings

This module processes PDF documents and generates multimodal embeddings
using Gemini Embedding 2, storing them in ChromaDB for retrieval.

Usage:
    python ingest.py <path_to_pdf>
"""

import base64
import os
from pathlib import Path
import sys
from typing import Optional

from chromadb import PersistentClient
from chromadb.config import Settings
import fitz  # PyMuPDF
from google.genai import Client as GeminiClient

# Configuration
CHROMA_DB_PATH = os.environ.get("CHROMA_DB_PATH", "./chroma_db")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "pdf_pages")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "10"))
IMAGE_CACHE_DIR = Path(os.environ.get("IMAGE_CACHE_DIR", "./image_cache"))


def setup_chroma() -> tuple[PersistentClient, any]:
    """
    Initialize ChromaDB persistent client and create/get the collection.

    Returns:
        Tuple of (client, collection) for ChromaDB operations.
    """
    client = PersistentClient(
        path=CHROMA_DB_PATH,
        settings=Settings(anonymized_telemetry=False)
    )

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )

    return client, collection


def extract_page_content(pdf_doc: fitz.Document, page_num: int) -> tuple[str, bytes]:
    """
    Extract text and render page as PNG image from a PDF page.

    Args:
        pdf_doc: Open PyMuPDF Document object.
        page_num: Zero-based page index.

    Returns:
        Tuple of (extracted_text, png_image_bytes).
    """
    page = pdf_doc[page_num]

    # Extract text content
    text = page.get_text()

    # Render page as high-resolution PNG image
    mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for better quality
    pix = page.get_pixmap(matrix=mat)

    # Convert to PNG bytes
    img_bytes = pix.tobytes("png")

    return text, img_bytes


def generate_multimodal_embedding(
    gemini_client: GeminiClient,
    text: str,
    image_bytes: bytes
) -> list[float]:
    """
    Generate a multimodal embedding using Gemini Embedding 2.

    Combines text and image into a single 1536-dimensional vector.

    Args:
        gemini_client: Initialized Gemini client.
        text: Text content from the page.
        image_bytes: PNG image bytes of the rendered page.

    Returns:
        List of 1536 float values representing the embedding.
    """
    # Encode image as base64
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = gemini_client.models.embed_content(
        model="gemini-embedding-002",
        contents=[{
            "text": text,
            "inline_data": {
                "mime_type": "image/png",
                "data": image_b64
            }
        }],
        config={"task_type": "retrieval_document"}
    )

    # Extract embedding values
    embedding = response.embeddings[0].values
    return list(embedding)


def ingest_pdf(pdf_path: str, verbose: bool = True) -> dict:
    """
    Ingest a PDF document into ChromaDB with multimodal embeddings.

    Processes each page, extracts text and renders as image,
    generates embeddings, and stores in ChromaDB.

    Args:
        pdf_path: Path to the PDF file.
        verbose: Whether to print progress information.

    Returns:
        Dictionary with ingestion statistics.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Initialize clients
    gemini_client = GeminiClient(api_key=os.environ["GOOGLE_API_KEY"])
    _, collection = setup_chroma()

    # Create image cache directory
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Open PDF
    pdf_doc = fitz.open(pdf_path)
    total_pages = len(pdf_doc)

    if verbose:
        print(f"Processing PDF: {pdf_path.name}")
        print(f"Total pages: {total_pages}")

    # Storage for batch upsert
    ids: list[str] = []
    embeddings: list[list[float]] = []
    metadatas: list[dict] = []
    documents: list[str] = []

    stats = {
        "pages_processed": 0,
        "embeddings_generated": 0,
        "errors": []
    }

    try:
        for page_num in range(total_pages):
            try:
                if verbose:
                    print(f"Processing page {page_num + 1}/{total_pages}...")

                # Extract content
                text, image_bytes = extract_page_content(pdf_doc, page_num)

                # Save cached image
                cache_path = IMAGE_CACHE_DIR / f"{pdf_path.stem}_page_{page_num + 1}.png"
                cache_path.write_bytes(image_bytes)

                # Generate embedding
                embedding = generate_multimodal_embedding(gemini_client, text, image_bytes)

                # Create text excerpt (first 500 chars for metadata)
                text_excerpt = text.strip()[:500] if text else ""

                # Prepare data for batch upsert
                page_id = f"{pdf_path.stem}_page_{page_num + 1}"
                ids.append(page_id)
                embeddings.append(embedding)
                metadatas.append({
                    "page": page_num + 1,
                    "source_file": pdf_path.name,
                    "text": text_excerpt
                })
                documents.append(text)

                stats["embeddings_generated"] += 1

                # Batch upsert when batch size is reached
                if len(ids) >= BATCH_SIZE:
                    collection.upsert(
                        ids=ids,
                        embeddings=embeddings,
                        metadatas=metadatas,
                        documents=documents
                    )
                    if verbose:
                        print(f"  Upserted batch of {len(ids)} embeddings")

                    # Reset batch storage
                    ids, embeddings, metadatas, documents = [], [], [], []

                stats["pages_processed"] += 1

            except Exception as e:
                error_msg = f"Error processing page {page_num + 1}: {str(e)}"
                stats["errors"].append(error_msg)
                if verbose:
                    print(f"  WARNING: {error_msg}")

        # Upsert any remaining items
        if ids:
            collection.upsert(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents
            )
            if verbose:
                print(f"  Upserted final batch of {len(ids)} embeddings")

    finally:
        pdf_doc.close()

    if verbose:
        print(f"\nIngestion complete!")
        print(f"  Pages processed: {stats['pages_processed']}")
        print(f"  Embeddings generated: {stats['embeddings_generated']}")
        if stats["errors"]:
            print(f"  Errors: {len(stats['errors'])}")

    return stats


def main() -> None:
    """CLI entry point for PDF ingestion."""
    if len(sys.argv) < 2:
        print("Usage: python ingest.py <path_to_pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    if "GOOGLE_API_KEY" not in os.environ:
        print("Error: GOOGLE_API_KEY environment variable not set")
        sys.exit(1)

    try:
        stats = ingest_pdf(pdf_path)
        if stats["errors"]:
            sys.exit(1)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
