import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from universal_agent.trace_catalog import (
    emit_trace_catalog,
    extract_local_tool_trace_ids_from_trace,
    save_trace_catalog_md,
    save_trace_catalog_work_product,
)


class TestTraceCatalog(unittest.TestCase):
    def test_extract_local_tool_trace_ids_from_trace(self):
        trace = {
            "tool_results": [
                {
                    "content_preview": (
                        "[local-toolkit-trace-id: 019c356bbc2065263aabeb9ec2689190]\n"
                        "ok"
                    )
                },
                {
                    "content_preview": (
                        "prefix [local-toolkit-trace-id: 019c356bbc2065263aabeb9ec2689191] suffix"
                    )
                },
                {"content_preview": "[local-toolkit-trace-id: not-a-trace-id]"},
                {"content_preview": None},
            ]
        }
        trace_ids = extract_local_tool_trace_ids_from_trace(trace)
        self.assertEqual(
            trace_ids,
            [
                "019c356bbc2065263aabeb9ec2689190",
                "019c356bbc2065263aabeb9ec2689191",
            ],
        )

    def test_emit_trace_catalog_embedded_local_mode(self):
        main_trace_id = "019c356bbc2065263aabeb9ec2689190"
        with redirect_stdout(StringIO()):
            catalog = emit_trace_catalog(
                trace_id=main_trace_id,
                run_id="run-1",
                local_toolkit_trace_ids=[main_trace_id],
            )
        self.assertEqual(catalog["local_toolkit"]["mode"], "embedded_in_main")
        self.assertEqual(catalog["local_toolkit"]["distinct_trace_ids"], [])
        self.assertTrue(catalog["local_toolkit"]["overlap_with_main"])
        self.assertEqual(catalog["all_trace_ids"], [main_trace_id])

    def test_save_trace_catalog_work_product_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with redirect_stdout(StringIO()):
                catalog = emit_trace_catalog(
                    trace_id="019c356bbc2065263aabeb9ec2689190",
                    run_id="run-2",
                    local_toolkit_trace_ids=[
                        "019c356bbc2065263aabeb9ec2689191",
                        "019c356bbc2065263aabeb9ec2689190",
                    ],
                )
            root_path = save_trace_catalog_md(catalog, tmpdir)
            wp_paths = save_trace_catalog_work_product(catalog, tmpdir)

            with open(root_path, "r", encoding="utf-8") as handle:
                root_md = handle.read()
            self.assertIn("Trace Catalog", root_md)
            self.assertIn("Main Agent Trace", root_md)

            with open(wp_paths["md_path"], "r", encoding="utf-8") as handle:
                work_product_md = handle.read()
            self.assertIn("Trace Catalog", work_product_md)
            self.assertIn("Local Toolkit Trace IDs", work_product_md)

            with open(wp_paths["json_path"], "r", encoding="utf-8") as handle:
                work_product_json = json.load(handle)
            self.assertEqual(work_product_json["run_id"], "run-2")
            self.assertIn("main_agent", work_product_json)
            self.assertIn("local_toolkit", work_product_json)


if __name__ == "__main__":
    unittest.main()
