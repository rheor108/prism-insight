"""One report-pipeline model contract shared by KR and US entry points."""

from __future__ import annotations

import re
from prism_core.ai_models import settings


REPORT_MODEL = settings("price_volume").model
REPORT_EFFORT = settings("price_volume").effort
REPORT_AUX_MODEL = settings("telegram_summary").model
REPORT_AUX_EFFORT = settings("telegram_summary").effort


def report_model_slug(model: str | None = None) -> str:
    """Return a stable filename-safe slug that reflects the actual model."""
    value = str(model or REPORT_MODEL).strip().lower()
    value = re.sub(r"[^a-z0-9.]+", "-", value).strip("-")
    return value or "unknown-model"


__all__ = [
    "REPORT_AUX_EFFORT",
    "REPORT_AUX_MODEL",
    "REPORT_EFFORT",
    "REPORT_MODEL",
    "report_model_slug",
]
