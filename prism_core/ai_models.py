"""Validated, subscription-only model choices. No SDK imports or credentials."""
from dataclasses import dataclass
import json
import os
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / 'config/ai_models.json'
MODELS = {'gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna',
          'gpt-5.5', 'gpt-5.4-mini', 'gpt-5.3-codex-spark',
          'claude-sonnet-5', 'claude-opus-5'}
EFFORTS = {'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}

@dataclass(frozen=True)
class Stage:
    key: str
    number: int
    label: str
    enabled: bool
    model: str | None
    effort: str | None
    timeout_seconds: int
    max_tool_rounds: int

    @property
    def provider(self):
        return "claude_subscription" if (self.model or "").startswith("claude-") else "codex_subscription"


def settings(key: str, *, variant: str | None = None, path=None) -> Stage:
    data = json.loads(Path(path or os.getenv('PRISM_AI_CONFIG') or CONFIG).read_text())
    if data.get('provider') != 'codex_subscription':
        raise ValueError('This fork requires subscription authentication; API fallback is disabled')
    raw = dict(data['stages'][key])
    if variant:
        raw.update(raw[variant])
    if type(raw['enabled']) is not bool:
        raise ValueError('enabled must be a boolean')
    if raw['enabled']:
        if raw['model'] not in MODELS or raw['effort'] not in EFFORTS:
            raise ValueError(f'Unsupported model/effort for {key}')
        if raw['effort'] == 'ultra' and raw['model'] not in {'gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra'}:
            raise ValueError(f'Unsupported ultra effort for {raw["model"]}')
        if raw['effort'] == 'max' and raw['model'] in {'gpt-5.5','gpt-5.4-mini','gpt-5.3-codex-spark'}:
            raise ValueError(f'Unsupported max effort for {raw["model"]}')
    if key == 'embedding' and raw['enabled']:
        raise ValueError('Codex text models cannot replace an embedding endpoint')
    for field, minimum, maximum in [('timeout_seconds',10,2400),('max_tool_rounds',1,40)]:
        if type(raw[field]) is not int or not minimum <= raw[field] <= maximum:
            raise ValueError(f'Invalid {field} for {key}')
    return Stage(key=key, **{f:raw[f] for f in Stage.__dataclass_fields__ if f != 'key'})


def request_params(stage: str, **kwargs):
    from mcp_agent.workflows.llm.augmented_llm import RequestParams
    choice = settings(stage)
    kwargs.update(model=choice.model, reasoning_effort=choice.effort)
    return RequestParams(**kwargs)

REPORT_STAGES = {
    'price_volume_analysis':'price_volume', 'investor_trading_analysis':'holdings_flow',
    'institutional_holdings_analysis':'holdings_flow', 'company_status':'financials',
    'company_overview':'company', 'news_analysis':'news', 'market_index_analysis':'market',
}
