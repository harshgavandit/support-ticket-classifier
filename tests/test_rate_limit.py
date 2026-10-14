import json
import os
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from production_modules.rate_limit import RedisRateLimiter, RateLimitUnavailable


def make_limiter(result=None, status=200):
    def handler(request):
        command = json.loads(request.content)
        assert command[0] == 'EVAL'
        assert command[2] == '1'
        assert '192.0.2.1' not in command[3]
        assert request.headers['Authorization'] == 'Bearer test-token'
        return httpx.Response(status, json={'result': result} if result is not None else {'error': 'unavailable'})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return RedisRateLimiter('https://test.upstash.io', 'test-token', limit=2, window_seconds=60, client=client)


def test_allowed_request_has_remaining_and_reset():
    with make_limiter([1, 1, 59900]) as limiter:
        result = limiter.check('192.0.2.1')
    assert result.allowed
    assert result.remaining == 1
    assert result.retry_after == 60


def test_exhausted_window_returns_denial():
    with make_limiter([0, 0, 1001]) as limiter:
        result = limiter.check('192.0.2.1')
    assert not result.allowed
    assert result.retry_after == 2


@pytest.mark.parametrize('status,result', [(503, None), (200, None), (200, ['bad'])])
def test_backend_failures_do_not_allow_requests(status, result):
    with make_limiter(result, status) as limiter:
        with pytest.raises(RateLimitUnavailable):
            limiter.check('192.0.2.1')


def test_disabled_limiter_does_not_call_redis():
    limiter = RedisRateLimiter('', '', enabled=False)
    with patch.object(limiter.client, 'post') as post:
        assert limiter.check('192.0.2.1') is None
    post.assert_not_called()
    limiter.close()


def test_missing_configuration_fails_at_startup():
    with pytest.raises(ValueError, match='UPSTASH'):
        RedisRateLimiter('', '', enabled=True)


def test_configuration_typo_cannot_silently_disable_protection():
    with patch.dict(os.environ, {'RATE_LIMIT_ENABLED': 'tru'}, clear=True):
        with pytest.raises(ValueError, match='RATE_LIMIT_ENABLED'):
            RedisRateLimiter.from_env()


def test_timeout_denies_without_leaking_connection_details():
    with make_limiter([1, 1, 1000]) as limiter:
        with patch.object(limiter.client, 'post', side_effect=httpx.ConnectTimeout('private-connection-details')):
            with pytest.raises(RateLimitUnavailable) as error:
                limiter.check('192.0.2.1')
    assert 'private-connection-details' not in str(error.value)


def test_api_429_and_outage_never_call_ai():
    import main
    client = TestClient(main.app)
    with make_limiter([0, 0, 2000]) as limiter, patch.object(main, 'rate_limiter', limiter), patch('main.run_pipeline') as pipeline:
        response = client.post('/classify', json={'ticket_text': 'My order has not arrived'})
    assert response.status_code == 429
    assert response.headers['retry-after'] == '2'
    assert response.headers['x-ratelimit-remaining'] == '0'
    pipeline.assert_not_called()
    with make_limiter(status=503) as limiter, patch.object(main, 'rate_limiter', limiter), patch('main.run_pipeline') as pipeline:
        response = client.post('/classify', json={'ticket_text': 'My order has not arrived'})
    assert response.status_code == 503
    pipeline.assert_not_called()


def test_health_and_demo_do_not_consume_limit():
    import main
    with patch.object(main.rate_limiter, 'check') as check:
        client = TestClient(main.app)
        assert client.get('/health').status_code == 200
        assert client.get('/').status_code == 200
    check.assert_not_called()


def test_forwarded_header_is_not_used_as_identity():
    import main
    from production_modules.rate_limit import RateLimitDecision
    from tests.test_classifier import make_valid_classification
    with patch.object(main.rate_limiter, 'check', return_value=RateLimitDecision(True, 1, 60, 2)) as check, patch('main.run_pipeline', return_value={'classification': make_valid_classification()}):
        response = TestClient(main.app).post('/classify', headers={'X-Forwarded-For': 'spoofed'}, json={'ticket_text': 'My order has not arrived'})
    assert response.status_code == 200
    assert check.call_args.args[0] == 'testclient'
    assert response.headers['x-ratelimit-remaining'] == '1'
