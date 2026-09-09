#!/usr/bin/env python3
"""Install a reproducible diagnostic patch into this interpreter's KRX 0.4.2.

Run with .venv/bin/python. Unknown dependency sources fail without modification.
No login, network request, credential/config change, or trading operation.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile

BASE_SHA256 = '88488ec3165a3d23decdc1fad860e6fd9e0d5f37b623e04c52dbdb1e8f23e513'
ROOT = Path(__file__).resolve().parents[1]
MARKER = '# PRISM KRX authentication diagnostics v1'


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('KRX dependency source differs; review patch before installing')
    return source.replace(old, new, 1)


def patched_source(source):
    if hashlib.sha256(source.encode()).hexdigest() != BASE_SHA256:
        raise ValueError('Unsupported KRX dependency hash; no files changed')
    source = replace_once(source, 'import requests\n',
        f'{MARKER}\nfrom prism_krx_auth_diagnostics import AuthDiagnostics, validation_response\n\nimport requests\n')
    source = replace_once(source, '            # 응답이 비어있거나 HTML인 경우 (로그인 필요)\n',
        '            if "text/html" in resp.headers.get("Content-Type", "") or resp.status_code >= 400:\n'
        '                validation_response(logger, resp)\n\n'
        '            # 응답이 비어있거나 HTML인 경우 (로그인 필요)\n')
    start = source.index('    async def _login_async_krx(')
    end = source.index('    async def _cleanup_browser(', start)
    method = source[start:end]
    method = replace_once(method, '        page = await context.new_page()\n',
        '        page = await context.new_page()\n'
        '        diag = AuthDiagnostics(logger, (self.krx_id, self.krx_pw))\n'
        '        diag.attach(page)\n')
    method = replace_once(method, '            await login_btn.click()\n',
        '            diag.phase = "login_submit"\n            await login_btn.click()\n')
    method = replace_once(method, '            # KRX 홈 페이지로 명시적 이동하여 로그인 상태 확인\n',
        '            await diag.capture_page("after_submit")\n\n'
        '            # KRX 홈 페이지로 명시적 이동하여 로그인 상태 확인\n')
    method = replace_once(method, '            await page.goto(home_url,',
        '            diag.phase = "home"\n            await page.goto(home_url,')
    method = replace_once(method, '                # 로그인 페이지로 리다이렉트됨 = 로그인 실패\n',
        '                await diag.failure("home_redirect")\n'
        '                # 로그인 페이지로 리다이렉트됨 = 로그인 실패\n')
    method = method.replace('KRX 직접 로그인 실패. 아이디/비밀번호를 확인하세요.',
                            'KRX 인증 확인 실패: 홈에서 로그인 페이지로 돌아왔습니다. 원인 미확정.')
    method = method.replace('KRX 직접 로그인 성공! 현재 URL:',
                            'KRX 홈 진입 확인 (데이터 인증 미검증). 현재 URL:')
    method = replace_once(method, '            await page.goto(data_page_url,',
        '            diag.phase = "data_page"\n            await page.goto(data_page_url,')
    method = replace_once(method, '            # 데이터 조회 페이지에서 리다이렉트되면 세션이 무효화된 것임\n',
        '            # 데이터 조회 페이지의 인증 미확인; 리다이렉트만으로 원인 판정 불가\n')
    method = method.replace('            # (다른 프로세스에서 로그인하여 기존 세션이 만료됨)\n', '')
    method = replace_once(method, '            if "MDCCOMS001" in current_url:\n                logger.warning(',
        '            if "MDCCOMS001" in current_url:\n                await diag.failure("data_redirect")\n                logger.warning(')
    method = method.replace('다른 프로세스에서 로그인하여 세션이 무효화되었을 수 있습니다.',
                            '인증 실패 원인은 미확정입니다. KRX_AUTH_DIAGNOSTIC을 확인하세요.')
    method = method.replace('다른 프로세스의 로그인으로 세션이 무효화되었습니다.',
                            '인증 실패 원인 미확정 (중복 로그인 여부는 확인되지 않음).')
    method = replace_once(method, '        except (KRXBlockedError, KRX2FARequiredError):\n',
        '        except (KRXBlockedError, KRX2FARequiredError) as e:\n'
        '            await diag.failure("login_exception", e)\n')
    method = replace_once(method, '        except Exception as e:\n            logger.error(',
        '        except Exception as e:\n            await diag.failure("login_exception", e)\n            logger.error(')
    method = replace_once(method, '        finally:\n            await self._cleanup_browser()\n',
        '        finally:\n            await diag.close()\n            await self._cleanup_browser()\n')
    source = source[:start] + method + source[end:]
    # The dependency previously printed complete JavaScript cookie values.
    source = source.replace(
        'logger.info(f"[시도 {retry+1}/{max_cookie_retries}] JavaScript 쿠키: {js_cookies_str}")',
        'logger.info("JavaScript 쿠키 확인 (값은 기록하지 않음)")')
    compile(source, 'krx_data_client.py', 'exec')
    return source


def atomic_write(path, data, mode):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.krx-diag-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    spec = importlib.util.find_spec('krx_data_client')
    if spec is None:
        raise SystemExit('krx_data_client is not installed in this interpreter')
    target = Path(spec.origin)
    backup = target.with_suffix('.py.prism-original')
    current = target.read_bytes()
    original = backup.read_bytes() if MARKER.encode() in current else current
    expected = patched_source(original.decode()).encode()
    if current not in (original, expected):
        raise SystemExit('Existing patch differs; no files changed')
    helper = target.with_name('prism_krx_auth_diagnostics.py')
    helper_data = (ROOT / 'patches/krx/auth_diagnostics.py').read_bytes()
    compile(helper_data, str(helper), 'exec')
    ready = current == expected and helper.exists() and helper.read_bytes() == helper_data
    if args.check:
        print(f'KRX diagnostics installed={ready} module={target}')
        raise SystemExit(0 if ready else 1)
    if backup.exists() and backup.read_bytes() != original:
        raise SystemExit('Backup differs; no files changed')
    if not backup.exists():
        shutil.copy2(target, backup)
    atomic_write(helper, helper_data, 0o644)
    atomic_write(target, expected, target.stat().st_mode & 0o777)
    print(f'KRX diagnostics installed: {target}; original retained: {backup}')


if __name__ == '__main__':
    main()
