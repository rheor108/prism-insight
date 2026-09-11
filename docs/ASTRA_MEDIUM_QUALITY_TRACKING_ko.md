# Astra medium 판단 품질 추적

2026-09-11 사용자 요청으로 strategy, kr_buy, us_buy, kr_sell, us_sell의
Astra effort만 xhigh → medium으로 변경합니다. Sol 재무 분석 xhigh와 나머지
모델, 프롬프트, 진입·청산·예산 규칙은 그대로 유지합니다.

목표는 추론 비용과 시간을 줄이되 판단의 일관성과 제약 준수 저하 여부를 관찰하는 것입니다.
변경 전 xhigh 호출 기록은 저장소 바깥 migration/quality_tracking/astra_medium_baseline.json에
보관했습니다. 실행 중 이미 만들어진 모델 선택은 xhigh일 수 있으므로 배치 시작 시각보다
실제 metrics의 stage/model/effort로 구분합니다.

## 관찰 계약

- logs/codex_calls.jsonl에서 호출 ID로 attempt와 call을 연결합니다. 단계·시장·effort별
  표본 수, 성공/실패/재시도, 실행 시간 중앙값, 입력·캐시·출력 토큰을 비교합니다.
  계정 사용률 %p는 공유 지표이므로 개별 모델의 비용으로 합산하지 않습니다.
- 배치 로그의 최종 시나리오, 보고서, 당시 보유/계좌 제약을 읽기 전용으로 대조합니다.
  판단 누락, 근거·결론 모순, 보유·예산·손절 제약 위반, 존재하지 않는 자료를 근거로 한
  판단을 증거 행/파일과 함께 기록합니다. 확인할 자료가 없으면 '미확인'으로 둡니다.
- 실제 체결은 증권사 체결 확인 데이터로만 판정합니다. 시뮬레이터의 Bought/Sold나
  AI의 entry/exit 제안만으로 체결 또는 실현 수익을 확정하지 않습니다.
- KRX 자료 누락, CLI 통신 실패, 시장·종목 차이는 별도 원인으로 분리합니다.
  이번 KRX 수정도 같은 날 적용됐으므로 전후 비교만으로 effort의 인과 효과를 주장하지 않습니다.
- 건별 관찰은 migration/quality_tracking 아래에 호출/배치/종목 식별자와 근거를 기록하고,
  이미 검토한 항목은 중복 집계하지 않습니다. 개인 데이터나 원본 로그를 Git에 올리지 않습니다.
- 최소 5거래일 및 시장별 최종 판단 10건을 확보한 뒤 중간 평가합니다.
  제약 위반·심각한 모순은 표본 수에 관계없이 즉시 보고합니다. 표본이 부족하면 결론을 유보합니다.
- 자동 모델 상향, 전략 수정, 주문 재실행은 하지 않습니다. 외부 전송은 아래 승인된 텔레그램 품질 알림으로 한정합니다.
  문제가 확인되면 해당 단계만 high로 올리는 안을 증거와 함께 사용자에게 제안합니다.

이는 운영 관찰이며 같은 입력에 대한 xhigh/medium 대조 실험이나 수익성 검증은 아닙니다.
추적은 현재 Codex 작업의 heartbeat로 6시간마다 새 결과를 확인하며, 변화가 없으면 알리지 않습니다.

## 텔레그램 알림 (2026-09-11 사용자 승인)

중간 평가 또는 심각한 모순·제약 위반 등 보고할 새 내용이 생기면 기존 .env의
TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID 수신처로 한국어 요약도 전송합니다.
새 결과·의미 있는 변화가 없으면 전송하지 않습니다. 원문 계좌정보나 비밀값은 포함하지 않습니다.

검토 결과를 migration/quality_tracking 아래 UTF-8 텍스트 파일로 먼저 저장하고,
운영 루트에서 `.venv/bin/python tools/send_quality_notification.py --event-id <안정적인 사건ID> --message-file <파일경로>`를 실행합니다.
같은 사건에는 같은 ID와 같은 본문을 사용합니다. 전송 상태는 logs/quality_notifications.sqlite에
기록합니다. sent만 성공이며 rejected/uncertain/pending은 자동 재전송하지 말고 Codex 작업에
보고합니다. duplicate_suppressed의 previous_status도 확인합니다.
전송 성공 후 실제 message_id와 사건 ID를 검토 기록에 남깁니다. Telegram API가 성공을 반환해도
사용자의 기기에서 푸시 알림이 표시됐거나 읽었다는 뜻은 아닙니다.
