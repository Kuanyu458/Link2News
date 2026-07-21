import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import main as pipeline_main  # noqa: E402
import newspaper  # noqa: E402


class NewspaperLayoutTests(unittest.TestCase):
    def test_issue_metadata_uses_requested_week(self):
        self.assertEqual(
            newspaper._issue_metadata("2026-W28"),
            ("2026-28", "2026 年 07 月 06-12 日"),
        )

    def test_template_has_desktop_mobile_and_print_layouts(self):
        template = (ROOT / "templates" / "newspaper.html").read_text(encoding="utf-8")
        self.assertIn("width: min(1200px", template)
        self.assertIn("max-width: 1023px", template)
        self.assertIn("max-width: 739px", template)
        self.assertIn("@media print", template)
        self.assertIn("column-span: none", template)
        self.assertIn("overflow-wrap: anywhere", template)

    def test_rerender_reuses_layout_without_external_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            payloads = {
                "ingested.json": {"papers": [], "repos": [], "news": []},
                "citations.json": {"references": [], "library_dir": ""},
                "layout.json": {"issue_title": "測試", "focus": []},
            }
            for name, payload in payloads.items():
                (out / name).write_text(json.dumps(payload), encoding="utf-8")
            (out / "1_名詞說明報告.md").write_text("terms", encoding="utf-8")
            pdf_path = out / "weekly_2026-W28.pdf"

            with patch("main.output_dir", return_value=out), \
                    patch("newspaper.render_newspaper", return_value=pdf_path) as render:
                result = pipeline_main.rerender_existing(
                    {"report": {}, "podcast": {}}, "2026-W28")

            self.assertEqual(result, pdf_path)
            kwargs = render.call_args.kwargs
            self.assertEqual(kwargs["layout_override"]["issue_title"], "測試")
            self.assertIsNone(render.call_args.args[6])

    def test_rerender_reports_missing_inputs(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch("main.output_dir", return_value=Path(tmp)):
            with self.assertRaisesRegex(RuntimeError, "ingested.json"):
                pipeline_main.rerender_existing({}, "2026-W28")


if __name__ == "__main__":
    unittest.main()
