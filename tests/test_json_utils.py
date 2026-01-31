import pytest
from pydantic import BaseModel, Field
from typing import List
from universal_agent.utils.json_utils import extract_json_payload

class MockSection(BaseModel):
    id: str
    title: str

class MockModel(BaseModel):
    title: str
    sections: List[MockSection]

def test_extract_clean_json():
    text = '{"title": "Clean", "sections": [{"id": "1", "title": "S1"}]}'
    result = extract_json_payload(text, model=MockModel)
    assert isinstance(result, MockModel)
    assert result.title == "Clean"

def test_extract_markdown_wrapped():
    text = "Here is the plan:\n```json\n" + '{"title": "Wrapped", "sections": []}' + "\n```"
    result = extract_json_payload(text, model=MockModel)
    assert result.title == "Wrapped"

def test_extract_html_wrapped():
    text = """
    <html>
    <body>
    <script>
    {"title": "HTML", "sections": []}
    </script>
    </body>
    </html>
    """
    result = extract_json_payload(text, model=MockModel)
    assert result.title == "HTML"

def test_repair_unquoted_keys():
    text = "{title: 'Repair Me', sections: [{id: '1', title: 'S1'}]}"
    result = extract_json_payload(text, model=MockModel)
    assert result.title == "Repair Me"

def test_repair_trailing_commas():
    text = '{"title": "Commas", "sections": [{"id": "1", "title": "S1"},],}'
    result = extract_json_payload(text, model=MockModel)
    assert result.title == "Commas"

def test_repair_python_literals():
    text = '{"title": "Pythonic", "is_active": True, "data": None, "sections": []}'
    result = extract_json_payload(text) 
    # The repair should turn True -> true (bool) and None -> null (NoneType)
    assert result["is_active"] is True
    assert result["data"] is None

def test_extract_corrupted_json():
    # Text with NO brackets or structure - should definitely fail
    text = 'The report is not ready yet because of ERROR 500 without any JSON'
    with pytest.raises(ValueError, match="Failed to extract or repair JSON"):
        extract_json_payload(text)

def test_aggressive_repair():
    # json-repair is intentionally aggressive. This tests that it DOES fix partials.
    text = 'He said: { "key": "partial val" ... and then he left'
    result = extract_json_payload(text)
    assert result == {"key": "partial val"}

def test_pydantic_validation_error():
    # Model expects 'sections' list
    text = '{"title": "Invalid Schema", "sections": "not a list"}'
    with pytest.raises(ValueError, match="Extracted data did not match required schema"):
        extract_json_payload(text, model=MockModel, require_model=True)

def test_full_html_failure():
    # Case where LLM outputs ONLY HTML with no JSON block
    text = """
    <!DOCTYPE html>
    <html>
    <head><title>Not JSON</title></head>
    <body><h1>This is a report, not an outline</h1></body>
    </html>
    """
    with pytest.raises(ValueError, match="Failed to extract or repair JSON"):
        extract_json_payload(text)

if __name__ == "__main__":
    pytest.main([__file__])
