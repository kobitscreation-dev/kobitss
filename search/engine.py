"""
search/engine.py
================
In-memory document search engine with:
  - Inverted index for fast term-based lookup
  - TF-IDF (Term Frequency × Inverse Document Frequency) scoring
  - Multi-term query support with score accumulation
  - Case-insensitive tokenisation with punctuation stripping
  - Thread-safe index updates via a simple lock

Public API
----------
    Document    – lightweight dataclass representing an indexed document
    SearchResult – ranked result returned by ``SearchEngine.search``
    SearchEngine – the main engine class

Quick-start
-----------
    >>> from search.engine import SearchEngine
    >>> engine = SearchEngine()
    >>> engine.add_document("doc1", "Python is a great programming language")
    >>> engine.add_document("doc2", "Java is also a programming language")
    >>> engine.add_document("doc3", "Python and Java are both popular")
    >>> results = engine.search("Python programming")
    >>> results[0].doc_id  # most relevant document
    'doc1'
"""

from __future__ import annotations

import math
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

@dataclass
class Document:
    """A single document stored in the search engine.

    Attributes
    ----------
    doc_id:
        Unique identifier supplied by the caller.
    text:
        Raw text content of the document.
    metadata:
        Optional arbitrary key/value pairs attached to the document.
    """

    doc_id: str
    text: str
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.doc_id:
            raise ValueError("doc_id must be a non-empty string")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")


@dataclass
class SearchResult:
    """One ranked result returned by ``SearchEngine.search``.

    Attributes
    ----------
    doc_id:
        Identifier of the matching document.
    score:
        Aggregated TF-IDF relevance score (higher == more relevant).
    document:
        The full :class:`Document` object (if ``include_documents=True``).
    """

    doc_id: str
    score: float
    document: Optional[Document] = None


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# Matches one or more non-alphanumeric characters (used as token delimiters).
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")

# Common English stop-words that carry little retrieval signal.
_STOP_WORDS: Set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "on",
    "at", "by", "for", "with", "about", "as", "into", "through", "from",
    "and", "or", "but", "not", "this", "that", "it", "its", "i", "we",
    "you", "he", "she", "they", "also", "both", "all", "any",
}


def _tokenize(text: str) -> List[str]:
    """Lower-case, strip punctuation, and split *text* into tokens.

    Stop-words and empty strings are removed so that they do not pollute
    the inverted index or distort TF-IDF scores.

    Parameters
    ----------
    text:
        Raw input string.

    Returns
    -------
    List[str]
        Ordered list of meaningful tokens.
    """
    lowered = text.lower()
    raw_tokens = _TOKEN_SPLIT_RE.split(lowered)
    return [t for t in raw_tokens if t and t not in _STOP_WORDS]


# ---------------------------------------------------------------------------
# SearchEngine
# ---------------------------------------------------------------------------

