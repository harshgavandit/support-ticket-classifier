"""Atomic fixed-window admission control using Upstash Redis REST."""
from dataclasses import dataclass
import hashlib
import hmac
import math
import os
import time
from urllib.parse import urlparse

import httpx

# The window starts at the first admitted request. Denials do not extend its TTL.
LUA_SCRIPT = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
local ttl = redis.call('PTTL', KEYS[1])
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
if ttl <= 0 then
    redis.call('SET', KEYS[1], 1, 'PX', window)
    return {1, limit - 1, window}
end
if count >= limit then
    return {0, 0, ttl}
end
count = redis.call('INCR', KEYS[1])
return {1, limit - count, ttl}
"""


class RateLimitUnavailable(Exception):
    """Redis could not provide a valid admission decision."""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after: int
    limit: int

    @property
    def headers(self) -> dict[str, str]:
        return {
            'X-RateLimit-Limit': str(self.limit),
            'X-RateLimit-Remaining': str(self.remaining),
            'X-RateLimit-Reset': str(math.ceil(time.time()) + self.retry_after),
        }


class RedisRateLimiter:
    def __init__(self, url: str, token: str, *, limit: int = 10,
                 window_seconds: int = 60, enabled: bool = True,
                 prefix: str = 'support-ticket:classify', client: httpx.Client | None = None):
        if limit < 1 or window_seconds < 1:
            raise ValueError('Rate limit and window must be positive integers')
        parsed = urlparse(url)
        if enabled and (not token or parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
            raise ValueError('Set a valid HTTPS UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN')
        self.url = url.rstrip('/')
        self.token = token
        self.limit = limit
        self.window_seconds = window_seconds
        self.enabled = enabled
        self.prefix = prefix
        self.client = client or httpx.Client(timeout=httpx.Timeout(5, connect=3))

    @classmethod
    def from_env(cls):
        enabled = os.getenv('RATE_LIMIT_ENABLED', 'true').strip().lower()
        if enabled not in {'true', 'false'}:
            raise ValueError('RATE_LIMIT_ENABLED must be true or false')
        return cls(
            os.getenv('UPSTASH_REDIS_REST_URL', ''),
            os.getenv('UPSTASH_REDIS_REST_TOKEN', ''),
            limit=int(os.getenv('RATE_LIMIT_REQUESTS', '10')),
            window_seconds=int(os.getenv('RATE_LIMIT_WINDOW_SECONDS', '60')),
            enabled=enabled == 'true',
            prefix=os.getenv('RATE_LIMIT_PREFIX', 'support-ticket:classify'),
        )

    def _command(self, command: list) -> object:
        try:
            response = self.client.post(self.url, headers={'Authorization': f'Bearer {self.token}'}, json=command)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or 'error' in data or 'result' not in data:
                raise ValueError('Invalid Redis response')
            return data['result']
        except (httpx.HTTPError, ValueError, TypeError):
            raise RateLimitUnavailable('Rate limit service unavailable') from None

    def ping(self) -> bool:
        return not self.enabled or self._command(['PING']) == 'PONG'

    def check(self, identity: str) -> RateLimitDecision | None:
        if not self.enabled:
            return None
        digest = hmac.new(self.token.encode(), identity.encode(), hashlib.sha256).hexdigest()
        result = self._command(['EVAL', LUA_SCRIPT, '1', f'{self.prefix}:{digest}', str(self.limit), str(self.window_seconds * 1000)])
        if (not isinstance(result, list) or len(result) != 3
                or any(type(value) is not int for value in result)
                or result[0] not in (0, 1) or not 0 <= result[1] <= self.limit
                or not 0 < result[2] <= self.window_seconds * 1000):
            raise RateLimitUnavailable('Invalid rate limit decision')
        return RateLimitDecision(bool(result[0]), result[1], max(1, math.ceil(result[2] / 1000)), self.limit)

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
