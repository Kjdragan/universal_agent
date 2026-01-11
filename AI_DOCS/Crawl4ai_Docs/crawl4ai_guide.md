# Crawl4AI Usage Guide

## Quickstart

### Extract Structured Data with LLM

This Python example demonstrates using the `crawl4ai` library's `LLMExtractionStrategy` to extract structured pricing data from a webpage. It utilizes a Pydantic `BaseModel` to define the schema for extraction and supports different LLM providers by passing an `api_token`.

```python
from crawl4ai import LLMExtractionStrategy
from pydantic import BaseModel, Field
import os, json

class OpenAIModelFee(BaseModel):
    model_name: str = Field(..., description="Name of the OpenAI model.")
    input_fee: str = Field(..., description="Fee for input token for the OpenAI model.")
    output_fee: str = Field(
        ..., description="Fee for output token for the OpenAI model."
    )

async def extract_structured_data_using_llm(provider: str, api_token: str = None, extra_headers: dict = None):
    print(f"\n--- Extracting Structured Data with {provider} ---")
    
    # Skip if API token is missing (for providers that require it)
    if api_token is None and provider != "ollama":
        print(f"API token is required for {provider}. Skipping this example.")
        return

    extra_args = {"extra_headers": extra_headers} if extra_headers else {}

    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(
            url="https://openai.com/api/pricing/",
            word_count_threshold=1,
            extraction_strategy=LLMExtractionStrategy(
                provider=provider,
                api_token=api_token,
                schema=OpenAIModelFee.schema(),
                extraction_type="schema",
                instruction="Extract all model names along with fees for input and output tokens."
            ),
            bypass_cache=True,
            **extra_args,
        )

    if result.success:
        data = json.loads(result.extracted_content)
        print("Extracted items:", len(data))
        for item in data[:3]:  # Print first 3 items
            print(item)
    else:
        print("Error:", result.error_message)

# Usage
# await extract_structured_data_using_llm("openai/gpt-4o", os.getenv("OPENAI_API_KEY"))
```

### Basic Sync/Async Usage

```python
from crawl4ai import AsyncWebCrawler

async def main():
    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(url="https://example.com")
        print(result.markdown)

# asyncio.run(main())
```

## Advanced Strategies

### CSS Extraction
```python
extraction_strategy=JsonCssExtractionStrategy(
    schema={
        "name": "css_selector",
        "baseSelector": ".product-card",
        "fields": [
            {"name": "title", "selector": "h2", "type": "text"},
            {"name": "price", "selector": ".price", "type": "text"}
        ]
    }
)
```

### Cosine Similarity Filtering
```python
extraction_strategy=CosineStrategy(
    semantic_filter="finance investing stocks",
    word_count_threshold=10
)
```

### LLM Config Object (v0.7.8+)
```python
from crawl4ai import LLMConfig

config = LLMConfig(
    provider="openai/gpt-4o",
    api_token=os.getenv("OPENAI_API_KEY"),
    temperature=0.1
)
```
