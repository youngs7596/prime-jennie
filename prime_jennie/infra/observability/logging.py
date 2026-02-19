"""Structured logging — structlog 기반 설정.

Usage:
    from prime_jennie.infra.observability.logging import setup_logging

    setup_logging(service_name="scout-job")
    logger = structlog.get_logger()
    logger.info("pipeline.started", phase="universe", count=200)
"""

import logging
import sys
from typing import Optional

import structlog


def setup_logging(
    service_name: str = "prime-jennie",
    *,
    log_level: str = "INFO",
    json_output: bool = True,
) -> None:
    """전역 structlog + stdlib 로깅 설정.

    Args:
        service_name: 로그에 포함할 서비스 이름
        log_level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR)
        json_output: True면 JSON 형식, False면 사람이 읽기 쉬운 형식
    """
    log_level_int = getattr(logging, log_level.upper(), logging.INFO)

    # stdlib 로깅 기본 설정
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level_int,
    )

    # 공통 프로세서
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # stdlib handler에 structlog 포매터 연결
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    root = logging.getLogger()
    for handler in root.handlers:
        handler.setFormatter(formatter)

    # 서비스 이름 바인딩
    structlog.contextvars.bind_contextvars(service=service_name)
