"""Submit dry-run misses and populate the ordinary response cache."""
import json
import time

from text_content.evaluation import BatchText as T
from .gateway import request_ceiling, response_dict, usage_charge, validate_prices
from .store import atomic_json, guard_cached_request, read_json


def run_batch(out, ledger, config, client, *, resume=False, wait=False, sleep=time.sleep):
    if config.get('provider', 'anthropic-messages') not in {'anthropic', 'anthropic-messages'}:
        raise ValueError(T.anthropic_only)
    prices = validate_prices(config, executing=True)
    path = out / 'batch.json'
    state = read_json(path) if path.exists() else None
    if not resume and state and state['status'] != 'ended':
        raise ValueError(T.pending)
    if resume and (not state or not state.get('id')):
        raise ValueError(T.no_id)
    requests = {}
    with (out / 'batch-requests.jsonl').open(encoding='utf-8') as handle:
        for line in handle:
            guard_cached_request(line, 'batch_requests')
            row = json.loads(line)
            key = row.pop('key')
            if row.get('provider', 'anthropic-messages') not in {'anthropic', 'anthropic-messages'}:
                raise ValueError(T.anthropic_only)
            requests[key] = row
    if not resume:
        requests = {key: row for key, row in requests.items()
                    if not (ledger.get(key) or {}).get('state') == 'completed'}
        if not requests:
            raise ValueError(T.empty)
        # Short documented-style IDs; the SHA-256 cache identity stays local.
        keys = {f'request-{i}': key for i, key in enumerate(requests)}
        payload = [{'custom_id': name, 'params': requests[key]['request']}
                   for name, key in keys.items()]
        if len(payload) > 100_000 or len(json.dumps({'requests': payload}).encode()) > 256_000_000:
            raise ValueError(T.too_large)
        ledger.reserve_many([(key, row, request_ceiling(row['request'], prices) / 2)
                             for key, row in requests.items()])
        state = {'id': None, 'status': 'submitting', 'keys': keys, 'results': {}}
        atomic_json(path, state)
        # No automatic retry: a lost creation response may already have spent money.
        try:
            batch = client.messages.batches.create(requests=payload)
        except BaseException as exc:
            # A definite client rejection created no batch. Transport failures
            # keep the reservation and submitting marker for reconciliation.
            if getattr(exc, 'status_code', None) in {400, 401, 403, 404, 413, 422, 429}:
                for key in requests:
                    ledger.release_batch(key)
                state['status'] = 'ended'
                atomic_json(path, state)
            raise
        state.update(id=batch.id, status='pending')
        atomic_json(path, state)
    if state['status'] == 'ended':
        return state
    while True:
        batch = client.messages.batches.retrieve(state['id'])
        if batch.processing_status == 'ended':
            break
        if not wait:
            return state
        sleep(60)
    for item in client.messages.batches.results(state['id']):
        key = state['keys'][item.custom_id]
        if key in state['results']:
            continue
        result = item.result
        if result.type == 'succeeded':
            cached = ledger.get(key)
            if cached is None:
                raise ValueError(T.missing_reservation)
            if cached['state'] != 'completed':
                raw = {**response_dict(result.message), '_batch_id': state['id']}
                ledger.capture_response(key, raw)
                rate = prices[requests[key]['request']['model']]
                ledger.settle(key, raw, usage_charge(raw, rate, batch=True))
        elif result.type in {'errored', 'expired', 'canceled'}:
            ledger.release_batch(key)
        else:
            raise ValueError(T.unknown_result.format(status=result.type))
        state['results'][key] = result.type
        atomic_json(path, state)
    if set(state['results']) != set(state['keys'].values()):
        raise ValueError(T.incomplete)
    state['status'] = 'ended'
    atomic_json(path, state)
    return state
