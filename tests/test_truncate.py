"""本文の切り詰め処理（_truncate_body_content）の分岐を検証する。"""

from typing import Any

import httpx2

from notepm_mcp_server import notepm

from .conftest import InstallMock


async def truncate(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    data: Any,
    max_length: int,
) -> Any:
    """クライアントを生成して切り詰め処理だけを適用する。"""
    mock_api(lambda request: httpx2.Response(200, json={}))
    async with notepm.NotePMAPIClient(config) as client:
        client._truncate_body_content(data, max_length)
    return data


async def test_truncates_each_page_in_search_result(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    data = await truncate(
        config,
        mock_api,
        {"pages": [{"body": "あ" * 20}, {"body": "い" * 3}]},
        max_length=5,
    )

    assert data["pages"][0]["body"] == "あ" * 5 + "..."
    # 上限以下の本文には省略記号を付けない
    assert data["pages"][1]["body"] == "い" * 3


async def test_truncates_page_object_in_detail_shape(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    data = await truncate(
        config, mock_api, {"page": {"body": "う" * 20}}, max_length=5
    )

    assert data["page"]["body"] == "う" * 5 + "..."


async def test_keeps_page_object_at_the_boundary(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    data = await truncate(config, mock_api, {"page": {"body": "え" * 5}}, max_length=5)

    assert data["page"]["body"] == "え" * 5


async def test_ignores_unexpected_shapes(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    # body を持たない、あるいは dict ですらない応答でも例外にしない
    assert await truncate(config, mock_api, {"pages": [{"title": "本文なし"}]}, 5) == {
        "pages": [{"title": "本文なし"}]
    }
    assert await truncate(config, mock_api, {"page": {"title": "本文なし"}}, 5) == {
        "page": {"title": "本文なし"}
    }
    assert await truncate(config, mock_api, ["想定外"], 5) == ["想定外"]
    assert await truncate(config, mock_api, {"pages": "想定外"}, 5) == {
        "pages": "想定外"
    }
