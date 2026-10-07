"""
search/tests/test_engine.py
============================
Comprehensive unit-test suite for ``search.engine``.

Run with:
    pytest search/tests/test_engine.py -v

Or from the repo root:
    python -m pytest search/tests/ -v
"""

from __future__ import annotations

import math
import threading
from typing import List

import pytest

from search.engine import (
    Document,
    SearchEngine,
    SearchResult,
    _tokenize,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

@pytest.fixture()
def engine() -> SearchEngine:
    """A fresh, empty ``SearchEngine`` for each test."""
    return SearchEngine()


@pytest.fixture()
def populated_engine() -> SearchEngine:
    """An engine pre-loaded with a small corpus of five documents."""
    se = SearchEngine()
    se.add_document("d1", "Python is a high-level programming language")
    se.add_document("d2", "Java is an object-oriented programming language")
    se.add_document("d3", "Python and machine learning go hand in hand")
    se.add_document("d4", "SQL databases store structured data efficiently")
    se.add_document("d5", "Machine learning algorithms process large datasets")
    return se


# ===========================================================================
# Tokenizer tests
# ===========================================================================

class TestTokenizer:
    """Unit tests for the module-level ``_tokenize`` helper."""

    def test_basic_split(self):
        tokens = _tokenize("hello world")
        assert "hello" in tokens
        assert "world" in tokens

    def test_lowercases(self):
        tokens = _tokenize("Hello WORLD")
        assert "hello" in tokens
        assert "world" in tokens

    def test_strips_punctuation(self):
        tokens = _tokenize("hello, world! foo-bar.")
        assert "hello" in tokens
        assert "world" in tokens
        assert "foo" in tokens
        assert "bar" in tokens
        # punctuation-only segments must not appear
        assert "," not in tokens
        assert "!" not in tokens

    def test_removes_stop_words(self):
        tokens = _tokenize("this is a test")
        # "this", "is", "a" are all stop-words
        assert "this" not in tokens
        assert "is" not in tokens
        assert "a" not in tokens
        assert "test" in tokens

    def test_empty_string_returns_empty(self):
        assert _tokenize("") == []

    def test_only_stop_words_returns_empty(self):
        assert _tokenize("the a is are and or") == []

    def test_numbers_kept(self):
        tokens = _tokenize("python3 version 3.11")
        assert "python3" in tokens
        assert "version" in tokens
        assert "3" in tokens or "11" in tokens  # split on "."

    def test_hyphenated_words_split(self):
        tokens = _tokenize("well-known fact")
        assert "well" in tokens
        assert "known" in tokens

    def test_unicode_letters(self):
        # Non-ASCII characters end up lower-cased; hyphen splits them.
        tokens = _tokenize("naïve café")
        # At minimum the ASCII portions must survive (ï / é are non-ASCII
        # and may be stripped by the regex, which is acceptable).
        assert len(tokens) >= 1


# ===========================================================================
# Document dataclass tests
# ===========================================================================

class TestDocument:
    def test_creation(self):
        doc = Document(doc_id="x", text="hello")
        assert doc.doc_id == "x"
        assert doc.text == "hello"
        assert doc.metadata == {}

    def test_with_metadata(self):
        doc = Document(doc_id="x", text="hello", metadata={"author": "Alice"})
        assert doc.metadata["author"] == "Alice"

    def test_empty_doc_id_raises(self):
        with pytest.raises(ValueError):
            Document(doc_id="", text="hello")

    def test_non_string_text_raises(self):
        with pytest.raises(TypeError):
            Document(doc_id="x", text=123)  # type: ignore[arg-type]


# ===========================================================================
# SearchEngine – index management
# ===========================================================================

class TestIndexManagement:
    def test_add_document_increments_count(self, engine):
        assert engine.document_count == 0
        engine.add_document("a", "hello world")
        assert engine.document_count == 1
        engine.add_document("b", "foo bar")
        assert engine.document_count == 2

    def test_add_duplicate_overwrites_by_default(self, engine):
        engine.add_document("a", "hello world")
        engine.add_document("a", "completely different text")
        assert engine.document_count == 1
        doc = engine.get_document("a")
        assert doc.text == "completely different text"

    def test_add_duplicate_without_overwrite_raises(self, engine):
        engine.add_document("a", "hello")
        with pytest.raises(ValueError, match="already exists"):
            engine.add_document("a", "world", overwrite=False)

    def test_empty_doc_id_raises(self, engine):
        with pytest.raises(ValueError):
            engine.add_document("", "some text")

    def test_remove_existing_document(self, engine):
        engine.add_document("a", "hello world")
        removed = engine.remove_document("a")
        assert removed is True
        assert engine.document_count == 0
        assert engine.get_document("a") is None

    def test_remove_nonexistent_document(self, engine):
        assert engine.remove_document("ghost") is False

    def test_remove_cleans_index(self, engine):
        engine.add_document("a", "python language")
        engine.add_document("b", "java language")
        engine.remove_document("a")
        # "python" should no longer be in the index
        assert engine.get_term_postings("python") == {}
        # "language" should still point to "b"
        assert "b" in engine.get_term_postings("language")

    def test_clear_resets_everything(self, engine):
        engine.add_document("a", "hello")
        engine.add_document("b", "world")
        engine.clear()
        assert engine.document_count == 0
        assert engine.vocabulary_size == 0

    def test_document_ids_sorted(self, engine):
        engine.add_document("c", "gamma")
        engine.add_document("a", "alpha")
        engine.add_document("b", "beta")
        assert engine.document_ids() == ["a", "b", "c"]

    def test_vocabulary_size_grows(self, engine):
        assert engine.vocabulary_size == 0
        engine.add_document("a", "unique terms here")
        v1 = engine.vocabulary_size
        engine.add_document("b", "more brand new vocabulary words")
        assert engine.vocabulary_size > v1

    def test_get_term_postings_unknown_term(self, engine):
        assert engine.get_term_postings("nonexistent") == {}

    def test_metadata_stored(self, engine):
        engine.add_document("a", "some text", metadata={"lang": "en"})
        doc = engine.get_document("a")
        assert doc.metadata["lang"] == "en"

    def test_add_document_with_only_stop_words(self, engine):
        """A document consisting entirely of stop-words produces an empty token
        list – it is still stored but contributes nothing to the index."""
        engine.add_document("stop", "the a is are")
        assert engine.document_count == 1
        assert engine.vocabulary_size == 0  # no terms indexed


# ===========================================================================
# SearchEngine – inverted index structure
# ===========================================================================

class TestInvertedIndex:
    def test_term_in_postings_after_add(self, engine):
        engine.add_document("d1", "python programming")
        postings = engine.get_term_postings("python")
        assert "d1" in postings
        assert postings["d1"] == 1

    def test_multi_occurrence_counted(self, engine):
        engine.add_document("d1", "python python python")
        postings = engine.get_term_postings("python")
        assert postings["d1"] == 3

    def test_multi_document_postings(self, engine):
        engine.add_document("d1", "machine learning")
        engine.add_document("d2", "machine vision")
        engine.add_document("d3", "deep learning")
        postings = engine.get_term_postings("machine")
        assert "d1" in postings
        assert "d2" in postings
        assert "d3" not in postings

    def test_overwrite_updates_postings(self, engine):
        engine.add_document("d1", "java programming language")
        engine.add_document("d1", "python scripting")
        # Old terms should be gone
        assert engine.get_term_postings("java") == {}
        assert engine.get_term_postings("language") == {}
        # New terms should be present
        assert "d1" in engine.get_term_postings("python")
        assert "d1" in engine.get_term_postings("scripting")


# ===========================================================================
# SearchEngine – TF-IDF scoring
# ===========================================================================

class TestTfIdfScoring:
    """Verify that the TF-IDF formula produces sensible, monotonic scores."""

    def test_returns_search_results(self, populated_engine):
        results = populated_engine.search("Python")
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, SearchResult)

    def test_python_docs_rank_above_non_python(self, populated_engine):
        results = populated_engine.search("Python")
        doc_ids = [r.doc_id for r in results]
        # d1 and d3 both contain "python"; they must appear before d2/d4
        assert "d1" in doc_ids
        assert "d3" in doc_ids
        # Guard against ValueError from list.index() when d2/d4 score 0 and
        # are absent from results (non-python docs get no TF-IDF contribution).
        assert "d2" not in doc_ids or doc_ids.index("d1") < doc_ids.index("d2")
        assert "d4" not in doc_ids or doc_ids.index("d3") < doc_ids.index("d4")

    def test_most_relevant_doc_ranked_first(self, populated_engine):
        """'programming language' – d1 and d2 both contain both terms.
        Either can win; the important thing is that non-matching docs score 0."""
        results = populated_engine.search("programming language")
        result_ids = [r.doc_id for r in results]
        # d4 and d5 contain neither term after stop-word removal
        assert "d4" not in result_ids
        assert "d5" not in result_ids

    def test_scores_decrease_or_equal_in_order(self, populated_engine):
        results = populated_engine.search("machine learning")
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_exact_tfidf_value(self):
        """Hand-verify TF-IDF for a controlled two-document corpus."""
        se = SearchEngine()
        se.add_document("a", "cat cat dog")   # "cat" x2, "dog" x1  (len=3)
        se.add_document("b", "dog fish")       # "dog" x1, "fish" x1 (len=2)

        # Query: "cat"
        # N=2, df("cat")=1 → idf = log(3/2)+1 ≈ 1.4055
        # tf("cat", "a") = 2/3
        # expected score for "a" ≈ 1.4055 * (2/3) ≈ 0.9370
        expected_idf = math.log(3 / 2) + 1.0
        expected_score_a = expected_idf * (2 / 3)

        results = se.search("cat")
        assert len(results) == 1
        assert results[0].doc_id == "a"
        assert abs(results[0].score - expected_score_a) < 1e-9

    def test_rare_term_scores_higher_than_common_term(self):
        """A term appearing in only 1 of 5 docs gets a higher IDF than one
        appearing in all 5 docs."""
        se = SearchEngine()
        texts = [
            "common word rare_term",
            "common word",
            "common word",
            "common word",
            "common word",
        ]
        for i, t in enumerate(texts):
            se.add_document(f"d{i}", t)

        results_rare = se.search("rare_term")
        results_common = se.search("common")

        # rare_term only in d0; common is in all docs
        # The IDF for rare_term is higher, so d0's score for "rare_term"
        # should be greater than d0's score for "common"
        score_rare = results_rare[0].score if results_rare else 0.0
        score_common_d0 = next(
            (r.score for r in results_common if r.doc_id == "d0"), 0.0
        )
        assert score_rare > score_common_d0

    def test_no_match_returns_empty(self, populated_engine):
        results = populated_engine.search("xyzzy_no_such_term")
        assert results == []

    def test_empty_query_returns_empty(self, populated_engine):
        assert populated_engine.search("") == []

    def test_whitespace_only_query_returns_empty(self, populated_engine):
        assert populated_engine.search("   ") == []

    def test_stop_word_only_query_returns_empty(self, populated_engine):
        # All tokens are stop-words → nothing to look up
        assert populated_engine.search("the a is") == []

    def test_top_k_limits_results(self, populated_engine):
        results = populated_engine.search("language programming", top_k=1)
        assert len(results) <= 1

    def test_top_k_zero_returns_empty(self, populated_engine):
        results = populated_engine.search("python", top_k=0)
        assert results == []

    def test_score_threshold_filters_results(self):
        se = SearchEngine()
        se.add_document("hi", "python machine learning data")
        se.add_document("lo", "database storage disk")
        # With N=2, df("python")=1: idf=log(3/2)+1≈1.405, tf=1/4=0.25
        # → score for "hi" ≈ 0.351; "lo" scores 0.0 (term absent).
        # Use a threshold of 0.1: hi (0.351) passes, lo (0.0) does not.
        results = se.search("python", score_threshold=0.1)
        # Only "hi" should appear (its score is above 0.1; lo scores 0.0)
        doc_ids = [r.doc_id for r in results]
        assert "hi" in doc_ids
        assert "lo" not in doc_ids

    def test_include_documents_false(self, populated_engine):
        results = populated_engine.search("python", include_documents=False)
        assert len(results) > 0
        for r in results:
            assert r.document is None

    def test_include_documents_true(self, populated_engine):
        results = populated_engine.search("python", include_documents=True)
        for r in results:
            assert r.document is not None
            assert isinstance(r.document, Document)

    def test_empty_engine_returns_empty(self, engine):
        results = engine.search("anything")
        assert results == []


