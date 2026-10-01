"""In-process MCP tests for the server's tool surface (no network access)."""

import io
import json

import anyio
from mcp import Client
from PIL import Image

from ukmfolio_mcp import server

EXPECTED_TOOLS = {
    "list_courses", "list_deadlines", "get_submission_status",
    "list_announcements", "get_discussion", "list_documents", "read_document",
    "list_modules", "list_images", "view_image", "whoami",
}


class FakeClient:
    def get_courses(self):
        return [{"course_id": 1, "course_name": "Demo", "course_shortname": "DEMO"}]

    def view_image(self, image_url, max_edge=1568):
        if "missing" in image_url:
            raise ValueError("404 Client Error: Not Found")
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buf, "PNG")
        return buf.getvalue(), "png", {"image_url": image_url, "size": [8, 8]}


def _call(monkeypatch, name, args):
    monkeypatch.setattr(server, "get_client", lambda: FakeClient())

    async def go():
        async with Client(server.mcp) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            return tools, await client.call_tool(name, args)

    return anyio.run(go)


def test_all_tools_registered(monkeypatch):
    tools, result = _call(monkeypatch, "list_courses", {})
    assert tools == EXPECTED_TOOLS
    assert result.structured_content["result"][0]["course_shortname"] == "DEMO"


def test_view_image_returns_text_then_image_without_structured_copy(monkeypatch):
    _, result = _call(monkeypatch, "view_image", {"image_url": "https://x/a.png"})
    assert not result.is_error
    assert [c.type for c in result.content] == ["text", "image"]
    assert json.loads(result.content[0].text)["size"] == [8, 8]
    assert result.content[1].mime_type == "image/png"
    assert result.structured_content is None  # base64 not duplicated


def test_tool_errors_reach_the_model(monkeypatch):
    _, result = _call(monkeypatch, "view_image", {"image_url": "https://x/missing.png"})
    assert result.is_error
    assert "ValueError: 404 Client Error: Not Found" in result.content[0].text
