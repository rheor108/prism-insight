# Codex 구독 전용 AI 단계 설정

2026-09-09: 승인된 33단계 모델 배정을 새 fork에 반영했습니다.
주식 KR/US 경로가 대상이며 별도 BTC 프로젝트는 이 구성에 포함하지 않습니다.

운영 연결 검사에서 CLI 0.152.1은 Astra에 필요한 버전보다 낮아 0.153.4로
업데이트했습니다. `gpt-5.4-mini`는 현재 ChatGPT 계정에서 지원되지 않아
메시지 관리 단계만 실제 연결이 확인된 Luna/low로 변경했습니다.

## 모델 선택

`config/ai_models.json`이 실제 호출의 모델·추론 수준을 결정합니다. 각 단계의
`model`, `effort`를 수정하면 다음 AI 호출부터 읽습니다. 별도 파일을 쓰려면
`PRISM_AI_CONFIG`에 절대 경로를 지정합니다. 오래 실행된 객체의 표시용 모델명과
보고서 파일명은 시작 시 읽은 값일 수 있으므로 설정 변경 후 해당 프로세스도 재시작하십시오.
보고서 파일명의 Sol 표기는 기본 분석 모델이며 전략·요약까지 같은 모델이라는 뜻은 아닙니다.

구형 `REPORT_MODEL`, `REPORT_AUX_MODEL`, `TELEGRAM_ANALYSIS_MODEL` 및 개별 호출의
`RequestParams.model`은 이 프로필에서 모델 선택을 덮어쓰지 않습니다.
Codex Fast 매매 분기도 별도 모델을 선택하지 않습니다. 대시보드에 설정 UI를 추가한 것은 아닙니다.

| 번호 | 단계 키 | 기능 | 모델 | 추론 수준 |
|---|---|---|---|---|
| 1 | `macro` | 거시경제·시장 이슈 | gpt-5.6-sol | high |
| 2 | `price_volume` | 주가·거래량 | gpt-5.6-sol | high |
| 3 | `holdings_flow` | 수급·기관 보유 | gpt-5.6-sol | high |
| 4 | `financials` | 재무·실적·밸류에이션 | gpt-5.6-sol | xhigh |
| 5 | `company` | 기업 개요·경쟁력 | gpt-5.6-sol | high |
| 6 | `news` | 뉴스·이벤트 | gpt-5.6-sol | high |
| 7 | `market` | 시장지수 | gpt-5.6-sol | high |
| 8 | `strategy` | 종합 투자 전략 | gpt-6-astra | xhigh |
| 9 | `report_summary` | 보고서 요약 | gpt-5.6-terra | high |
| 10 | `kr_buy` | 국내 매수 | gpt-6-astra | xhigh |
| 11 | `us_buy` | 미국 매수 | gpt-6-astra | xhigh |
| 12 | `kr_sell` | 국내 보유·매도 | gpt-6-astra | xhigh |
| 13 | `us_sell` | 미국 보유·매도 | gpt-6-astra | xhigh |
| 14 | `telegram_summary` | 알림 요약 | gpt-5.6-terra | medium |
| 15 | `telegram_evaluator` | 알림 품질 평가 | gpt-5.6-sol | medium |
| 16 | `journal` | 매매 회고 | gpt-5.6-sol | high |
| 17 | `memory_summary` | 회고 압축 | gpt-5.6-terra | medium |
| 18 | `memory_intuition` | 장기 기억·직관 | gpt-5.6-sol | high |
| 19 | `intuition_refresh` | 직관 갱신 | gpt-5.6-sol | high |
| 20 | `translation` | 다국어 번역 | gpt-5.6-luna | low |
| 21 | `company_translation` | 기업명 번역 | gpt-5.6-luna | low |
| 22 | `consultation` | 보유 진단·상담 | gpt-5.6-sol | high |
| 23 | `followup` | 분석 후속 질문 | gpt-5.6-terra | high |
| 24 | `journal_chat` | 회고 대화 | gpt-5.6-sol | high |
| 25 | `search_analysis` | 검색 결과 분석 | gpt-5.6-sol | high |
| 26 | `moderation` | 메시지 관리 | gpt-5.6-luna | low |
| 27 | `archive_query` | 아카이브 질의 | gpt-5.6-sol | high |
| 28 | `archive_insight` | 아카이브 인사이트 | gpt-5.6-sol | high |
| 29 | `vision` | 차트 이미지 해석 | gpt-5.6-sol | high |
| 30 | `render_qa` | 차트 시각 검사 | gpt-5.6-terra | medium |
| 31 | `embedding` | 의미 검색 임베딩 | OFF | — |
| 32 | `research` | 외부 자료 조사 | gpt-5.6-sol | high |
| 33 | `video` | 영상 전사문 분석 | gpt-5.6-sol | high |

영상의 제목 필터는 `video.filter`의 `gpt-5.6-terra / medium`을 사용합니다.
설정의 `enabled`는 호출 허용 여부이며 배치·봇·선택 기능을 자동으로 켜는 스위치가 아닙니다.

## 인증과 실행 경로

