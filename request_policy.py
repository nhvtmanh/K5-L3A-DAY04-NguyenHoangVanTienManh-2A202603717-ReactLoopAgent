"""Bounded transport retries and shared pacing; never changes agent step limits."""
import math
import os
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError

_LOCK = threading.Lock()
_LAST_REQUEST = {}


def settings():
    values = {
        'min_interval': float(os.getenv('PROVIDER_MIN_INTERVAL', '0')),
        'max_retries': int(os.getenv('PROVIDER_MAX_RETRIES', '0')),
        'max_retry_wait': float(os.getenv('PROVIDER_MAX_RETRY_WAIT', '60')),
    }
    if (any(not math.isfinite(value) or value < 0 for value in values.values())
            or values['max_retries'] > 3 or values['min_interval'] > 60
            or values['max_retry_wait'] > 60):
        raise ValueError('Request policy: interval/wait từ 0..60 giây, retries từ 0..3.')
    return values


def retry_after(headers):
    raw = headers.get('Retry-After') if headers else None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (ValueError, TypeError):
        try:
            value = parsedate_to_datetime(raw).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, value) if math.isfinite(value) else None


def _pace(scope, interval, events):
    if not interval:
        return
    with _LOCK:
        previous = _LAST_REQUEST.get(scope)
        wait = max(0, previous + interval - time.monotonic()) if previous is not None else 0
        if wait:
            events.append({'event': 'request_wait', 'seconds': round(wait, 3)})
            time.sleep(wait)
        _LAST_REQUEST[scope] = time.monotonic()


def request(operation, scope, policy, events):
    """Retry only 429/503, with a total wait budget and no raw exception text."""
    waited = 0
    for attempt in range(policy['max_retries'] + 1):
        _pace(scope, policy['min_interval'], events)
        try:
            return operation()
        except Exception as exc:
            cause, seen = exc, set()
            while cause is not None and not isinstance(cause, HTTPError) and id(cause) not in seen:
                seen.add(id(cause))
                cause = cause.__cause__ or cause.__context__
            if not isinstance(cause, HTTPError) or cause.code not in (429, 503):
                raise
            delay = retry_after(cause.headers)
            delay = delay if delay is not None else 2 ** (attempt + 1)
            if attempt == policy['max_retries'] or waited + delay > policy['max_retry_wait']:
                if policy['max_retries']:
                    events.append({'event': 'request_failed', 'http_status': cause.code,
                                   'attempt': attempt + 1, 'retry_after': delay})
                raise
            events.append({'event': 'request_retry', 'http_status': cause.code,
                           'attempt': attempt + 1, 'seconds': delay})
            cause.close()
            time.sleep(delay)
            waited += delay
