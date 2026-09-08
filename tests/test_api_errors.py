"""API が成功以外の応答を返したときの分類と、そこで何を出すかを検証する。

呼び出し側が「待てば直るのか、設定を直すのか、渡した値が誤っているのか」を区別できること
（Issue #11）と、応答本文と API トークンがエラーメッセージへ混ざらないことを固定する。
"""

import json
import logging
import re

import httpx2
import pytest

from notepm_mcp_server import notepm

from .conftest import API_TOKEN, InstallMock

# NotePM API のドキュメント (https://notepm.jp/docs/api) が挙げるステータスと、こちらの
# 分類の対応。403 は記載が無いが、権限不足として返り得るため認証側に寄せている。
STATUS_CASES = [
    pytest.param(400, notepm.NotePMBadRequestError, id="400はリクエストを受け付けない"),
    pytest.param(401, notepm.NotePMAuthError, id="401は認証"),
    pytest.param(403, notepm.NotePMAuthError, id="403も認証に寄せる"),
    pytest.param(404, notepm.NotePMNotFoundError, id="404は対象が無い"),
    pytest.param(429, notepm.NotePMRateLimitError, id="429はレート制限"),
    pytest.param(500, notepm.NotePMServerError, id="500はNotePM側の失敗"),
    pytest.param(503, notepm.NotePMServerError, id="503もNotePM側の失敗"),
]


