import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline.adapters import (
    Artifact,
    CollectRequest,
    DeliveryEvent,
    DeliveryReceipt,
    FileSourceAdapter,
    LocalDeliveryAdapter,
    SourceLink,
    deduplicate_external_ids,
    load_delivery_adapter,
    load_source_adapter,
)


class AdapterContractTests(unittest.TestCase):
    def test_file_source_normalizes_deduplicates_and_keeps_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "links.txt"
            path.write_text(
                "paper https://arxiv.org/pdf/1706.03762?utm_source=test\n"
                "duplicate https://arxiv.org/abs/1706.03762\n"
                "repo https://github.com/karpathy/nanoGPT/tree/master\n",
                encoding="utf-8",
            )
            adapter = FileSourceAdapter({}, {}, {"path": str(path)})
            links = adapter.collect(CollectRequest(week="2026-W30"))

        self.assertEqual(
            [item.url for item in links],
            [
                "https://arxiv.org/abs/1706.03762",
                "https://github.com/karpathy/nanoGPT",
            ],
        )
        self.assertEqual(links[0].text, "paper")
        self.assertTrue(links[0].external_id.startswith("file:1:"))

    def test_file_source_honors_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "links.txt"
            path.write_text(
                "\n".join(f"https://example.test/{index}" for index in range(4)),
                encoding="utf-8",
            )
            links = FileSourceAdapter({}, {}, {"path": str(path)}).collect(
                CollectRequest(week="2026-W30", limit=2))
        self.assertEqual(len(links), 2)

    def test_local_delivery_returns_absolute_artifact_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "report.pdf"
            pdf.write_bytes(b"%PDF-test")
            artifact = Artifact.from_path("pdf", pdf, "application/pdf")
            receipt = LocalDeliveryAdapter({}, {}).publish(DeliveryEvent(
                status="completed",
                phase="done",
                week="2026-W30",
                artifacts=(artifact,),
            ))
        self.assertTrue(receipt.ok)
        self.assertEqual(receipt.items["pdf"], str(pdf.resolve()))
        self.assertEqual(len(artifact.sha256), 64)

    def test_local_delivery_reports_missing_files(self):
        receipt = LocalDeliveryAdapter({}, {}).publish(DeliveryEvent(
            status="completed",
            phase="done",
            week="2026-W30",
            artifacts=(Artifact("pdf", Path("/missing/report.pdf"), "application/pdf"),),
        ))
        self.assertFalse(receipt.ok)
        self.assertIn("missing artifacts", receipt.error)

    def test_unknown_adapter_error_names_entry_point_group(self):
        with mock.patch("pipeline.adapters.importlib.metadata.entry_points") as points:
            points.return_value.select.return_value = []
            with self.assertRaisesRegex(
                    LookupError, "link2news.sources:missing-adapter"):
                load_source_adapter("missing-adapter", {}, {})

    def test_built_in_factories_are_available(self):
        self.assertIsInstance(load_source_adapter("file", {}, {}, {"path": "-"}),
                              FileSourceAdapter)
        self.assertIsInstance(load_delivery_adapter("local", {}, {}),
                              LocalDeliveryAdapter)

    def test_duplicate_external_id_is_dropped_within_same_source(self):
        links = deduplicate_external_ids([
            SourceLink("https://example.test/1", external_id="m1", source_id="room"),
            SourceLink("https://example.test/2", external_id="m1", source_id="room"),
            SourceLink("https://example.test/3", external_id="m1", source_id="other-room"),
        ])
        self.assertEqual([item.url for item in links], [
            "https://example.test/1",
            "https://example.test/3",
        ])

    def test_receipt_can_report_partial_delivery_without_generation_failure(self):
        receipt = DeliveryReceipt(
            ok=False,
            items={"pdf": "platform-pdf-id", "audio": None},
            error="audio delivery failed",
        )
        self.assertFalse(receipt.ok)
        self.assertEqual(receipt.items["pdf"], "platform-pdf-id")


if __name__ == "__main__":
    unittest.main()
