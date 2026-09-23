import asyncio
import sys
import types
from pathlib import Path

sys.modules.setdefault("markdown", types.SimpleNamespace(markdown=lambda text: text))

import report_generator
from cores.agents.report_agent import ReportAgent
from prism_core import codex_subscription
import pytest
from unittest.mock import AsyncMock


@pytest.mark.parametrize("name,stage", [
    ("evaluation_agent", "consultation"), ("us_evaluation_agent", "consultation"),
    ("evaluation_fallback_agent", "consultation"), ("followup_agent", "followup"),
    ("us_followup_agent", "followup"), ("journal_conversation_agent", "journal_chat"),
    ("firecrawl_search_analyst", "search_analysis"), ("firecrawl_followup_agent", "search_analysis"),
])
def test_generate_telegram_text_preserves_runtime_contract(monkeypatch, name, stage):
    mock = AsyncMock(return_value="backend-result")
    monkeypatch.setattr(codex_subscription, "run_with_registry", mock)
    agent = ReportAgent(name=name, instruction="contract instructions", server_names=("perplexity", "time"))
    result = asyncio.run(report_generator._generate_telegram_text(
        agent=agent, message="contract message", max_tokens=4321))
    assert result == "backend-result"
    mock.assert_awaited_once_with(stage, agent.instruction, "contract message", agent.server_names)


def test_consultation_timeout_is_preserved(monkeypatch):
    cancelled = []
    async def never(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    monkeypatch.setattr(codex_subscription, "run_with_registry", never)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(report_generator._generate_telegram_text(
            agent=ReportAgent("evaluation_agent", "prompt"), message="q", max_tokens=100,
            timeout_seconds=0.01))
    assert cancelled == [True]


def test_telegram_runtime_sources_are_mcp_agent_free():
    root = Path(__file__).resolve().parents[1]
    for relative in ("report_generator.py", "telegram_ai_bot.py"):
        source = (root / relative).read_text()
        assert "mcp_agent" not in source
        assert "MCPApp" not in source


def test_clean_model_response_removes_telegram_markdown_artifacts():
    result = report_generator.clean_model_response(
        """# 📌 결론: **매수**

        - 왜 이렇게 보냐면
        - 추세가 살아 있습니다.

        [차트 보기](https://example.com/chart)
        """
    )

    assert "#" not in result
    assert "**" not in result
    assert "- 왜" not in result
    assert "📌 결론: 매수" in result
    assert "차트 보기 (https://example.com/chart)" in result