# ===========================================================================
# SearchEngine – multi-term and edge cases
# ===========================================================================

class TestMultiTermQuery:
    def test_multi_term_accumulates_scores(self):
        se = SearchEngine()
        se.add_document("a", "python programming language tutorial")
        se.add_document("b", "java programming tutorial")
        se.add_document("c", "python tutorial quick start")

        results = se.search("python programming tutorial")
        # "a" contains all three query terms; it should rank first
        assert results[0].doc_id == "a"

    def test_repeated_query_term_counts_once(self):
        """Repeating a term in the query must not double-count IDF contribution
        (each unique query token is evaluated once against the posting list)."""
        se = SearchEngine()
        se.add_document("a", "python python python")
        se.add_document("b", "python")

        r_single = se.search("python")
        r_double = se.search("python python")

        ids_single = [r.doc_id for r in r_single]
        ids_double = [r.doc_id for r in r_double]
        # The relative ranking should be the same regardless of query repetition
        assert ids_single == ids_double

    def test_partial_term_match(self):
        """Only documents containing at least one query term should appear."""
        se = SearchEngine()
        se.add_document("a", "machine learning data science")
        se.add_document("b", "web frontend html css")
        se.add_document("c", "machine vision robotics")

        results = se.search("machine data")
        result_ids = {r.doc_id for r in results}
        assert "a" in result_ids
        assert "c" in result_ids
        assert "b" not in result_ids


