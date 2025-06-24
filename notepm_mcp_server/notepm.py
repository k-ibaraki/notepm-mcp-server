from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import BaseModel
import httpx
import os
from typing import Optional
from dotenv import load_dotenv
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

    def __init__(self):
        self.team = os.getenv("NOTEPM_TEAM")
        self.api_token = os.getenv("NOTEPM_API_TOKEN")
        if not self.team or not self.api_token:
            raise ValueError("環境変数NOTEPM_TEAMとNOTEPM_API_TOKENが必要です")
        self.api_base = f"https://{self.team}.notepm.jp/api/v1/pages"

        # 本文の最大文字数を環境変数から取得（デフォルト: 200）
        self.max_body_length = int(os.getenv("NOTEPM_MAX_BODY_LENGTH", "200"))


class SearchParams(BaseModel):
    """NotePM API検索パラメータモデル

    Attributes:
        q (str): 検索クエリ
        only_title (int): タイトルのみを検索するかどうか (0: 全文検索, 1: タイトルのみ)
        include_archived (int): アーカイブされたページを含めるかどうか (0: 含めない, 1: 含める)
        note_code (Optional[str]): ノートコードによるフィルタリング
        tag_name (Optional[str]): タグ名によるフィルタリング
        created (Optional[str]): 作成日によるフィルタリング
        page (int): ページ番号 (デフォルト: 1)
        per_page (int): 1ページあたりの結果数 (デフォルト: 10)
    """

    q: str
    only_title: int = 0
    include_archived: int = 0
    note_code: Optional[str] = None
    tag_name: Optional[str] = None
    created: Optional[str] = None
    page: int = 1
    per_page: int = 10  # デフォルトを50から10に削減してレスポンスサイズを抑制


class NotePMDetailParams(BaseModel):
    """NotePM API詳細取得パラメータモデル

    Attributes:
        page_code (str): ページコード
    """

    page_code: str


class NotePMAPIClient:
    """NotePM APIクライアント

    非同期HTTPクライアントを使用してNotePM APIと通信を行います。
    コンテキストマネージャとして使用することで、リソースの適切な解放を保証します。
    """

    def __init__(self, config: NotePMConfig):
        """
        Args:
            config (NotePMConfig): API設定
        """
        self.config = config
        self._client = httpx.AsyncClient()

    async def __aenter__(self):
        """非同期コンテキストマネージャのエントリーポイント"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
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
            params=params.dict(exclude_none=True),  # Noneの値を除外してパラメータを構築
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

    def _truncate_body_content(self, data: dict, max_length: int = 1000) -> None:
        """レスポンスデータの本文部分を指定された文字数で省略します

        Args:
            data (dict): NotePM APIのレスポンスデータ
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

async def serve() -> None:
    """MCPサーバーのメインエントリーポイント

    NotePM検索機能を提供するMCPサーバーを起動し、標準入出力を使用して
    他のプロセスとコマンド通信を行います。
    """
    config = NotePMConfig()
    server: Server = Server("notepm-mcp")

    # ツールの説明文のデフォルト値
    default_search_description = """
                    NotePM(ノートPM)で指定されたクエリを検索します。
                    検索ワードは単語のAND検索です。自然言語での検索はサポートされていません。
                    検索結果はJSON形式で返されます。
                    記事の本文が長い場合は、本文の全文が返されないことがあります。
                    全文を取得するには、notepm_page_detailを使用してください。
                """
    
    default_detail_description = "NotePM(ノートPM)で指定されたページコードの記事に対して詳細な内容を取得します。"

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """利用可能なツールのリストを返します"""
        return [
            Tool(
                name="notepm_search",
                description=get_tool_description("NOTEPM_SEARCH_DESCRIPTION", default_search_description),
                inputSchema=SearchParams.schema(),
            ),
            Tool(
                name="notepm_page_detail",
                description=get_tool_description("NOTEPM_PAGE_DETAIL_DESCRIPTION", default_detail_description),
                inputSchema=NotePMDetailParams.schema(),
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """クライアントからのツール呼び出しを処理します

        Args:
            name (str): 呼び出すツールの名前
            arguments (dict): ツールに渡す引数

        Returns:
            list[TextContent]: ツールの実行結果

        Raises:
            ValueError: 不明なツールが指定された場合
        """
        if name == "notepm_search":
            search_params: SearchParams = SearchParams(**arguments)
            async with NotePMAPIClient(config) as client:
                result = await client.search(search_params)
                return [TextContent(type="text", text=result)]
        elif name == "notepm_page_detail":
            detail_params: NotePMDetailParams = NotePMDetailParams(**arguments)
            async with NotePMAPIClient(config) as client:
                result = await client.get_notepm_page_detail(detail_params)
                return [TextContent(type="text", text=result)]

        raise ValueError(f"不明なツールです: {name}")

    # サーバーの初期化オプションを作成
    options = server.create_initialization_options()
    # 標準入出力を使用してサーバーを起動
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=True)
