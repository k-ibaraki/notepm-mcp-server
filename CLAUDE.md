# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## このリポジトリについて

NotePM の検索と記事取得を MCP ツールとして公開する、stdio ベースの MCP サーバーです。
ドキュメント・コミットメッセージ・PR 本文は日本語で書きます。コミットは Conventional Commits の
接頭辞（`fix:` / `chore:` / `docs:` / `test:` など）に日本語の要約を続け、本文には「なぜそうしたか」を書きます。

## コマンド

```sh
uv sync                       # 依存の導入
uv run notepm-mcp-server      # サーバーの起動（stdin/stdout で JSON-RPC を話す）
uv run pytest                 # テスト
uv run ty check               # 型チェック
uv lock --check               # uv.lock と pyproject.toml の整合を確認
```

テストを 1 件だけ走らせる場合:

```sh
uv run pytest tests/test_api_client.py::test_search_truncates_long_body
```

## 構成

- `notepm_mcp_server/__init__.py` — click の CLI エントリポイント。`-v` の数からログ水準を決めて `asyncio.run(serve())` を呼ぶ
- `notepm_mcp_server/notepm.py` — 設定・パラメータモデル・API クライアント・MCP サーバーのすべて
- `tests/` — pytest。実 API には接続しない

## 実装上の要点

- MCP の低レベルサーバは `mcp` 2.x の注入方式（`Server(on_list_tools=..., on_call_tool=...)`）で使います。1.x のデコレータ方式（`@server.list_tools`）は廃止済みで、移行時に書き換えた経緯があります
- `server.run()` の `raise_exceptions` は既定で無効です。ハンドラを抜けた例外は SDK 側でエラー応答に変換され、トレースバックが stderr のログに残ります。`NOTEPM_RAISE_EXCEPTIONS=1` を渡したときだけ再送出され、その場合は **例外がプロセスごとサーバーを停止させます**。デバッグ専用の切り替えで、常駐運用では有効にしません
- ツールの異常は `CallToolResult(is_error=True)` で返します。ツール実行の本体は `call_notepm_tool()` にあり、そこで例外を捕捉してこの形に変換します。`on_call_tool` は委譲するだけです。この捕捉があるため、ツール実行中の例外は `NOTEPM_RAISE_EXCEPTIONS` の設定に関わらず再送出されません
- 捕捉した例外のログは原因で水準を分けます。呼び出しを拒否した場合（`PAGE_CODE_PATTERN` などの検証、`UnknownToolError`）は `logger.warning()` で値だけを残し、それ以外は `logger.exception()` でトレースバックまで残します。拒否は防御が働いた結果なので、本当の異常と同じ水準にはしません
- サーバーの組み立ては `create_server(config)` にあり、stdio への接続を含みません。テストは `mcp.client.Client` に渡して in-process で叩いています（`tests/test_server.py`）
- ツールの入力スキーマは pydantic モデル（`SearchParams` / `NotePMDetailParams`）の `model_json_schema()` をそのまま公開しています。パラメータを増減するときはモデル側を直します
- `page_code` は詳細取得 URL のパス要素へ直接埋め込むため、`PAGE_CODE_PATTERN` でパスの構造を変え得る文字（空白・制御文字と `/` `\` `.` `?` `#` `%` `:`）を拒みます。ここを緩めると、httpx2 の URL 正規化を介してページ詳細以外のエンドポイントへ到達できるようになります
- 検索応答は `_truncate_body_content()` で本文を切り詰めます（既定 200 文字、`NOTEPM_MAX_BODY_LENGTH` で変更可）。詳細取得は切り詰めません
- `NotePMConfig` が環境変数を読み、`NOTEPM_TEAM` か `NOTEPM_API_TOKEN` が欠けていれば起動時に `ValueError` を送出します。設定ミスは起動時に気付けるべきなので、この失敗はそのまま落とします
- `notepm` モジュールは import 時に `load_dotenv()` を呼びます

## 依存関係の方針

- `pyproject.toml` の依存には**必ず上限を付けます**。更新するときは上限を外さず、レンジごと引き上げてください
- デプロイは `uv sync --frozen --no-dev` のようにロックを尊重する経路で行います
- 経緯と背景は README の「依存関係の方針」を参照してください

## 型チェック

`ty` を使います。`pyproject.toml` の `[tool.ty.rules]` で `all = "error"` を指定しており、既定では警告にとどまる規則もエラーとして扱います。`ty` はまだ 0.0.x のプレビュー段階のため、更新時は新しい規則による指摘の増加を確認してください。

## テスト

- HTTP は `httpx2` の `MockTransport` に差し替えます。新しいテストは `tests/conftest.py` の `mock_api` フィクスチャを使ってください
- autouse のフィクスチャが、モックを介さない HTTP クライアントの生成を失敗させ、`NOTEPM_` 系の環境変数も毎回削除します。手元の `.env` に結果が左右されない前提を壊さないでください
