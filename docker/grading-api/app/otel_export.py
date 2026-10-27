"""Best-effort OTLP log export for grading-api (disabled by default)."""
from __future__ import annotations

import os
from typing import Any

from opentelemetry import _logs
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from . import telemetry

_log_provider = None
_log_handler = None


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _validate_endpoint(url: str) -> bool:
    if not url:
        return False
    if "://" not in url:
        return False
    scheme, rest = url.split("://", 1)
    if scheme not in ("http", "https"):
        return False
    if "@" in rest.split("/")[0]:
        return False
    return True


def setup_otlp_logs() -> bool:
    global _log_provider, _log_handler
    if _log_provider is not None:
        return True

    enabled = _bool_env("GRADING_LOG_OTLP_ENABLED", False)
    endpoint = os.environ.get("GRADING_OTLP_LOGS_ENDPOINT", "").strip()
    if not enabled or not endpoint:
        return False
    if not _validate_endpoint(endpoint):
        return False

    try:
        resource = Resource.create({"service.name": "grading-api", "service.namespace": "dicom-app"})
        exporter = OTLPLogExporter(endpoint=endpoint.rstrip("/") + "/v1/logs", timeout=2)
        processor = BatchLogRecordProcessor(exporter)
        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(processor)
        _logs.set_logger_provider(provider)
        handler = LoggingHandler(level=0, logger_provider=provider)
        import logging

        logging.getLogger().addHandler(handler)
        _log_handler = handler
        _log_provider = provider
        return True
    except Exception:
        return False


def shutdown_otlp_logs(timeout_seconds: float = 5.0) -> None:
    global _log_provider, _log_handler
    if _log_provider is None:
        return
    try:
        _log_provider.shutdown()
    except Exception:
        pass
    _log_provider = None
    _log_handler = None
