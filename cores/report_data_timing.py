"""KR report-only date labels; never changes source OHLCV or trading calculations."""
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def now_kst():
    return datetime.now(KST)


def row_status(value, retrieved_at):
    """A wall clock alone cannot certify today's closing auction/source finality."""
    if retrieved_at.tzinfo is None:
        raise ValueError('retrieved_at must be timezone-aware')
    try:
        text = str(value)
        row_date = datetime.strptime(text, '%Y%m%d' if len(text) == 8 else '%Y-%m-%d').date()
    except ValueError:
        return 'date_unknown'
    today = retrieved_at.astimezone(KST).date()
    if row_date < today:
        return 'historical_daily'
    if row_date == today:
        return 'same_day_unconfirmed'
    return 'future_date_unverified'


def timing_note(retrieved_at):
    stamp = retrieved_at.astimezone(KST).isoformat(timespec='seconds')
    return (
        f'> **수집 시각 / retrieved_at (KST):** {stamp} (원천 시세 시각/source timestamp 아님)\n'
        '> **일봉 상태 / Daily bar status:** historical_daily=과거 일봉(공급자 제공, 정정 가능); '
        'same_day_unconfirmed=당일 잠정(장중/마감 확정 미확인); '
        'future_date_unverified/date_unknown=날짜 검증 불가.\n'
        '> Close/종가 열 이름은 당일 확정 종가의 증거가 아닙니다. '
        '당일 Volume/거래량은 종일 확정치로 간주하지 마십시오. '
        '수집 시각만으로 마감 확정을 추정하지 마십시오. '
        'Same-day Close/Volume are provisional; retrieval time does not prove finality.\n\n'
    )


def report_timing_rules(language='ko'):
    stamp = now_kst().isoformat(timespec='seconds')
    if language == 'en':
        return f'''
## Price/volume timing rules (report creation: {stamp})
- State the date, available source timestamp and provisional/final status for price and volume. Report creation/retrieval time is not the source quote time.
- same_day_unconfirmed is not a confirmed closing price or full-day volume. A Close column, last row, requested reference date, or clock after market close does not establish finality. If tool output lacks status, treat same-day rows as unconfirmed; future/unknown dates are unverified. Historical daily rows may be used for historical daily comparisons.
- Label same-day unconfirmed values as intraday/provisional observations, with finality unknown, never as confirmed closes. Do not guess a source timestamp.
- Never infer daily volume contraction or weakening momentum by comparing partial-day volume with a previous full day or a full-day average. A like-for-like comparison requires matching elapsed-session windows with known timestamps. Otherwise state that the daily comparison is unavailable; do not linearly extrapolate missing volume.
- If indicators include an unconfirmed row, label them provisional and identify the last row/date used. If confirmed-only indicators are also shown, distinguish their input dates. Do not silently mix prices, volumes or indicators from different snapshots.
- Preserve these qualifiers in tables and conclusions. This is a reporting rule, not an additional entry/exit gate.
'''
    return f'''
## 가격·거래량 시점 표시 규칙 (보고서 작성 시각: {stamp})
- 가격·거래량의 기준일, 확인 가능한 원천 시각, 잠정/확정 상태를 명시합니다. 작성·수집 시각은 원천 시세 시각이 아닙니다.
- same_day_unconfirmed는 확정 종가·종일 거래량이 아닙니다. Close/종가라는 열 이름, 마지막 행, 요청 기준일, 마감 이후라는 시각만으로 확정을 추정하지 않습니다. 도구 응답에 상태가 없으면 당일 행은 확정 미확인, 미래·불명 날짜는 검증 불가로 취급합니다. 과거 일봉끼리의 일간 비교는 가능합니다.
- 당일 미확정 값은 '장중/잠정 가격·누적 거래량(마감 확정 미확인)'으로 표시합니다. '당일 확정 종가'로 단정하거나 원천 시각을 만들어내지 않습니다.
- 장중 누적 거래량을 전일 종일 거래량·과거 일평균과 비교해 '일간 거래량 감소'나 '상승 탄력 약화'를 단정하지 않습니다. 동일 경과시간 구간과 원천 시각이 확인된 경우에만 같은 기준으로 비교하며, 없으면 일간 증감 판단을 유보합니다. 단순 시간 비례로 종일 거래량을 추정하지 않습니다.
- 미확정 행을 포함한 RSI·이동평균·MACD·볼린저 등은 '잠정 지표'로 표시하고 마지막 입력 기준일을 명시합니다. 확정 일봉만 사용한 지표를 함께 제시한다면 입력 기준일을 구분합니다. 서로 다른 시점의 가격·거래량·지표를 섞지 않습니다.
- 표·핵심 요약·결론에서도 위 한정 표현을 유지합니다. 이 규칙은 보고서 표시 규칙이며 추가 매수·매도 차단 조건이 아닙니다.
'''
