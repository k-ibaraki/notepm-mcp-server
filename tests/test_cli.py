"""CLI の引数まわりだけを確認する。

main() を引数なしで呼ぶと serve() が起動し、資格情報が揃った環境では標準入出力を
待ち受けたまま処理が返ってこない。そのため、引数の解析だけで終わる呼び出しと、
そこから切り出した関数に絞って検証する。
"""

import logging

import pytest
from click.testing import CliRunner

from notepm_mcp_server import main, resolve_log_level


def test_help_does_not_advertise_repository_option() -> None:
    """テンプレート由来の --repository が --help に残っていないこと。"""
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "repository" not in result.output


@pytest.mark.parametrize("args", [["--repository", "/tmp"], ["-r", "/tmp"]])
def test_repository_option_is_rejected(args: list[str]) -> None:
    """削除したオプションは黙って無視されず、使い方エラーになること。"""
    result = CliRunner().invoke(main, args)

    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("verbose", "expected"),
    [
        pytest.param(0, logging.WARNING, id="既定は警告以上"),
        pytest.param(1, logging.INFO, id="-vでINFO"),
        pytest.param(2, logging.DEBUG, id="-vvでDEBUG"),
        pytest.param(5, logging.DEBUG, id="重ねてもDEBUG止まり"),
    ],
)
def test_verbose_count_maps_to_a_log_level(verbose: int, expected: int) -> None:
    """-v の指定回数がログの水準に効いていること（Issue #11）。"""
    assert resolve_log_level(verbose) == expected
