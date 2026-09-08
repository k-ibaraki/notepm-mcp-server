"""CLI の引数まわりだけを確認する。

main() を引数なしで呼ぶと serve() が起動し、資格情報が揃った環境では標準入出力を
待ち受けたまま処理が返ってこない。そのため、引数の解析だけで終わる呼び出しに
絞って検証する。
"""

import pytest
from click.testing import CliRunner

from notepm_mcp_server import main


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
