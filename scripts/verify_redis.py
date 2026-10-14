"""Opt-in live Redis verification; AI calls are mocked, keys expire automatically."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sys
import time
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from production_modules.rate_limit import RedisRateLimiter
from fastapi.testclient import TestClient
import main
from tests.test_classifier import make_valid_classification


def verify():
    base = main.rate_limiter
    prefix = f'support-ticket:verification:{uuid4().hex}'
    kwargs = dict(limit=2, window_seconds=3, prefix=prefix)
    report = {'redis_ping': base.ping(), 'concurrent_attempts': 12}
    with RedisRateLimiter(base.url, base.token, **kwargs) as first:
        with ThreadPoolExecutor(max_workers=6) as pool:
            decisions = list(pool.map(first.check, ['concurrent-test'] * 12))
        allowed = sum(item.allowed for item in decisions)
        assert allowed == 2, f'Expected exactly 2 admissions, got {allowed}'
        report['concurrent_admitted'] = allowed
        report['concurrent_denied'] = len(decisions) - allowed
        with RedisRateLimiter(base.url, base.token, **kwargs) as second:
            assert not second.check('concurrent-test').allowed
            report['counter_shared_between_instances'] = True

        client = TestClient(main.app)
        with patch.object(main, 'rate_limiter', first), patch('main.run_pipeline', return_value={'classification': make_valid_classification()}) as pipeline:
            responses = [client.post('/classify', json={'ticket_text': 'Synthetic delivery ticket'}) for _ in range(3)]
            assert [r.status_code for r in responses] == [200, 200, 429]
            assert pipeline.call_count == 2
            assert responses[-1].headers['x-ratelimit-remaining'] == '0'
            assert int(responses[-1].headers['retry-after']) > 0
            report['api_statuses'] = [r.status_code for r in responses]
            report['ai_pipeline_calls'] = pipeline.call_count
            report['denial_headers'] = {key: value for key, value in responses[-1].headers.items() if key.startswith('x-ratelimit') or key == 'retry-after'}
            time.sleep(3.1)
            recovery = client.post('/classify', json={'ticket_text': 'Synthetic delivery ticket'})
            assert recovery.status_code == 200
            assert recovery.headers['x-ratelimit-remaining'] == '1'
            report['window_recovery_status'] = recovery.status_code
    report['result'] = 'PASS'
    output = Path(__file__).resolve().parents[1] / 'runtime' / 'redis-verification.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    verify()
