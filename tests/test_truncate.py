"""本文の切り詰め処理（_truncate_search_bodies）の分岐を検証する。"""

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
        client._truncate_search_bodies(data, max_length)
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


async def test_keeps_body_at_the_boundary(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    data = await truncate(config, mock_api, {"pages": [{"body": "え" * 5}]}, 5)

    assert data["pages"][0]["body"] == "え" * 5


async def test_ignores_page_number_beside_the_results(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    # 検索応答の page はページ番号を表す整数。切り詰めの対象ではない
    data = await truncate(
        config, mock_api, {"page": 1, "pages": [{"body": "お" * 20}]}, 5
    )

    assert data["page"] == 1
    assert data["pages"][0]["body"] == "お" * 5 + "..."


async def test_leaves_detail_shaped_payload_untouched(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    # 詳細取得の応答は切り詰めない方針なので、page オブジェクトには手を触れない
    data = await truncate(config, mock_api, {"page": {"body": "う" * 20}}, 5)

    assert data["page"]["body"] == "う" * 20


async def test_ignores_unexpected_shapes(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    # body を持たない、あるいは dict ですらない応答でも例外にしない
    assert await truncate(config, mock_api, {"pages": [{"title": "本文なし"}]}, 5) == {
        "pages": [{"title": "本文なし"}]
    }
    assert await truncate(config, mock_api, {"pages": ["想定外"]}, 5) == {
        "pages": ["想定外"]
    }
    assert await truncate(config, mock_api, {"pages": [{"body": 42}]}, 5) == {
        "pages": [{"body": 42}]
    }
    assert await truncate(config, mock_api, ["想定外"], 5) == ["想定外"]
    assert await truncate(config, mock_api, {"pages": "想定外"}, 5) == {
        "pages": "想定外"
    }
