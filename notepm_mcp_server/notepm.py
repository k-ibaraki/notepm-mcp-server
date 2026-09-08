from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import BaseModel, Field, ValidationError
import httpx2
import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Annotated, Any, Literal, Optional
from dotenv import load_dotenv
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import logging
import math

# 環境変数の読み込み
load_dotenv()

logger = logging.getLogger(__name__)

# 真として扱う環境変数の値。大文字小文字は区別しない。
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


class NotePMConfig:
    """NotePM APIの設定を管理するクラス

    環境変数から必要な設定を読み込み、APIエンドポイントのURLを生成します。

    Attributes:
        team (str): NotePMのチーム名
        api_token (str): NotePM APIのトークン
        api_base (str): APIのベースURL
        max_body_length (int): 検索結果の本文の最大文字数
        raise_exceptions (bool): ハンドラの例外を再送出するか（開発時のみ有効にする）
    """

    def __init__(self) -> None:
        self.team = os.getenv("NOTEPM_TEAM")
        self.api_token = os.getenv("NOTEPM_API_TOKEN")
        if not self.team or not self.api_token:
            raise ValueError("環境変数NOTEPM_TEAMとNOTEPM_API_TOKENが必要です")
        self.api_base = f"https://{self.team}.notepm.jp/api/v1/pages"

        # 検索結果の本文の最大文字数を環境変数から取得（デフォルト: 200）
        self.max_body_length = int(os.getenv("NOTEPM_MAX_BODY_LENGTH", "200"))

        # 例外の再送出はデバッグ用。有効にすると想定外の例外でサーバーが停止するため、
        # 常駐する通常起動では無効のままにする（デフォルト: 無効）。
        self.raise_exceptions = (
            os.getenv("NOTEPM_RAISE_EXCEPTIONS", "").strip().lower()
            in _TRUTHY_ENV_VALUES
        )


# NotePM API が日付の絞り込みで受け付ける書式（YYYY-MM-DD）。
# 実在する日付かまでは検査しない。ここでの目的は "2020/08/01" や
# ISO 8601 の日時のような別書式を、リクエストを送る前に弾くことにある。
# \d ではなく [0-9] と書くのは、pydantic の pattern が使う正規表現では \d が
# Unicode の数字全体に一致し、全角の "２０２０-０８-０１" まで通してしまうため。
# JSON Schema の pattern は ECMA-262 準拠（\d は ASCII のみ）とされているので、
# \d のままだと公開したスキーマの意味とサーバー側の判定もずれる。
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"

DateFilter = Optional[Annotated[str, Field(pattern=DATE_PATTERN)]]


