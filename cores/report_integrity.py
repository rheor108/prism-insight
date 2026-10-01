"""Reject known incomplete artifacts without imposing a trading strategy gate."""
import re


class IncompleteReportError(ValueError):
    def __init__(self, reasons):
        self.reasons = dict(reasons)
        super().__init__('REPORT_INCOMPLETE: ' + ','.join(reasons) +
                         ' (' + ','.join(f'{k}={v}' for k, v in reasons.items()) + ')')


def validate_sections(sections, required):
    invalid = {}
    for name in required:
        text = str(sections.get(name) or '').strip()
        body = re.sub(r'^\s*#{1,6}[^\n]*$', '', text, flags=re.M).strip()
        if not body:
            invalid[name] = 'empty_body'
        elif body.casefold() in {'테스트', 'test', 'testing', 'todo', 'placeholder'}:
            invalid[name] = 'placeholder'
        elif re.search(r'REPORT_DATA_UNAVAILABLE', text, re.I):
            invalid[name] = 'source_unavailable'
        elif re.search(r'Analysis failed:|Investment strategy analysis failed', text, re.I):
            invalid[name] = 'upstream_failure'
    if invalid:
        raise IncompleteReportError(invalid)


def source_contract(company_name, company_code, reference_date):
    return (f'\nAuthoritative target identity: {company_name} ({company_code}). '
            'Never infer a different company from a ticker or replace this identity. '
            'If sources refer to another company, exclude those sources and explicitly state the conflict. '
            f'Analysis reference date: {reference_date}. '
            'Distinguish last completed-session close from timestamped intraday quotes. '
            'Do not label intraday prices as finalized closes. Do not infer falling volume '
            'by comparing a partial session with a full-day average; use matched elapsed-time '
            'data or state the comparison is unavailable. Keep numerical indicators tied to '
            'the source timestamp and calculation basis.\n')
