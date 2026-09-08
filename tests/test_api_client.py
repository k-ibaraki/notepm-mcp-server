"""NotePMAPIClient の検索・詳細取得の振る舞いを検証する。"""

import json

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


async def test_search_raises_on_error_status(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    requests = mock_api(lambda request: httpx2.Response(401, text="Unauthorized"))

    with pytest.raises(ValueError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    assert "401" in str(error.value)
    assert "Unauthorized" in str(error.value)
    # 認証エラーは何度送っても同じ答えなので、再試行しない
    assert len(requests) == 1


async def test_search_raises_on_broken_json(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    mock_api(lambda request: httpx2.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(ValueError, match="Invalid JSON response"):
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))


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


async def test_detail_raises_on_error_status(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    mock_api(lambda request: httpx2.Response(404, text="Not Found"))

    with pytest.raises(ValueError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.get_notepm_page_detail(
                notepm.NotePMDetailParams(page_code="missing")
            )

    assert "404" in str(error.value)
    assert "Not Found" in str(error.value)


async def test_detail_raises_on_broken_json(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    mock_api(lambda request: httpx2.Response(200, text="not json"))

    with pytest.raises(ValueError, match="Invalid JSON response"):
        async with notepm.NotePMAPIClient(config) as client:
            await client.get_notepm_page_detail(
                notepm.NotePMDetailParams(page_code="abc123")
            )


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

    with pytest.raises(ValueError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    assert "503" in str(error.value)
    assert len(requests) == notepm.MAX_ATTEMPTS


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
