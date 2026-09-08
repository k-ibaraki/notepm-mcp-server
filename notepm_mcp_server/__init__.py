import click
import logging
import sys
from notepm_mcp_server.notepm import serve


def resolve_log_level(verbose: int) -> int:
    """-v の指定回数からログの水準を決める

    切り出してあるのは、main() が serve() まで進んで標準入出力を待ち続けるため、
    水準の対応づけだけをテストから確かめられるようにするためである。

    Args:
        verbose (int): -v の指定回数

    Returns:
        int: logging の水準。既定は WARNING、1 回で INFO、2 回以上で DEBUG。
    """
    if verbose >= 2:
        return logging.DEBUG
    if verbose == 1:
        return logging.INFO
    return logging.WARNING


@click.command()
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="ログを詳しくする。既定は WARNING、-v で INFO、-vv 以上で DEBUG。",
)
def main(verbose: int) -> None:
    """NotePM MCP Server - NotePM の検索と記事取得を MCP ツールとして提供する"""
    import asyncio

    # ルートロガーを設定するため、依存ライブラリのログも同じ水準で出る。-v なら
    # httpx2 が発行するリクエストの 1 行が、-vv なら接続の詳細まで加わる。
    logging.basicConfig(level=resolve_log_level(verbose), stream=sys.stderr)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
