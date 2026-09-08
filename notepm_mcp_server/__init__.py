import click
import logging
import sys
from notepm_mcp_server.notepm import serve


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

    logging_level = logging.WARN
    if verbose == 1:
        logging_level = logging.INFO
    elif verbose >= 2:
        logging_level = logging.DEBUG

    logging.basicConfig(level=logging_level, stream=sys.stderr)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
