"""call_notepm_tool のツール振り分けを検証する。

ここは serve() の閉包から外へ出した層で、既存のテストが API クライアントを直接
叩いているため素通しになっていた。ツール名の取り違えや、未知のツール名で例外を
送出する退行を捕まえる。呼び出し側が読める結果として返すのがこの層の責務で、
例外を投げ返すと、拒否の理由がプロトコルのエラーに潰れて伝わらない。
"""

import json
import logging

import httpx2
import pytest
from mcp import types

from notepm_mcp_server import notepm

from .conftest import API_TOKEN, InstallMock, content_text


async def test_search_tool_reaches_the_search_endpoint(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    payload = {"pages": [{"title": "議事録", "body": "本文"}]}
    requests = mock_api(lambda request: httpx2.Response(200, json=payload))

    async with notepm.NotePMAPIClient(config) as client:
        result = await notepm.call_notepm_tool(
            client,
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

    async with notepm.NotePMAPIClient(config) as client:
        result = await notepm.call_notepm_tool(
            client,
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

    async with notepm.NotePMAPIClient(config) as client:
        result = await notepm.call_notepm_tool(
            client, types.CallToolRequestParams(name="notepm_search", arguments=None)
        )

    assert result.is_error is True
    assert "q" in content_text(result)
    assert requests == []


async def test_unknown_tool_is_logged_as_a_rejection(
    config: notepm.NotePMConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """公開していない名前での呼び出しは、拒否として警告に留める。

    サーバーの不具合ではないため、トレースバック付きの ERROR にはしない。
    """
    with caplog.at_level(logging.WARNING, logger=notepm.__name__):
        async with notepm.NotePMAPIClient(config) as client:
            await notepm.call_notepm_tool(
                client, types.CallToolRequestParams(name="notepm_unknown", arguments={})
            )

    records = [r for r in caplog.records if r.name == notepm.__name__]
    assert [(r.levelno, r.exc_info is None) for r in records] == [
        (logging.WARNING, True)
    ]


async def test_tool_name_cannot_forge_a_log_line(
    config: notepm.NotePMConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """ツール名は外部由来なので、改行を含んでいても 1 行に収める。

    生のまま出すと、偽の ERROR 行を差し込んで本物の記録に見せかけられる。
    """
    forged = "x\nERROR:notepm_mcp_server.notepm:偽の行です"

    with caplog.at_level(logging.WARNING, logger=notepm.__name__):
        async with notepm.NotePMAPIClient(config) as client:
            result = await notepm.call_notepm_tool(
                client, types.CallToolRequestParams(name=forged, arguments={})
            )

    assert result.is_error is True
    records = [r for r in caplog.records if r.name == notepm.__name__]
    assert "\n" not in records[0].getMessage()
