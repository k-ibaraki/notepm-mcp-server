"""NotePMConfig の起動時チェックを検証する。"""

import pytest

from notepm_mcp_server import notepm

from .conftest import API_BASE, API_TOKEN, TEAM


def test_builds_api_base_from_team(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTEPM_TEAM", TEAM)
    monkeypatch.setenv("NOTEPM_API_TOKEN", API_TOKEN)

    config = notepm.NotePMConfig()

    assert config.api_base == API_BASE
    assert config.api_token == API_TOKEN


@pytest.mark.parametrize(
    "present",
    [
        pytest.param({}, id="両方欠けている"),
        pytest.param({"NOTEPM_TEAM": TEAM}, id="APIトークンが欠けている"),
        pytest.param({"NOTEPM_API_TOKEN": API_TOKEN}, id="チーム名が欠けている"),
        pytest.param(
            {"NOTEPM_TEAM": "", "NOTEPM_API_TOKEN": API_TOKEN}, id="チーム名が空文字"
        ),
    ],
)
def test_requires_team_and_token(
    monkeypatch: pytest.MonkeyPatch, present: dict[str, str]
) -> None:
    for name, value in present.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="NOTEPM_TEAM"):
        notepm.NotePMConfig()


def test_max_body_length_defaults_to_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTEPM_TEAM", TEAM)
    monkeypatch.setenv("NOTEPM_API_TOKEN", API_TOKEN)

    assert notepm.NotePMConfig().max_body_length == 200


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("50", 50, id="正の整数"),
        pytest.param(" 50 ", 50, id="前後に空白の整数"),
        pytest.param("0", 0, id="0は本文を丸ごと省略する指定"),
        pytest.param("", 200, id="空文字は未設定と同じ"),
    ],
)
def test_max_body_length_reads_env(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: int
) -> None:
    monkeypatch.setenv("NOTEPM_TEAM", TEAM)
    monkeypatch.setenv("NOTEPM_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("NOTEPM_MAX_BODY_LENGTH", value)

    assert notepm.NotePMConfig().max_body_length == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("abc", id="整数として読めない"),
        pytest.param("1.5", id="小数"),
        pytest.param("-1", id="負の値"),
    ],
)
def test_max_body_length_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """不正な値は起動時に落とす。どの環境変数の話かをメッセージから読み取れること。"""
    monkeypatch.setenv("NOTEPM_TEAM", TEAM)
    monkeypatch.setenv("NOTEPM_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("NOTEPM_MAX_BODY_LENGTH", value)

    with pytest.raises(ValueError, match="NOTEPM_MAX_BODY_LENGTH"):
        notepm.NotePMConfig()


def test_raise_exceptions_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTEPM_TEAM", TEAM)
    monkeypatch.setenv("NOTEPM_API_TOKEN", API_TOKEN)

    assert notepm.NotePMConfig().raise_exceptions is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("1", True, id="1"),
        pytest.param("true", True, id="true"),
        pytest.param("TRUE", True, id="大文字のtrue"),
        pytest.param(" yes ", True, id="前後に空白のyes"),
        pytest.param("on", True, id="on"),
        pytest.param("0", False, id="0"),
        pytest.param("false", False, id="false"),
        pytest.param("", False, id="空文字"),
        pytest.param("maybe", False, id="解釈できない値"),
    ],
)
def test_raise_exceptions_reads_env(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv("NOTEPM_TEAM", TEAM)
    monkeypatch.setenv("NOTEPM_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("NOTEPM_RAISE_EXCEPTIONS", value)

    assert notepm.NotePMConfig().raise_exceptions is expected
