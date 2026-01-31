import json
import re
import logging
from typing import Any, Dict, Optional, Type, TypeVar, Union
from pydantic import BaseModel, ValidationError
import json_repair

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

def extract_json_payload(
    text: str, 
    model: Optional[Type[T]] = None, 
    require_model: bool = False
) -> Union[Dict[str, Any], T, None]:
    """
    Systematic 5-layer JSON extraction and repair logic.
    
    1. Layer 1: Standard json.loads()
    2. Layer 2: json_repair.loads() (Handles missing quotes, trailing commas, etc.)
    3. Layer 3: Regex bracket extraction (Bypasses conversational filler/HTML)
    4. Layer 4: Python literal cleanup (True -> true, etc.)
    5. Layer 5: Pydantic validation (Enforces schema and defaults)
    
    Args:
        text: The raw string containing JSON.
        model: Optional Pydantic model to validate against.
        require_model: If True, returns None if validation fails instead of the raw dict.
        
    Returns:
        A dict or Pydantic model instance if successful, else raises ValueError.
    """
    if not text or not isinstance(text, str):
        raise ValueError("Input text must be a non-empty string.")

    # Layer 0: Normalize Python literals early
    # This prevents json_repair from turning True into "True" (string)
    normalized_text = text
    try:
        normalized_text = re.sub(r"\bTrue\b", "true", normalized_text)
        normalized_text = re.sub(r"\bFalse\b", "false", normalized_text)
        normalized_text = re.sub(r"\bNone\b", "null", normalized_text)
    except Exception:
        pass

    # Layer 1: Standard Parse
    try:
        data = json.loads(normalized_text)
        return _validate(data, model, require_model)
    except json.JSONDecodeError:
        pass

    # Layer 2: json_repair.loads
    try:
        data = json_repair.loads(normalized_text)
        # STRICTOR: Only accept if it's a dict or list. 
        # If json_repair returns a string, it likely failed to find a real structure.
        if isinstance(data, (dict, list)):
            return _validate(data, model, require_model)
    except Exception:
        pass

    # Layer 3: Regex extraction
    # Find the largest {...} or [...] block
    match = re.search(r"(\{.*\}|\[.*\])", normalized_text, re.DOTALL)
    if match:
        potential_json = match.group(1)
        try:
            data = json_repair.loads(potential_json)
            if isinstance(data, (dict, list)):
                return _validate(data, model, require_model)
        except Exception:
            pass

    raise ValueError("Failed to extract or repair JSON payload from the provided text.")

def _validate(data: Any, model: Optional[Type[T]], require_model: bool) -> Union[Dict[str, Any], T, None]:
    """Internal helper to handle Pydantic validation if a model is provided."""
    if model is None:
        return data

    try:
        return model.model_validate(data)
    except ValidationError as e:
        logger.warning(f"Pydantic validation failed for {model.__name__}: {e}")
        if require_model:
            raise ValueError(f"Extracted data did not match required schema {model.__name__}: {e}")
        return data
