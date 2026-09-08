"""NotePMAPIClient が応答をどう組み立て、どこまで送り直すかを検証する。

失敗をどう分類し、何をログに出すかは test_api_errors.py にある。ここでは、その分類に
至るまでの送信の振る舞い（再試行するかどうか、打ち切り）を扱う。
"""

import asyncio
import json
from typing import Any

import httpx2
import pytest

from notepm_mcp_server import notepm

from .conftest import API_BASE, API_TOKEN, TEAM, InstallMock


async def test_search_returns_response_as_json(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    payload = {
        "page": 1,
        "per_page": 10,
        "total": 1,
        "pages": [{"page_id": 1, "title": "議事録", "body": "短い本文"}],
    }
    mock_api(lambda request: httpx2.Response(200, json=payload))

    async with notepm.NotePMAPIClient(config) as client:
        result = await client.search(notepm.SearchParams(q="議事録"))

    assert json.loads(result) == payload
    # ensure_ascii=False のため、日本語がエスケープされずそのまま含まれる
    assert "議事録" in result


async def test_search_sends_token_and_omits_unset_params(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    requests = mock_api(lambda request: httpx2.Response(200, json={"pages": []}))

    async with notepm.NotePMAPIClient(config) as client:
        await client.search(notepm.SearchParams(q="設計", page=2, per_page=5))

    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/api/v1/pages"
    assert request.url.host == f"{TEAM}.notepm.jp"
    assert request.url.params["q"] == "設計"
    assert request.url.params["page"] == "2"
    assert request.url.params["per_page"] == "5"
    # 既定値のまま指定しなかった絞り込みは 0 として送信される
    assert request.url.params["only_title"] == "0"
    assert request.url.params["include_archived"] == "0"
    # 未指定の絞り込みは exclude_none により送信されない
    assert "note_code" not in request.url.params
    assert "tag_name" not in request.url.params
    assert "created" not in request.url.params
    assert request.headers["Authorization"] == f"Bearer {API_TOKEN}"


async def test_search_truncates_long_body(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    payload = {"pages": [{"title": "長文", "body": "あ" * 250}]}
    mock_api(lambda request: httpx2.Response(200, json=payload))

    async with notepm.NotePMAPIClient(config) as client:
        result = await client.search(notepm.SearchParams(q="長文"))

    assert json.loads(result)["pages"][0]["body"] == "あ" * 200 + "..."


async def test_search_keeps_short_body_untouched(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    body = "あ" * 200
    mock_api(lambda request: httpx2.Response(200, json={"pages": [{"body": body}]}))

    async with notepm.NotePMAPIClient(config) as client:
        result = await client.search(notepm.SearchParams(q="短文"))

    assert json.loads(result)["pages"][0]["body"] == body


async def test_search_uses_max_body_length_from_env(
    monkeypatch: pytest.MonkeyPatch, mock_api: InstallMock
) -> None:
    monkeypatch.setenv("NOTEPM_TEAM", TEAM)
    monkeypatch.setenv("NOTEPM_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("NOTEPM_MAX_BODY_LENGTH", "10")
    config = notepm.NotePMConfig()
    mock_api(lambda request: httpx2.Response(200, json={"pages": [{"body": "あ" * 30}]}))

    async with notepm.NotePMAPIClient(config) as client:
        result = await client.search(notepm.SearchParams(q="長文"))

    assert json.loads(result)["pages"][0]["body"] == "あ" * 10 + "..."


async def test_auth_error_is_not_retried(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    requests = mock_api(lambda request: httpx2.Response(401, text="Unauthorized"))

    with pytest.raises(notepm.NotePMAuthError):
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    # 認証エラーは何度送っても同じ答えなので、再試行しない
    assert len(requests) == 1


async def test_detail_requests_page_code_and_keeps_full_body(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    body = "い" * 500
    payload = {"page": {"page_code": "abc123", "title": "設計メモ", "body": body}}
    requests = mock_api(lambda request: httpx2.Response(200, json=payload))

    async with notepm.NotePMAPIClient(config) as client:
        result = await client.get_notepm_page_detail(
            notepm.NotePMDetailParams(page_code="abc123")
        )

    assert str(requests[0].url) == f"{API_BASE}/abc123"
    # 詳細取得では本文を切り詰めない
    assert json.loads(result)["page"]["body"] == body


async def test_client_is_closed_after_context_exit(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    mock_api(lambda request: httpx2.Response(200, json={"pages": []}))

    async with notepm.NotePMAPIClient(config) as client:
        await client.search(notepm.SearchParams(q="議事録"))

    assert client._client.is_closed


async def test_requests_carry_the_configured_timeout(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """タイムアウトは httpx2 の既定（5 秒）任せにせず、明示した値を送る。"""
    requests = mock_api(lambda request: httpx2.Response(200, json={"pages": []}))

    async with notepm.NotePMAPIClient(config) as client:
        await client.search(notepm.SearchParams(q="議事録"))

    assert client._client.timeout == notepm.HTTP_TIMEOUT
    assert requests[0].extensions["timeout"] == notepm.HTTP_TIMEOUT.as_dict()
    # 全文検索は時間がかかり得るので、読み取りだけは既定より長く取っている
    assert notepm.HTTP_TIMEOUT.read == 30.0


async def test_client_is_built_with_the_configured_limits(
    config: notepm.NotePMConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接続数と keepalive も既定任せにしない。

    httpx2 は limits を公開しないため、生成時の引数で確かめるほかない。
    渡し忘れると keepalive が既定の 5 秒に戻り、接続の再利用が静かに効かなくなる。
    """
    factory = notepm.httpx2.AsyncClient
    captured: list[dict[str, Any]] = []

    def recording_factory(**kwargs: Any) -> httpx2.AsyncClient:
        captured.append(kwargs)
        return factory(**kwargs)

    monkeypatch.setattr(notepm.httpx2, "AsyncClient", recording_factory)

    async with notepm.NotePMAPIClient(config):
        pass

    assert captured[0]["limits"] is notepm.HTTP_LIMITS


async def test_search_retries_rate_limited_response(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """429 は待ってから送り直し、成功したらその結果を返す。"""
    payload = {"pages": [{"title": "議事録", "body": "本文"}]}
    responses = [
        httpx2.Response(429, text="Too Many Requests"),
        httpx2.Response(200, json=payload),
    ]
    requests = mock_api(lambda request: responses.pop(0))

    async with notepm.NotePMAPIClient(config) as client:
        result = await client.search(notepm.SearchParams(q="議事録"))

    assert json.loads(result) == payload
    assert len(requests) == 2


async def test_search_gives_up_after_max_attempts(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """一時的なサーバーエラーが続く場合は、上限まで試してエラーにする。"""
    requests = mock_api(lambda request: httpx2.Response(503, text="Unavailable"))

    with pytest.raises(notepm.NotePMServerError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    assert "503" in str(error.value)
    assert len(requests) == notepm.MAX_ATTEMPTS


async def test_search_stops_at_the_total_timeout(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """応答が返らないまま上限に達したら、待ち続けずにエラーとして返す。

    試行ごとの上限しか持たないと、再試行の分だけ 1 回の呼び出しが伸びる。
    ホスト側の制限に先に打ち切られると理由が伝わらないため、こちらで打ち切る。
    """
    monkeypatch.setattr(notepm, "TOTAL_TIMEOUT_SECONDS", 0.05)

    async def never_answers(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(30)
        return httpx2.Response(200, json={"pages": []})

    mock_api(never_answers)

    with pytest.raises(notepm.NotePMTimeoutError, match="秒以内に結果を得られませんでした"):
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))


async def test_total_timeout_also_bounds_the_retry_waits(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上限は再試行の待ち時間も含めて効く。

    待っている最中に上限へ達したら、次の試行は行わない。
    """
    monkeypatch.setattr(notepm, "TOTAL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(notepm, "RETRY_BACKOFF_SECONDS", 30.0)
    monkeypatch.setattr(notepm, "MAX_RETRY_WAIT_SECONDS", 30.0)

    requests = mock_api(lambda request: httpx2.Response(503, text="Unavailable"))

    with pytest.raises(notepm.NotePMTimeoutError, match="秒以内に結果を得られませんでした"):
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    assert len(requests) == 1


async def test_client_survives_a_total_timeout(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """打ち切った後も、同じクライアントで次の呼び出しができる。

    クライアントはサーバーの生存期間を通じて使い回すので、一度の打ち切りが
    以降の呼び出しまで巻き込んではならない。
    """
    monkeypatch.setattr(notepm, "TOTAL_TIMEOUT_SECONDS", 0.05)

    async def slow_then_fast(request: httpx2.Request) -> httpx2.Response:
        if request.url.params.get("q") == "遅い":
            await asyncio.sleep(30)
        return httpx2.Response(200, json={"pages": []})

    mock_api(slow_then_fast)

    async with notepm.NotePMAPIClient(config) as client:
        with pytest.raises(notepm.NotePMTimeoutError, match="秒以内に結果を得られませんでした"):
            await client.search(notepm.SearchParams(q="遅い"))

        # 二度目は本来の上限で送る。詰めた上限のままだと、遅い環境で
        # 「使えなくなった」のか「間に合わなかった」のか区別が付かない。
        monkeypatch.setattr(notepm, "TOTAL_TIMEOUT_SECONDS", 60.0)
        result = await client.search(notepm.SearchParams(q="速い"))

    assert json.loads(result) == {"pages": []}


async def test_search_retries_a_broken_connection(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """張り置いた接続が切られていた場合は、繋ぎ直して送り直す。

    keepalive を伸ばした分だけ、相手に閉じられた接続を掴む機会は増える。
    """
    attempts: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx2.RemoteProtocolError("Server disconnected", request=request)
        return httpx2.Response(200, json={"pages": []})

    requests = mock_api(handler)

    async with notepm.NotePMAPIClient(config) as client:
        result = await client.search(notepm.SearchParams(q="議事録"))

    assert json.loads(result) == {"pages": []}
    assert len(requests) == 2


async def test_read_timeout_is_not_retried(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """応答を返さない相手を待つ時間が、試行回数の分だけ積み上がらないようにする。

    接続系のエラーと違い、読み取りのタイムアウトは 1 回あたりの待ちが長い。
    再試行の対象に含めると、全体の上限をすぐ食い潰す。
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    requests = mock_api(handler)

    # 全体の打ち切りに化けさせず、原因が分かる形のまま返す
    with pytest.raises(httpx2.ReadTimeout):
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    assert len(requests) == 1


async def test_detail_retries_transient_failure(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """詳細取得も検索と同じ再試行の経路を通る。"""
    payload = {"page": {"page_code": "abc123", "body": "本文"}}
    responses = [
        httpx2.Response(502, text="Bad Gateway"),
        httpx2.Response(200, json=payload),
    ]
    requests = mock_api(lambda request: responses.pop(0))

    async with notepm.NotePMAPIClient(config) as client:
        result = await client.get_notepm_page_detail(
            notepm.NotePMDetailParams(page_code="abc123")
        )

    assert json.loads(result) == payload
    assert len(requests) == 2
    assert str(requests[0].url) == f"{API_BASE}/abc123"
