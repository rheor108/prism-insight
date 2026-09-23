# API 요금 없는 로컬 페이지 수집

보고서 MCP의 `firecrawl` 이름은 호환성을 위해 유지하지만,
`firecrawl_scrape`는 로컬 Crawl4AI와 Chromium으로 실행합니다.
Firecrawl API 키나 크레딧은 사용하지 않습니다. LLM 기반 추출도 사용하지 않습니다.
검색은 기존 구독 런타임의 Codex research 경로를 사용하므로 Codex 사용량은 발생합니다.
서버 CPU·메모리·네트워크 비용은 별개입니다.

## 설치

저장소 루트에서 실행합니다. 거래용 `.venv`와 의존성을 분리합니다.

```bash
python3 -m venv .venv-crawl4ai
.venv-crawl4ai/bin/pip install -r requirements-crawl4ai.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/.venv-crawl4ai/browsers" \
  .venv-crawl4ai/bin/python -m playwright install chromium
.venv-crawl4ai/bin/pip check
```

호스트에 Chromium 시스템 라이브러리가 없다면 Playwright가 안내하는 OS 의존성도
설치해야 합니다. 설치와 공개 사이트 접근에는 네트워크가 필요합니다.

`cores/llm/mcp_servers.yaml`이 `prism_core.scraper_bootstrap`을 실행합니다.
한국·미국 보고서는 같은 MCP 설정을 사용합니다. 부트스트랩은 저장소 루트를 기준으로
격리 환경을 찾으므로 미국 분석 하위 디렉터리에서도 동일하게 동작합니다.
기존 비밀 설정 파일은 수정하지 않습니다.

선택 환경 변수:

- `PRISM_CRAWL_PYTHON`: 다른 격리 Python의 절대 경로
- `PLAYWRIGHT_BROWSERS_PATH`: Chromium 설치 경로; 기본 `.venv-crawl4ai/browsers`
- `CRAWL4_AI_BASE_DIRECTORY`: Crawl4AI 작업 경로; 기본 `runtime/crawl4ai`

## 지원 범위와 실패 처리

- 공개 HTTP(S) 페이지를 렌더링하고 표·iframe 내용을 Markdown으로 반환합니다.
- `formats=["markdown"]`, `onlyMainContent`, `waitFor`, `timeout`을 지원합니다.
- 기본 렌더링 대기는 5초입니다. 기존 프롬프트의 `maxAge`는 호환 입력으로
  허용하지만 캐시는 사용하지 않고 항상 새로 조회합니다.
- Firecrawl의 search/crawl/map, JSON/LLM 추출, 로그인·클릭·사용자 스크립트는 지원하지 않습니다.
- 매 요청 새 브라우저를 사용하며 페이지 캐시는 비활성화합니다.
- 응답에는 원본/최종 URL, 최종/최초 HTTP 상태, 제목, 조회 시각, 잘림 여부가 포함됩니다.
- 최대 60,000자 이후는 잘림으로 표시합니다. 기본 페이지 제한은 45초이며,
  정리 여유 시간을 더한 뒤 실패합니다. MCP 읽기 제한은 90초입니다.
- 최종 HTTP 오류·빈 본문·시간 초과는 도구 오류로 반환합니다. 다른 출처 검색이 필요하며,
  원래 사이트의 자료를 읽었다고 간주해서는 안 됩니다.
- 공개 주소만 허용하고 브라우저 요청에도 사설 주소 차단을 적용합니다.
  페이지 내용은 신뢰할 수 없는 외부 자료입니다.
- 사이트 구조 변경, 차단, 늦은 렌더링에 따라 수집 품질이 달라질 수 있습니다.
  HTTP 성공만으로 필요한 금융 데이터가 모두 존재한다고 보장하지 않습니다.

## 검증

```bash
.venv/bin/python -m pytest tests/test_local_scraper.py \
  tests/test_firecrawl_mcp_config.py tests/test_subscription_models.py \
  tests/test_report_agent_contract.py cores/llm/tests/test_config.py -q
```

2026-09-23 실제 보고서 RegistryTools → MCP 경로로 미코(059090)의 WiseReport
기업현황·기업개요를 조회하여 회사명, 재무·매출구성 표의 보존을 확인했습니다.
네이버 구 URL에서 새 증권 URL로 이동한 뒤 5초 렌더링 대기를 적용하여 종목명,
뉴스 제목·날짜·요약·기사 링크도 확인했습니다. 1초 대기로는 뉴스가 로딩되지 않았습니다.
Yahoo Finance의 AAPL 뉴스 페이지는 이번 실제 조회에서 시간 초과로 실패했습니다.
이 사이트의 직접 수집 성공은 검증되지 않았으며, 미국 뉴스는 기존 research 도구로
다른 출처를 검색해야 할 수 있습니다.
이는 수집기 연결 검증이며 전체 장중 분석·자동매매 실행 검증은 아닙니다.

되돌릴 경우 코드 변경을 revert하는 PR을 배포합니다. 기존 Firecrawl 서비스로
복귀하려면 별도로 유효한 Firecrawl API 키가 필요합니다. 예제 키로는 동작하지 않습니다.
