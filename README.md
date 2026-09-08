# NotePM MCP Server

NotePMのコンテンツを検索するためのModel Context Protocol (MCP) サーバーです。このサーバーを使用することで、NotePMの検索機能をMCP対応のクライアントから利用することができます。

## 機能

- NotePMのコンテンツ全文検索
- タイトルのみの検索
- タグによる検索
- ノートコードによる検索
- 作成者（ユーザーコード）による絞り込み
- 作成日時・更新日時の範囲による絞り込み
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

`--no-dev` を省くと `[dependency-groups] dev`（ty や pytest）まで実行環境に入り、
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
| `pydantic` | `>=2.12.0,<3` | ツールの入力スキーマ生成に使用 |
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

## 開発

### 型チェック

型チェックには [ty](https://github.com/astral-sh/ty) を使用します。dev 依存に含まれているため、
`uv sync` 済みであれば追加の準備は要りません。

```sh
uv run ty check
```

ty はまだ 0.0.x のプレビュー段階です。本リポジトリでは `pyproject.toml` の `[tool.ty.rules]` で
`all = "error"` を指定し、既定では警告にとどまる規則も含めてすべてエラーとして扱っています。
ty を更新すると新しい規則の追加によって指摘が増えることがあるため、バージョンの上限
（`ty>=0.0.79,<0.1`）は外さず、レンジごと引き上げてください。

### テスト

テストは [pytest](https://docs.pytest.org/) で実行します。

```sh
uv run pytest
```

実際の NotePM API には接続しません。HTTP 応答は `httpx2` の `MockTransport` で差し替えており、
差し替えを忘れたテストは `tests/conftest.py` のフィクスチャが検知して失敗させます。
同じフィクスチャが `NOTEPM_` 系の環境変数も毎回消すため、手元に `.env` があっても結果は変わりません。

## 環境設定

以下の環境変数を設定する必要があります：

- `NOTEPM_TEAM`: NotePMのチーム名
- `NOTEPM_API_TOKEN`: NotePM APIトークン

`.env`ファイルを作成して設定することもできます：

```.env
NOTEPM_TEAM=your-team-name
NOTEPM_API_TOKEN=your-api-token
```

### 任意の環境変数

- `NOTEPM_MAX_BODY_LENGTH`: 検索結果の本文を切り詰める文字数（既定: 200）。超えた分は末尾を `...` に置き換えます。詳細取得（`notepm_page_detail`）は全文を返すため影響を受けません
- `NOTEPM_SEARCH_DESCRIPTION` / `NOTEPM_PAGE_DETAIL_DESCRIPTION`: ツールの説明文の差し替え
- `NOTEPM_RAISE_EXCEPTIONS`: デバッグ用。`1` / `true` / `yes` / `on` のいずれかで有効

`NOTEPM_RAISE_EXCEPTIONS` を有効にすると、ハンドラを抜けた例外がそのまま送出され、
サーバープロセスが停止します。原因の切り分けには便利ですが、常駐させる通常の運用では
設定しないでください。既定の無効のままなら、想定外の例外はクライアントへのエラー応答に
変換され、サーバーは動き続けます。

なお、ツール実行中の例外は `call_notepm_tool()` が捕捉して `isError` の結果に変換するため、
このフラグの影響を受けません。フラグが効くのは、ツール一覧の取得など、ハンドラの外へ
例外が抜ける経路だけです。

### HTTP 接続の扱い

NotePM への HTTP クライアントは、サーバーの起動から停止まで使い回します
（個々の接続は 60 秒の keepalive を過ぎると入れ替わります）。停止時には接続を解放します。
ツール呼び出しのたびにクライアントを作り直すと、TLS ハンドシェイクとコネクションプールが
毎回捨てられ、検索してから詳細を取るという典型的な流れで接続確立のコストが積み上がります。

タイムアウトと再試行は環境変数ではなく、`notepm_mcp_server/notepm.py` の定数として明示しています。

| 項目 | 値 | 備考 |
| --- | --- | --- |
| 1 回の呼び出し全体の上限 | 60 秒 | 再試行と待ち時間を含みます。超えたらエラーとして返します |
| タイムアウト（接続 / 読み取り / 書き込み / プール待ち） | 5 / 30 / 10 / 5 秒 | 全文検索は時間がかかり得るため、読み取りだけ長く取っています |
| 試行回数 | 最大 3 回（初回を含む） | 深追いするとツール呼び出しが戻らなくなります |
| 再試行の対象 | 429 / 500 / 502 / 503 / 504、接続の確立失敗、切られていた接続 | 認証エラーなど他の 4xx は何度送っても同じ答えなので対象外です |
| 再試行までの待ち時間 | 0.5 秒から倍々、上限 10 秒 | `Retry-After` が秒数として読めればそちらに従い、同じ上限で丸めます |

読み取りのタイムアウト（`ReadTimeout`）は再試行しません。応答を返さない相手に対して、
待ち時間が試行回数の分だけ積み上がるためです。

全体の上限を別に持っているのは、試行ごとの上限だけでは 1 回の呼び出しが読み取り 30 秒 ×
3 試行で 90 秒を超え得るためです。MCP のホスト側にもツール実行の制限があり
（Claude Code の `MCP_TOOL_TIMEOUT` など）、そちらに先に打ち切られると、
何が起きたのかが呼び出し側に伝わりません。

### ログ

ログは stderr に出力します（stdout は JSON-RPC が使うため）。既定では警告以上のみで、
`-v` で INFO、`-vv` で DEBUG まで下がります。

想定外の失敗はトレースバック付きで記録します。一方、検証や未知のツール名で弾いた
呼び出しは防御が働いた結果なので、値だけを警告として残します。

```sh
uv run notepm-mcp-server -v
```

MCP クライアント経由で起動している場合、この出力はクライアント側のログに記録されます。

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
      "--frozen",
      "--no-dev",
      "notepm-mcp-server"
    ],
    "env": {
      "NOTEPM_TEAM": "your-team-name",
      "NOTEPM_API_TOKEN": "your-api-token"
    }
  }
}
```
