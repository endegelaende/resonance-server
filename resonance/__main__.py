"""
Resonance Music Server - Entry Point

Run with: python -m resonance
"""

import argparse
import asyncio
import logging
import sys

from resonance.server import ResonanceServer


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce noise from third-party libraries
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="resonance",
        description="Resonance Music Server - A modern Squeezebox-compatible music server",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (debug) logging",
    )

    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=3483,
        help="Slimproto port (default: 3483)",
    )

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host address to bind to (default: 0.0.0.0)",
    )

    parser.add_argument(
        "--web-port",
        type=int,
        default=9000,
        help="Web/Streaming port (default: 9000)",
    )

    parser.add_argument(
        "--cli-port",
        type=int,
        default=9090,
        help="Telnet CLI port (default: 9090, use 0 to disable)",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    return parser.parse_args()


async def run_server(host: str, port: int, web_port: int, cli_port: int) -> None:
    """Start and run the Resonance server."""
    server = ResonanceServer(host=host, port=port, web_port=web_port, cli_port=cli_port)
    await server.run()


def main() -> int:
    """Main entry point for the application."""
    args = parse_args()
    setup_logging(verbose=args.verbose)

    logger = logging.getLogger(__name__)
    logger.info("Starting Resonance Music Server...")

    try:
        asyncio.run(
            run_server(
                host=args.host,
                port=args.port,
                web_port=args.web_port,
                cli_port=args.cli_port,
            )
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        return 1

    logger.info("Server stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

