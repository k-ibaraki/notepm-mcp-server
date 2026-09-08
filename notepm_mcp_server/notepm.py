from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import BaseModel, Field, ValidationError
import httpx2
import os
from types import TracebackType
from typing import Annotated, Any, Literal, Optional
from dotenv import load_dotenv
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import logging

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
        max_body_length (int): 本文の最大文字数
        raise_exceptions (bool): ハンドラの例外を再送出するか（開発時のみ有効にする）
    """

    def __init__(self) -> None:
        self.team = os.getenv("NOTEPM_TEAM")
        self.api_token = os.getenv("NOTEPM_API_TOKEN")
        if not self.team or not self.api_token:
            raise ValueError("環境変数NOTEPM_TEAMとNOTEPM_API_TOKENが必要です")
        self.api_base = f"https://{self.team}.notepm.jp/api/v1/pages"

        # 本文の最大文字数を環境変数から取得（デフォルト: 200）
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


class NotePMAPIClient:
    """NotePM APIクライアント

    非同期HTTPクライアントを使用してNotePM APIと通信を行います。
    コンテキストマネージャとして使用することで、リソースの適切な解放を保証します。
    """

    def __init__(self, config: NotePMConfig) -> None:
        """
        Args:
            config (NotePMConfig): API設定
        """
        self.config = config
        self._client = httpx2.AsyncClient()

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

    async def search(self, params: SearchParams) -> str:
        """NotePMの検索APIを呼び出します

        Args:
            params (SearchParams): 検索パラメータ

        Returns:
            str: 検索結果のJSON文字列

        Raises:
            ValueError: APIリクエストが失敗した場合
        """
        headers = {"Authorization": f"Bearer {self.config.api_token}"}
        response = await self._client.get(
            self.config.api_base,
            params=params.model_dump(exclude_none=True),  # Noneの値を除外してパラメータを構築
            headers=headers,
        )

        if response.status_code != 200:
            raise ValueError(
                f"NotePM APIからのデータ取得に失敗しました: {response.status_code} {response.text}"
            )

        try:
            data = json.loads(response.text)
            # レスポンスの本文部分を設定された文字数で制限
            self._truncate_body_content(data, self.config.max_body_length)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}")
        return json.dumps(data, ensure_ascii=False)

    async def get_notepm_page_detail(self, params: NotePMDetailParams) -> str:
        """NotePMの詳細取得APIを呼び出します

        Args:
            params (NotePMDetailParams): 詳細取得パラメータ

        Returns:
            str: 詳細取得結果のJSON文字列

        Raises:
            ValueError: APIリクエストが失敗した場合
        """
        headers = {"Authorization": f"Bearer {self.config.api_token}"}
        # ここで安全にパス要素へ埋め込めるのは、NotePMDetailParams が
        # PAGE_CODE_PATTERN で検証済みだからである。制約を緩めるときは注意すること。
        url = f"{self.config.api_base}/{params.page_code}"
        response = await self._client.get(url, headers=headers)

        if response.status_code != 200:
            raise ValueError(
                f"NotePM APIからのデータ取得に失敗しました: {response.status_code} {response.text}"
            )

        try:
            data = json.loads(response.text)
            # 詳細表示では本文を省略しない
            return json.dumps(data, ensure_ascii=False)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}")

    def _truncate_body_content(self, data: Any, max_length: int = 1000) -> None:
        """レスポンスデータの本文部分を指定された文字数で省略します

        Args:
            data (Any): NotePM APIのレスポンスデータ（JSONデコード結果）
            max_length (int): 本文の最大文字数 (デフォルト: 1000)
        """

        if isinstance(data, dict):
            # 検索結果の場合（pagesフィールドが存在する場合）
            if "pages" in data and isinstance(data["pages"], list):
                for page in data["pages"]:
                    if isinstance(page, dict) and "body" in page:
                        original_body = page["body"]

                        if (
                            isinstance(original_body, str)
                            and len(original_body) > max_length
                        ):
                            page["body"] = original_body[:max_length] + "..."

            # 詳細取得結果の場合（pageフィールドが存在する場合）
            elif "page" in data and isinstance(data["page"], dict):
                page = data["page"]
                if "body" in page:
                    original_body = page["body"]

                    if (
                        isinstance(original_body, str)
                        and len(original_body) > max_length
                    ):
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
    config: NotePMConfig, params: types.CallToolRequestParams
) -> types.CallToolResult:
    """ツール呼び出しを実行し、結果を CallToolResult として返します

    異常は例外を送出せず、必ず is_error=True の結果として返します。ツールの失敗は
    クライアントが読める応答であるべきで、JSON-RPC のエラーにする必要がないためです。

    ログの水準は原因で分けます。呼び出しの拒否（PAGE_CODE_PATTERN などの検証、
    未知のツール名）は防御が働いた結果でサーバーの不具合ではないため、警告として
    値だけを残します。それ以外は想定外の失敗なので、トレースバック付きで記録します。

    Args:
        config (NotePMConfig): API設定
        params (types.CallToolRequestParams): ツール名と引数

    Returns:
        types.CallToolResult: ツールの実行結果。失敗時は is_error=True の結果。
    """
    arguments = params.arguments or {}
    try:
        if params.name == "notepm_search":
            search_params = SearchParams(**arguments)
            async with NotePMAPIClient(config) as client:
                result = await client.search(search_params)
        elif params.name == "notepm_page_detail":
            detail_params = NotePMDetailParams(**arguments)
            async with NotePMAPIClient(config) as client:
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


def create_server(config: NotePMConfig) -> Server[dict[str, Any]]:
    """ツールを登録した MCP サーバーを組み立てます

    stdio への接続を含まないため、テストからは in-process のクライアントで
    そのまま叩けます。

    Args:
        config (NotePMConfig): API設定

    Returns:
        Server[dict[str, Any]]: 起動可能な状態のサーバー
    """

    async def on_list_tools(
        ctx: ServerRequestContext[dict[str, Any]],
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
        ctx: ServerRequestContext[dict[str, Any]],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        """クライアントからのツール呼び出しを処理します

        Args:
            ctx (ServerRequestContext): リクエストごとのコンテキスト
            params (types.CallToolRequestParams): ツール名と引数

        Returns:
            types.CallToolResult: ツールの実行結果
        """
        return await call_notepm_tool(config, params)

    return Server(
        "notepm-mcp",
        version=get_server_version(),
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
