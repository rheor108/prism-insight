"""Read-only KIS balance and cumulative buy-fill snapshots for KR/US.

Field contracts follow koreainvestment/open-trading-api examples_llm:
domestic_stock/{inquire_daily_ccld,order_resv_ccnl} and
overseas_stock/inquire_ccnl. Order acknowledgements/quotes are never fills.
"""
from datetime import datetime, timezone
import time

from prism_core.entry_costs import number, order_number


def _pages(fetch, path, tr_id, params, output, width):
    data, seen = [], set()
    params = dict(params)
    continuation = ''
    for _ in range(100):
        response = fetch(path, tr_id, continuation, params)
        if not response.isOK():
            raise ValueError('KIS entry-cost inquiry failed')
        body = response.getBody()
        page = getattr(body, output, None)
        if not isinstance(page, list) or not all(isinstance(r, dict) for r in page):
            raise ValueError('Malformed KIS inquiry page')
        data.extend(page)
        if str(getattr(response.getHeader(), 'tr_cont', '')).strip().upper() not in ('M', 'F'):
            return data
        fk = getattr(body, f'ctx_area_fk{width}', None)
        nk = getattr(body, f'ctx_area_nk{width}', None)
        if not isinstance(fk, str) or not isinstance(nk, str) or (fk, nk) in seen or not (fk.strip() or nk.strip()):
            raise ValueError('Incomplete or repeated KIS pagination cursor')
        seen.add((fk, nk))
        params[f'CTX_AREA_FK{width}'] = fk
        params[f'CTX_AREA_NK{width}'] = nk
        continuation = 'N'
        time.sleep(0.1)
    raise ValueError('KIS pagination limit reached')


def _balances(raw, market):
    found = {}
    for row in raw:
        ticker = str(row.get('pdno' if market == 'KR' else 'ovrs_pdno', '')).strip()
        qty = number(row.get('hldg_qty' if market == 'KR' else 'ovrs_cblc_qty'))
        if qty == 0:
            continue
        if not ticker:
            raise ValueError('Broker balance missing symbol')
        price = number(row.get('pchs_avg_pric'), positive=True)
        value = {'ticker': ticker, 'quantity': float(qty), 'avg_price': float(price)}
        # Overseas balance responses can repeat the same symbol across exchanges.
        if ticker in found and found[ticker] != value:
            raise ValueError('Conflicting duplicate broker balance')
        found[ticker] = value
    return list(found.values())


def _fills(raw, market):
    found = {}
    for row in raw:
        if row.get('sll_buy_dvsn_cd') != '02':
            continue
        ticker, order_no, day = row.get('pdno'), order_number(row.get('odno')), row.get('ord_dt')
        if not ticker or not order_no:
            raise ValueError('Buy fill missing order identity')
        datetime.strptime(day, '%Y%m%d')
        qty = number(row.get('tot_ccld_qty' if market == 'KR' else 'ft_ccld_qty'))
        remaining = number(row.get('rmn_qty' if market == 'KR' else 'nccs_qty'))
        amount = number(row.get('tot_ccld_amt' if market == 'KR' else 'ft_ccld_amt3'))
        if (qty == 0) != (amount == 0):
            raise ValueError('Fill quantity and amount disagree')
        value = {'ticker': ticker, 'order_no': order_no, 'order_date': day,
                 'filled_quantity': float(qty), 'filled_amount': float(amount),
                 'remaining_quantity': float(remaining)}
        key = (day, order_no)
        if key in found and found[key] != value:
            raise ValueError('Conflicting cumulative order rows')
        found[key] = value
    return list(found.values())


