"""Unit tests for Python AST and JSON tool manifest loaders."""

from __future__ import annotations

import json
from pathlib import Path

from mcp_doctor.loaders import load_json_manifest, load_python_file


def test_python_ast_loader_extracts_fastmcp_and_sdk_tools(tmp_path: Path) -> None:
    src = tmp_path / "server.py"
    src.write_text(
        '''
from mcp.types import Tool

class Server:
    def tool(self, name=None, description=None):
        def wrap(fn):
            return fn
        return wrap

mcp = Server()

@mcp.tool(name="search_tickets")
async def _search(ctx, query: str, limit: int = 25) -> list[str]:
    """Search issue tracker tickets by keyword.

    Args:
        query: Search string.
        limit: Max results.
    """
    limit = min(limit, 100)
    return [query] * limit

LEGACY_TOOL = Tool(
    name="ping_cluster",
    description="Check cluster control-plane reachability.",
    inputSchema={"type": "object", "properties": {}},
)
''',
        encoding="utf-8",
    )

    tools = load_python_file(src)
    assert [t.name for t in tools] == ["search_tickets", "ping_cluster"]
    first = tools[0]
    assert [p.name for p in first.parameters] == ["query", "limit"]
    assert first.parameters[0].required is True
    assert first.parameters[0].description == "Search string."
    assert first.parameters[1].required is False
    assert first.parameters[1].has_bounds is True


def test_python_ast_loader_handles_syntax_errors_gracefully(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def oops(:\n    pass\n", encoding="utf-8")
    assert load_python_file(broken) == []


def test_json_loader_supports_mcp_openai_and_anthropic_formats(tmp_path: Path) -> None:
    openai_file = tmp_path / "openai_tools.json"
    openai_file.write_text(
        json.dumps(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather_forecast",
                        "description": "Retrieve 3-day forecast for an IATA airport code.",
                        "parameters": {
                            "type": "object",
                            "required": ["airport"],
                            "properties": {
                                "airport": {"type": "string", "maxLength": 4},
                                "days": {"type": "integer", "maximum": 7},
                            },
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_json_manifest(openai_file)
    assert len(loaded) == 1
    assert loaded[0].name == "get_weather_forecast"
    assert len(loaded[0].parameters) == 2
    assert all(p.has_bounds for p in loaded[0].parameters)

    # Unrelated JSON (e.g. package.json) must produce 0 tools rather than false positives
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text('{"name": "my-app", "version": "1.0.0"}', encoding="utf-8")
    assert load_json_manifest(pkg_json) == []


def test_python_ast_expands_basemodel_and_typeddict(tmp_path: Path) -> None:
    src = tmp_path / "basemodel_server.py"
    src.write_text('''
from pydantic import BaseModel, Field
from typing import TypedDict

class SearchRequest(BaseModel):
    query: str = Field(..., description="Search term")
    limit: int = Field(default=10, le=100, description="Max results")

class SearchResp(TypedDict):
    items: list[str]
    total: int

@mcp.tool(name="search")
def search(req: SearchRequest, resp: SearchResp = None) -> list[str]:
    """Search."""
    return []
''')
    from mcp_doctor.loaders import load_python_file
    tools = load_python_file(src)
    assert len(tools) == 1
    names = [p.name for p in tools[0].parameters]
    assert "req.query" in names
    assert "req.limit" in names
    # TypedDict fields expanded
    assert "resp.items" in names
    assert "resp.total" in names
