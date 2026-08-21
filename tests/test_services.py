"""Tests for application services (cache + document processing).

No external services required: the cache falls back to its in-memory
store when Redis is unavailable.
"""

import unittest
import uuid

from services.cache_service import (
    get_prompt_cache,
    is_personal_query,
    prompt_cache_key,
    set_prompt_cache,
)
from services.document_processing import chunk_text, parse_file


class TestPromptCache(unittest.TestCase):
    """Exact prompt-cache roundtrip (RAM fallback when Redis is down)."""

    def test_cache_key_is_deterministic(self):
        k1 = prompt_cache_key("What is IPC", "model-x")
        k2 = prompt_cache_key("  what   is ipc  ", "model-x")
        self.assertEqual(k1, k2)

    def test_cache_key_differs_per_model(self):
        self.assertNotEqual(
            prompt_cache_key("what is ipc", "model-a"),
            prompt_cache_key("what is ipc", "model-b"),
        )

    def test_set_then_get_roundtrip(self):
        query = f"cache roundtrip probe {uuid.uuid4().hex}"
        payload = {"reply": "cached reply", "analysis": {"intent": "question"}}
        write = set_prompt_cache(query, "model-x", payload)
        self.assertTrue(write.get("wrote"), write)

        entry, report = get_prompt_cache(query, "model-x")
        self.assertIsNotNone(entry, report)
        self.assertEqual(entry.get("reply"), "cached reply")
        self.assertTrue(report["used"])

    def test_miss_on_unknown_query(self):
        entry, report = get_prompt_cache(f"never stored {uuid.uuid4().hex}", "model-x")
        self.assertIsNone(entry)
        self.assertFalse(report["used"])

    def test_personal_queries_are_never_cached(self):
        self.assertTrue(is_personal_query("who am i"))
        self.assertTrue(is_personal_query("what is my name"))
        self.assertFalse(is_personal_query("what is ipc"))
        write = set_prompt_cache("who am i", "model-x", {"reply": "x"})
        self.assertFalse(write.get("wrote"))


class TestDocumentProcessing(unittest.TestCase):
    def test_chunk_text_splits_long_text(self):
        text = ("This is a sentence about law. " * 200).strip()
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertTrue(0 < len(c) <= 1200)

    def test_chunk_text_short_text_single_chunk(self):
        self.assertEqual(chunk_text("short text"), ["short text"])

    def test_parse_file_plain_text(self):
        result = parse_file("note.txt", "text/plain", b"The Indian Penal Code was enacted in 1860.")
        self.assertEqual(result["kind"], "text")
        self.assertIn("Indian Penal Code", result["text"])
        self.assertGreater(result["chars"], 0)

    def test_parse_file_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_file("empty.txt", "text/plain", b"")

    def test_parse_file_oversized_raises(self):
        from services.document_processing import MAX_UPLOAD_BYTES

        with self.assertRaises(ValueError):
            parse_file("big.txt", "text/plain", b"x" * (MAX_UPLOAD_BYTES + 1))


if __name__ == "__main__":
    unittest.main()