def read_entry_cost_snapshot(fetch, *, market, account_key, account_no, product, mode, start, end):
    """fetch must be the caller's account-activated GET wrapper; never submits orders."""
    for day in (start, end):
        datetime.strptime(day, '%Y%m%d')
    common = {'CANO': account_no, 'ACNT_PRDT_CD': product}
    if market == 'KR':
        raw = _pages(fetch, '/uapi/domestic-stock/v1/trading/inquire-balance',
                     'TTTC8434R' if mode == 'real' else 'VTTC8434R',
                     {**common, 'AFHR_FLPR_YN':'N','OFL_YN':'','INQR_DVSN':'02','UNPR_DVSN':'01',
                      'FUND_STTL_ICLD_YN':'N','FNCG_AMT_AUTO_RDPT_YN':'N','PRCS_DVSN':'00',
                      'CTX_AREA_FK100':'','CTX_AREA_NK100':''}, 'output1', 100)
    elif market == 'US':
        raw = []
        for exchange in ('NASD','NYSE','AMEX'):
            raw.extend(_pages(fetch, '/uapi/overseas-stock/v1/trading/inquire-balance',
                       'TTTS3012R' if mode == 'real' else 'VTTS3012R',
                       {**common,'OVRS_EXCG_CD':exchange,'TR_CRCY_CD':'USD',
                        'CTX_AREA_FK200':'','CTX_AREA_NK200':''}, 'output1', 200))
            time.sleep(0.1)
    else:
        raise ValueError('Unsupported market')
    result = {'market': market, 'account_key': account_key,
              'checked_at': datetime.now(timezone.utc).isoformat(),
              'balance_authoritative': True, 'holdings': _balances(raw, market),
              'orders_authoritative': False, 'orders': []}
    try:
        if market == 'KR':
            raw = _pages(fetch, '/uapi/domestic-stock/v1/trading/inquire-daily-ccld',
                         'TTTC0081R' if mode == 'real' else 'VTTC0081R',
                         {**common,'INQR_STRT_DT':start,'INQR_END_DT':end,'SLL_BUY_DVSN_CD':'02',
                          'PDNO':'','CCLD_DVSN':'00','INQR_DVSN':'00','INQR_DVSN_3':'00',
                          'ORD_GNO_BRNO':'','ODNO':'','INQR_DVSN_1':'',
                          'CTX_AREA_FK100':'','CTX_AREA_NK100':''}, 'output1', 100)
        else:
            raw = []
            for exchange in ('NASD','NYSE','AMEX'):
                raw.extend(_pages(fetch, '/uapi/overseas-stock/v1/trading/inquire-ccnl',
                           'TTTS3035R' if mode == 'real' else 'VTTS3035R',
                           {**common,'PDNO':'%' if mode == 'real' else '',
                            'ORD_STRT_DT':start,'ORD_END_DT':end,'SLL_BUY_DVSN':'02',
                            'CCLD_NCCS_DVSN':'00','OVRS_EXCG_CD':exchange,'SORT_SQN':'DS',
                            'ORD_DT':'','ORD_GNO_BRNO':'','ODNO':'',
                            'CTX_AREA_FK200':'','CTX_AREA_NK200':''}, 'output', 200))
                time.sleep(0.1)
        result['orders'] = _fills(raw, market)
        result['orders_authoritative'] = True
    except (ValueError, AttributeError, TypeError):
        # A valid balance is still useful for an unlinked single legacy lot.
        return result
    if market == 'KR' and mode == 'real':
        try:
            reservations = _pages(fetch, '/uapi/domestic-stock/v1/trading/order-resv-ccnl',
                'CTSC0004R', {**common,'RSVN_ORD_ORD_DT':start,'RSVN_ORD_END_DT':end,
                 'TMNL_MDIA_KIND_CD':'00','PRCS_DVSN_CD':'0','CNCL_YN':'Y',
                 'RSVN_ORD_SEQ':'','PDNO':'','SLL_BUY_DVSN_CD':'02',
                 'CTX_AREA_FK200':'','CTX_AREA_NK200':''}, 'output', 200)
            for reservation in reservations:
                matches = [o for o in result['orders'] if o['ticker'] == reservation.get('pdno')
                           and o['order_no'] == order_number(reservation.get('odno'))]
                if len(matches) == 1:
                    # Reservation namespace keeps its own creation date.
                    match = dict(matches[0], reservation_no=order_number(reservation.get('rsvn_ord_seq')),
                                 reservation_date=reservation['rsvn_ord_ord_dt'])
                    result['orders'].append(match)
        except (ValueError, AttributeError, TypeError, KeyError):
            pass  # Unmatched reservations stay unconfirmed; never guess by price.
    if market == 'US' and mode == 'real':
        try:
            reservations = []
            for exchange in ('NASD', 'NYSE', 'AMEX'):
                reservations.extend(_pages(fetch, '/uapi/overseas-stock/v1/trading/order-resv-list',
                    'TTTT3039R', {**common,'INQR_STRT_DT':start,'INQR_END_DT':end,
                    'INQR_DVSN_CD':'00','OVRS_EXCG_CD':exchange,'PRDT_TYPE_CD':'512',
                    'CTX_AREA_FK200':'','CTX_AREA_NK200':''}, 'output', 200))
                time.sleep(0.1)
            aliases = {}
            for reservation in reservations:
                if reservation.get('sll_buy_dvsn_cd') != '02':
                    continue
                matches = [o for o in result['orders'] if o['ticker'] == reservation.get('pdno')
                           and o['order_no'] == order_number(reservation.get('odno'))
                           and o['order_date'] == reservation.get('ord_dt')]
                if len(matches) == 1:
                    match = dict(matches[0], reservation_no=order_number(reservation.get('ovrs_rsvn_odno')),
                                 reservation_date=reservation['rsvn_ord_rcit_dt'])
                    key = (match['reservation_no'], match['reservation_date'])
                    if key in aliases and aliases[key] != match:
                        raise ValueError('Conflicting US reservation mapping')
                    aliases[key] = match
            result['orders'].extend(aliases.values())
        except (ValueError, AttributeError, TypeError, KeyError):
            pass
    return result
