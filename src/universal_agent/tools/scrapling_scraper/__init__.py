"""
Scrapling-based inbox scraper module.

Processes JSON files containing URLs from an inbox directory, scrapes
each URL using the full Scrapling fetcher hierarchy (basic → dynamic →
stealthy), converts results to Markdown, and moves files through the
inbox pipeline (pending → processing → done/failed).
"""

from .inbox_processor import InboxProcessor, run_inbox_processor
from .fetcher_strategy import FetcherStrategy, FetcherLevel
from .content_converter import page_to_markdown

__all__ = [
    "InboxProcessor",
    "run_inbox_processor",
    "FetcherStrategy",
    "FetcherLevel",
    "page_to_markdown",
]
