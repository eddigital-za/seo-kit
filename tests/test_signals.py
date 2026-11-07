import asyncio
import json
import os
import sys
import types
import unittest
from contextlib import asynccontextmanager, nullcontext
from unittest.mock import patch

from app.gsc_mcp import collect_gsc, private_url, read_tool


def run(coroutine):
    # These fakes complete synchronously; no Windows socket/event-loop setup is needed.
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    raise AssertionError("Unexpected asynchronous I/O in unit test")


class SignalsTest(unittest.TestCase):
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
                data = {"properties": [{"site_url": "https://biketransport.co.za/"}]} if name == "list_properties" else {"rows": [{"query": "bike transport", "page": "https://biketransport.co.za/", "clicks": 3, "impressions": 40, "ctr": .075, "position": 4.2}]}
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
            self.assertEqual(json.loads(calls[-1][1]["filters"]), [
                {"dimension": "country", "operator": "equals", "expression": "zaf"},
                {"dimension": "device", "operator": "equals", "expression": "MOBILE"}])
            self.assertEqual(calls[-1][1]["data_state"], "final")
            self.assertEqual(result["mobile_organic_baseline"]["rows"][0]["clicks"], 3)
            calls.clear()
            with self.assertRaises(ValueError):
                run(collect_gsc("https://wrong-property.example/"))
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
