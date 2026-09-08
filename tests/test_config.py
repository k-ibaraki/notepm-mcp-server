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


def test_max_body_length_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTEPM_TEAM", TEAM)
    monkeypatch.setenv("NOTEPM_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("NOTEPM_MAX_BODY_LENGTH", "50")

    assert notepm.NotePMConfig().max_body_length == 50