@pytest.mark.parametrize(("status", "expected"), STATUS_CASES)
async def test_search_classifies_error_status(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    status: int,
    expected: type[notepm.NotePMAPIError],
) -> None:
    mock_api(lambda request: httpx2.Response(status, text="エラーの詳細"))

    with pytest.raises(notepm.NotePMAPIError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    # 派生関係ではなく型そのものを見る。上位の型で一致しても区別が付かないため。
    assert type(error.value) is expected
    assert error.value.status_code == status
    assert str(status) in str(error.value)


@pytest.mark.parametrize(("status", "expected"), STATUS_CASES)
async def test_detail_classifies_error_status(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    status: int,
    expected: type[notepm.NotePMAPIError],
) -> None:
    mock_api(lambda request: httpx2.Response(status, text="エラーの詳細"))

    with pytest.raises(notepm.NotePMAPIError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.get_notepm_page_detail(
                notepm.NotePMDetailParams(page_code="abc123")
            )

    assert type(error.value) is expected
    assert error.value.status_code == status


async def test_detail_not_found_names_the_page_code(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """詳細取得の 404 は、どのページコードで失敗したのかを伝える。"""
    mock_api(lambda request: httpx2.Response(404, text="Not Found"))

    with pytest.raises(notepm.NotePMNotFoundError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.get_notepm_page_detail(
                notepm.NotePMDetailParams(page_code="missing0001")
            )

    assert "missing0001" in str(error.value)


async def test_detail_bad_request_names_the_page_code(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """詳細取得の 400 も、どのページコードで失敗したのかを伝える。

    実 API は存在しない page_code に 404 ではなく 400 を返す（Issue #31）。ここが
    一般的な文言のままだと、呼び出し側は次に何を試せばよいのか読み取れない。
    """
    # 本文は実 API が返す形に寄せつつ、中身は組み立てた文面と重ならない目印にする。
    # 実本文の「権限がありません」をそのまま使うと、下の assert が「意図した文面が
    # 返っている」のか「応答本文が漏れた」のかを区別できなくなる。
    mock_api(lambda request: httpx2.Response(400, text='{"messages":["MARKER"]}'))

    with pytest.raises(notepm.NotePMBadRequestError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.get_notepm_page_detail(
                notepm.NotePMDetailParams(page_code="missing0001")
            )

    message = str(error.value)
    assert "missing0001" in message
    # NotePM が存在しないページと権限不足を区別しないため、こちらも断定しない。
    assert "存在しない" in message
    assert "権限" in message
    # 呼び出し元が文面を差し込めるようになった経路でも、応答本文は混ざらない。
    assert "MARKER" not in message


async def test_search_bad_request_stays_generic(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """検索の 400 は一般的な文言のまま。page_code のような手掛かりを持たないため。"""
    mock_api(lambda request: httpx2.Response(400, text="エラーの詳細"))

    with pytest.raises(notepm.NotePMBadRequestError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    message = str(error.value)
    assert "指定したパラメータの値を見直してください" in message
    assert "エラーの詳細" not in message


async def test_rate_limit_message_claims_no_specific_limit(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """429 の文言に具体的な上限値は載せない。

    NotePM の API ドキュメントは 1 分あたり 60 リクエストとするが、実応答の
    X-RateLimit-Limit ヘッダは 120 を返す。どちらが実際の上限かこちらでは確かめ
    られないため、確かめられない数字を呼び出し側へ渡さない（Issue #32）。
    """
    mock_api(lambda request: httpx2.Response(429, text="Too Many Requests"))

    with pytest.raises(notepm.NotePMRateLimitError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    message = str(error.value)
    # 60 と 120 だけを弾いても、別の数字に書き換わったときに素通りする。守りたいのは
    # 「数量の案内を載せない」ことなので、数量表現そのものが無いことを見る。
    assert re.search(r"[0-9]+\s*リクエスト", message) is None
    assert "再試行" in message


async def test_unclassified_status_stays_generic(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """ドキュメントに無いステータスは、原因を推測せず汎用のエラーにする。"""
    mock_api(lambda request: httpx2.Response(418, text="I'm a teapot"))

    with pytest.raises(notepm.NotePMAPIError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    assert type(error.value) is notepm.NotePMAPIError
    assert error.value.status_code == 418


async def test_error_message_leaks_neither_body_nor_token(
    config: notepm.NotePMConfig, mock_api: InstallMock
) -> None:
    """応答本文はエラーメッセージへ載せない。

    本文に何が含まれるかは NotePM 側の都合で決まる。そのまま連結すると、クライアントの
    記録や LLM の文脈へ制御できない内容が流れ出る。ここでは本文にトークンを混ぜて、
    本文経由でも漏れないことまで確かめる。
    """
    body = f"認証に失敗しました。token={API_TOKEN} は無効です"
    mock_api(lambda request: httpx2.Response(401, text=body))

    with pytest.raises(notepm.NotePMAuthError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            await client.search(notepm.SearchParams(q="議事録"))

    message = str(error.value)
    assert API_TOKEN not in message
    assert "無効です" not in message


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        pytest.param(logging.DEBUG, True, id="DEBUGなら残す"),
        pytest.param(logging.WARNING, False, id="既定の水準では出さない"),
    ],
)
async def test_response_body_is_logged_only_at_debug(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    caplog: pytest.LogCaptureFixture,
    level: int,
    expected: bool,
) -> None:
    """本文は運用者が切り分けるときにだけ要る。既定では出さない。"""
    mock_api(lambda request: httpx2.Response(500, text="内部エラーの詳細"))

    with caplog.at_level(level, logger=notepm.__name__):
        with pytest.raises(notepm.NotePMServerError):
            async with notepm.NotePMAPIClient(config) as client:
                await client.search(notepm.SearchParams(q="議事録"))

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert ("内部エラーの詳細" in logged) is expected


async def test_logged_response_body_is_truncated_and_kept_on_one_line(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """長い本文は先頭だけに切り詰め、改行はエスケープして 1 行に収める。

    切り詰めるのは、メンテナンス時に返る HTML のような長い応答でログを埋めないため。
    改行を落とすのは、本文の中身で偽のログ行を作られないようにするため。
    """
    body = "あ\n" * 500
    mock_api(lambda request: httpx2.Response(500, text=body))

    with caplog.at_level(logging.DEBUG, logger=notepm.__name__):
        with pytest.raises(notepm.NotePMServerError):
            async with notepm.NotePMAPIClient(config) as client:
                await client.search(notepm.SearchParams(q="議事録"))

    body_records = [r for r in caplog.records if "body=" in r.getMessage()]
    assert len(body_records) == 1
    message = body_records[0].getMessage()
    assert "\n" not in message
    # 全文（1000 文字）ではなく、上限までしか残らない
    assert message.count("あ") == notepm.ERROR_BODY_LOG_LIMIT // 2


@pytest.mark.parametrize(
    "call",
    [
        pytest.param("search", id="検索"),
        pytest.param("detail", id="詳細取得"),
    ],
)
async def test_broken_json_becomes_a_response_error(
    config: notepm.NotePMConfig, mock_api: InstallMock, call: str
) -> None:
    """JSON として読めない応答は、ステータス由来の失敗と別の型で伝える。"""
    mock_api(lambda request: httpx2.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(notepm.NotePMResponseError) as error:
        async with notepm.NotePMAPIClient(config) as client:
            if call == "search":
                await client.search(notepm.SearchParams(q="議事録"))
            else:
                await client.get_notepm_page_detail(
                    notepm.NotePMDetailParams(page_code="abc123")
                )

    assert "JSON" in str(error.value)
    # 読めなかった本文自体はメッセージへ載せない
    assert "maintenance" not in str(error.value)
    # 元の例外は握りつぶさず、トレースバックから辿れるようにしておく
    assert isinstance(error.value.__cause__, json.JSONDecodeError)


async def test_api_token_never_reaches_the_logs(
    config: notepm.NotePMConfig,
    mock_api: InstallMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """-vv 相当の水準でも、API トークンはどのロガーにも出ない。

    ルートロガーで受けるのは、-vv が logging.basicConfig でルートの水準を下げ、依存
    ライブラリのログもまとめて出るようになるため。このサーバー自身の出力に加えて、
    httpx2 がクライアント層で出す 1 行（method と URL とステータス）もここに含まれる。
    届かないのは MockTransport が飛ばす httpcore2 の層だけで、そちらは Trace が DEBUG で
    出す Request の repr にヘッダを含まないことを実装で確認している。
    """
    mock_api(lambda request: httpx2.Response(401, text="Unauthorized"))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(notepm.NotePMAuthError):
            async with notepm.NotePMAPIClient(config) as client:
                await client.search(notepm.SearchParams(q="議事録"))

    assert caplog.records, "DEBUG の記録が 1 件も無い（ログ自体が出ていない）"
    for record in caplog.records:
        assert API_TOKEN not in record.getMessage()
