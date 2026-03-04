from loguru import logger

logger.disable("feedforger")


def setup_logging(level: str = "INFO") -> None:
    """Configure loguru for feedforger."""
    import sys

    logger.enable("feedforger")
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <level>{message}</level>",
    )
