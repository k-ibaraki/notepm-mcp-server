"""NotePMAPIClient の検索・詳細取得が、応答をどう組み立てるかを検証する。

成功したときの経路だけを扱う。失敗したときの分類とログは test_api_errors.py にある。
"""

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
