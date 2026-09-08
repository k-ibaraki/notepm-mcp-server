"""page_code の入力検証を検証する。

page_code は詳細取得 URL のパス要素へ埋め込まれるため、検証が無いと httpx2 の
URL 正規化によってページ詳細以外のエンドポイントへ到達できてしまう（Issue #5）。
MCP のツール引数は LLM が組み立てる以上、外部から混入した値が届く前提で守る。
"""

import json

import httpx2
import pytest
from mcp import types
from pydantic import ValidationError

from notepm_mcp_server import notepm

from .conftest import API_BASE, TEAM, InstallMock, content_text

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

# 拒むべき ASCII 記号。制御文字（C0 0x00-0x1f・DEL 0x7f・C1 0x80-0x9f）は
# 別途まとめて扱う。
DENIED_ASCII_SYMBOLS = set(" /\\.?#%:")

# 不変条件テスト用の探り文字。区切りに見える記号・各種の空白・書式制御・BMP 外まで、
# 「通ったとして本当に単一のパス要素へ収まるのか」を疑いたくなるものを並べる。
# 個々の可否は他のテストの担当なので、ここでは通った値の行き先だけを見る。
INVARIANT_PROBE_CHARS = [
    "\uff0f",  # FULLWIDTH SOLIDUS
    "\u2044",  # FRACTION SLASH
    "\u2215",  # DIVISION SLASH
    "\uff3c",  # FULLWIDTH REVERSE SOLIDUS
    "\uff0e",  # FULLWIDTH FULL STOP
    "\uff03",  # FULLWIDTH NUMBER SIGN
    "\uff1f",  # FULLWIDTH QUESTION MARK
    "\uff05",  # FULLWIDTH PERCENT SIGN
    "\uff1a",  # FULLWIDTH COLON
    "\u00a0",  # NO-BREAK SPACE
    "\u2007",  # FIGURE SPACE
    "\u200b",  # ZERO WIDTH SPACE
    "\u2028",  # LINE SEPARATOR
    "\u2029",  # PARAGRAPH SEPARATOR
    "\u202a",  # LEFT-TO-RIGHT EMBEDDING
    "\u202e",  # RIGHT-TO-LEFT OVERRIDE
    "\u2066",  # LEFT-TO-RIGHT ISOLATE
    "\ufeff",  # BOM
    "\u0085",  # NEL
    "\u3000",  # 全角スペース
    "\u180e",  # MONGOLIAN VOWEL SEPARATOR
    "\U0001f600",  # BMP 外の絵文字
    "é",
    "中",
]


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
    """不正な値では HTTP を一切発行せず、エラーとして返る。

    例外ではなく結果として返ることが要点。serve() は raise_exceptions=True で
    起動しているため、ここで例外を送出するとサーバーごと停止する。
    """
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
    """文字クラスに意図しない範囲指定が紛れていないことを 1 文字ずつ確認する。

    拒否リスト方式では、範囲指定の書き損じがそのまま穴になる。ASCII 全域に加え、
    C1 制御文字（U+0080-U+009F）まで判定を突き合わせ、境界を固定する。
    """
    mismatched: list[str] = []
    for code_point in range(0xA0):
        char = chr(code_point)
        is_control = code_point <= 0x1F or 0x7F <= code_point <= 0x9F
        should_accept = not is_control and char not in DENIED_ASCII_SYMBOLS

        try:
            notepm.NotePMDetailParams(page_code=f"a{char}b")
            accepted = True
        except ValidationError:
            accepted = False

        if accepted != should_accept:
            mismatched.append(f"U+{code_point:04X} {char!r}: 期待={should_accept}")

    assert mismatched == []


async def test_accepted_values_never_leave_the_page_detail_path(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """検証を通った値は、必ずページ詳細の単一パス要素に収まる。

    この修正が本当に守っているのは、受け付ける文字の一覧ではなくこの不変条件の
    ほうである。文字の可否を変えるのは構わないが、その結果リクエストが
    /api/v1/pages/<単一要素> から出てはならない、という形で表明する。

    候補には既知の危険値も混ぜてある。いまは検証に弾かれて素通りするが、将来
    拒否リストを緩めれば通るようになり、そのとき逸脱としてここで捕まる。
    """
    requests = mock_api(lambda request: httpx2.Response(200, json={"page": {}}))

    candidates = [*UNSAFE_PAGE_CODES, *SAFE_PAGE_CODES]
    for char in INVARIANT_PROBE_CHARS:
        candidates += [char, f"a{char}b"]

    violations: list[str] = []
    accepted = 0
    for page_code in candidates:
        try:
            params = notepm.NotePMDetailParams(page_code=page_code)
        except ValidationError:
            continue  # 弾かれた値は不変条件の対象外
        accepted += 1

        try:
            async with notepm.NotePMAPIClient(config) as client:
                await client.get_notepm_page_detail(params)
        except Exception as e:  # URL 組み立て自体が壊れる値も逸脱として扱う
            violations.append(f"{page_code!r} -> {type(e).__name__}: {e}")
            continue

        url = requests[-1].url
        within_page_detail = (
            url.host == f"{TEAM}.notepm.jp"
            and url.path.startswith("/api/v1/pages/")
            and len(url.path.strip("/").split("/")) == 4
            and url.query == b""
            and not url.fragment
        )
        if not within_page_detail:
            violations.append(f"{page_code!r} -> {url}")

    assert violations == []
    # 候補が全部弾かれていると、上の表明は何も確かめていないことになる
    assert accepted > 0