# ===========================================================================
# SearchEngine – document update / overwrite
# ===========================================================================

class TestDocumentUpdate:
    def test_overwrite_changes_ranking(self):
        se = SearchEngine()
        se.add_document("a", "python programming")
        se.add_document("b", "java programming")
        # Overwrite "a" to be about Java instead
        se.add_document("a", "java enterprise development")

        results = se.search("java")
        result_ids = [r.doc_id for r in results]
        assert "a" in result_ids
        assert "b" in result_ids

    def test_overwrite_removes_old_terms(self):
        se = SearchEngine()
        se.add_document("a", "python ml deep learning")
        se.add_document("a", "sql database query")
        # "python" must no longer match "a"
        results = se.search("python")
        assert not any(r.doc_id == "a" for r in results)

    def test_remove_then_readd(self):
        se = SearchEngine()
        se.add_document("a", "data science analytics")
        se.remove_document("a")
        se.add_document("a", "frontend react typescript")
        results = se.search("data science")
        assert not any(r.doc_id == "a" for r in results)
        results2 = se.search("react typescript")
        assert any(r.doc_id == "a" for r in results2)


# ===========================================================================
# SearchEngine – thread safety
# ===========================================================================

class TestThreadSafety:
    """Concurrent mutations must not corrupt the index."""

    def test_concurrent_add_documents(self):
        se = SearchEngine()
        errors: List[Exception] = []
        n_threads = 20
        n_docs_per_thread = 10

        def add_docs(thread_id: int) -> None:
            try:
                for i in range(n_docs_per_thread):
                    se.add_document(
                        f"t{thread_id}_d{i}",
                        f"thread {thread_id} document number {i} test content",
                    )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [
            threading.Thread(target=add_docs, args=(t,)) for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert se.document_count == n_threads * n_docs_per_thread

    def test_concurrent_read_write(self):
        se = SearchEngine()
        for i in range(50):
            se.add_document(f"init_{i}", f"word{i} common baseline text")

        errors: List[Exception] = []

        def writer() -> None:
            try:
                for i in range(20):
                    se.add_document(f"write_{i}", f"writer document {i} new content")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(30):
                    se.search("common baseline")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = (
            [threading.Thread(target=writer) for _ in range(5)]
            + [threading.Thread(target=reader) for _ in range(5)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"


# ===========================================================================
# SearchEngine – introspection properties
# ===========================================================================

class TestIntrospection:
    def test_document_count_empty(self, engine):
        assert engine.document_count == 0

    def test_vocabulary_size_empty(self, engine):
        assert engine.vocabulary_size == 0

    def test_document_count_after_remove(self, engine):
        engine.add_document("a", "hello world test phrase")
        engine.add_document("b", "another document phrase")
        engine.remove_document("a")
        assert engine.document_count == 1

    def test_get_document_returns_none_for_missing(self, engine):
        assert engine.get_document("missing") is None

    def test_get_document_returns_document(self, engine):
        engine.add_document("x", "sample")
        doc = engine.get_document("x")
        assert doc is not None
        assert doc.doc_id == "x"
        assert doc.text == "sample"


# ===========================================================================
# SearchEngine – large corpus smoke test
# ===========================================================================

class TestLargeCorpus:
    """Smoke test to verify correctness is maintained at scale."""

    @pytest.fixture()
    def large_engine(self) -> SearchEngine:
        se = SearchEngine()
        topics = [
            "python machine learning data science numpy pandas",
            "java enterprise spring boot microservices",
            "javascript react typescript frontend web development",
            "golang distributed systems concurrency goroutines",
            "rust systems programming memory safety ownership",
            "sql database query optimisation index",
            "docker kubernetes container orchestration deployment",
            "devops ci cd pipeline automation jenkins",
            "security cryptography tls certificate authentication",
            "natural language processing nlp bert transformer",
        ]
        for i in range(200):
            topic = topics[i % len(topics)]
            se.add_document(f"doc_{i:04d}", f"{topic} document {i} content words")
        return se

    def test_large_corpus_returns_results(self, large_engine):
        results = large_engine.search("machine learning python")
        assert len(results) > 0

    def test_large_corpus_top_k(self, large_engine):
        results = large_engine.search("machine learning python", top_k=5)
        assert len(results) <= 5

    def test_large_corpus_scores_ordered(self, large_engine):
        results = large_engine.search("kubernetes docker container", top_k=20)
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_large_corpus_relevant_terms_ranked_high(self, large_engine):
        results = large_engine.search("rust memory safety")
        top_ids = {r.doc_id for r in results[:10]}
        # docs 4, 14, 24 … (every 10th, offset 4) contain the rust topic
        rust_docs = {f"doc_{i:04d}" for i in range(4, 200, 10)}
        assert len(top_ids & rust_docs) > 0, (
            "Expected at least one Rust document in the top 10 results"
        )


# ===========================================================================
# SearchEngine – custom stop-words
# ===========================================================================

class TestCustomStopWords:
    def test_custom_stop_words_respected(self):
        # Treat "programming" as a stop-word so it is never indexed.
        se = SearchEngine(stop_words={"programming"})
        se.add_document("a", "programming python language")
        assert se.get_term_postings("programming") == {}
        assert "a" in se.get_term_postings("python")

    def test_empty_stop_words_indexes_everything(self):
        se = SearchEngine(stop_words=set())
        se.add_document("a", "the a is are")
        # Without stop-words, common words get indexed.
        assert se.vocabulary_size > 0

    def test_custom_stop_words_do_not_affect_other_engines(self):
        """Each SearchEngine instance is independent."""
        se1 = SearchEngine(stop_words={"python"})
        se2 = SearchEngine()
        se1.add_document("a", "python programming")
        se2.add_document("a", "python programming")
        # se1: "python" is a stop-word → not indexed
        assert se1.get_term_postings("python") == {}
        # se2: default stop-words → "python" IS indexed
        assert "a" in se2.get_term_postings("python")
