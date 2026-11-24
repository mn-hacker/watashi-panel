# watashi v12.3.57dev
"""
Server-side notification feed for the panel bell.

Notifications live in Redis (list per admin id, newest first, capped), so
nothing here needs a database migration. Anything in the panel that wants the
admin to know something happened calls ws_push_admin_event() with a plain
message, a kind and an optional severity; every signed-in admin page picks the
events up through the polling endpoint.
"""

import json

from loguru import logger

from watashipanel.cache import redis_client

WS_FEED_MAX = 50
_WS_FEED_TTL = 60 * 60 * 24 * 7  # a week of silence empties the tray
_WS_KEY = 'watashi:bell:%s'


def ws_feed_key(admin_id):
    return _WS_KEY % int(admin_id)


def ws_push_admin_event(admin_id, message, kind='info', meta=None):
    """Record one event for one admin. Never raises into the caller."""
    try:
        import time as _time
        entry = {'m': str(message), 'k': kind,
                 'ts': int(_time.time() * 1000) if not (isinstance(meta, dict) and meta.get('ts')) else int(meta['ts']),
                 'meta': meta if isinstance(meta, dict) else None}
        entry = {k: v for k, v in entry.items() if v is not None}
        key = ws_feed_key(admin_id)
        pipe = redis_client.pipeline()
        pipe.lpush(key, json.dumps(entry, ensure_ascii=False))
        pipe.ltrim(key, 0, WS_FEED_MAX - 1)
        pipe.expire(key, _WS_FEED_TTL)
        pipe.execute()
        return True
    except Exception as e:
        logger.warning(f'watashi: the bell event could not be saved ({e})')
        return False


def ws_push_all_admins(message, kind='info', meta=None):
    """Fan an event out to every admin account (node down, backup done, ...)."""
    try:
        from watashipanel.models import AdminUser
        ids = [row[0] for row in AdminUser.query.with_entities(AdminUser.id).all()]
        for admin_id in ids:
            ws_push_admin_event(admin_id, message, kind, meta)
        return len(ids)
    except Exception as e:
        logger.warning(f'watashi: the bell fanout failed ({e})')
        return 0


def ws_read_feed(admin_id, after_ts=None):
    """The admin's feed, newest first. With after_ts, only newer entries."""
    try:
        raw = redis_client.lrange(ws_feed_key(admin_id), 0, WS_FEED_MAX - 1)
    except Exception as e:
        logger.warning(f'watashi: the bell feed could not be read ({e})')
        return []
    out = []
    for item in raw:
        try:
            entry = json.loads(item)
        except (ValueError, TypeError):
            continue
        if after_ts is not None and entry.get('ts', 0) <= float(after_ts):
            continue
        out.append(entry)
    return out
