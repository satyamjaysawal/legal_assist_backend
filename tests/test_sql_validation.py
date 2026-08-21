"""Tests for the read-only SQL guard used by db_chat / query_lawyer_database.

Written as unittest.TestCase classes (executed by pytest).
"""

import unittest

from connectors.neon_postgres import MAX_ROWS, validate_select_sql


class TestValidateSelectSqlAccepts(unittest.TestCase):
    """SQL that MUST pass validation."""

    def test_simple_select(self):
        clean, err = validate_select_sql("SELECT id, name FROM lawyers")
        self.assertEqual(err, "")
        self.assertIsNotNone(clean)
        self.assertTrue(clean.upper().startswith("SELECT"))

    def test_limit_is_appended_when_missing(self):
        clean, _ = validate_select_sql("SELECT * FROM lawyers")
        self.assertIn(f"LIMIT {MAX_ROWS}", clean)

    def test_limit_is_capped_to_max_rows(self):
        clean, _ = validate_select_sql("SELECT * FROM lawyers LIMIT 100000")
        self.assertIn(f"LIMIT {MAX_ROWS}", clean)
        self.assertNotIn("100000", clean)

    def test_existing_small_limit_is_kept(self):
        clean, _ = validate_select_sql("SELECT * FROM lawyers LIMIT 3")
        self.assertIn("LIMIT 3", clean)

    def test_with_cte_is_allowed(self):
        sql = "WITH top AS (SELECT * FROM lawyers LIMIT 5) SELECT * FROM top"
        clean, err = validate_select_sql(sql)
        self.assertIsNotNone(clean)
        self.assertEqual(err, "")

    def test_trailing_semicolon_stripped(self):
        clean, _ = validate_select_sql("SELECT 1;")
        self.assertIsNotNone(clean)

    def test_line_comments_are_stripped(self):
        clean, _ = validate_select_sql("SELECT 1 -- a comment")
        self.assertIsNotNone(clean)
        self.assertNotIn("comment", clean)


class TestValidateSelectSqlRejects(unittest.TestCase):
    """SQL that MUST be rejected."""

    def test_empty_sql(self):
        clean, err = validate_select_sql("")
        self.assertIsNone(clean)
        self.assertIn("Empty", err)

    def test_non_select_statement(self):
        clean, err = validate_select_sql("SHOW TABLES")
        self.assertIsNone(clean)

    def test_write_statements_rejected(self):
        for stmt in (
            "INSERT INTO lawyers VALUES (1)",
            "UPDATE lawyers SET name='x'",
            "DELETE FROM lawyers",
            "DROP TABLE lawyers",
            "TRUNCATE lawyers",
            "ALTER TABLE lawyers ADD COLUMN x TEXT",
        ):
            clean, _ = validate_select_sql(stmt)
            self.assertIsNone(clean, f"Should reject: {stmt}")

    def test_multiple_statements_rejected(self):
        clean, err = validate_select_sql("SELECT 1; DROP TABLE lawyers")
        self.assertIsNone(clean)
        self.assertIn("Multiple statements", err)

    def test_keyword_smuggled_in_comment_rejected(self):
        clean, _ = validate_select_sql("SELECT 1 /* */ ; DROP TABLE lawyers")
        self.assertIsNone(clean)

    def test_forbidden_keyword_inside_select_rejected(self):
        clean, _ = validate_select_sql("SELECT * FROM lawyers; DROP TABLE x")
        self.assertIsNone(clean)


if __name__ == "__main__":
    unittest.main()
