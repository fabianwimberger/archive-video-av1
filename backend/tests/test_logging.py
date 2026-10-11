import logging

import pytest

from app import main


@pytest.mark.parametrize(
    ("log_level", "server_level", "request_level"),
    [
        ("DEBUG", logging.DEBUG, logging.DEBUG),
        ("INFO", logging.INFO, logging.WARNING),
        ("WARNING", logging.WARNING, logging.WARNING),
        ("ERROR", logging.ERROR, logging.ERROR),
    ],
)
def test_log_level_applies_to_all_loggers(
    monkeypatch, log_level, server_level, request_level
):
    monkeypatch.setattr(main.settings, "LOG_LEVEL", log_level)
    try:
        main.configure_logging()

        for name in ("", "uvicorn", "uvicorn.error"):
            assert logging.getLogger(name).level == server_level
        for name in ("uvicorn.access", "httpx"):
            assert logging.getLogger(name).level == request_level
    finally:
        monkeypatch.undo()
        main.configure_logging()
