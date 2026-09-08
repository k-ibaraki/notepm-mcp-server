"""再試行までの待ち時間の決め方を検証する。

conftest の no_retry_waits が全体では待ち時間を 0 にしているため、実際の値を
固定するのはこのファイルの役目になる。ここでは HTTP を発行せず、応答から
待ち時間を導く関数だけを直接見る。
"""

import httpx2
import pytest

from notepm_mcp_server import notepm


@pytest.fixture(autouse=True)
def real_retry_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """no_retry_waits が 0 にした定数を、本来の値に戻す。"""
    monkeypatch.setattr(notepm, "RETRY_BACKOFF_SECONDS", 0.5)
    monkeypatch.setattr(notepm, "MAX_RETRY_WAIT_SECONDS", 10.0)


def test_delay_doubles_with_each_attempt() -> None:
    """Retry-After が無ければ、試行ごとに待ち時間を倍にする。"""
    assert notepm._retry_delay(1, None) == 0.5
    assert notepm._retry_delay(2, None) == 1.0


def test_retry_after_takes_precedence_over_backoff() -> None:
    """待ち時間を相手が指定してきたら、そちらに従う。"""
    response = httpx2.Response(429, headers={"Retry-After": "3"})

    assert notepm._retry_delay(1, response) == 3.0


def test_long_retry_after_is_capped() -> None:
    """長すぎる Retry-After は上限で丸める。

    そのまま従うと、ツール呼び出しが何分も戻らなくなる。
    """
    response = httpx2.Response(429, headers={"Retry-After": "600"})

    assert notepm._retry_delay(1, response) == 10.0


def test_unparsable_retry_after_falls_back_to_backoff() -> None:
    """HTTP-date 形式など秒数として読めない値は、バックオフに任せる。"""
    response = httpx2.Response(
        429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    )

    assert notepm._retry_delay(1, response) == 0.5


def test_negative_retry_after_does_not_go_below_zero() -> None:
    response = httpx2.Response(429, headers={"Retry-After": "-5"})

    assert notepm._retry_delay(1, response) == 0.0
