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
        self.assertNotIn(".featured-head { break-before: page; }", template)
        self.assertIn(".featured-head { column-span: none; }", template)
        self.assertIn(".closing-head { break-before: auto; }", template)
        self.assertIn(".roundup h3.headline { break-inside: avoid; break-after: avoid; }", template)
        self.assertIn('class="focus-figure"', template)
        self.assertIn('class="roundup-figure"', template)

        focus_grid = template.split(".focus-grid {", 1)[1].split("}", 1)[0]
        self.assertIn('grid-template-areas: "lead lead" "secondary-one secondary-two"',
                      focus_grid)

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

    def test_old_layout_gets_figures_for_every_article_type(self):
        layout = {
            "focus": [{"headline": "焦點", "refs": [4]}],
            "featured": [{"ref": 4, "headline": "Gemma 4", "figures": []}],
            "roundup": {"headline": "學術動向", "paragraphs": ["另一篇文獻〔5〕"]},
        }
        ingested = {
            "papers": [
                {
                    "ref": 4,
                    "figures": [
                        {"path": "/tmp/gemma-figure.png", "caption": "Gemma 原文圖"},
                        {"path": "/tmp/gemma-alternate.png", "caption": "Gemma 原文圖 2"},
                    ],
                },
                {
                    "ref": 5,
                    "figures": [{"path": "/tmp/ai-premium.png", "caption": "AI Premium 原文圖"}],
                },
            ],
        }

        newspaper._fill_missing_featured_figures(layout, ingested)

        self.assertEqual(layout["focus"][0]["figures"][0]["caption"], "Gemma 原文圖 2")
        self.assertEqual(layout["featured"][0]["figures"][0]["caption"], "Gemma 原文圖")
        self.assertEqual(layout["roundup"]["figures"][0]["caption"], "AI Premium 原文圖")
        self.assertTrue(layout["featured"][0]["figures"][0]["path"].startswith("file:"))

    def test_editor_prompt_uses_configured_or_generic_reader_context(self):
        reports = {"terms": "", "papers": "", "github": ""}
        ingested = {"papers": []}
        citations = {"references": []}
        empty_layout = {"focus": [], "featured": [], "roundup": {}, "terms": []}

        with patch("newspaper.ask_json", return_value=empty_layout) as ask:
            newspaper._editor_payload(reports, ingested, citations, {})
        default_prompt = ask.call_args.args[0]
        self.assertIn("讀者背景：技術與研究工作者", default_prompt)
        self.assertNotIn("AI 醫材工程師", default_prompt)

        with patch("newspaper.ask_json", return_value=empty_layout) as ask:
            newspaper._editor_payload(
                reports, ingested, citations, {"project_context": "醫學影像研究者"})
        self.assertIn("讀者背景：醫學影像研究者", ask.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
