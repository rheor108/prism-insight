"""
Phase 6 S1 — Vision plumbing unit tests.

All tests are fully mocked: zero network calls, zero real OpenAI client.
Run with:  .venv/bin/python -m pytest tests/test_vision_plumbing.py -q
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def forbid_unmocked_subscription(monkeypatch):
    monkeypatch.setattr("prism_core.codex_subscription._invoke",
                        AsyncMock(side_effect=AssertionError("Live inference forbidden in unit tests")))


class SimpleSchema(BaseModel):
    label: str
    confidence: int


def _make_mock_response(text: str) -> MagicMock:
    """Build a fake Responses API response with one message output item."""
    part = MagicMock()
    part.text = text

    message_item = MagicMock()
    message_item.type = "message"
    message_item.content = [part]

    response = MagicMock()
    response.output = [message_item]
    return response


# ---------------------------------------------------------------------------
# capabilities.py tests
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_has_api_key_false_when_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "")
        assert capabilities.has_api_key() is False

    def test_has_api_key_false_for_placeholder(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "chatgpt-oauth-placeholder")
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "")
        assert capabilities.has_api_key() is False

    def test_has_api_key_true_for_real_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-realkey")
        from cores.llm import capabilities
        assert capabilities.has_api_key() is True

    def test_has_api_key_true_from_secrets_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "sk-secret")
        assert capabilities.has_api_key() is True
        assert capabilities.resolve_openai_api_key() == "sk-secret"

    def test_resolve_env_takes_priority_over_secrets(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "sk-secret")
        assert capabilities.resolve_openai_api_key() == "sk-env"

    def test_vision_enabled_default_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        from cores.llm import capabilities
        assert capabilities.vision_enabled() is False

    def test_vision_enabled_on(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        from cores.llm import capabilities
        assert capabilities.vision_enabled() is True

    def test_vision_shadow_default_true(self, monkeypatch):
        monkeypatch.delenv("PRISM_VISION_SHADOW", raising=False)
        from cores.llm import capabilities
        assert capabilities.vision_shadow() is True

    def test_vision_shadow_false_when_set(self, monkeypatch):
        monkeypatch.setenv("PRISM_VISION_SHADOW", "false")
        from cores.llm import capabilities
        assert capabilities.vision_shadow() is False

    def test_vision_in_report_default_off(self, monkeypatch):
        # Safety contract: report is byte-identical unless explicitly enabled.
        monkeypatch.delenv("PRISM_FEATURE_VISION_IN_REPORT", raising=False)
        from cores.llm import capabilities
        assert capabilities.vision_in_report() is False

    def test_vision_in_report_on(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION_IN_REPORT", "on")
        from cores.llm import capabilities
        assert capabilities.vision_in_report() is True

    def test_vision_model_default(self, monkeypatch):
        monkeypatch.delenv("PRISM_VISION_MODEL", raising=False)
        from cores.llm import capabilities
        assert capabilities.vision_model() == "gpt-5.6-sol"

    def test_vision_model_override(self, monkeypatch, tmp_path):
        from prism_core.ai_models import CONFIG
        data = json.loads(CONFIG.read_text())
        data['stages']['vision']['model'] = 'gpt-5.6-terra'
        config = tmp_path / 'models.json'
        config.write_text(json.dumps(data))
        monkeypatch.setenv('PRISM_AI_CONFIG', str(config))
        from cores.llm import capabilities
        assert capabilities.vision_model() == "gpt-5.6-terra"

    def test_vision_auth_subscription(self, monkeypatch):
        monkeypatch.delenv("PRISM_VISION_AUTH", raising=False)
        from cores.llm import capabilities
        assert capabilities.vision_auth() == "codex_subscription"

    def test_vision_available_false_when_off(self, monkeypatch):
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        from cores.llm import capabilities
        assert capabilities.vision_available() is False

    def test_vision_available_true_without_api_key(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from cores.llm import capabilities
        monkeypatch.setattr(capabilities, "_secrets_api_key", lambda: "")
        assert capabilities.vision_available() is True

    def test_vision_available_true_when_on_and_key(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        from cores.llm import capabilities
        assert capabilities.vision_available() is True

    def test_vision_buy_quality_inactive_when_lifecycle_is_off(self, monkeypatch):
        from cores.llm import capabilities

        monkeypatch.setattr(
            "cores.shadow_lifecycle.feature_mode",
            lambda feature: "off" if feature == "vision_buy_quality" else "shadow",
        )

        assert capabilities.vision_buy_quality_active() is False

    def test_vision_buy_quality_active_while_lifecycle_is_shadow(self, monkeypatch):
        from cores.llm import capabilities

        monkeypatch.setattr(
            "cores.shadow_lifecycle.feature_mode",
            lambda feature: "shadow",
        )

        assert capabilities.vision_buy_quality_active() is True

    def test_vision_buy_quality_fails_closed_when_lifecycle_lookup_fails(
        self, monkeypatch
    ):
        from cores.llm import capabilities

        def _fail(_feature):
            raise OSError("state unavailable")

        monkeypatch.setattr("cores.shadow_lifecycle.feature_mode", _fail)

        assert capabilities.vision_buy_quality_active() is False


# ---------------------------------------------------------------------------
# analyze_image — OFF path (default): returns None, zero client calls
# ---------------------------------------------------------------------------


class TestAnalyzeImageOff:
    @pytest.mark.asyncio
    async def test_returns_none_when_vision_off(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")

        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header bytes

        with patch("openai.AsyncOpenAI") as mock_client_cls:
            from cores.llm.features.vision import analyze_image
            result = await analyze_image(str(img), "describe this chart")

        assert result is None
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_encoding_when_vision_off(self, monkeypatch, tmp_path):
        """When vision is off, image bytes must never be read/encoded."""
        monkeypatch.delenv("PRISM_FEATURE_VISION", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")

        # Point to a non-existent file — would fail if read was attempted
        with patch("openai.AsyncOpenAI") as mock_client_cls:
            from cores.llm.features.vision import analyze_image
            result = await analyze_image("/nonexistent/path/chart.png", "describe")

        assert result is None
        mock_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# analyze_image — subscription authentication without API keys
# ---------------------------------------------------------------------------


class TestSubscriptionVision:
    @pytest.fixture(autouse=True)
    def subscription_only(self, monkeypatch):
        monkeypatch.setenv("PRISM_FEATURE_VISION", "on")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("openai.AsyncOpenAI", side_effect=AssertionError("API fallback forbidden")):
            yield

    @pytest.mark.asyncio
    async def test_no_key_uses_subscription_and_returns_text(self, monkeypatch, tmp_path):
        img = tmp_path / "chart.png"
        img.write_bytes(b"PNG")
        mock = AsyncMock(return_value="bullish cup-and-handle detected")
        monkeypatch.setattr("prism_core.codex_subscription.run_stage", mock)
        from cores.llm.features.vision import analyze_image
        assert await analyze_image(img, "describe the chart pattern") == "bullish cup-and-handle detected"
        mock.assert_awaited_once()
        assert mock.call_args.args[0] == "vision"
        assert mock.call_args.args[2] == "describe the chart pattern"
        assert mock.call_args.kwargs["images"] == [img]

    @pytest.mark.asyncio
    async def test_structured_output_parsed_from_json(self, monkeypatch):
        mock = AsyncMock(return_value=json.dumps({"label": "cup-handle", "confidence": 87}))
        monkeypatch.setattr("prism_core.codex_subscription.run_stage", mock)
        from cores.llm.features.vision import analyze_image
        result = await analyze_image(b"PNG", "classify the base", schema=SimpleSchema)
        assert isinstance(result, SimpleSchema)
        assert result.label == "cup-handle" and result.confidence == 87
        assert mock.call_args.kwargs["response_model"] is SimpleSchema

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", [TimeoutError("timeout"), RuntimeError("quota exceeded")])
    async def test_subscription_error_returns_none_and_logs(self, monkeypatch, caplog, error):
        import logging
        monkeypatch.setattr("prism_core.codex_subscription.run_stage", AsyncMock(side_effect=error))
        from cores.llm.features.vision import analyze_image
        with caplog.at_level(logging.WARNING, logger="cores.llm.features.vision"):
            assert await analyze_image(b"PNG", "analyse") is None
        assert any("[VISION_ERROR]" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("images", [b"PNG", [b"DAILY", b"WEEKLY"]])
    async def test_image_order_single_call_and_temp_cleanup(self, monkeypatch, images):
        from pathlib import Path
        seen = []
        async def run(stage, instruction, prompt, **kwargs):
            paths = kwargs["images"]
            seen.extend(paths)
            assert [p.read_bytes() for p in paths] == (images if isinstance(images, list) else [images])
            return "image analysis"
        mock = AsyncMock(side_effect=run)
        monkeypatch.setattr("prism_core.codex_subscription.run_stage", mock)
        from cores.llm.features.vision import analyze_image
        assert await analyze_image(images, "daily then weekly") == "image analysis"
        mock.assert_awaited_once()
        assert all(not p.exists() for p in seen)

    @pytest.mark.asyncio
    async def test_invalid_structured_answer_returns_none(self, monkeypatch):
        monkeypatch.setattr("prism_core.codex_subscription.run_stage", AsyncMock(return_value='{"label": "missing confidence"}'))
        from cores.llm.features.vision import analyze_image
        assert await analyze_image(b"PNG", "analyse", schema=SimpleSchema) is None
