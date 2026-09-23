from pathlib import Path
import yaml
import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("path,key", [("cores/llm/mcp_servers.yaml","servers"), ("mcp_agent.config.yaml.example","mcp")])
def test_scraper_is_local_and_has_no_paid_credentials(path,key):
    config = yaml.safe_load((ROOT / path).read_text())
    servers = config["servers"] if key == "servers" else config["mcp"]["servers"]
    scraper = servers["firecrawl"]
    assert scraper["args"] == ["-m", "prism_core.scraper_bootstrap"]
    assert "FIRECRAWL_API_KEY" not in scraper.get("env", {})
    assert scraper["read_timeout_seconds"] >= 80
