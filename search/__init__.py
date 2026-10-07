"""
search – In-memory document search engine.

Exposes:
    SearchEngine  – the main class with inverted index + TF-IDF ranking.
"""

from search.engine import SearchEngine, Document, SearchResult

__all__ = ["SearchEngine", "Document", "SearchResult"]
