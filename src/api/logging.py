import logging
import sys

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from api.config import Settings

SERVICE_NAME_VALUE = "ai-product-investigator"


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
            if settings.environment == "prod"
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def configure_tracing(settings: Settings) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: SERVICE_NAME_VALUE}))
    if settings.otel_console_export:
        processor: BatchSpanProcessor | SimpleSpanProcessor = SimpleSpanProcessor(
            ConsoleSpanExporter()
        )
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    return provider
