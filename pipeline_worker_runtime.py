"""Thread-local adapter for shared extraction and explicit usage persistence.

The guarded computation modules already support ``extraction_runtime``. Entering
that context keeps their settings/usage lookups away from Streamlit without
patching globals (including those used by concurrent offline evaluations).
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import logging

from google.api_core.exceptions import GoogleAPIError
from google.cloud import firestore

from ai_pricing import UsageAccumulator
from ai_runtime import extraction_runtime

logger = logging.getLogger(__name__)
API_TIMEOUT_SECONDS = 90


def usage_recorder(db, job_id, batch_id=None):
    """Adapt the shared cost accumulator to raw-client atomic rollup writes."""
    def record(response, model, label):
        usage = getattr(response, 'usage', None)
        if usage is None:
            logger.warning('pipeline job %s: response lacks usage (flow=%s)', job_id, label)
            return
        totals = UsageAccumulator()
        totals.record(label, model, usage)
        row = next(totals.rows())[2]
        metrics = {
            'calls': row['calls'], 'cost_usd': row['cost'],
            'input_tokens': row['input'], 'output_tokens': row['output'],
            'cache_read_tokens': row['cache_read'],
            'cache_write_tokens': row['cache_write'],
        }
        def bucket():
            return {key: firestore.Increment(value) for key, value in metrics.items()}
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        data = {'date': day, **{f'total_{k}': v for k, v in bucket().items()},
                'by_model': {model: bucket()},
                'by_flow': {(label or 'unlabelled').replace('.', '_'): bucket()}}
        try:
            db.collection('api_usage').document(day).set(data, merge=True)
            if job_id:
                db.collection('pipeline_jobs').document(job_id).set(
                    {'usage': bucket()}, merge=True,
                )
            if batch_id:
                db.collection('photo_batches').document(batch_id).set(
                    {'usage': bucket()}, merge=True,
                )
        except (GoogleAPIError, OSError, ValueError, TypeError) as exc:
            logger.warning('pipeline job %s: usage persistence failed: %s', job_id, exc)
    return record


@contextmanager
def worker_runtime(client, settings, db, job_id, batch_id=None):
    """Inject explicit dependencies; provider calls have bounded timeouts."""
    # Fake clients need not implement SDK configuration. Real clients always do.
    configure = getattr(client, 'with_options', None)
    bounded_client = configure(timeout=API_TIMEOUT_SECONDS, max_retries=1) if callable(configure) else client
    with extraction_runtime(
        client=bounded_client, settings=settings,
        usage_sink=usage_recorder(db, job_id, batch_id),
    ):
        yield bounded_client
