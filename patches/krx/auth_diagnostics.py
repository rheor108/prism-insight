"""Bounded, redacted KRX authentication evidence; never changes login decisions."""
import asyncio
from collections import deque
import json
import logging
import os
import re
import time
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

FIELDS = {'message', 'msg', 'errormessage', 'errormsg', 'error', 'code',
          'errorcode', 'result', 'resultcode', 'resultmessage', 'resultmsg'}


def safe_url(value):
    try:
        url = urlsplit(str(value))
        return f'{url.scheme}://{url.hostname or ""}{url.path}'
    except ValueError:
        return '[invalid URL]'


def redact(value, secrets=()):
    text = str(value)
    for secret in sorted(set(str(s) for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    urls = []
    def keep_url(match):
        urls.append(safe_url(match[0]))
        return f'URLPLACEHOLDER{len(urls)-1}X'
    text = re.sub(r'https?://[^\s<>"\']+', keep_url, text)
    text = re.sub(r'(?i)(bearer\s+)\S+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:password|passwd|pw|token|cookie|session|authorization|mbrid|userid|account|비밀번호|아이디)\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL]', text)
    text = re.sub(r'(?<!\w)\d[\d -]{6,}\d(?!\w)', '[NUMBER]', text)
    text = re.sub(r'[A-Za-z0-9_+/=-]{24,}', '[OPAQUE]', text)
    for index, url in enumerate(urls):
        text = text.replace(f'URLPLACEHOLDER{index}X', url)
    # Keep logs single-line, including terminal/control characters.
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', text)[:1600]


def response_fields(data, depth=0):
    """Only error/message/code fields, never arbitrary response or request bodies."""
    if depth > 2 or not isinstance(data, dict):
        return {}
    result = {}
    for key, value in list(data.items())[:40]:
        normalized = re.sub('[^a-z]', '', key.lower())
        if normalized in FIELDS and isinstance(value, (str, int, bool)):
            result[key] = str(value)[:1600]
        elif isinstance(value, dict):
            nested = response_fields(value, depth + 1)
            if nested:
                result[key] = nested
    return result


class SafeLogFilter(logging.Filter):
    def __init__(self, secrets):
        super().__init__()
        self.secrets = secrets

    def filter(self, record):
        record.msg = redact(record.getMessage(), self.secrets)
        record.args = ()
        return True


class AuthDiagnostics:
    def __init__(self, logger, secrets=()):
        self.logger = logger
        self.secrets = list(secrets)
        self.events = deque(maxlen=20)
        self.login_events = deque(maxlen=20)
        self.tasks = set()
        self.phase = 'setup'
        self.started = time.monotonic()
        self.attempt = f'{os.getpid()}-{time.monotonic_ns()}'
        self.page = None
        self.emitted = False
        # Existing dependency logs also contain redirect queries and dialog text.
        self.log_filter = SafeLogFilter(self.secrets)
        logger.addFilter(self.log_filter)

    def attach(self, page):
        self.page = page
        page.on('response', self.on_response)
        page.on('dialog', self.on_dialog)

    def on_dialog(self, dialog):
        self.login_events.append({'kind': 'dialog', 'phase': self.phase,
                            'type': dialog.type, 'message': dialog.message})

    def on_response(self, response):
        try:
            host = urlsplit(response.url).hostname or ''
            if not (host == 'krx.co.kr' or host.endswith('.krx.co.kr')):
                return
            if response.request.resource_type not in {'document', 'xhr', 'fetch'}:
                return
            if len(self.tasks) >= 8:
                return
            phase = self.phase
            task = asyncio.create_task(self.capture_response(response, phase))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        except Exception:
            pass

    async def capture_response(self, response, phase):
        event = {'kind': 'response', 'phase': phase, 'url': safe_url(response.url),
                 'status': response.status, 'method': response.request.method}
        (self.login_events if phase in {'login_submit','after_submit'} else self.events).append(event)
        try:
            # Do not read HTML or unlimited bodies. Navigation evidence comes from
            # visible text at the checkpoint, not scripts, inputs or hidden DOM.
            headers = response.headers
            event['content_type'] = headers.get('content-type', '')[:80]
            size = int(headers.get('content-length', '0'))
            if 'json' in event['content_type'] and 0 < size <= 16384:
                async with asyncio.timeout(1):
                    event['fields'] = response_fields(await response.json())
            else:
                event['body_evidence'] = 'skipped: non-JSON or unbounded/large body'
        except Exception:
            event['body_evidence'] = 'unavailable'

    async def capture_page(self, phase):
        self.phase = phase
        if self.page is None:
            return
        try:
            async with asyncio.timeout(2):
                # Cookie values are used only as redaction patterns, never logged.
                for cookie in await self.page.context.cookies():
                    if cookie.get('value'):
                        self.secrets.append(cookie['value'])
                for frame in list(self.page.frames)[:3]:
                    host = urlsplit(frame.url).hostname or ''
                    if not (host == 'krx.co.kr' or host.endswith('.krx.co.kr')):
                        continue
                    text = await frame.locator('body').inner_text(timeout=500)
                    # Prefer error notices over the page's common navigation text.
                    lines = [s.strip() for s in text.splitlines() if re.search(
                        r'실패|오류|차단|제한|인증|중복|잠금|초과|잘못|일치|비정상|error|denied|invalid|captcha', s, re.I)]
                    (self.login_events if phase == 'after_submit' else self.events).append({'kind': 'page', 'phase': phase,
                                        'url': safe_url(frame.url),
                                        'notice': '\n'.join(lines)[:3000] or text[:600]})
        except Exception as exc:
            self.events.append({'kind': 'capture_error', 'phase': phase,
                                'error_type': type(exc).__name__})

    async def failure(self, phase, error=None):
        if self.emitted:
            return
        self.emitted = True
        await self.capture_page(phase)
        if self.tasks:
            await asyncio.wait(list(self.tasks), timeout=1)
        record = {'attempt': self.attempt, 'pid': os.getpid(), 'phase': phase,
                  'elapsed_seconds': round(time.monotonic() - self.started, 1),
                  'error_type': type(error).__name__ if error else 'LoginRedirect',
                  'reason': 'unknown; inspect response and visible notice',
                  'events': list(self.login_events) + list(self.events)}
        # Sanitize values before serializing (credentials may contain quotes).
        def clean(value):
            if isinstance(value, dict):
                return {redact(k, self.secrets): (v if k == 'attempt' else clean(v)) for k, v in value.items()}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return redact(value, self.secrets) if isinstance(value, str) else value
        # Dedicated logger avoids truncation by the dependency's legacy filter.
        logging.getLogger('prism.krx_auth').warning(
            '[KRX_AUTH_DIAGNOSTIC] %s', json.dumps(clean(record), ensure_ascii=False))

    async def close(self):
        try:
            for task in list(self.tasks):
                task.cancel()
            if self.tasks:
                await asyncio.gather(*list(self.tasks), return_exceptions=True)
            if self.page is not None:
                for event, listener in [('response', self.on_response), ('dialog', self.on_dialog)]:
                    try:
                        self.page.remove_listener(event, listener)
                    except Exception:
                        # Playwright sends subscription updates even on removal.
                        # A stopped driver must not replace the original auth error.
                        pass
        finally:
            self.logger.removeFilter(self.log_filter)


def validation_response(logger, response):
    """No body, cookies, query strings or headers other than content type."""
    logger.warning('[KRX_SESSION_VALIDATION] status=%s url=%s content_type=%s',
                   response.status_code, safe_url(response.url),
                   response.headers.get('Content-Type', '')[:80])


AUTH_RETRY_COOLDOWN_SECONDS = 300


def _retry_path(manager):
    identity = str(getattr(manager, 'krx_id', '') or getattr(manager, 'kakao_id', ''))
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return Path(manager.LOCK_PATH).with_name(f'.krx_auth_retry_{digest}.json')


def auth_retry_remaining(manager):
    try:
        stamp = json.loads(_retry_path(manager).read_text())['failed_at']
        return max(0, min(AUTH_RETRY_COOLDOWN_SECONDS,
                          AUTH_RETRY_COOLDOWN_SECONDS - (time.time() - float(stamp))))
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def mark_auth_failure(manager):
    # Caller holds the dependency's cross-process login lock. No credentials stored.
    try:
        path = _retry_path(manager)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'failed_at': time.time()}, stream)
    except OSError:
        logging.getLogger('prism.krx_auth').warning('[KRX_AUTH_BACKOFF] state write failed')


def clear_auth_failure(manager):
    try:
        _retry_path(manager).unlink(missing_ok=True)
    except OSError:
        pass
