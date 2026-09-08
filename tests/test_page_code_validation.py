"""page_code の入力検証を検証する。

page_code は詳細取得 URL のパス要素へ埋め込まれるため、検証が無いと httpx2 の
URL 正規化によってページ詳細以外のエンドポイントへ到達できてしまう（Issue #5）。
MCP のツール引数は LLM が組み立てる以上、外部から混入した値が届く前提で守る。
"""

import json

import httpx2
import pytest
from mcp import types
from mcp.types import TextContent
from pydantic import ValidationError

from notepm_mcp_server import notepm

from .conftest import API_BASE, InstallMock

# 検証が無ければリクエスト先を変えられてしまう値。
# 前半は Issue で実測された値、後半は同じ狙いの変種。
UNSAFE_PAGE_CODES = [
    "../notes",  # ドットセグメントで /api/v1/notes へ抜ける
    "abc?foo=1",  # 任意のクエリを付与する
    "../../../",  # ホストのルートへ抜ける
    "a/b",  # パス要素を増やす
    "abc#frag",  # フラグメントを付与する
    "%2e%2e%2fnotes",  # パーセント符号化で "/../" を持ち込む
    "..%2Fnotes",
    "..\\notes",  # バックスラッシュ経由の区切り
    "//evil.example.com",
    "https://evil.example.com/x",
    "a:b",
    "a b",
    "　",  # 全角スペースのみ
    "a\x00b",  # 制御文字
    "abc\n",  # 正規表現の $ が行末に緩まないことの確認
    "",  # 空文字
]

# 受け付けるべき値。拒否リスト方式のため、通常のページコードのほかに
# 「危険ではないが変わった記号」も通る。いずれも単一のパス要素に収まる。
SAFE_PAGE_CODES = ["abc123", "ABCdef", "a-b_c", "9", "日本語コード", "a~b", "a&b=c"]

# 拒むべき ASCII 記号。制御文字（0x00-0x1f, 0x7f）は別途まとめて扱う。
DENIED_ASCII_SYMBOLS = set(" /\\.?#%:")


def content_text(result: types.CallToolResult) -> str:
    """CallToolResult の先頭ブロックからテキストを取り出す。"""
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


@pytest.mark.parametrize("page_code", UNSAFE_PAGE_CODES)
def test_rejects_page_codes_that_change_the_request_target(page_code: str) -> None:
    with pytest.raises(ValidationError):
        notepm.NotePMDetailParams(page_code=page_code)


@pytest.mark.parametrize("page_code", SAFE_PAGE_CODES)
def test_accepts_ordinary_page_codes(page_code: str) -> None:
    assert notepm.NotePMDetailParams(page_code=page_code).page_code == page_code


@pytest.mark.parametrize("page_code", UNSAFE_PAGE_CODES)
async def test_unsafe_page_code_is_rejected_before_any_request(
    config: notepm.NotePMConfig, mock_api: InstallMock, page_code: str
) -> None:
    """不正な値では HTTP を一切発行せず、エラーとして返る。"""
    requests = mock_api(lambda request: httpx2.Response(200, json={"page": {}}))

    result = await notepm.call_notepm_tool(
        config,
        types.CallToolRequestParams(
            name="notepm_page_detail", arguments={"page_code": page_code}
        ),
    )

    assert result.is_error is True
    # NotePM へは一度も出て行かない
    assert requests == []
    # 呼び出し側が原因を特定できるよう、項目名と与えた値が示される
    assert "page_code" in content_text(result)


async def test_error_is_returned_not_raised(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """例外送出はサーバーごと停止させるため、結果として返ることを固定する。

    serve() は raise_exceptions=True で起動している。
    """
    mock_api(lambda request: httpx2.Response(200, json={"page": {}}))

    result = await notepm.call_notepm_tool(
        config,
        types.CallToolRequestParams(
            name="notepm_page_detail", arguments={"page_code": "../notes"}
        ),
    )

    assert isinstance(result, types.CallToolResult)
    assert result.is_error is True


async def test_valid_page_code_still_reaches_the_detail_endpoint(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """正常なページコードでの取得は従来どおり動作する。"""
    payload = {"page": {"page_code": "abc123", "title": "設計メモ", "body": "本文"}}
    requests = mock_api(lambda request: httpx2.Response(200, json=payload))

    result = await notepm.call_notepm_tool(
        config,
        types.CallToolRequestParams(
            name="notepm_page_detail", arguments={"page_code": "abc123"}
        ),
    )

    assert result.is_error is False
    # エラーでないことだけでなく、応答がそのまま返っていることまで確かめる
    assert json.loads(content_text(result)) == payload
    assert str(requests[0].url) == f"{API_BASE}/abc123"


@pytest.mark.parametrize("page_code", SAFE_PAGE_CODES)
async def test_accepted_page_codes_stay_within_the_pages_path(
    config: notepm.NotePMConfig, mock_api: InstallMock, page_code: str
) -> None:
    """受け付けた値は、必ずページ詳細の下の単一パス要素に収まる。"""
    requests = mock_api(lambda request: httpx2.Response(200, json={"page": {}}))

    async with notepm.NotePMAPIClient(config) as client:
        await client.get_notepm_page_detail(
            notepm.NotePMDetailParams(page_code=page_code)
        )

    path = requests[0].url.path
    assert path.startswith("/api/v1/pages/")
    # /api/v1/pages/<code> の 4 要素より深くならない
    assert len(path.strip("/").split("/")) == 4
    assert requests[0].url.query == b""


def test_page_code_constraint_is_published_in_the_tool_schema() -> None:
    """LLM が制約を読めるよう、入力スキーマに露出させる。"""
    schema = notepm.NotePMDetailParams.model_json_schema()
    page_code = schema["properties"]["page_code"]

    assert page_code["pattern"] == notepm.PAGE_CODE_PATTERN
    assert page_code["description"]


def test_character_class_matches_the_intended_deny_set() -> None:
    """文字クラスに意図しない範囲指定が紛れていないことを ASCII 全域で確認する。

    拒否リスト方式では、範囲指定の書き損じがそのまま穴になる。1 文字ずつ判定を
    突き合わせ、拒むべき文字と受け入れるべき文字の境界を固定する。
    """
    mismatched: list[str] = []
    for code_point in range(0x80):
        char = chr(code_point)
        is_control = code_point <= 0x1F or code_point == 0x7F
        should_accept = not is_control and char not in DENIED_ASCII_SYMBOLS

        try:
            notepm.NotePMDetailParams(page_code=f"a{char}b")
            accepted = True
        except ValidationError:
            accepted = False

        if accepted != should_accept:
            mismatched.append(f"U+{code_point:04X} {char!r}: 期待={should_accept}")

    assert mismatched == []
