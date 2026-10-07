"""Tests for the stdlib-only ``version_info`` build-metadata utility.

Run with::

    python -m unittest test_version_info -v
    python -m unittest discover

Covers the frozen public contract of :func:`version_info.get_build_metadata`:
exact shape, fresh-object semantics, mutation isolation and determinism.
"""

import unittest

from version_info import get_build_metadata


class GetBuildMetadataTests(unittest.TestCase):
    """Frozen-contract coverage for ``get_build_metadata``."""

    def test_exact_shape(self):
        """The returned mapping matches the exact frozen shape and values."""
        self.assertEqual(
            get_build_metadata(),
            {"name": "kobits", "status": "ready", "api_version": "v1"},
        )

    def test_fresh_instance(self):
        """Each call returns a distinct ``dict`` instance."""
        self.assertIsNot(get_build_metadata(), get_build_metadata())

    def test_no_mutation_leak(self):
        """Mutating a returned dict must not affect later calls."""
        first = get_build_metadata()
        first["name"] = "tampered"
        first["extra"] = "added"
        first.clear()

        self.assertEqual(
            get_build_metadata(),
            {"name": "kobits", "status": "ready", "api_version": "v1"},
        )

    def test_deterministic(self):
        """Repeated calls produce equal results."""
        self.assertEqual(get_build_metadata(), get_build_metadata())
        self.assertEqual(get_build_metadata(), get_build_metadata())


if __name__ == "__main__":
    unittest.main()