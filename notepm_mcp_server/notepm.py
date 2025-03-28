import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import BaseModel
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

# NotePM API関連の設定
NOTEP_API_BASE: str = f"https://{os.getenv('NOTEPM_TEAM')}.notepm.jp/api/v1/pages"
AUTH_TOKEN = os.getenv("NOTEPM_API_TOKEN")


class SearchParams(BaseModel):
    q: str
    only_title: int = 0
    include_archived: int = 0
    note_code: str | None = None
    tag_name: str | None = None
    created: str | None = None
    page: int = 1
    per_page: int = 50


async def serve() -> None:
    logger = logging.getLogger(__name__)

    server: Server = Server("notepm-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="search_notepm",
                description="NotePMで指定されたクエリを検索します。",
                inputSchema=SearchParams.schema(),
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "search_notepm":
            params = SearchParams(**arguments)
            headers = {
                "Authorization": f"Bearer {AUTH_TOKEN}",
            }

            async with httpx.AsyncClient() as client:
                response = await client.get(NOTEP_API_BASE, params=params.dict(), headers=headers)

            if response.status_code != 200:
                raise ValueError(
                    f"Failed to fetch data from NotePM API: {response.status_code} {response.text}"
                )

            return [TextContent(type="text", text=response.text)]

        raise ValueError(f"Unknown tool: {name}")

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=True)