기존 사용자 계정의 Codex CLI ChatGPT 로그인을 사용합니다. 먼저 같은 OS 계정에서
`codex login status`가 `Logged in using ChatGPT`인지 확인합니다. CLI가 PATH에 없으면
`PRISM_CODEX_BIN`에 실행 파일 경로를 지정합니다. 구독 사용 가능량은 이 계정의
다른 Codex 작업과 공유합니다. 별도의 API 키를 새로 발급할 필요가 없습니다.

공통 호출부는 `prism_core/codex_subscription.py`입니다. `codex exec`를 임시 디렉터리에서
실행하며 승인된 모델과 추론 수준, ChatGPT 로그인 강제 설정을 매번 전달합니다.
사용자 Codex 설정·규칙·플러그인·셸 실행 도구를 상속하지 않고, API 키 환경변수를
제거합니다. 로그인 실패·한도 소진·시간 초과 시 다른 유료 AI API로 전환하지 않습니다.
기존 매매 코드의 규칙 기반 대체 판단과 오류 처리는 그대로 유지됩니다.

LLM이 구조화된 도구 요청을 제안하면 부모 Python 프로세스가 기존 MCP 조회 도구를
호출하고, 실제 응답을 다음 추론에 전달합니다. SQLite는 SELECT 조회 도구만 허용하며
LLM에 주문 도구를 제공하지 않습니다. 원래 프로그램의 주문·DB 기록 로직은 그대로입니다.
도구를 다 쓰면 미완성 답변을 성공으로 반환하지 않고 오류를 냅니다. 단계별 전체 시간
제한과 반복 횟수는 `timeout_seconds`, `max_tool_rounds`입니다. 상담의 기존 더 짧은
호출 제한도 유지합니다. 시간 초과·취소 시 Codex 자식 프로세스를 종료합니다.

2026-09-09 운영 요청으로 기존 600초인 단계의 제한을 2400초(40분)로
늘렸습니다. 국내·미국 매수/매도 판단과 분석·리서치 등이 대상입니다.
기존 300초 단계 및 전체 배치 120분 제한은 유지합니다. 40분은 각 시도에서
AI 응답과 도구 조회를 합친 시간입니다. 설정은 다음 단계 호출부터 반영되며,
이미 실행 중인 호출의 시간 제한은 변경되지 않습니다.

로그의 `[CODEX_STAGE]`에서 실제 단계·모델·추론 수준을 확인할 수 있습니다.

## 선택 기능과 데이터 수집

- 차트 이미지와 렌더링 검사는 Codex 이미지 입력에 연결했습니다. 기존 기능 플래그는
  기본 OFF 상태를 유지하며, 켜진 경우에도 해당 단계 설정을 따릅니다.
- 임베딩 생성은 OFF입니다. 기존 벡터 데이터는 삭제하지 않으며 새 검색은 FTS 경로를
  사용할 수 있습니다. 텍스트 모델을 임베딩 엔드포인트로 오인해 연결하지 않습니다.
- Perplexity 추론과 Firecrawl Spark agent 호출은 `research` 단계의 Codex 네이티브
  웹 검색으로 대체했습니다. Firecrawl 검색·스크래핑, 시세 등 일반 자료 수집 서비스의
  인증과 서비스 비용은 별개입니다. 모든 외부 데이터 서비스가 무료라는 뜻은 아닙니다.
- 영상 분석은 `video_info['transcript']` 또는 `TRANSCRIPTS_DIR` 아래의 기존
  전사문을 입력으로 사용합니다. 실제 디렉터리 상수는 `events/jeoningu_trading.py`의
  `TRANSCRIPTS_DIR`을 확인하십시오. 음성 전사 API는 비활성화했고, 전사문이 없는
  영상은 다운로드·분석을 건너뜁니다.
- 아카이브 질의 캐시 키에 제공 방식·모델·추론 수준을 포함해 이전 모델 답변과 구분합니다.

## 검증과 운영 상태

관련 회귀 테스트 202개 통과, 1개 의도적 생략. 보고서 기본 설정 검사 3개도 통과했습니다.
검증 범위는 33단계 설정, 보고서·전략·상담 연결, 요약 작성/평가 모델 분리, MCP 응답
전달, JSON 검증, 금지 도구 거부, 시간 초과 프로세스 종료, API 자동 전환 차단,
이미지 순서·임시 파일 정리 및 기존 체결 원가·계좌·회고·매도 동시성 동작입니다.

실제 구독으로 Luna/low 기업명 번역과 Sol/high 시간 조회 MCP 왕복을 확인했습니다.
모든 모델의 실제 접근성·실거래 판단 품질·투자 성과를 검증했다는 뜻은 아닙니다.

이번 변경은 새 fork 코드에 반영한 것입니다. 기존 운영 fork·cron·대시보드 프로세스와
운영 DB를 전환하지 않았으며, 테스트에서 주문이나 Telegram 발송을 하지 않았습니다.
운영 전환 시에는 기존 배치 중지 후 최신 DB 재복사·검증 및 계좌 정합화가 필요합니다.
