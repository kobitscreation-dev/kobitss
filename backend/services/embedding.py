import re
import zlib
import numpy as np
from typing import List
from abc import ABC, abstractmethod

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")
_CAMEL_SPLIT_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|\d+")

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when", "where",
    "what", "which", "how", "should", "would", "could", "have", "has", "had",
    "are", "was", "were", "will", "can", "not", "all", "any", "some", "new",
    "self", "cls", "none", "true", "false", "return", "def", "class", "import",
    "const", "let", "var", "function", "async", "await", "public", "private",
}


def tokenize_code_identifiers(text: str) -> List[str]:
    """
    Extracts both raw identifiers (e.g. `merge_task_worktree`, `SandboxManager`)
    and split sub-tokens (`merge`, `task`, `worktree`, `sandbox`, `manager`).
    Used by both the deterministic embedding engine and hybrid BM25 lexical scorer.
    """
    if not text:
        return []
    tokens: List[str] = []
    for match in _TOKEN_RE.finditer(text):
        raw = match.group(0)
        low = raw.lower()
        if len(low) >= 2 and low not in STOPWORDS:
            tokens.append(low)
        # Split snake_case and camelCase / PascalCase into atomic sub-tokens
        parts = [p for p in raw.split("_") if p]
        sub_tokens: List[str] = []
        for part in parts:
            for sub in _CAMEL_SPLIT_RE.findall(part):
                s_low = sub.lower()
                if len(s_low) >= 2 and s_low not in STOPWORDS:
                    sub_tokens.append(s_low)
        for st in sub_tokens:
            if st != low:
                tokens.append(st)
    return tokens


def _deterministic_slot_and_sign(feature: str, dimensions: int) -> tuple[int, float]:
    """
    Cross-process deterministic hash using CRC32 (unlike Python's built-in `hash()`,
    which is randomized by PYTHONHASHSEED across every CLI invocation).
    Returns (bucket_index, +1.0 or -1.0 sign) for CountSketch projection.
    """
    h = zlib.crc32(feature.encode("utf-8", errors="ignore")) & 0xFFFFFFFF
    idx = h % dimensions
    sign = 1.0 if ((h >> 16) & 1) == 0 else -1.0
    return idx, sign


class BaseEmbeddingProvider(ABC):
    @property
    @abstractmethod
    def version(self) -> str:
        """Returns the version/model identifier for this provider."""
        pass

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Returns the dimensionality of the generated vectors."""
        pass

    @abstractmethod
    def generate_embedding(self, text: str) -> List[float]:
        """Generates a semantic embedding for the given text."""
        pass


class LocalNGramEmbeddingProvider(BaseEmbeddingProvider):
    """
    Deterministic multi-granular code & natural-language embedding provider.
    Combines:
      1. Exact identifier & symbol tokens (weight 2.5x)
      2. Split camelCase / snake_case sub-tokens (weight 3.0x)
      3. Adjacent identifier bigrams for structural phrase matching (weight 2.0x)
      4. Character 3-grams on identifiers for prefix/typo resilience (weight 0.4x)
    Uses CRC32 signed feature hashing (CountSketch) so vectors stored in SQLite
    are 100% reproducible across separate CLI runs and OS processes.
    """

    @property
    def version(self) -> str:
        return "v2.code_hybrid_384"

    @property
    def dimensions(self) -> int:
        return 384

    def generate_embedding(self, text: str) -> List[float]:
        if not text or not text.strip():
            return np.zeros(self.dimensions).tolist()

        dim = self.dimensions
        vec = np.zeros(dim, dtype=np.float64)

        # 1. Extract code identifiers & sub-tokens
        raw_matches = [m.group(0) for m in _TOKEN_RE.finditer(text)]
        atomic_subtokens: List[str] = []

        for raw in raw_matches:
            low = raw.lower()
            if len(low) >= 2 and low not in STOPWORDS:
                idx, sign = _deterministic_slot_and_sign(f"id:{low}", dim)
                vec[idx] += sign * 2.5

            for part in raw.split("_"):
                if not part:
                    continue
                for sub in _CAMEL_SPLIT_RE.findall(part):
                    s_low = sub.lower()
                    if len(s_low) >= 2 and s_low not in STOPWORDS:
                        atomic_subtokens.append(s_low)
                        idx, sign = _deterministic_slot_and_sign(f"sub:{s_low}", dim)
                        vec[idx] += sign * 3.0

                        # Prefix stem (first 4-5 chars) to bridge e.g. "authenticate" <-> "authentication"
                        if len(s_low) >= 5:
                            stem = s_low[:5]
                            s_idx, s_sign = _deterministic_slot_and_sign(f"stem:{stem}", dim)
                            vec[s_idx] += s_sign * 1.5

        # 2. Sub-token bigrams
        for i in range(len(atomic_subtokens) - 1):
            bg = f"bg:{atomic_subtokens[i]}_{atomic_subtokens[i + 1]}"
            idx, sign = _deterministic_slot_and_sign(bg, dim)
            vec[idx] += sign * 2.0

        # 3. Character tri-grams (bounded to first 2,000 chars for speed & noise control)
        compact = re.sub(r"\s+", " ", text.lower().strip())[:2000]
        for i in range(max(0, len(compact) - 2)):
            trigram = compact[i : i + 3]
            if not trigram.strip():
                continue
            idx, sign = _deterministic_slot_and_sign(f"tri:{trigram}", dim)
            vec[idx] += sign * 0.35

        # L2 Normalize so dot product == cosine similarity
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        else:
            vec = np.zeros(dim, dtype=np.float64)

        vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
        return vec.tolist()


class EmbeddingUtils:
    """
    Facade for the active embedding provider.
    Uses the deterministic multi-granular code embedding provider by default.
    """
    _active_provider: BaseEmbeddingProvider = LocalNGramEmbeddingProvider()

    @classmethod
    def get_provider(cls) -> BaseEmbeddingProvider:
        return cls._active_provider

    @classmethod
    def set_provider(cls, provider: BaseEmbeddingProvider):
        cls._active_provider = provider

    @classmethod
    def generate_embedding(cls, text: str) -> List[float]:
        return cls._active_provider.generate_embedding(text)

    @classmethod
    def get_version(cls) -> str:
        return cls._active_provider.version

