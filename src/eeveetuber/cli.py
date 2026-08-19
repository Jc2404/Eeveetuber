"""Console entry point for the local Eeveetuber server."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from eeveetuber.api.app import create_app
from eeveetuber.config import get_settings
from eeveetuber.observability import configure_logging, get_logger


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    settings = get_settings()
    verbose_path = settings.verbose_log_path if args.verbose else None
    configured_path = configure_logging(
        settings.log_level,
        text_log_path=verbose_path,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    if configured_path is not None:
        get_logger(component="cli").info(
            "verbose_file_logging_enabled",
            path=str(configured_path),
            rotation_bytes=settings.log_max_bytes,
            retained_backups=settings.log_backup_count,
        )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        log_config=None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Eeveetuber server.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="also write a rotating DEBUG text log in the per-user Eeveetuber log directory",
    )
    return parser


if __name__ == "__main__":
    main()
