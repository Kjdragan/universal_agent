import json
import pytest
from unittest.mock import patch, MagicMock

from universal_agent.wiki.llm import (
    extract_entities,
    extract_concepts,
    generate_summary,
    SemanticExtractionError
)
from universal_agent.wiki.core import wiki_ingest_external_source


@patch("universal_agent.wiki.llm._get_anthropic_client")
def test_successful_llm_extraction(mock_get_client):
    """Test that LLM successfully extracts entities and concepts."""
    # Mock Anthropic client
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    
    # Mock the response for entities
    mock_response_entities = MagicMock()
    mock_block_entities = MagicMock()
    mock_block_entities.text = '["OpenAI", "Anthropic"]'
    mock_response_entities.content = [mock_block_entities]
    
    # Configure the message create mock to return this response
    mock_client.messages.create.return_value = mock_response_entities
    
    entities = extract_entities("OpenAI and Anthropic are AI companies.")
    assert "OpenAI" in entities
    assert "Anthropic" in entities

    # Mock the response for concepts
    mock_response_concepts = MagicMock()
    mock_block_concepts = MagicMock()
    mock_block_concepts.text = '["Artificial Intelligence", "LLMs"]'
    mock_response_concepts.content = [mock_block_concepts]
    mock_client.messages.create.return_value = mock_response_concepts
    
    concepts = extract_concepts("Artificial Intelligence and LLMs are evolving.")
    assert "Artificial Intelligence" in concepts
    assert "LLMs" in concepts


@patch("universal_agent.wiki.llm._get_anthropic_client")
def test_heuristic_fallback_on_error(mock_get_client):
    """Test that heuristic fallback correctly triggers when LLM fails."""
    # Make get_client raise an exception to simulate missing API key or package
    mock_get_client.side_effect = Exception("No Anthropic API key available")
    
    text = "We are using Python and Pytest to test the System."
    entities = extract_entities(text)
    
    # Heuristic fallback looks for capitalized words with length > 3
    assert "Python" in entities
    assert "Pytest" in entities
    
    concepts = extract_concepts(text)
    # Heuristic fallback for concepts returns an empty list
    assert isinstance(concepts, list)
    assert len(concepts) == 0

    summary = generate_summary(text)
    # Heuristic fallback for summary returns first sentence
    assert "We are using Python and Pytest to test the System." in summary


@patch("universal_agent.wiki.core.extract_entities")
@patch("universal_agent.wiki.core.extract_concepts")
@patch("universal_agent.wiki.core.generate_summary")
def test_wiki_ingest_external_source(mock_generate_summary, mock_extract_concepts, mock_extract_entities, tmp_path):
    """Test that wiki_ingest_external_source successfully consumes and tags content."""
    mock_extract_entities.return_value = ["Entity1"]
    mock_extract_concepts.return_value = ["Concept1"]
    mock_generate_summary.return_value = "This is a summary."
    
    source_content = "This is the source content."
    source_title = "Test Source"
    
    # We pass the temporary directory as the root_override
    result = wiki_ingest_external_source(
        vault_slug="test-vault",
        source_title=source_title,
        source_content=source_content,
        root_override=str(tmp_path),
        source_id="test_id_1"
    )
    
    assert result["status"] == "success"
    assert "Entity1" in result["entities"]
    assert "Concept1" in result["concepts"]
    assert result["summary"] == "This is a summary."
    
    # Check if file was created
    file_path = tmp_path / result["path"]
    assert file_path.exists()
    
    # Verify the contents and frontmatter
    content = file_path.read_text()
    assert "Entity1" in content
    assert "Concept1" in content
    assert "This is a summary." in content
    assert "This is the source content." in content
