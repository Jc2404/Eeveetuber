"""Console entry point for the local Eeveetuber server."""

from __future__ import annotations

import uvicorn

from eeveetuber.api.app import create_app
from eeveetuber.config import get_settings
from eeveetuber.observability import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

