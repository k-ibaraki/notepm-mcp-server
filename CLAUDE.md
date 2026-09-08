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
- `notepm_mcp_server/__main__.py` — `python -m notepm_mcp_server` から `main()` を呼ぶだけの薄い入口
- `notepm_mcp_server/notepm.py` — 設定・パラメータモデル・API クライアント・MCP サーバーのすべて
- `tests/` — pytest。実 API には接続しない

## 実装上の要点

- MCP の低レベルサーバは `mcp` 2.x の注入方式（`Server(on_list_tools=..., on_call_tool=...)`）で使います。1.x のデコレータ方式（`@server.list_tools`）は廃止済みで、移行時に書き換えた経緯があります
- `server.run()` の `raise_exceptions` は既定で無効です。ハンドラを抜けた例外は SDK 側でエラー応答に変換され、トレースバックが stderr のログに残ります。`NOTEPM_RAISE_EXCEPTIONS=1` を渡したときだけ再送出され、その場合は **例外がプロセスごとサーバーを停止させます**。デバッグ専用の切り替えで、常駐運用では有効にしません
- ツールの異常は `CallToolResult(is_error=True)` で返します。ツール実行の本体は `call_notepm_tool()` にあり、そこで例外を捕捉してこの形に変換します。`on_call_tool` は委譲するだけです。この捕捉があるため、ツール実行中の例外は `NOTEPM_RAISE_EXCEPTIONS` の設定に関わらず再送出されません
- NotePM とのやり取りで生じた失敗は `NotePMError` の階層に分類します。ステータスの振り分けは `_raise_for_status()` にあり、401 / 403 は `NotePMAuthError`、400 は `NotePMBadRequestError`、404 は `NotePMNotFoundError`、429 は `NotePMRateLimitError`、5xx は `NotePMServerError` です。分類は NotePM の API ドキュメントが挙げるステータスに合わせてあり、記載の無いステータスは推測せず `NotePMAPIError` のままにします。JSON として読めない応答は `NotePMResponseError`、全体の上限時間での打ち切りは `NotePMTimeoutError` で、ステータス由来の失敗と分けています
- エラーメッセージには応答本文も API トークンも載せません。本文の中身は NotePM 側の都合で決まり、そのまま返すとクライアントの記録や LLM の文脈へ制御できない内容が流れ出るためです。本文は `_log_failed_response_body()` が DEBUG のときだけ、`%r` で先頭 `ERROR_BODY_LOG_LIMIT` 文字まで残します
- 捕捉した例外のログは原因で三段に分けます。呼び出しを拒否した場合（`PAGE_CODE_PATTERN` などの検証、`UnknownToolError`）と NotePM とのやり取りで生じた失敗（`NotePMError`）は `logger.warning()` で値だけを残し、それ以外は `logger.exception()` でトレースバックまで残します。前の二つは防御が働いた結果か相手側の都合であって、本当の異常と同じ水準にはしません。なお、ツール名は外部から届く未検証の値なので、ログには必ず `%r` で出します。生のまま流すと、改行を含む名前で偽のログ行を作られます
- ログの水準は `-v` の回数で決めます。対応づけは `notepm_mcp_server/__init__.py` の `resolve_log_level()` にあり、`logging.basicConfig()` でルートロガーへ設定するため依存ライブラリのログも同じ水準で出ます。INFO ではサーバーの起動・終了とツール呼び出しの開始・完了を、DEBUG では発行する URL とクエリ、応答のステータス、失敗した応答の本文を出します
- サーバーの組み立ては `create_server(config)` にあり、stdio への接続を含みません。テストは `mcp.client.Client` に渡して in-process で叩いています（`tests/test_server.py`）
- HTTP クライアントは `create_server()` の lifespan がサーバーの生存期間にひとつだけ持ちます。`server.run()` の内側で開き、抜けるときに閉じます。ツール呼び出しは `ctx.lifespan_context` から受け取ります。呼び出しごとに作り直すと、TLS ハンドシェイクとコネクションプールが毎回捨てられます（Issue #10）
- タイムアウトと接続数は `HTTP_TIMEOUT` / `HTTP_LIMITS` にコード定数として明示しています。環境変数では変えません。`keepalive_expiry` を既定の 5 秒から伸ばしているのは、検索から詳細取得までの間に呼び出し側の思考時間が挟まるためです
- 再試行は `NotePMAPIClient._get_with_retry()` にあり、待てば結果が変わり得る失敗だけを対象にします（`RETRYABLE_STATUS_CODES` と `RETRYABLE_TRANSPORT_ERRORS`、最大 `MAX_ATTEMPTS` 回）。`ReadTimeout` は待ち時間が試行回数の分だけ積み上がるため含めません。`Retry-After` は秒数として読めるときだけ従い、`MAX_RETRY_WAIT_SECONDS` で丸めます。再試行のログには状態コードと試行回数だけを残し、応答本文は出しません
- 1 回の呼び出しは、再試行と待ち時間まで含めて `TOTAL_TIMEOUT_SECONDS`（60 秒）で打ち切ります。`_get()` が `asyncio.wait_for()` で `_get_with_retry()` を囲み、超えたら他の API 失敗と同じ `ValueError` にします。試行ごとの上限しか持たないと読み取り 30 秒 × 3 試行で 90 秒を超え得るうえ、MCP のホスト側の制限に先に打ち切られると理由が呼び出し側に伝わりません
- ツールの入力スキーマは pydantic モデル（`SearchParams` / `NotePMDetailParams`）の `model_json_schema()` をそのまま公開しています。パラメータを増減するときはモデル側を直します。モデルの docstring と各フィールドの `description` は呼び出し側へそのまま配信されるため、保守者向けのメモはクラスの外のコメントに書きます
- 日付での絞り込みは `DATE_PATTERN` で書式を検査します。`\d` ではなく `[0-9]` と書くのは、pydantic の正規表現では `\d` が全角数字にも一致してしまい、公開するスキーマ（ECMA-262 準拠で `\d` は ASCII のみ）と判定がずれるためです
- 検索の `per_page` は既定 10 です。NotePM API の既定（20）ではなく、応答が大きくなりすぎないようこのサーバーの判断で小さく取っています。上限の 100 だけが API 仕様に由来します
- `page_code` は詳細取得 URL のパス要素へ直接埋め込むため、`PAGE_CODE_PATTERN` でパスの構造を変え得る文字（空白・制御文字と `/` `\` `.` `?` `#` `%` `:`）を拒みます。ここを緩めると、httpx2 の URL 正規化を介してページ詳細以外のエンドポイントへ到達できるようになります
- 検索応答は `_truncate_search_bodies()` で本文を切り詰めます（既定 200 文字、`NOTEPM_MAX_BODY_LENGTH` で変更可）。詳細取得は意図的に切り詰めません（全文への経路を残すため。理由は `get_notepm_page_detail` の docstring に書いてあります）
- `NotePMConfig` が環境変数を読み、`NOTEPM_TEAM` か `NOTEPM_API_TOKEN` が欠けていれば起動時に `ValueError` を送出します。設定ミスは起動時に気付けるべきなので、この失敗はそのまま落とします
- `notepm` モジュールは import 時に `load_dotenv()` を呼びます

