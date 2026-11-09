"""Opt-in telemetry must not start a network exporter by default or break grading.

Mock transports: no external collector is contacted. These tests address the
uncovered monitoring addition that lowered the existing 95% CI coverage gate.
"""

from unittest.mock import Mock

import pytest

from app import otel_export as otel


@pytest.fixture(autouse=True)
def isolated_provider(monkeypatch):
    monkeypatch.setattr(otel, "_log_provider", None)
    monkeypatch.setattr(otel, "_log_handler", None)
    monkeypatch.delenv("GRADING_LOG_OTLP_ENABLED", raising=False)
    monkeypatch.delenv("GRADING_OTLP_LOGS_ENDPOINT", raising=False)


def test_default_and_disabled_export_never_construct_transport(monkeypatch):
    exporter = Mock(side_effect=AssertionError("unexpected network exporter"))
    monkeypatch.setattr(otel, "OTLPLogExporter", exporter)
    assert otel.setup_otlp_logs() is False
    monkeypatch.setenv("GRADING_OTLP_LOGS_ENDPOINT", "http://collector:4318")
    assert otel.setup_otlp_logs() is False
    monkeypatch.setenv("GRADING_LOG_OTLP_ENABLED", "false")
    assert otel.setup_otlp_logs() is False
    exporter.assert_not_called()


@pytest.mark.parametrize("endpoint", ["", "collector:4318", "file:///tmp/logs", "https://user:secret@collector"])
def test_invalid_config_never_constructs_exporter(monkeypatch, endpoint):
    monkeypatch.setenv("GRADING_LOG_OTLP_ENABLED", "true")
    monkeypatch.setenv("GRADING_OTLP_LOGS_ENDPOINT", endpoint)
    exporter = Mock(side_effect=AssertionError("unexpected network exporter"))
    monkeypatch.setattr(otel, "OTLPLogExporter", exporter)
    assert otel.setup_otlp_logs() is False
    exporter.assert_not_called()


def test_exporter_setup_failure_is_nonfatal_and_does_not_claim_ready(monkeypatch):
    monkeypatch.setenv("GRADING_LOG_OTLP_ENABLED", "on")
    monkeypatch.setenv("GRADING_OTLP_LOGS_ENDPOINT", "http://collector:4318/")
    exporter = Mock(side_effect=OSError("collector initialization failed"))
    monkeypatch.setattr(otel, "OTLPLogExporter", exporter)
    assert otel.setup_otlp_logs() is False
    exporter.assert_called_once_with(endpoint="http://collector:4318/v1/logs", timeout=2)
    assert otel._log_provider is None
    assert otel._log_handler is None


def test_repeated_setup_does_not_replace_existing_provider(monkeypatch):
    provider = Mock()
    monkeypatch.setattr(otel, "_log_provider", provider)
    exporter = Mock(side_effect=AssertionError("duplicate exporter"))
    monkeypatch.setattr(otel, "OTLPLogExporter", exporter)
    assert otel.setup_otlp_logs() is True
    exporter.assert_not_called()
    assert otel._log_provider is provider


@pytest.mark.parametrize("fail", [False, True])
def test_shutdown_clears_provider_even_if_exporter_fails(monkeypatch, fail):
    provider = Mock()
    if fail:
        provider.shutdown.side_effect = OSError("collector unavailable")
    monkeypatch.setattr(otel, "_log_provider", provider)
    otel.shutdown_otlp_logs()
    provider.shutdown.assert_called_once_with()
    assert otel._log_provider is None
    assert otel._log_handler is None
    otel.shutdown_otlp_logs()
    provider.shutdown.assert_called_once_with()
