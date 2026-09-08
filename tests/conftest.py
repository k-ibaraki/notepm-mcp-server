"""テスト全体で共有するフィクスチャ。

実際の NotePM API には接続せず、httpx2 の MockTransport で応答を差し替える。
"""

import os
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from notepm_mcp_server import notepm

TEAM = "example-team"
API_TOKEN = "test-token"
API_BASE = f"https://{TEAM}.notepm.jp/api/v1/pages"

Handler = Callable[[httpx2.Request], httpx2.Response]
InstallMock = Callable[[Handler], list[httpx2.Request]]

# 差し替え前の本物のクライアント。forbid_real_http で置き換わる前に捕まえておく。
_REAL_ASYNC_CLIENT = httpx2.AsyncClient


@pytest.fixture(autouse=True)
def clear_notepm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """NOTEPM_ で始まる環境変数を毎回消し、手元の .env に影響されないようにする。

    notepm モジュールは import 時に load_dotenv() を呼ぶため、開発者の環境に .env が
    あると環境変数が設定済みの状態でテストが始まってしまう。個別に列挙すると環境変数が
    増えたときに消し漏れるため、接頭辞で走査する。
    """
    for name in [key for key in os.environ if key.startswith("NOTEPM_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock_api を使わずに HTTP クライアントを生成したら失敗させる。

    モックの差し替えを書き忘れたテストが、実際の NotePM API へ出ていくのを防ぐ。
    """

    def guard(**kwargs: Any) -> httpx2.AsyncClient:
        raise AssertionError(
            "実 API へ接続しようとしました。mock_api フィクスチャで応答を差し替えてください。"
        )

    monkeypatch.setattr(notepm.httpx2, "AsyncClient", guard)


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch) -> notepm.NotePMConfig:
    """必須の環境変数が揃った状態の設定を返す。"""
    monkeypatch.setenv("NOTEPM_TEAM", TEAM)
    monkeypatch.setenv("NOTEPM_API_TOKEN", API_TOKEN)
    return notepm.NotePMConfig()


@pytest.fixture
def mock_api(monkeypatch: pytest.MonkeyPatch) -> InstallMock:
    """NotePM API の応答を差し替え、送信されたリクエストを記録する。

    戻り値の関数にハンドラを渡すと、以降 NotePMAPIClient が生成する
    httpx2.AsyncClient がモックのトランスポートを使うようになる。
    関数はリクエストの記録先リストを返し、テスト側から送信内容を検証できる。
    """

    def install(handler: Handler) -> list[httpx2.Request]:
        requests: list[httpx2.Request] = []

        def recording_handler(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return handler(request)

        transport = httpx2.MockTransport(recording_handler)

        def factory(**kwargs: Any) -> httpx2.AsyncClient:
            kwargs.setdefault("transport", transport)
            return _REAL_ASYNC_CLIENT(**kwargs)

        monkeypatch.setattr(notepm.httpx2, "AsyncClient", factory)
        return requests

    return install
