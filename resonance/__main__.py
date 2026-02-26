"""
Resonance Music Server - Entry Point

Run with: python -m resonance

Utility commands:
    python -m resonance --hash-password
        Prompt for a password and print the hashed value for use in
        ``resonance.toml`` under ``[security] auth_password_hash``.
"""

import argparse
import asyncio
import getpass
import logging
import sys

from resonance.config.settings import (
    ServerSettings,
    init_settings,
    load_settings,
)
from resonance.server import ResonanceServer


def setup_logging(settings: ServerSettings) -> None:
    """Configure logging for the application based on settings."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if settings.log_file is not None:
        try:
            file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
            handlers.append(file_handler)
        except Exception as exc:
            # Fall back to stderr-only if log file can't be opened
            print(f"Warning: Could not open log file {settings.log_file!r}: {exc}", file=sys.stderr)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
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
        default=None,
        help="Slimproto port (default: 3483)",
    )

    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host address to bind to (default: 0.0.0.0)",
    )

    parser.add_argument(
        "--web-port",
        type=int,
        default=None,
        help="Web/Streaming port (default: 9000)",
    )

    parser.add_argument(
        "--cli-port",
        type=int,
        default=None,
        help="Telnet CLI port (default: 9090, use 0 to disable)",
    )

    parser.add_argument(
        "--cors-origins",
        type=str,
        default=None,
        help='Allowed CORS origins. "*" permits all (default). '
             'Comma-separated list to restrict, e.g. "http://localhost:3000,https://my.app"',
    )

    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to TOML config file (default: auto-detect resonance.toml)",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    parser.add_argument(
        "--hash-password",
        action="store_true",
        help="Interactively hash a password for use in resonance.toml "
             "[security] auth_password_hash, then exit.",
    )

    parser.add_argument(
        "--outgoing-frame-diag",
        action="store_true",
        help="Enable 'outgoing frame diagnostic' output in stderr even if the slimproto logger is not set to DEBUG",
    )

    return parser.parse_args()


def _build_cli_overrides(args: argparse.Namespace) -> dict[str, object]:
    """
    Build a dict of CLI overrides from parsed arguments.

    Only includes arguments that were explicitly provided (non-None).
    """
    overrides: dict[str, object] = {}

    if args.verbose:
        overrides["verbose"] = True
    if args.host is not None:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = args.port
    if args.web_port is not None:
        overrides["web_port"] = args.web_port
    if args.cli_port is not None:
        overrides["cli_port"] = args.cli_port
    if args.cors_origins is not None:
        overrides["cors_origins"] = args.cors_origins
    if args.outgoing_frame_diag is not None:
        overrides["outgoing_frame_diag"] = args.outgoing_frame_diag

    return overrides


async def run_server(settings: ServerSettings) -> None:
    """Start and run the Resonance server."""
    server = ResonanceServer(
        host=settings.host,
        port=settings.slimproto_port,
        web_port=settings.web_port,
        cli_port=settings.cli_port,
        cors_origins=settings.cors_origins,
    )
    await server.run()


def _hash_password_interactive() -> int:
    """Prompt for a password, print the hash, and exit."""
    from resonance.web.security import hash_password

    print("Generate a password hash for resonance.toml [security] auth_password_hash")
    print()

    try:
        password = getpass.getpass("Password: ")
        if not password:
            print("Error: password must not be empty.", file=sys.stderr)
            return 1
        confirm = getpass.getpass("Confirm:  ")
        if password != confirm:
            print("Error: passwords do not match.", file=sys.stderr)
            return 1
    except (KeyboardInterrupt, EOFError):
        print()
        return 130

    hashed = hash_password(password)
    print()
    print("Add this to your resonance.toml:")
    print()
    print("[security]")
    print(f'auth_password_hash = "{hashed}"')
    print()
    return 0


def main() -> int:
    """Main entry point for the application."""
    args = parse_args()

    # Handle --hash-password before anything else (no config needed)
    if args.hash_password:
        return _hash_password_interactive()

    # Load settings: TOML config + CLI overrides
    cli_overrides = _build_cli_overrides(args)

    outgoing_frame_diag = cli_overrides.pop('outgoing_frame_diag', None)
    if outgoing_frame_diag is not None:
        from resonance.protocol import slimproto
        slimproto.OUTGOING_FRAME_DEBUG = outgoing_frame_diag

    try:
        settings = load_settings(
            config_path=args.config,
            cli_overrides=cli_overrides,
        )
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    # Register as global singleton
    init_settings(settings)

    # Set up logging based on resolved settings
    setup_logging(settings)

    logger = logging.getLogger(__name__)
    logger.info("Starting Resonance Music Server...")

    if settings._config_path is not None:
        logger.info("Config loaded from %s", settings._config_path)
    else:
        logger.info("No config file found, using defaults + CLI arguments")

    try:
        asyncio.run(run_server(settings))
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        return 1

    logger.info("Server stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
