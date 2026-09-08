"""create_server() が組み立てたサーバーと、serve() の結線を検証する。

mcp.client.Client は in-process の Server をそのまま受け取れるため、stdio を
介さずにクライアント側から叩ける。ツールの失敗でサーバーが停止しないことを、
ここで確認する（Issue #6）。
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from mcp import types
from mcp.client import Client
from mcp.server import Server
from mcp.shared.exceptions import MCPError

from notepm_mcp_server import notepm

from .conftest import API_TOKEN, TEAM, InstallMock


async def test_list_tools_exposes_both_tools(config: notepm.NotePMConfig) -> None:
    async with Client(notepm.create_server(config)) as client:
        result = await client.list_tools()

    assert [tool.name for tool in result.tools] == [
        "notepm_search",
        "notepm_page_detail",
    ]


async def test_tool_failure_keeps_the_connection_alive(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """API が失敗しても is_error の結果が返り、後続の呼び出しは成功する。"""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.params.get("q") == "失敗":
            return httpx2.Response(500, text="サーバーエラー")
        return httpx2.Response(200, json={"pages": []})

    mock_api(handler)

    with caplog.at_level(logging.ERROR, logger=notepm.__name__):
        async with Client(notepm.create_server(config)) as client:
            failed = await client.call_tool("notepm_search", {"q": "失敗"})
            succeeded = await client.call_tool("notepm_search", {"q": "成功"})

    assert failed.is_error is True
    assert succeeded.is_error is False

    # 想定外の失敗は握りつぶさず、トレースバックを残す（原因を追える）
    records = [r for r in caplog.records if r.name == notepm.__name__]
    assert [(r.levelno, r.exc_info is not None) for r in records] == [
        (logging.ERROR, True)
    ]


async def test_http_client_is_shared_across_tool_calls(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP クライアントは lifespan が 1 つだけ作り、停止時に閉じる（Issue #10）。

    呼び出しごとに作り直すと、コネクションプールと TLS ハンドシェイクが毎回
    捨てられる。ここでは検索と詳細取得をまたいで同じクライアントが使われること、
    そしてセッションを抜けた時点で閉じられていることを固定する。
    """
    mock_api(lambda request: httpx2.Response(200, json={"pages": []}))

    factory = notepm.httpx2.AsyncClient
    created: list[httpx2.AsyncClient] = []

    def recording_factory(**kwargs: Any) -> httpx2.AsyncClient:
        http_client = factory(**kwargs)
        created.append(http_client)
        return http_client

    monkeypatch.setattr(notepm.httpx2, "AsyncClient", recording_factory)

    async with Client(notepm.create_server(config)) as client:
        searched = await client.call_tool("notepm_search", {"q": "議事録"})
        detailed = await client.call_tool("notepm_page_detail", {"page_code": "abc123"})

    assert searched.is_error is False
    assert detailed.is_error is False
    assert len(created) == 1
    assert created[0].is_closed


async def test_unexpected_exception_does_not_stop_the_server(
    config: notepm.NotePMConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ハンドラを抜けた想定外の例外でも、接続は次の呼び出しを受け付ける。

    Client 側の raise_exceptions には config の値をそのまま渡す。既定（無効）では
    例外がエラー応答に変換され、接続は保たれる。
    """
    assert config.raise_exceptions is False
    calls: list[str] = []

    async def explode(
        client: notepm.NotePMAPIClient, params: object
    ) -> types.CallToolResult:
        calls.append("call")
        raise RuntimeError("想定外の失敗")

    monkeypatch.setattr(notepm, "call_notepm_tool", explode)

    client = Client(
        notepm.create_server(config), raise_exceptions=config.raise_exceptions
    )
    async with client:
        # ハンドラを抜けた例外はプロトコルのエラー応答になる（接続は切れない）
        with pytest.raises(MCPError):
            await client.call_tool("notepm_search", {"q": "一度目"})
        with pytest.raises(MCPError):
            await client.call_tool("notepm_search", {"q": "二度目"})

        # 接続は生きたままなので、例外を投げないツール一覧は変わらず取得できる
        listed = await client.list_tools()

    assert calls == ["call", "call"]
    assert len(listed.tools) == 2


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        pytest.param(None, False, id="未設定なら無効"),
        pytest.param("1", True, id="1で有効"),
    ],
)
async def test_serve_passes_raise_exceptions_from_config(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: bool
) -> None:
    """serve() は NotePMConfig の値をそのまま server.run() へ渡す。"""
    monkeypatch.setenv("NOTEPM_TEAM", TEAM)
    monkeypatch.setenv("NOTEPM_API_TOKEN", API_TOKEN)
    if env_value is not None:
        monkeypatch.setenv("NOTEPM_RAISE_EXCEPTIONS", env_value)

    recorded: dict[str, object] = {}

    @asynccontextmanager
    async def fake_stdio_server() -> AsyncIterator[tuple[object, object]]:
        yield (object(), object())

    async def fake_run(
        self: Server[Any],
        read_stream: object,
        write_stream: object,
        initialization_options: object,
        raise_exceptions: bool = False,
    ) -> None:
        recorded["raise_exceptions"] = raise_exceptions

    monkeypatch.setattr(notepm, "stdio_server", fake_stdio_server)
    monkeypatch.setattr(Server, "run", fake_run)

    await notepm.serve()

    assert recorded["raise_exceptions"] is expected
