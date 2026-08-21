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


class TestPerUserCacheIsolation(unittest.TestCase):
    """Privacy invariant: cached replies must never cross users."""

    def test_cache_key_differs_per_user(self):
        self.assertNotEqual(
            prompt_cache_key("what is ipc", "model-a", "", "user-1"),
            prompt_cache_key("what is ipc", "model-a", "", "user-2"),
        )

    def test_cross_user_exact_cache_isolation(self):
        query = f"isolation probe {uuid.uuid4().hex}"
        alice = f"alice-{uuid.uuid4().hex}"
        bob = f"bob-{uuid.uuid4().hex}"
        set_prompt_cache(query, "model-x", {"reply": "alice personalized reply"}, user_id=alice)

        leak, _ = get_prompt_cache(query, "model-x", user_id=bob)
        self.assertIsNone(leak, "user B must never read user A's cached reply")

        own, report = get_prompt_cache(query, "model-x", user_id=alice)
        self.assertIsNotNone(own)
        self.assertEqual(own.get("reply"), "alice personalized reply")
        self.assertTrue(report["used"])


def test_semantic_cache_is_per_user(monkeypatch):
    import services.cache_service as cs
    import services.embedding_service as emb

    monkeypatch.setattr(cs, "SEMANTIC_CACHE_ENABLED", True)
    monkeypatch.setattr(
        emb,
        "embed_texts",
        lambda texts, kind="query": ([[1.0, 0.0]] * len(texts), {"norms": [1.0] * len(texts)}),
    )

    alice = f"alice-{uuid.uuid4().hex}"
    bob = f"bob-{uuid.uuid4().hex}"
    query = f"semantic isolation probe about tenant eviction notice {uuid.uuid4().hex}"

    set_prompt_cache(query, "model-x", {"reply": "alice answer"}, user_id=alice)
    store = cs.semantic_cache_store(query, "model-x", user_id=alice)
    assert store.get("wrote"), store

    hit, _ = cs.semantic_cache_lookup(query, "model-x", user_id=alice)
    assert hit is not None and hit["reply"] == "alice answer"

    leak, _ = cs.semantic_cache_lookup(query, "model-x", user_id=bob)
    assert leak is None, "semantic cache must not cross users"


if __name__ == "__main__":
    unittest.main()