## 依存関係の方針

- `pyproject.toml` の依存には**必ず上限を付けます**。更新するときは上限を外さず、レンジごと引き上げてください
- デプロイは `uv sync --frozen --no-dev` のようにロックを尊重する経路で行います
- 経緯と背景は README の「依存関係の方針」を参照してください

## 型チェック

`ty` を使います。`pyproject.toml` の `[tool.ty.rules]` で `all = "error"` を指定しており、既定では警告にとどまる規則もエラーとして扱います。`ty` はまだ 0.0.x のプレビュー段階のため、更新時は新しい規則による指摘の増加を確認してください。

## テスト

- HTTP は `httpx2` の `MockTransport` に差し替えます。新しいテストは `tests/conftest.py` の `mock_api` フィクスチャを使ってください。応答を遅らせたいときはハンドラを `async def` で書けます
- autouse のフィクスチャが、モックを介さない HTTP リクエストの送信を失敗させ、`NOTEPM_` 系の環境変数も毎回削除します。手元の `.env` に結果が左右されない前提を壊さないでください。クライアントの生成自体は lifespan が毎回行うため、禁じているのは送信のほうです
- 同じく autouse の `no_retry_waits` が再試行の待ち時間を 0 にします。待ち時間の決め方そのものは `tests/test_retry_policy.py` で固定しています
- `asyncio_mode = "auto"` を指定しているため、非同期テストに `@pytest.mark.asyncio` は付けません
- 環境変数の揃った設定は `config` フィクスチャを、`CallToolResult` からの本文の取り出しは `tests/conftest.py` の `content_text()` を使います。ログの水準そのものを確かめるテストは `caplog` で見ています
