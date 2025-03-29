import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import BaseModel
import httpx
import os
from typing import Optional
from dotenv import load_dotenv

# 環境変数の読み込み
load_dotenv()


class NotePMConfig:
    """NotePM APIの設定を管理するクラス

    環境変数から必要な設定を読み込み、APIエンドポイントのURLを生成します。

    Attributes:
        team (str): NotePMのチーム名
        api_token (str): NotePM APIのトークン
        api_base (str): APIのベースURL
    """

    def __init__(self):
        self.team = os.getenv("NOTEPM_TEAM")
        self.api_token = os.getenv("NOTEPM_API_TOKEN")
        if not self.team or not self.api_token:
            raise ValueError("環境変数NOTEPM_TEAMとNOTEPM_API_TOKENが必要です")
        self.api_base = f"https://{self.team}.notepm.jp/api/v1/pages"


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
        per_page (int): 1ページあたりの結果数 (デフォルト: 50)
    """
    q: str
    only_title: int = 0
    include_archived: int = 0
    note_code: Optional[str] = None
    tag_name: Optional[str] = None
    created: Optional[str] = None
    page: int = 1
    per_page: int = 50


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
            headers=headers
        )

        if response.status_code != 200:
            raise ValueError(
                f"NotePM APIからのデータ取得に失敗しました: {response.status_code} {response.text}"
            )

        return response.text

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

        return response.text


async def serve() -> None:
    """MCPサーバーのメインエントリーポイント

    NotePM検索機能を提供するMCPサーバーを起動し、標準入出力を使用して
    他のプロセスとコマンド通信を行います。
    """
    logger = logging.getLogger(__name__)
    config = NotePMConfig()
    server: Server = Server("notepm-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """利用可能なツールのリストを返します"""
        return [
            Tool(
                name="search_notepm",
                description="""
                    NotePMで指定されたクエリを検索します。
                    記事の本文が長い場合は、本文の全文が返されないことがあります。
                    全文を取得するには、get_notepm_page_detailを使用してください。
                """,
                inputSchema=SearchParams.schema(),
            ),
            Tool(
                name="get_notepm_page_detail",
                description="NotePMで指定されたページコードの詳細を取得します。",
                inputSchema=NotePMDetailParams.schema(),
            )
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
        if name == "search_notepm":
            search_params: SearchParams = SearchParams(**arguments)
            async with NotePMAPIClient(config) as client:
                result = await client.search(search_params)
                return [TextContent(type="text", text=result)]
        elif name == "get_notepm_page_detail":
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