# このモデルの JSON スキーマは notepm_search の入力スキーマとしてそのまま公開される。
# クラスの docstring も schema の description として配信されるため、保守者向けのメモは
# ここに書き、docstring には呼び出し側にとって意味のある説明だけを残すこと。
# ツールを呼ぶ側が値の意味と範囲を読み取れるよう、フィールドごとに説明と制約を持たせて
# いる。制約は NotePM API の仕様に合わせること。
class SearchParams(BaseModel):
    """NotePM のページ検索 API (GET /api/v1/pages) に渡すパラメータ。"""

    q: Annotated[
        str,
        Field(
            description=(
                "検索する文字列。空白区切りの単語による AND 検索で、"
                "自然言語の文章は解釈されない。"
            )
        ),
    ]
    only_title: Annotated[
        Literal[0, 1],
        Field(description="検索する範囲。0 は本文を含む全文検索、1 はタイトルのみの検索。"),
    ] = 0
    include_archived: Annotated[
        Literal[0, 1],
        Field(description="アーカイブ済みページの扱い。0 は検索対象から除き、1 は含める。"),
    ] = 0
    note_code: Annotated[
        Optional[str],
        Field(
            description=(
                "特定のノートに絞り込む場合のノートコード（例: abcdef）。"
                "検索結果の note_code をそのまま渡せる。未指定なら全ノートが対象。"
            )
        ),
    ] = None
    tag_name: Annotated[
        Optional[str],
        Field(
            description=(
                "特定のタグに絞り込む場合のタグ名（例: 議事録）。"
                "タグ名そのものを渡す。未指定ならタグでは絞り込まない。"
            )
        ),
    ] = None
    created: Annotated[
        Optional[str],
        Field(
            description=(
                "作成者で絞り込む場合のユーザーコード（例: 0000000001）。"
                "検索結果の created_by.user_code に対応する。"
                "作成日での絞り込みではないので、日付は created_at_from / created_at_to を使う。"
            )
        ),
    ] = None
    created_at_from: Annotated[
        DateFilter,
        Field(description="作成日時の範囲の開始。YYYY-MM-DD 形式で指定する（例: 2020-08-01）。"),
    ] = None
    created_at_to: Annotated[
        DateFilter,
        Field(description="作成日時の範囲の終了。YYYY-MM-DD 形式で指定する（例: 2020-08-31）。"),
    ] = None
    updated_at_from: Annotated[
        DateFilter,
        Field(description="更新日時の範囲の開始。YYYY-MM-DD 形式で指定する（例: 2020-08-01）。"),
    ] = None
    updated_at_to: Annotated[
        DateFilter,
        Field(description="更新日時の範囲の終了。YYYY-MM-DD 形式で指定する（例: 2020-08-31）。"),
    ] = None
    page: Annotated[
        int,
        Field(
            ge=1,
            description="取得するページ番号。1 から始まる。件数の続きは page を増やして取得する。",
        ),
    ] = 1
    # 既定値は NotePM API の 20 ではなく 10。上限の 100 は API 仕様に合わせたもので、
    # 既定を小さく取っているのは応答が大きくなりすぎないようにするため（意図的）。
    per_page: Annotated[
        int,
        Field(
            ge=1,
            le=100,
            description=(
                "1 ページあたりの取得件数。NotePM API の上限は 100。"
                "応答が大きくなりすぎないよう、このサーバーの既定値は 10 にしている。"
            ),
        ),
    ] = 10


# page_code は詳細取得 URL のパス要素へそのまま埋め込まれる。httpx2 は URL を正規化する
# 際にドットセグメントを解決するため、検証が無いと "../notes" でページ詳細以外の
# エンドポイントへ、"abc?foo=1" で任意のクエリ付与へ到達できてしまう（Issue #5）。
# パスの構造を変え得る文字だけを拒む方針とし、以下を受け付けない:
#   空白類 / 制御文字(C0 0x00-0x1f・DEL 0x7f・C1 0x80-0x9f) /
#   パス区切り(/ \) / ドット(.) / クエリ(?) / フラグメント(#) /
#   スキーム区切り(:) / パーセント(%)
# % を拒むのは、"%2e%2e%2fnotes" が /api/v1/pages/../notes として送出されるため。
# ドットは "a.b" のように単独なら無害だが、".." だけを狙って除くと規則が読みにくく
# なるため一律で拒む。これらに該当しない値は、日本語を含めて単一のパス要素に収まる。
# \s は DATE_PATTERN の \d と同じくエンジン差が出るが、ずれるのは U+FEFF の 1 文字だけで、
# 公開スキーマ(ECMA-262)のほうが厳しい向きになるため、そのままにしている。
PAGE_CODE_PATTERN = r"^[^\s/\\.?#%:\x00-\x1f\x7f\x80-\x9f]+$"


# SearchParams と同じく、この docstring は notepm_page_detail の入力スキーマの
# description として公開される。保守者向けのメモはこちらのコメントへ書くこと。
class NotePMDetailParams(BaseModel):
    """NotePM のページ詳細取得 API (GET /api/v1/pages/{page_code}) に渡すパラメータ。"""

    page_code: Annotated[
        str,
        Field(
            pattern=PAGE_CODE_PATTERN,
            description=(
                "取得するページのページコード（例: aaaaad0001）。"
                "notepm_search の検索結果に含まれる page_code をそのまま渡す。"
                "空白・制御文字と / \\ . ? # % : は使えない。"
            ),
        ),
    ]


# httpx2 の既定は全体で 5 秒。全文検索は件数によっては数秒かかるため、読み取りだけ
# 長めに取り、接続とプール待ちは短いままにする。詰まっている相手を長く待つより、
# 早く失敗してエラーとして返したほうが呼び出し側は次の手を打てる。
HTTP_TIMEOUT = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

