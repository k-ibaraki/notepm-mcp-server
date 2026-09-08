from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import BaseModel, Field
import httpx2
import os
from types import TracebackType
from typing import Annotated, Any, Literal, Optional
from dotenv import load_dotenv
from importlib.metadata import PackageNotFoundError, version as package_version
import json

# 環境変数の読み込み
load_dotenv()


class NotePMConfig:
    """NotePM APIの設定を管理するクラス

    環境変数から必要な設定を読み込み、APIエンドポイントのURLを生成します。

    Attributes:
        team (str): NotePMのチーム名
        api_token (str): NotePM APIのトークン
        api_base (str): APIのベースURL
        max_body_length (int): 本文の最大文字数
    """

    def __init__(self) -> None:
        self.team = os.getenv("NOTEPM_TEAM")
        self.api_token = os.getenv("NOTEPM_API_TOKEN")
        if not self.team or not self.api_token:
            raise ValueError("環境変数NOTEPM_TEAMとNOTEPM_API_TOKENが必要です")
        self.api_base = f"https://{self.team}.notepm.jp/api/v1/pages"

        # 本文の最大文字数を環境変数から取得（デフォルト: 200）
        self.max_body_length = int(os.getenv("NOTEPM_MAX_BODY_LENGTH", "200"))


# NotePM API が日付の絞り込みで受け付ける書式（YYYY-MM-DD）。
# 実在する日付かまでは検査しない。ここでの目的は "2020/08/01" や
# ISO 8601 の日時のような別書式を、リクエストを送る前に弾くことにある。
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

DateFilter = Optional[Annotated[str, Field(pattern=DATE_PATTERN)]]


class SearchParams(BaseModel):
    """NotePM のページ検索 API (GET /api/v1/pages) に渡すパラメータ。

    このモデルの JSON スキーマがそのまま notepm_search ツールの入力スキーマとして
    公開される。ツールを呼ぶ側が値の意味と範囲を読み取れるよう、フィールドごとに
    説明と制約を持たせている。制約は NotePM API の仕様に合わせること。
    """

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


class NotePMDetailParams(BaseModel):
    """NotePM のページ詳細取得 API (GET /api/v1/pages/{page_code}) に渡すパラメータ。

    SearchParams と同じく、このモデルの JSON スキーマがそのまま
    notepm_page_detail ツールの入力スキーマとして公開される。
    """

    page_code: Annotated[
        str,
        Field(
            description=(
                "取得するページのページコード（例: aaaaad0001）。"
                "notepm_search の検索結果に含まれる page_code をそのまま渡す。"
            )
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


async def serve() -> None:
    """MCPサーバーのメインエントリーポイント

    NotePM検索機能を提供するMCPサーバーを起動し、標準入出力を使用して
    他のプロセスとコマンド通信を行います。
    """
    config = NotePMConfig()

    # ツールの説明文のデフォルト値
    default_search_description = """
                    NotePM(ノートPM)で指定されたクエリを検索します。
                    検索ワードは単語のAND検索です。自然言語での検索はサポートされていません。
                    検索結果はJSON形式で返されます。
                    記事の本文が長い場合は、本文の全文が返されないことがあります。
                    全文を取得するには、notepm_page_detailを使用してください。
                """

    default_detail_description = "NotePM(ノートPM)で指定されたページコードの記事に対して詳細な内容を取得します。"

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
                        "NOTEPM_SEARCH_DESCRIPTION", default_search_description
                    ),
                    input_schema=SearchParams.model_json_schema(),
                ),
                Tool(
                    name="notepm_page_detail",
                    description=get_tool_description(
                        "NOTEPM_PAGE_DETAIL_DESCRIPTION", default_detail_description
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
            types.CallToolResult: ツールの実行結果。失敗時は is_error=True の
                結果を返す（例外を送出するとサーバー自体が停止するため）。
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
                raise ValueError(f"不明なツールです: {params.name}")
        except Exception as e:
            return types.CallToolResult(
                content=[TextContent(type="text", text=str(e))], is_error=True
            )

        return types.CallToolResult(content=[TextContent(type="text", text=result)])

    server: Server[dict[str, Any]] = Server(
        "notepm-mcp",
        version=get_server_version(),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )

    # サーバーの初期化オプションを作成
    options = server.create_initialization_options()
    # 標準入出力を使用してサーバーを起動
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=True)
