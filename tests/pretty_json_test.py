import json
import tempfile
import unittest
from pathlib import Path

from scripts.pretty_json import expand_embedded_json, format_json_text, format_jsonl_text, format_path


class PrettyJsonTest(unittest.TestCase):
    def test_format_json_text_uses_indentation_and_utf8(self):
        formatted = format_json_text('{"city":"吉林市","count":2}', indent=2)

        self.assertIn('\n  "city": "吉林市"', formatted)
        self.assertTrue(formatted.endswith("\n"))

    def test_format_jsonl_text_formats_each_record(self):
        formatted = format_jsonl_text('{"a":1}\n{"b":2}\n', indent=2)

        self.assertIn('"a": 1', formatted)
        self.assertIn('"b": 2', formatted)

    def test_format_jsonl_text_readable_adds_record_headers(self):
        formatted = format_jsonl_text('{"event":"run_start"}\n', readable=True)

        self.assertIn("===== record 1 =====", formatted)
        self.assertIn('"event": "run_start"', formatted)

    def test_readable_jsonl_expands_embedded_json_content(self):
        record = {
            "request": {
                "messages": [
                    {"role": "user", "content": json.dumps({"tasks": [{"task_id": "1"}]})}
                ]
            }
        }
        formatted = format_jsonl_text(json.dumps(record) + "\n", readable=True)

        self.assertIn('"content": {', formatted)
        self.assertIn('"tasks": [', formatted)
        self.assertNotIn('\\"tasks\\"', formatted)

    def test_expand_embedded_json_keeps_plain_strings(self):
        payload = expand_embedded_json({"content": "plain prompt"})

        self.assertEqual(payload, {"content": "plain prompt"})

    def test_format_path_infers_jsonl_from_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.jsonl"
            path.write_text(json.dumps({"event": "llm_call"}) + "\n", encoding="utf-8")

            formatted = format_path(path)

        self.assertIn('"event": "llm_call"', formatted)


if __name__ == "__main__":
    unittest.main()