class SearchEngine:
    """In-memory document search engine using an inverted index and TF-IDF.

    The engine maintains three internal data structures:

    * ``_documents``   – ``{doc_id: Document}`` – the full document store.
    * ``_index``       – ``{term: {doc_id: term_frequency}}`` – inverted index.
    * ``_doc_lengths`` – ``{doc_id: total_token_count}`` – used for TF normalisation.

    TF-IDF formula used
    -------------------
    For a query term *t* and a document *d*:

        tf(t, d)  = count(t in d) / len(d)          (relative term frequency)
        idf(t)    = log( (1 + N) / (1 + df(t)) ) + 1  (smoothed IDF, sklearn-style)
        score contribution = tf(t, d) * idf(t)

    The final document score is the sum of per-term score contributions.

    Thread safety
    -------------
    All mutations (``add_document``, ``remove_document``, ``clear``) and
    queries (``search``) acquire a ``threading.RLock`` so the engine is safe
    to use from multiple threads.

    Parameters
    ----------
    stop_words:
        Optional custom set of stop-words; overrides the built-in set.
    """

    def __init__(self, stop_words: Optional[Set[str]] = None) -> None:
        self._documents: Dict[str, Document] = {}
        # inverted index:  term -> {doc_id -> raw term count}
        self._index: Dict[str, Dict[str, int]] = defaultdict(dict)
        # total number of tokens per document
        self._doc_lengths: Dict[str, int] = {}
        self._lock = threading.RLock()
        if stop_words is not None:
            # Monkey-patch the module-level tokenizer's stop-word set for this
            # instance.  We do this by capturing a closure reference instead.
            self._stop_words = stop_words
        else:
            self._stop_words = _STOP_WORDS

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> List[str]:
        """Instance-level tokenizer that respects ``self._stop_words``."""
        lowered = text.lower()
        raw_tokens = _TOKEN_SPLIT_RE.split(lowered)
        return [t for t in raw_tokens if t and t not in self._stop_words]

    def _term_frequencies(self, tokens: List[str]) -> Dict[str, int]:
        """Count raw occurrences of each token."""
        tf: Dict[str, int] = defaultdict(int)
        for token in tokens:
            tf[token] += 1
        return dict(tf)

    # ------------------------------------------------------------------
    # Index mutations
    # ------------------------------------------------------------------

    def add_document(
        self,
        doc_id: str,
        text: str,
        metadata: Optional[Dict] = None,
        *,
        overwrite: bool = True,
    ) -> None:
        """Index a document.

        Parameters
        ----------
        doc_id:
            Unique identifier.  If *doc_id* already exists the old entry is
            removed first (when ``overwrite=True``, which is the default).
        text:
            Document text to index.
        metadata:
            Optional dict of arbitrary key/value data stored alongside the doc.
        overwrite:
            When ``False``, raise ``ValueError`` if *doc_id* already exists.

        Raises
        ------
        ValueError
            If *doc_id* is empty or already exists and ``overwrite=False``.
        """
        if not doc_id:
            raise ValueError("doc_id must be a non-empty string")

        with self._lock:
            if doc_id in self._documents:
                if not overwrite:
                    raise ValueError(
                        f"Document '{doc_id}' already exists. "
                        "Pass overwrite=True to replace it."
                    )
                # Remove old index entries before re-indexing.
                self._remove_from_index(doc_id)

            document = Document(doc_id=doc_id, text=text, metadata=metadata or {})
            tokens = self._tokenize(text)
            tf_counts = self._term_frequencies(tokens)

            # Update documents store
            self._documents[doc_id] = document
            # Update inverted index
            for term, count in tf_counts.items():
                self._index[term][doc_id] = count
            # Record document length for TF normalisation
            self._doc_lengths[doc_id] = len(tokens)

    def remove_document(self, doc_id: str) -> bool:
        """Remove a document and all its index entries.

        Parameters
        ----------
        doc_id:
            Identifier of the document to remove.

        Returns
        -------
        bool
            ``True`` if the document was found and removed; ``False`` if it
            did not exist.
        """
        with self._lock:
            if doc_id not in self._documents:
                return False
            self._remove_from_index(doc_id)
            del self._documents[doc_id]
            del self._doc_lengths[doc_id]
            return True

    def _remove_from_index(self, doc_id: str) -> None:
        """Internal: remove all inverted-index entries for *doc_id*."""
        # We must iterate over a snapshot because we're modifying the dict.
        terms_to_clean: List[str] = [
            term for term, postings in self._index.items() if doc_id in postings
        ]
        for term in terms_to_clean:
            del self._index[term][doc_id]
            # Prune empty postings lists to keep the index compact.
            if not self._index[term]:
                del self._index[term]

    def clear(self) -> None:
        """Remove all documents and reset the index."""
        with self._lock:
            self._documents.clear()
            self._index.clear()
            self._doc_lengths.clear()

    # ------------------------------------------------------------------
    # TF-IDF scoring
    # ------------------------------------------------------------------

    def _tf(self, raw_count: int, doc_length: int) -> float:
        """Relative term frequency: count / document_length.

        Returns 0.0 for a zero-length document to avoid division by zero.
        """
        if doc_length == 0:
            return 0.0
        return raw_count / doc_length

    def _idf(self, df: int, n_docs: int) -> float:
        """Smoothed IDF (sklearn / Lucene style):

            idf = log( (1 + N) / (1 + df) ) + 1

        This avoids zero IDF for terms that appear in every document and
        ensures a minimum IDF of 1.0.
        """
        return math.log((1 + n_docs) / (1 + df)) + 1.0

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        *,
        include_documents: bool = True,
        score_threshold: float = 0.0,
    ) -> List[SearchResult]:
        """Search the index using TF-IDF ranking.

        The query is tokenized in the same way as indexed documents.  Each
        query term contributes ``tf(term, doc) * idf(term)`` to the score of
        every document in the term's posting list.  Documents are ranked by
        their total score in descending order.

        Parameters
        ----------
        query:
            Natural-language (or keyword) query string.
        top_k:
            Maximum number of results to return (default 10).
        include_documents:
            When ``True`` (the default) the full :class:`Document` object is
            attached to each :class:`SearchResult`.  Set to ``False`` for a
            lighter-weight response.
        score_threshold:
            Exclude results with a score below this value (default 0.0).

        Returns
        -------
        List[SearchResult]
            Ranked list of results, best match first.  Empty list if no
            documents match.
        """
        if not query or not query.strip():
            return []

        with self._lock:
            query_tokens = self._tokenize(query)
            if not query_tokens:
                return []

            n_docs = len(self._documents)
            if n_docs == 0:
                return []

            # Accumulate TF-IDF scores per document.
            scores: Dict[str, float] = defaultdict(float)

            for term in query_tokens:
                if term not in self._index:
                    continue  # term not in any document → no contribution

                postings = self._index[term]  # {doc_id: raw_count}
                df = len(postings)
                idf = self._idf(df, n_docs)

                for doc_id, raw_count in postings.items():
                    doc_len = self._doc_lengths[doc_id]
                    tf = self._tf(raw_count, doc_len)
                    scores[doc_id] += tf * idf

            # Filter by threshold, sort by score descending, truncate.
            ranked = sorted(
                (
                    (doc_id, score)
                    for doc_id, score in scores.items()
                    if score > score_threshold
                ),
                key=lambda x: x[1],
                reverse=True,
            )[:top_k]

            results: List[SearchResult] = []
            for doc_id, score in ranked:
                doc = self._documents.get(doc_id)
                results.append(
                    SearchResult(
                        doc_id=doc_id,
                        score=score,
                        document=doc if include_documents else None,
                    )
                )

            return results

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    @property
    def document_count(self) -> int:
        """Number of documents currently indexed."""
        with self._lock:
            return len(self._documents)

    @property
    def vocabulary_size(self) -> int:
        """Number of unique terms in the inverted index."""
        with self._lock:
            return len(self._index)

    def get_document(self, doc_id: str) -> Optional[Document]:
        """Retrieve a :class:`Document` by ID, or ``None`` if not found."""
        with self._lock:
            return self._documents.get(doc_id)

    def get_term_postings(self, term: str) -> Dict[str, int]:
        """Return the posting list for *term* (raw counts per document).

        Returns an empty dict if the term is not in the index.  Intended
        primarily for testing and diagnostics.
        """
        with self._lock:
            return dict(self._index.get(term.lower(), {}))

    def document_ids(self) -> List[str]:
        """Return a sorted list of all indexed document IDs."""
        with self._lock:
            return sorted(self._documents.keys())

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SearchEngine("
            f"docs={self.document_count}, "
            f"terms={self.vocabulary_size})"
        )
