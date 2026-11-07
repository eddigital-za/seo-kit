import asyncio
import json
import io
import os
import sys
import types
import unittest
from contextlib import asynccontextmanager, nullcontext
from unittest.mock import patch

from app.gsc_mcp import collect_gsc, private_url, read_tool
from app.signals import competitor_metadata, MetadataParser
from app.email_report import valid_report_key, render_email


def run(coroutine):
    # These fakes complete synchronously; no Windows socket/event-loop setup is needed.
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    raise AssertionError("Unexpected asynchronous I/O in unit test")


class SignalsTest(unittest.TestCase):
    def test_scoped_report_key_and_html_escaping(self):
        with patch.dict(os.environ, {"REPORT_API_KEY": "scoped-test-key"}):
            self.assertFalse(valid_report_key(None))
            self.assertFalse(valid_report_key("wrong"))
            self.assertTrue(valid_report_key("scoped-test-key"))
        with patch.dict(os.environ, {"REPORT_API_KEY": ""}):
            self.assertFalse(valid_report_key(""))
        result = render_email({"collected_at": "2026-11-07", "top_opportunities": [
            {"keyword": "<script>alert(1)</script>", "page": "https://example.com", "ctr": 0.05}],
            "gsc": {"status": "error", "error": "test failure"}})
        self.assertNotIn("<script>", result["email_html"])
        self.assertIn("Collection failed", result["email_html"])
        self.assertIn("Dated baseline", result["email_html"])

    def test_title_and_meta_description_extraction(self):
        parser = MetadataParser()
        parser.feed('<title>Bike &amp; Transport</title><meta name="Description" content="Door to door">')
        self.assertEqual(parser.title, "Bike & Transport")
        self.assertEqual(parser.description, "Door to door")
        absent = MetadataParser()
        absent.feed('<title>Title only</title>')
        self.assertIsNone(absent.description)

    def test_competitor_report_only_reads_selected_metadata(self):
        responses = [io.BytesIO(json.dumps({
            "watch1": {"url": "https://bikes2go.co.za/", "last_checked": 123},
            "other": {"url": "https://news.ycombinator.com/"},
        }).encode()), io.BytesIO(b"Bikes2Go | Reliable Bike Transport\nMotorcycle transport description"), io.BytesIO(b'{"123": "snapshot.txt"}'), io.BytesIO(b'<title>Bikes2Go | Reliable Bike Transport</title><meta name="description" content="Motorcycle transport description">')]
        requests = []
        class Opener:
            def open(self, req, timeout):
                requests.append(req)
                return responses.pop(0)
        with patch.dict(os.environ, {"COMPETITOR_METADATA_URL": "http://competitor.railway.internal:8080", "COMPETITOR_API_KEY": "test-key"}), patch("app.signals.build_opener", return_value=Opener()):
            result = competitor_metadata()
        self.assertEqual(result["competitor_watch_count"], 1)
        self.assertEqual(result["watches"][0]["history"], {"123": "snapshot.txt"})
        self.assertEqual([r.get_method() for r in requests], ["GET", "GET", "GET", "GET"])
        self.assertTrue(all(r.get_header("X-api-key") == "test-key" for r in requests[:3]))
        self.assertIsNone(requests[-1].get_header("X-api-key"))
        self.assertEqual(result["watches"][0]["live_html_metadata"]["description_status"], "present")

    def test_private_host_boundary(self):
        self.assertEqual(private_url("http://gsc.railway.internal:3001/sse"), "http://gsc.railway.internal:3001/sse")
        for url in ("https://example.com/sse", "http://gsc.railway.internal.evil/sse",
                    "http://secret@gsc.railway.internal/sse", "http://gsc.railway.internal/sse?key=x"):
            with self.assertRaises(ValueError):
                private_url(url)

    def test_destructive_tools_rejected_before_call(self):
        with self.assertRaises(ValueError):
            run(read_tool(None, "delete_site", {}))

    def test_tool_errors_are_not_empty_success(self):
        class Session:
            async def call_tool(self, name, args):
                return types.SimpleNamespace(isError=False, content=[types.SimpleNamespace(type="text", text="Error: permission denied")])
        with self.assertRaises(RuntimeError):
            run(read_tool(Session(), "list_properties", {}))

    def test_property_first_and_mobile_country_filters(self):
        calls = []
        class Session:
            def __init__(self, *args):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def initialize(self):
                pass
            async def call_tool(self, name, args):
                calls.append((name, args))
                data = {"properties": [{"site_url": "https://biketransport.co.za/"}]} if name == "list_properties" else {"rows": [{"query": "bike transport", "page": "https://biketransport.co.za/", "device": "MOBILE", "clicks": 3, "impressions": 40, "ctr": .075, "position": 4.2}, {"query": "other", "device": "DESKTOP"}]}
                return types.SimpleNamespace(isError=False, content=[types.SimpleNamespace(type="text", text=json.dumps(data))])
        @asynccontextmanager
        async def stream(*args, **kwargs):
            yield (None, None)
        modules = {"mcp": types.SimpleNamespace(ClientSession=Session),
                   "mcp.client": types.ModuleType("mcp.client"),
                   "mcp.client.sse": types.SimpleNamespace(sse_client=stream)}
        with patch.dict(sys.modules, modules), patch.dict(os.environ, {"GSC_MCP_URL": "http://gsc.railway.internal:3001/sse"}), patch("app.gsc_mcp.asyncio.timeout", return_value=nullcontext()):
            result = run(collect_gsc("https://biketransport.co.za/"))
            self.assertEqual(calls[0][0], "list_properties")
            self.assertEqual(calls[1][0], "get_search_analytics")
            self.assertEqual(calls[-1][1]["filter_dimension"], "country")
            self.assertEqual(calls[-1][1]["filter_expression"], "zaf")
            self.assertEqual(calls[-1][1]["dimensions"], "query,page,device")
            self.assertEqual(calls[-1][1]["data_state"], "final")
            self.assertEqual(result["mobile_organic_baseline"]["rows"][0]["clicks"], 3)
            self.assertEqual(len(result["mobile_organic_baseline"]["rows"]), 1)
            calls.clear()
            with self.assertRaises(ValueError):
                run(collect_gsc("https://wrong-property.example/"))
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
