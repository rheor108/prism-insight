from pathlib import Path
from unittest.mock import Mock

import pytest

from cores.agents.report_agent import ReportAgent
from prism_core import codex_subscription
import cores.report_generation as report_generation


@pytest.mark.asyncio
async def test_generate_agent_text_maps_report_contract(monkeypatch):
    calls = []
    async def run(stage, instruction, message, server_names, **kwargs):
        calls.append((stage, instruction, message, server_names))
        return "generated report"
    monkeypatch.setattr(codex_subscription, "run_with_registry", run)
    agent = ReportAgent(name="news_analysis_agent", instruction="Analyze verified news only.",
                        server_names=["perplexity", "firecrawl"])
    assert await report_generation._generate_agent_text(
        agent, "user prompt", stage="news", max_tokens=32000, max_iterations=3
    ) == "generated report"
    assert calls == [("news", agent.instruction, "user prompt", agent.server_names)]


@pytest.mark.asyncio
async def test_four_report_paths_preserve_stages_and_prompts(monkeypatch):
    calls = []
    async def run(stage, instruction, message, server_names, **kwargs):
        calls.append((stage, instruction, message, server_names))
        return "generated report"
    monkeypatch.setattr(codex_subscription, "run_with_registry", run)
    logger = Mock()
    agent = ReportAgent("section_agent", "section instructions")
    assert await report_generation.generate_report(
        agent, "company_status", "SK하이닉스", "000660", "20260718", logger
    ) == "generated report"
    assert await report_generation.generate_market_report(
        agent, "market_index_analysis", "20260718", logger
    ) == "generated report"
    assert await report_generation.generate_summary(
        {"company_status": "status report"}, "SK하이닉스", "000660", "20260718", logger
    ) == "generated report"
    assert await report_generation.generate_investment_strategy(
        {"company_status": "status report"}, "combined report", "SK하이닉스", "000660", "20260718", logger
    ) == "generated report"
    assert [c[0] for c in calls] == ["financials", "market", "report_summary", "strategy"]
    assert "SK하이닉스(000660)" in calls[0][2]
    assert "시장과 거시환경" in calls[1][2]
    assert "status report" in calls[2][2]
    assert "combined report" in calls[3][2]


def test_kr_report_pipeline_has_no_mcp_agent_runtime_imports():
    project_root = Path(__file__).parent.parent
    paths = [
        "cores/analysis.py",
        "cores/report_generation.py",
        "cores/agents/report_agent.py",
        "cores/agents/stock_price_agents.py",
        "cores/agents/company_info_agents.py",
        "cores/agents/news_strategy_agents.py",
        "cores/agents/market_index_agents.py",
    ]

    for relative_path in paths:
        source = (project_root / relative_path).read_text(encoding="utf-8")
        assert "mcp_agent" not in source, relative_path
        assert "MCPApp" not in source, relative_path
