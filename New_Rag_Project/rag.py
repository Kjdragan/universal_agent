"""
RAG Query Pipeline

This module implements the retrieval-augmented generation pipeline,
querying ChromaDB for relevant pages and using GPT-5.4 for generation.

Usage:
    from rag import rag_query
    answer = rag_query("What is the main topic of the document?")
"""

import os
from typing import Optional

from chromadb import PersistentClient
from chromadb.config import Settings
from google.genai import Client as GeminiClient
from openai import OpenAI

# Configuration
CHROMA_DB_PATH = os.environ.get("CHROMA_DB_PATH", "./chroma_db")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "pdf_pages")
TOP_K = int(os.environ.get("TOP_K", "5"))

# System prompt for the RAG assistant
SYSTEM_PROMPT = """You are a helpful document assistant. Your role is to answer questions based strictly on the provided document content.

Instructions:
- Answer the user's question using ONLY the information from the provided context pages
- If the answer cannot be found in the context, say so clearly
- Include page number references in your answer (e.g., "According to page 3...")
- Keep your response clear and concise
- If multiple pages are relevant, synthesize the information and cite all sources
- Do not make up information or use external knowledge

Remember: Be accurate and always cite your sources within the document."""


class RAGPipeline:
    """
    Retrieval-Augmented Generation pipeline using Gemini embeddings and GPT-5.4.
    """

    def __init__(self) -> None:
        """Initialize the RAG pipeline with required clients."""
        self._gemini_client: Optional[GeminiClient] = None
        self._openai_client: Optional[OpenAI] = None
        self._collection: Optional[any] = None

    @property
    def gemini_client(self) -> GeminiClient:
        """Lazy initialization of Gemini client."""
        if self._gemini_client is None:
            self._gemini_client = GeminiClient(api_key=os.environ["GOOGLE_API_KEY"])
        return self._gemini_client

    @property
    def openai_client(self) -> OpenAI:
        """Lazy initialization of OpenAI client."""
        if self._openai_client is None:
            self._openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        return self._openai_client

    @property
    def collection(self) -> any:
        """Lazy initialization of ChromaDB collection."""
        if self._collection is None:
            client = PersistentClient(
                path=CHROMA_DB_PATH,
                settings=Settings(anonymized_telemetry=False)
            )
            self._collection = client.get_collection(name=COLLECTION_NAME)
        return self._collection

    def embed_query(self, query: str) -> list[float]:
        """
        Generate an embedding for the query using Gemini Embedding 2.

        Uses 'retrieval_query' task type for optimal query-document matching.

        Args:
            query: The user's question or search query.

        Returns:
            List of 1536 float values representing the query embedding.
        """
        response = self.gemini_client.models.embed_content(
            model="gemini-embedding-002",
            contents=[{"text": query}],
            config={"task_type": "retrieval_query"}
        )

        embedding = response.embeddings[0].values
        return list(embedding)

    def search_documents(self, query_embedding: list[float]) -> list[dict]:
        """
        Search ChromaDB for the most similar document pages.

        Args:
            query_embedding: The embedded query vector.

        Returns:
            List of dictionaries containing matched documents with metadata.
        """
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=TOP_K,
            include=["documents", "metadatas", "distances"]
        )

        # Format results
        matches = []
        if results["documents"] and results["documents"][0]:
            for i in range(len(results["documents"][0])):
                matches.append({
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results["distances"] else None
                })

        return matches

    def build_context(self, matches: list[dict]) -> str:
        """
        Build a context string from matched documents.

        Args:
            matches: List of matched documents from search.

        Returns:
            Formatted context string for the generation model.
        """
        context_parts = []

        for i, match in enumerate(matches, 1):
            page_num = match["metadata"].get("page", "unknown")
            text = match["document"]
            context_parts.append(f"--- Page {page_num} ---\n{text}\n")

        return "\n".join(context_parts)

    def generate_answer(self, query: str, context: str) -> str:
        """
        Generate an answer using GPT-5.4 with the retrieved context.

        Args:
            query: The user's question.
            context: The assembled context from retrieved documents.

        Returns:
            The generated answer with source citations.
        """
        user_message = f"""Based on the following document content, please answer the question.

CONTEXT:
{context}

QUESTION:
{query}

Please provide a clear answer based only on the context above, and include page references."""

        response = self.openai_client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            temperature=0.3,
            max_tokens=1024
        )

        return response.choices[0].message.content

    def query(self, question: str) -> dict:
        """
        Execute the full RAG pipeline for a given question.

        Args:
            question: The user's question about the indexed documents.

        Returns:
            Dictionary containing:
                - answer: The generated answer
                - sources: List of source pages used
                - context: The full context provided to the model
        """
        # Embed the query
        query_embedding = self.embed_query(question)

        # Search for relevant documents
        matches = self.search_documents(query_embedding)

        if not matches:
            return {
                "answer": "I couldn't find any relevant information in the indexed documents to answer your question.",
                "sources": [],
                "context": ""
            }

        # Build context
        context = self.build_context(matches)

        # Generate answer
        answer = self.generate_answer(question, context)

        # Extract source pages
        sources = [m["metadata"].get("page") for m in matches if m["metadata"].get("page")]

        return {
            "answer": answer,
            "sources": sources,
            "context": context
        }


# Module-level pipeline instance for convenience
_pipeline: Optional[RAGPipeline] = None


def get_pipeline() -> RAGPipeline:
    """Get or create a shared RAGPipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = RAGPipeline()
    return _pipeline


def rag_query(question: str) -> dict:
    """
    Convenience function to execute a RAG query.

    Args:
        question: The user's question.

    Returns:
        Dictionary with answer, sources, and context.
    """
    return get_pipeline().query(question)


def main() -> None:
    """CLI entry point for RAG queries."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python rag.py <your question>")
        sys.exit(1)

    question = " ".join(sys.argv[1:])

    # Validate environment
    if "GOOGLE_API_KEY" not in os.environ:
        print("Error: GOOGLE_API_KEY environment variable not set")
        sys.exit(1)
    if "OPENAI_API_KEY" not in os.environ:
        print("Error: OPENAI_API_KEY environment variable not set")
        sys.exit(1)

    print(f"\nQuestion: {question}\n")
    print("Searching documents...")

    try:
        result = rag_query(question)

        print("\n" + "=" * 60)
        print("ANSWER:")
        print("=" * 60)
        print(result["answer"])

        if result["sources"]:
            print("\n" + "-" * 60)
            print(f"Source pages: {', '.join(map(str, result['sources']))}")

    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
