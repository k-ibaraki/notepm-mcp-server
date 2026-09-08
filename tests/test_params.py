"""ツールの入力スキーマ（SearchParams / NotePMDetailParams）を検証する。

スキーマはそのまま MCP のツール定義として公開されるため、説明と制約が
欠けていないことと、範囲外の値がリクエストを送る前に弾かれることを確かめる。
"""

from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, ValidationError

from notepm_mcp_server import notepm

from .conftest import InstallMock


def properties(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()["properties"]


@pytest.mark.parametrize(
    "model",
    [notepm.SearchParams, notepm.NotePMDetailParams],
    ids=["SearchParams", "NotePMDetailParams"],
)
def test_every_field_has_a_description(model: type[BaseModel]) -> None:
    """説明の無いフィールドがあると、ツールを呼ぶ側が値の意味を判断できない。"""
    missing = [
        name
        for name, schema in properties(model).items()
        if not schema.get("description")
    ]

    assert missing == []


def test_flags_expose_their_allowed_values() -> None:
    """0/1 のフラグは、取り得る値が列挙としてスキーマに現れる。"""
    props = properties(notepm.SearchParams)

    assert props["only_title"]["enum"] == [0, 1]
    assert props["include_archived"]["enum"] == [0, 1]


def test_pagination_exposes_its_bounds() -> None:
    """ページングの上下限は NotePM API の仕様（per_page は最大 100）に合わせる。"""
    props = properties(notepm.SearchParams)

    assert props["page"]["minimum"] == 1
    assert props["per_page"]["minimum"] == 1
    assert props["per_page"]["maximum"] == 100
    # 既定値を小さく取っているのは意図的なので、変わったら気づけるようにしておく
    assert props["per_page"]["default"] == 10


@pytest.mark.parametrize(
    "name",
    ["created_at_from", "created_at_to", "updated_at_from", "updated_at_to"],
)
def test_date_filters_expose_their_format(name: str) -> None:
    """日付の書式は自明でないため、スキーマから読み取れる必要がある。"""
    schema = properties(notepm.SearchParams)[name]
    patterns = [
        member["pattern"] for member in schema["anyOf"] if "pattern" in member
    ]

    assert patterns == [notepm.DATE_PATTERN]
    assert "YYYY-MM-DD" in schema["description"]


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"q": "議事録", "per_page": 101}, id="per_pageが上限超過"),
        pytest.param({"q": "議事録", "per_page": 0}, id="per_pageが0"),
        pytest.param({"q": "議事録", "page": 0}, id="pageが0"),
        pytest.param({"q": "議事録", "only_title": 2}, id="only_titleが範囲外"),
        pytest.param(
            {"q": "議事録", "include_archived": -1}, id="include_archivedが範囲外"
        ),
        pytest.param(
            {"q": "議事録", "created_at_from": "2020/08/01"}, id="日付がスラッシュ区切り"
        ),
        pytest.param(
            {"q": "議事録", "updated_at_to": "2020-08-01T10:10:10+09:00"},
            id="日付が日時形式",
        ),
        # 正規表現に \d を使うと Unicode の数字全体に一致し、ここが通ってしまう
        pytest.param(
            {"q": "議事録", "created_at_to": "２０２０-０８-０１"}, id="日付が全角数字"
        ),
    ],
)
def test_out_of_range_values_are_rejected_before_any_request(
    arguments: dict[str, Any],
) -> None:
    """範囲外の値はモデルの生成時点で弾かれる。

    mock_api を入れずに実行しているため、ここで HTTP クライアントが作られていれば
    conftest の forbid_real_http フィクスチャが失敗させる。つまりこのテストが通ることは
    リクエストを送る前に弾けていることを意味する。
    """
    with pytest.raises(ValidationError):
        notepm.SearchParams(**arguments)


@pytest.mark.parametrize(
    "value, expected",
    [(True, 1), (False, 0)],
    ids=["真偽値のTrue", "真偽値のFalse"],
)
def test_flags_accept_booleans(value: bool, expected: int) -> None:
    """ツールを呼ぶ側が true/false を送っても 0/1 として受け取れる。

    引数は JSON として届くので、型注釈を通さない model_validate で検証する。
    """
    params = notepm.SearchParams.model_validate({"q": "議事録", "only_title": value})

    assert params.only_title == expected


def test_boundary_values_are_accepted() -> None:
    params = notepm.SearchParams(
        q="議事録", page=1, per_page=100, created_at_from="2020-08-01"
    )

    assert params.per_page == 100
    assert params.created_at_from == "2020-08-01"


async def test_date_filters_are_sent_only_when_given(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    requests = mock_api(lambda request: httpx2.Response(200, json={"pages": []}))

    async with notepm.NotePMAPIClient(config) as client:
        await client.search(
            notepm.SearchParams(
                q="議事録", created_at_from="2020-08-01", created_at_to="2020-08-31"
            )
        )

    params = requests[0].url.params
    assert params["created_at_from"] == "2020-08-01"
    assert params["created_at_to"] == "2020-08-31"
    # 未指定のものは exclude_none により送信されない
    assert "updated_at_from" not in params
    assert "updated_at_to" not in params
    assert "created" not in params
