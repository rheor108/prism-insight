# KRX 인증 실패 진단

2026-09-09: `kospi-kosdaq-stock-server 0.4.2`의 KRX 직접 로그인 경로에
진단 정보를 추가했습니다. 로그인·재시도·차단 처리와 매매 판단은 바꾸지 않습니다.

## 설치와 확인

저장소 루트에서 운영 Python으로 실행합니다.

```bash
.venv/bin/python tools/install_krx_auth_diagnostics.py
.venv/bin/python tools/install_krx_auth_diagnostics.py --check
```

설치 도구는 원본 SHA-256과 패치 내용을 검증하고 해당 인터프리터의
`krx_data_client.py`에 적용합니다. 원본은 같은 디렉터리의
`krx_data_client.py.prism-original`에 보존합니다. 다른 소스 버전이면 변경 없이
중단합니다. 가상환경 재생성 또는 의존성 재설치 후에는 위 명령을 다시 실행해야 합니다.
이미 패치된 상태에서 재실행해도 중복 적용하지 않습니다.

현재 운영 cron과 kospi_kosdaq MCP는 같은 `.venv` Python을 사용합니다.
새 프로세스부터 적용되며 이미 import한 프로세스에는 소급 반영되지 않습니다.
다른 Python/uvx 환경을 쓰는 배포에서는 그 환경도 별도로 설치·확인해야 합니다.

## 기록되는 정보

- `[KRX_AUTH_DIAGNOSTIC]`: 로그인 시도 ID, PID, 경과 시간, 실패 단계.
- KRX 문서/XHR/fetch 응답의 경로, HTTP 상태, 메서드, Content-Type.
- 크기가 명시된 16KB 이하 JSON 응답의 message/error/code/result 계열 필드만 추출.
  HTML 원문·큰 응답·크기 미상 응답은 읽지 않고 생략 이유를 기록합니다.
- 로그인 제출 직후와 실패 시점의 본문 안내 문구, iframe 안내 문구, 팝업 메시지.
  오류·차단·인증 등의 문구를 우선 기록합니다. 최대 3개 프레임, 최근 20개 이벤트만
  보관하며 화면 캡처는 최대 2초, 진행 중 응답 수집 대기는 최대 1초입니다.
- `[KRX_SESSION_VALIDATION]`: 세션 검증에서 HTML/HTTP 오류가 반환된 경우 상태·경로.

계정 ID/비밀번호 및 현재 쿠키 값은 마스킹에만 사용합니다. 요청 본문,
Authorization/Set-Cookie 헤더, URL 쿼리, HTML 원문, 입력 필드 값, 스크린샷은
기록하지 않습니다. 기존 라이브러리의 JavaScript 쿠키 값 로그도 제거합니다.
문구에 포함된 이메일·긴 식별자·전화번호 등은 추가로 가립니다.

홈 페이지 진입만으로 “로그인 성공”을 확정하던 메시지를 “데이터 인증 미검증”으로
바꿨습니다. 로그인 페이지로 돌아왔을 때 “다른 프로세스가 로그인했다”는 추정도
제거했습니다. 응답/안내 문구가 원인을 명시하지 않으면 원인은 계속 미확정입니다.

## 검증

```bash
.venv/bin/python -m pytest tests/test_krx_auth_diagnostics.py -q
```

실제 설치 소스에 패치를 적용한 로그인 함수를 가짜 브라우저로 실행하여,
인증 실패 예외는 유지하면서 브라우저 정리 전에 증거를 남기는지 확인합니다.
민감값 마스킹, 이벤트/응답 크기 제한, 캡처 실패, 알 수 없는 의존성 거부를 검사합니다.
이 검증은 외부 로그인이나 주문을 실행하지 않습니다.