# stdio のサーバーは 1 クライアントからの逐次呼び出ししか受けないため、接続数は
# 少しでよい。keepalive_expiry を既定の 5 秒から伸ばすのは、検索してから詳細を取る
# までに呼び出し側の思考時間が挟まり、5 秒では張った接続が毎回捨てられるため。
# 伸ばした分だけサーバー側から切られた接続を掴む機会は増えるが、それは再試行が拾う。
HTTP_LIMITS = httpx2.Limits(
    max_connections=10, max_keepalive_connections=10, keepalive_expiry=60.0
)

# 再試行の対象とする状態コード。429 はレート制限、5xx は一時的なサーバー側の失敗で、
# いずれも待てば結果が変わり得る。認証エラーのような他の 4xx は何度送っても同じ答えが
# 返るだけなので、API に無駄な負荷をかけないよう対象にしない。
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# 再試行する転送エラー。接続を張れなかった場合と、張り置いた接続が相手に切られていた
# 場合（RemoteProtocolError）だけを拾う。ReadTimeout を含めると、応答を返さない相手に
# 対して待ち時間が試行回数の分だけ積み上がるため、あえて含めない。
RETRYABLE_TRANSPORT_ERRORS = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.RemoteProtocolError,
)

# 初回を含めた試行回数の上限。ツール呼び出しが長く戻らないほうが困るので、深追いしない。
MAX_ATTEMPTS = 3

# 再試行と待ち時間まで含めた、1 回の呼び出しの上限。試行ごとの上限しか持たないと、
# 読み取り 30 秒 × 3 試行で 90 秒を超え得る。MCP のホスト側にも独自の制限があり
# （Claude Code の MCP_TOOL_TIMEOUT など）、そちらに先に打ち切られると理由が呼び出し
# 側に伝わらないため、こちらで上限を持って明示的なエラーとして返す。
TOTAL_TIMEOUT_SECONDS = 60.0

# 1 回目の再試行までの待ち時間。以降は試行ごとに倍にする。
RETRY_BACKOFF_SECONDS = 0.5

# 待ち時間の上限。Retry-After は相手が決める値なので、大きな値をそのまま信じると
# ツール呼び出しが戻らなくなる。
MAX_RETRY_WAIT_SECONDS = 10.0


