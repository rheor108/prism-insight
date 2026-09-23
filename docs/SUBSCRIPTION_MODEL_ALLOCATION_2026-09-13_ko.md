# 단계별 구독 모델 적용 — 2026-09-13

사용자 승인한 혼합 모델 제안을 적용합니다. 운영 cron 및 품질점검 자동화는 중지 상태를 유지합니다.

- GPT: 기존 Codex ChatGPT 구독 경로를 사용합니다.
- Claude: claude.ai 구독 인증을 확인한 뒤 CLI를 실행합니다. API 인증과 다른 주 모델로의 대체를 거부합니다.
- Claude 내장 도구는 비활성화하고, 기존 부모 프로세스의 읽기 도구 허용 목록과 순차 실행을 공유합니다.
- Claude 외부 조사는 기존 research 단계의 GPT 웹 검색 경로를 사용합니다.
- Claude 호출 시간과 토큰은 logs/codex_calls.jsonl에 provider와 함께 기록합니다. 구독 사용률은 unavailable이며 0%가 아닙니다. Claude input_tokens에는 캐시 읽기/생성이 포함되지 않으므로 GPT 수치와 단순 합산하지 않습니다.
- 독립 품질점검은 기존 Claude Opus 5 / max를 유지합니다.
- 주가·거래량 high를 유지합니다. 지표 계산의 코드 이관이나 판단 규칙 변경은 포함하지 않습니다.

| 단계 | 역할 | 모델 | effort |
|---|---|---|---|
| 1 / macro | 거시경제·시장 이슈 | gpt-5.6-sol | medium |
| 2 / price_volume | 주가·거래량 | gpt-5.6-sol | high |
| 3 / holdings_flow | 수급·기관 보유 | gpt-5.6-sol | medium |
| 4 / financials | 재무·실적·밸류에이션 | gpt-5.6-sol | high |
| 5 / company | 기업 개요·경쟁력 | claude-sonnet-5 | medium |
| 6 / news | 뉴스·이벤트 | claude-sonnet-5 | medium |
| 7 / market | 시장지수 | gpt-5.6-terra | medium |
| 8 / strategy | 종합 투자 전략 | gpt-6-astra | medium |
| 9 / report_summary | 보고서 요약 | gpt-5.6-terra | medium |
| 10 / kr_buy | 국내 매수 | gpt-6-astra | medium |
| 11 / us_buy | 미국 매수 | gpt-6-astra | medium |
| 12 / kr_sell | 국내 보유·매도 | gpt-6-astra | medium |
| 13 / us_sell | 미국 보유·매도 | gpt-6-astra | medium |
| 14 / telegram_summary | 알림 요약 | gpt-5.6-terra | low |
| 15 / telegram_evaluator | 알림 품질 평가 | claude-sonnet-5 | medium |
| 16 / journal | 매매 회고 | claude-opus-5 | high |
| 17 / memory_summary | 회고 압축 | gpt-5.6-terra | medium |
| 18 / memory_intuition | 장기 기억·직관 | claude-opus-5 | high |
| 19 / intuition_refresh | 직관 갱신 | claude-opus-5 | medium |
| 20 / translation | 다국어 번역 | gpt-5.6-luna | low |
| 21 / company_translation | 기업명 번역 | gpt-5.6-luna | low |
| 22 / consultation | 보유 진단·상담 | claude-sonnet-5 | medium |
| 23 / followup | 분석 후속 질문 | gpt-5.6-terra | medium |
| 24 / journal_chat | 회고 대화 | claude-sonnet-5 | medium |
| 25 / search_analysis | 검색 결과 분석 | gpt-5.6-terra | medium |
| 26 / moderation | 메시지 관리 | gpt-5.6-luna | low |
| 27 / archive_query | 아카이브 질의 | gpt-5.6-sol | medium |
| 28 / archive_insight | 아카이브 인사이트 | claude-opus-5 | high |
| 29 / vision | 차트 이미지 해석 | gpt-5.6-sol | medium |
| 30 / render_qa | 차트 시각 검사 | gpt-5.6-terra | low |
| 31 / embedding | 의미 검색 임베딩 | 비활성화 | — |
| 32 / research | 외부 자료 조사 | gpt-5.6-sol | medium |
| 33 / video | 영상 전사문 분석 | claude-sonnet-5 | medium |

영상 사전 필터: GPT-5.6 Luna / low.

검증: 구독 라우팅·인증 거부·도구 중계·응답 스키마·시간초과 정리·기존 GPT 메트릭 및 이미지 경로 테스트 95개 통과. 실매매 및 텔레그램 전송을 실행하지 않았습니다.
