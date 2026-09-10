"""Allowlisted Codex timing, token and account quota telemetry."""
import asyncio
import json
import logging
import math
import os
from pathlib import Path
import time

log = logging.getLogger(__name__)
METRICS_PATH = Path(__file__).resolve().parents[1] / 'logs/codex_calls.jsonl'


def windows(result):
    buckets = result.get('rateLimitsByLimitId')
    if not isinstance(buckets, dict):
        legacy = result.get('rateLimits')
        buckets = {legacy.get('limitId') or 'codex': legacy} if isinstance(legacy, dict) else {}
    out = []
    for bucket, value in buckets.items():
        if not isinstance(value, dict):
            continue
        for name in ('primary', 'secondary'):
            w = value.get(name)
            if not isinstance(w, dict):
                continue
            percent = w.get('usedPercent')
            if type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100:
                continue
            out.append({'bucket': bucket, 'window': name, 'used_percent': percent,
                        'window_minutes': w.get('windowDurationMins'), 'resets_at': w.get('resetsAt')})
    return out


async def quota_snapshot(binary, env):
    """Read-only official app-server RPC; no inference or credit reset."""
    process = None
    try:
        async with asyncio.timeout(5):
            process = await asyncio.create_subprocess_exec(
                binary, 'app-server', '--stdio',
                '-c', 'forced_login_method="chatgpt"', '-c', 'mcp_servers={}',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=env, start_new_session=True,
                limit=1024*1024)
            async def send(value):
                process.stdin.write((json.dumps(value)+'\n').encode())
                await process.stdin.drain()
            async def response(identifier):
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        raise ValueError('RPC closed')
                    value = json.loads(line)
                    if value.get('id') == identifier:
                        if 'error' in value:
                            raise ValueError('RPC rejected')
                        return value['result']
            await send({'id': 1, 'method': 'initialize', 'params': {
                'clientInfo': {'name': 'prism_metrics', 'version': '1.0'}}})
            await response(1)
            await send({'method': 'initialized', 'params': {}})
            await send({'id': 2, 'method': 'account/rateLimits/read', 'params': {}})
            result = windows(await response(2))
            return {'status': 'available' if result else 'unavailable',
                    'observed_at': time.time(), 'windows': result}
    except Exception:
        # Telemetry must never fail inference or expose raw RPC/auth errors.
        return {'status': 'unavailable', 'observed_at': time.time(), 'windows': []}
    finally:
        if process is not None and process.returncode is None:
            from prism_core.codex_subscription import _terminate
            await _terminate(process)


def quota_delta(before, after):
    previous = {(w['bucket'], w['window']): w for w in before.get('windows', [])}
    out = []
    for w in after.get('windows', []):
        old = previous.get((w['bucket'], w['window']))
        comparable = (old is not None and w['resets_at'] is not None
                      and w['resets_at'] == old['resets_at']
                      and w['window_minutes'] == old['window_minutes']
                      and w['used_percent'] >= old['used_percent'])
        out.append({**w, 'before_used_percent': old['used_percent'] if old else None,
                    'account_delta_percentage_points': round(w['used_percent']-old['used_percent'], 6) if comparable else None})
    return out


def token_usage(stream):
    """Only numeric usage from CLI completion events, never model content."""
    stream.seek(0)
    usage = None
    for line in stream:
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or event.get('type') != 'turn.completed':
                continue
            raw = event.get('usage', {})
            values = {k: raw[k] for k in ('input_tokens', 'cached_input_tokens', 'output_tokens')
                      if type(raw.get(k)) is int and raw[k] >= 0}
            if values:
                if usage is None:
                    usage = {}
                for key, value in values.items():
                    usage[key] = usage.get(key, 0) + value
        except (ValueError, AttributeError, TypeError):
            continue
    return usage


def emit(record):
    try:
        METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps({'timestamp': time.time(), **record}, ensure_ascii=False)+'\n').encode()
        fd = os.open(METRICS_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        log.info('[CODEX_METRICS] %s', data.decode().strip())
    except Exception:
        log.warning('[CODEX_METRICS] telemetry_write_failed')