def _retry_after_seconds(response: httpx2.Response) -> float | None:
    """Retry-After ヘッダを秒数として読みます

    Args:
        response (httpx2.Response): 再試行対象の応答

    Returns:
        float | None: 秒数。ヘッダが無い場合と、HTTP-date のように
            秒数として読めない場合は None。
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        # HTTP-date 形式は解釈しない。読めない値はバックオフに任せる。
        return None
    if not math.isfinite(seconds):
        # float() は "nan" や "inf" も受け付ける。nan は上限で丸めても nan のまま
        # sleep へ渡ってしまうため、ヘッダが無かったものとして扱う。
        return None
    return max(seconds, 0.0)


def _retry_delay(attempt: int, response: httpx2.Response | None) -> float:
    """次の試行までに待つ秒数を返します

    Args:
        attempt (int): 失敗した試行の番号（1 始まり）
        response (httpx2.Response | None): 失敗した応答。転送エラーなら None

    Returns:
        float: 待ち時間（秒）。Retry-After が読めればそれを優先し、
            読めなければ指数バックオフに落とす。いずれも上限で丸める。
    """
    # 試行ごとに倍にする（1 << n は 2 の n 乗）
    delay = RETRY_BACKOFF_SECONDS * (1 << (attempt - 1))
    if response is not None:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            delay = retry_after
    return min(delay, MAX_RETRY_WAIT_SECONDS)


class NotePMAPIClient:
    """NotePM APIクライアント

    非同期HTTPクライアントを使用してNotePM APIと通信を行います。
    サーバーの生存期間にひとつだけ作り、呼び出しをまたいで使い回す前提です
    （create_server() の lifespan を参照）。呼び出しごとに作り直すと、TLS の
    ハンドシェイクとコネクションプールが毎回捨てられます。
    コンテキストマネージャとして使用することで、リソースの適切な解放を保証します。
    """

    def __init__(self, config: NotePMConfig) -> None:
        """
        Args:
            config (NotePMConfig): API設定
        """
        self.config = config
        # 認証ヘッダは毎回同じなのでクライアントに持たせる。タイムアウトと接続数は
        # 既定任せにせず、上の定数で明示する。
        self._client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {config.api_token}"},
            timeout=HTTP_TIMEOUT,
            limits=HTTP_LIMITS,
        )

    async def __aenter__(self) -> "NotePMAPIClient":
        """非同期コンテキストマネージャのエントリーポイント"""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """非同期コンテキストマネージャの終了処理

        HTTPクライアントのリソースを適切に解放します。
        """
        await self._client.aclose()

    async def _get(
        self, url: str, params: dict[str, Any] | None = None
    ) -> httpx2.Response:
        """GET を発行します。再試行と待ち時間を含めて上限の時間で打ち切ります

        Args:
            url (str): 送信先の URL
            params (dict[str, Any] | None): クエリパラメータ

        Returns:
            httpx2.Response: 最後の試行の応答（成否は問わない）

        Raises:
            ValueError: 上限の時間までに応答を得られなかった場合
            httpx2.TransportError: 最後の試行でも接続できなかった場合
        """
        try:
            return await asyncio.wait_for(
                self._get_with_retry(url, params), TOTAL_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as e:
            raise ValueError(
                "NotePM APIからのデータ取得に失敗しました: "
                f"{TOTAL_TIMEOUT_SECONDS:.0f}秒以内に結果を得られませんでした"
            ) from e

    async def _get_with_retry(
        self, url: str, params: dict[str, Any] | None = None
    ) -> httpx2.Response:
        """GET を発行し、一時的な失敗であれば間を置いて再試行します

        再試行するのは、待てば結果が変わり得る失敗だけです（レート制限・一時的な
        サーバーエラー・接続の確立失敗・切られていた接続）。恒久的な失敗は最初の
        応答をそのまま返し、判断は呼び出し元に委ねます。

        Args:
            url (str): 送信先の URL
            params (dict[str, Any] | None): クエリパラメータ

        Returns:
            httpx2.Response: 最後の試行の応答（成否は問わない）

        Raises:
            httpx2.TransportError: 最後の試行でも接続できなかった場合
        """
        for attempt in range(1, MAX_ATTEMPTS):
            try:
                response = await self._client.get(url, params=params)
            except RETRYABLE_TRANSPORT_ERRORS as e:
                delay = _retry_delay(attempt, None)
                # 例外の文言にはサーバー由来の受信データが混ざり得る。ツール名と
                # 同じく %r で改行ごと落とし、偽のログ行を作られないようにする。
                logger.warning(
                    "NotePM API への接続に失敗しました（%d 回目、%.1f 秒後に再試行）: %r",
                    attempt,
                    delay,
                    e,
                )
            else:
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    return response
                delay = _retry_delay(attempt, response)
                # 応答本文は外部由来なのでログに出さない。状態コードだけで追える。
                logger.warning(
                    "NotePM API が %d を返しました（%d 回目、%.1f 秒後に再試行）",
                    response.status_code,
                    attempt,
                    delay,
                )
            await asyncio.sleep(delay)

        # 最後の試行は結果をそのまま返す。ここでの失敗は呼び出し元がエラーにする。
        return await self._client.get(url, params=params)

    async def search(self, params: SearchParams) -> str:
        """NotePMの検索APIを呼び出します

        Args:
            params (SearchParams): 検索パラメータ

        Returns:
            str: 検索結果のJSON文字列。各ページの本文は
                NotePMConfig.max_body_length で切り詰める

        Raises:
            ValueError: APIリクエストが失敗した場合
        """
        response = await self._get(
            self.config.api_base,
            params.model_dump(exclude_none=True),  # Noneの値を除外してパラメータを構築
        )

        if response.status_code != 200:
            raise ValueError(
                f"NotePM APIからのデータ取得に失敗しました: {response.status_code} {response.text}"
            )

        try:
            data = json.loads(response.text)
            # 検索結果の本文を設定された文字数で制限
            self._truncate_search_bodies(data, self.config.max_body_length)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}")
        return json.dumps(data, ensure_ascii=False)

    async def get_notepm_page_detail(self, params: NotePMDetailParams) -> str:
        """NotePMの詳細取得APIを呼び出します

        応答サイズの方針: 本文には上限を設けない。notepm_search の説明が
        「全文を取得するには notepm_page_detail を使う」と案内している以上、
        ここで切り詰めると全文へ辿り着く経路が無くなるため。長いページで応答が
        大きくなり得ることは、その代償として受け入れている。切り詰めるのは
        検索結果だけ（_truncate_search_bodies を参照）。

        Args:
            params (NotePMDetailParams): 詳細取得パラメータ

        Returns:
            str: 詳細取得結果のJSON文字列。本文は切り詰めない

        Raises:
            ValueError: APIリクエストが失敗した場合
        """
        # ここで安全にパス要素へ埋め込めるのは、NotePMDetailParams が
        # PAGE_CODE_PATTERN で検証済みだからである。制約を緩めるときは注意すること。
        url = f"{self.config.api_base}/{params.page_code}"
        response = await self._get(url)

        if response.status_code != 200:
            raise ValueError(
                f"NotePM APIからのデータ取得に失敗しました: {response.status_code} {response.text}"
            )

        try:
            data = json.loads(response.text)
            # 本文は切り詰めない（理由は docstring の「応答サイズの方針」を参照）
            return json.dumps(data, ensure_ascii=False)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}")

    def _truncate_search_bodies(self, data: Any, max_length: int) -> None:
        """検索結果の各ページの本文を指定された文字数で省略します

        対象は検索結果の pages 配列だけです。詳細取得は意図的に切り詰めないため
        （get_notepm_page_detail を参照）、page キーは見ません。検索応答の page は
        ページ番号を表す整数であって、本文の在り処ではありません。

        Args:
            data (Any): NotePMの検索APIのレスポンスデータ（JSONデコード結果）
            max_length (int): 本文の最大文字数
        """

        if not isinstance(data, dict):
            return

        pages = data.get("pages")
        if not isinstance(pages, list):
            return

        for page in pages:
            if not isinstance(page, dict):
                continue

            original_body = page.get("body")
            if isinstance(original_body, str) and len(original_body) > max_length:
                page["body"] = original_body[:max_length] + "..."


def get_tool_description(env_var_name: str, default_description: str) -> str:
    """環境変数からツールの説明を取得する

    Args:
        env_var_name (str): 環境変数の名前
        default_description (str): デフォルトの説明文

    Returns:
        str: 環境変数から取得した説明文、または環境変数が設定されていない場合はデフォルトの説明文
    """
    return os.getenv(env_var_name, default_description)


def get_server_version() -> str:
    """自身のパッケージバージョンを返す

    Returns:
        str: インストール済みの notepm-mcp-server のバージョン。
            未インストールのまま実行された場合は空文字列。
    """
    try:
        return package_version("notepm-mcp-server")
    except PackageNotFoundError:
        return ""


class UnknownToolError(ValueError):
    """公開していないツール名で呼び出されたときのエラー"""


async def call_notepm_tool(
    client: NotePMAPIClient, params: types.CallToolRequestParams
) -> types.CallToolResult:
    """ツール呼び出しを実行し、結果を CallToolResult として返します

    異常は例外を送出せず、必ず is_error=True の結果として返します。ツールの失敗は
    クライアントが読める応答であるべきで、JSON-RPC のエラーにする必要がないためです。

    ログの水準は原因で分けます。呼び出しの拒否（PAGE_CODE_PATTERN などの検証、
    未知のツール名）は防御が働いた結果でサーバーの不具合ではないため、警告として
    値だけを残します。それ以外は想定外の失敗なので、トレースバック付きで記録します。

    Args:
        client (NotePMAPIClient): サーバーが保持している API クライアント
        params (types.CallToolRequestParams): ツール名と引数

    Returns:
        types.CallToolResult: ツールの実行結果。失敗時は is_error=True の結果。
    """
    arguments = params.arguments or {}
    try:
        if params.name == "notepm_search":
            search_params = SearchParams(**arguments)
            result = await client.search(search_params)
        elif params.name == "notepm_page_detail":
            detail_params = NotePMDetailParams(**arguments)
            result = await client.get_notepm_page_detail(detail_params)
        else:
            raise UnknownToolError(f"不明なツールです: {params.name!r}")
    except (ValidationError, UnknownToolError) as e:
        # 呼び出しの拒否は想定内。トレースバックは原因の特定に寄与せず、LLM が
        # 組み立てた値が届くたびに ERROR が並ぶと、本当の異常が埋もれる。
        # ツール名は検証されていない外部由来の値なので、%r で改行ごと落とす。
        # 生のまま出すと、改行を含む名前で偽のログ行を作られる。
        logger.warning("ツール %r の呼び出しを受け付けませんでした: %s", params.name, e)
        return types.CallToolResult(
            content=[TextContent(type="text", text=str(e))], is_error=True
        )
    except Exception as e:
        logger.exception("ツール %r の実行に失敗しました", params.name)
        return types.CallToolResult(
            content=[TextContent(type="text", text=str(e))], is_error=True
        )

    return types.CallToolResult(content=[TextContent(type="text", text=result)])


# ツールの説明文のデフォルト値。環境変数で上書きできる（get_tool_description を参照）。
DEFAULT_SEARCH_DESCRIPTION = """
NotePM(ノートPM)で指定されたクエリを検索します。
検索ワードは単語のAND検索です。自然言語での検索はサポートされていません。
検索結果はJSON形式で返されます。
記事の本文が長い場合は、本文の全文が返されないことがあります。
全文を取得するには、notepm_page_detailを使用してください。
"""

DEFAULT_DETAIL_DESCRIPTION = (
    "NotePM(ノートPM)で指定されたページコードの記事に対して詳細な内容を取得します。"
)


def create_server(config: NotePMConfig) -> Server[NotePMAPIClient]:
    """ツールを登録した MCP サーバーを組み立てます

    stdio への接続を含まないため、テストからは in-process のクライアントで
    そのまま叩けます。

    Args:
        config (NotePMConfig): API設定

    Returns:
        Server[NotePMAPIClient]: 起動可能な状態のサーバー
    """

    @asynccontextmanager
    async def lifespan(server: Server[NotePMAPIClient]) -> AsyncIterator[NotePMAPIClient]:
        """API クライアントをサーバーの生存期間に合わせて開閉します

        server.run() の内側で開かれ、抜けるときに閉じられます。ツール呼び出しは
        ctx.lifespan_context からこのクライアントを受け取るため、検索から詳細取得
        までが同じ接続に乗り、停止時には接続が確実に解放されます。
        """
        async with NotePMAPIClient(config) as client:
            yield client

    async def on_list_tools(
        ctx: ServerRequestContext[NotePMAPIClient],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        """利用可能なツールのリストを返します"""
        return types.ListToolsResult(
            tools=[
                Tool(
                    name="notepm_search",
                    description=get_tool_description(
                        "NOTEPM_SEARCH_DESCRIPTION", DEFAULT_SEARCH_DESCRIPTION
                    ),
                    input_schema=SearchParams.model_json_schema(),
                ),
                Tool(
                    name="notepm_page_detail",
                    description=get_tool_description(
                        "NOTEPM_PAGE_DETAIL_DESCRIPTION", DEFAULT_DETAIL_DESCRIPTION
                    ),
                    input_schema=NotePMDetailParams.model_json_schema(),
                ),
            ]
        )

    async def on_call_tool(
        ctx: ServerRequestContext[NotePMAPIClient],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        """クライアントからのツール呼び出しを処理します

        Args:
            ctx (ServerRequestContext): リクエストごとのコンテキスト。
                lifespan_context に API クライアントが入っている
            params (types.CallToolRequestParams): ツール名と引数

        Returns:
            types.CallToolResult: ツールの実行結果
        """
        return await call_notepm_tool(ctx.lifespan_context, params)

    return Server(
        "notepm-mcp",
        version=get_server_version(),
        lifespan=lifespan,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve() -> None:
    """MCPサーバーのメインエントリーポイント

    NotePM検索機能を提供するMCPサーバーを起動し、標準入出力を使用して
    他のプロセスとコマンド通信を行います。
    """
    config = NotePMConfig()
    server = create_server(config)

    # サーバーの初期化オプションを作成
    options = server.create_initialization_options()
    # 標準入出力を使用してサーバーを起動
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            options,
            raise_exceptions=config.raise_exceptions,
        )
