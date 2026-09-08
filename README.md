# NotePM MCP Server

NotePMのコンテンツを検索するためのModel Context Protocol (MCP) サーバーです。このサーバーを使用することで、NotePMの検索機能をMCP対応のクライアントから利用することができます。

## 機能

- NotePMのコンテンツ全文検索
- タイトルのみの検索
- タグによる検索
- ノートコードによる検索
- アーカイブされたページの検索オプション
- ページネーション対応
- 詳細な記事の内容を取得

## 必要条件

- Python 3.10以上（開発時の想定は3.12）
- NotePMのアカウントとAPI Token
- [uv](https://github.com/astral-sh/uv)

## インストール

```sh
uv sync
```

## 依存関係の方針（重要）

**デプロイ時は必ず `uv.lock` を尊重してインストールしてください。**

```sh
uv sync --frozen --no-dev   # uv.lock の固定バージョンのみを使う（開発用依存は除く）
```

`--no-dev` を省くと `[dependency-groups] dev`（mypy など）まで実行環境に入り、
不要な依存をコンテナ起動ごとに取得することになります。

uv を使わないイメージ（`pip install` 系）では、ロックから requirements を書き出してください：

```sh
uv export --frozen --no-dev -o requirements.txt
pip install -r requirements.txt --no-deps
```

逆に `uv.lock` を無視する経路（`uvx`、`pip install git+...` など）を使うと、
起動ごとに PyPI から最新版を取得するため、上流の破壊的変更でそのまま起動不能になります。

> 2026-07-28 に公開された `mcp` 2.0.0 で `mcp.server.lowlevel.server.request_ctx` が削除され、
> これを参照する同居ライブラリごと ImportError で起動失敗する障害が発生しました。
> `uv.lock` は当時 `mcp==1.6.0` を固定していたため、ロックを尊重していれば影響はありませんでした。

二重の防御として `pyproject.toml` の依存には上限（`mcp>=2.2.0,<3` など）を付けています。
依存を更新する際は、上限を外さずにレンジごと引き上げてください。

### 現在の主要依存

| パッケージ | レンジ | 備考 |
| --- | --- | --- |
| `mcp` | `>=2.2.0,<3` | 低レベルサーバは 2.x のハンドラ注入方式（`on_list_tools` / `on_call_tool`）を使用 |
| `httpx2` | `>=2.12.0,<3` | `httpx` の後継。`httpx` 0.28.1 は実質保守停止のため移行済み |
| `pydantic` | `>=2.0.0,<3` | ツールの入力スキーマ生成に使用 |
| `click` | `>=8.1.0,<9` | CLI エントリポイント |
| `python-dotenv` | `>=1.1.0,<2` | `.env` の読み込み |

`mcp` の CLI extra（`mcp[cli]`）は使用していないため外しています。
本体は `click` を直接使っており、`mcp.cli` を import していません。

### TLS 証明書の取り扱い（デプロイ時の注意）

`httpx` から `httpx2` への移行に伴い、CA 証明書の解決方法が変わりました。
`httpx2` は `certifi` を同梱せず、既定では `truststore` 経由で OS のトラストストアを参照します。

そのため CA 証明書を持たない最小構成のイメージ（distroless、`ca-certificates` 未導入の Alpine など）では、
NotePM への HTTPS 接続が証明書検証エラーになります。イメージに `ca-certificates` を導入するか、
`SSL_CERT_FILE` もしくは `SSL_CERT_DIR` で証明書の場所を明示してください。

## 環境設定

以下の環境変数を設定する必要があります：

- `NOTEPM_TEAM`: NotePMのチーム名
- `NOTEPM_API_TOKEN`: NotePM APIトークン

`.env`ファイルを作成して設定することもできます：

```.env
NOTEPM_TEAM=your-team-name
NOTEPM_API_TOKEN=your-api-token
```

## 使用方法

### サーバーの起動

```bash
uv run notepm-mcp-server
```

### MCPクライアントの設定

```json
"servers": {
  "notepm-mcp-server": {
  "command": "uv",
    "args": [
      "--directory",
      "/<path to mcp-servers>/notepm-mcp-server",
      "run",
      "notepm-mcp-server"
    ],
    "env": {
      "NOTEPM_TEAM": "your-team-name",
      "NOTEPM_API_TOKEN": "your-api-token"
    }
  }
}
```
