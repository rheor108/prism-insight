#!/usr/bin/env python3
"""Send one deduplicated quality report to the configured PRISM Telegram channel."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[1]


def send(event_id, message, *, state_path=None, post=None):
    from dotenv import load_dotenv
    import requests
    load_dotenv(ROOT / '.env')
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat = os.getenv('TELEGRAM_CHANNEL_ID')
    if not token or not chat:
        raise ValueError('Telegram configuration missing')
    if not event_id or not message.strip() or len(message) > 3500:
        raise ValueError('Event ID and message of 1..3500 characters required')
    path = Path(state_path or ROOT / 'logs/quality_notifications.sqlite')
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS notifications (event_id TEXT PRIMARY KEY, body_hash TEXT, status TEXT, created_at REAL, message_id INTEGER)')
        digest = hashlib.sha256(message.encode()).hexdigest()
        try:
            db.execute('INSERT INTO notifications VALUES (?, ?, ?, ?, NULL)',
                       (event_id, digest, 'pending', time.time()))
            db.commit()
        except sqlite3.IntegrityError:
            previous = db.execute('SELECT body_hash, status FROM notifications WHERE event_id=?', (event_id,)).fetchone()
            if previous[0] != digest:
                raise ValueError('Event ID already used with different content')
            return {'status': 'duplicate_suppressed', 'previous_status': previous[1]}
        # Reserve before sending: uncertain network outcomes are never retried
        # automatically because Telegram sendMessage has no idempotency key.
        status, message_id = 'uncertain', None
        try:
            response = (post or requests.post)(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': chat, 'text': message}, timeout=30)
            value = response.json()
            if value.get('ok') is True:
                status = 'sent'
                message_id = value.get('result', {}).get('message_id')
            else:
                status = 'rejected'
        except Exception:
            pass  # Never log request URLs, tokens, or response descriptions.
        db.execute('UPDATE notifications SET status=?, message_id=? WHERE event_id=?',
                   (status, message_id, event_id))
        db.commit()
        return {'status': status, 'message_id': message_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event-id', required=True)
    parser.add_argument('--message-file', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = send(args.event_id, args.message_file.read_text())
    except Exception:
        print(json.dumps({'status': 'configuration_or_input_error'}))
        raise SystemExit(1)
    print(json.dumps(result))
    if result['status'] not in ('sent', 'duplicate_suppressed'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
