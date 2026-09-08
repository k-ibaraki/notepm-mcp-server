"""call_notepm_tool のツール振り分けを検証する。

ここは serve() の閉包から外へ出した層で、既存のテストが API クライアントを直接
叩いているため素通しになっていた。ツール名の取り違えや、未知のツール名で例外を
送出する退行を捕まえる。serve() は raise_exceptions=True で起動しているため、
この層で例外を送出するとサーバーごと停止する。
"""

import json

import httpx2
from mcp import types

from notepm_mcp_server import notepm

from .conftest import API_TOKEN, InstallMock, content_text


async def test_search_tool_reaches_the_search_endpoint(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    payload = {"pages": [{"title": "議事録", "body": "本文"}]}
    requests = mock_api(lambda request: httpx2.Response(200, json=payload))

    result = await notepm.call_notepm_tool(
        config,
        types.CallToolRequestParams(name="notepm_search", arguments={"q": "議事録"}),
    )

    assert result.is_error is False
    assert json.loads(content_text(result)) == payload
    assert requests[0].url.path == "/api/v1/pages"
    assert requests[0].url.params["q"] == "議事録"
    assert requests[0].headers["Authorization"] == f"Bearer {API_TOKEN}"


async def test_unknown_tool_returns_error_instead_of_raising(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    requests = mock_api(lambda request: httpx2.Response(200, json={"pages": []}))

    result = await notepm.call_notepm_tool(
        config,
        types.CallToolRequestParams(name="notepm_unknown", arguments={}),
    )

    assert result.is_error is True
    # どのツール名が届いたのかを呼び出し側が判別できる
    assert "notepm_unknown" in content_text(result)
    assert requests == []


async def test_missing_required_argument_returns_error(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """引数不足も例外にせず、HTTP を発行せずにエラーとして返す。"""
    requests = mock_api(lambda request: httpx2.Response(200, json={"pages": []}))

    result = await notepm.call_notepm_tool(
        config, types.CallToolRequestParams(name="notepm_search", arguments=None)
    )

    assert result.is_error is True
    assert "q" in content_text(result)
    assert requests == []
