"""Thread-local adapter for shared extraction and explicit usage persistence.

The guarded computation modules already support ``extraction_runtime``. Entering
that context keeps their settings/usage lookups away from Streamlit without
patching globals (including those used by concurrent offline evaluations).
"""
from contextlib import contextmanager
import logging

from google.api_core.exceptions import GoogleAPIError
from ai_runtime import extraction_runtime

logger = logging.getLogger(__name__)
API_TIMEOUT_SECONDS = 90


def usage_recorder(db, job_id, batch_id=None):
    """Use the shared daily rollup with the worker's explicit database client."""
    from utilities import api_usage_update

    def record(response, model, label, *, provider=None):
        try:
            data = api_usage_update(response, model, label, provider=provider)
            db.collection('api_usage').document(data['date']).set(data, merge=True)
            if job_id:
                db.collection('pipeline_jobs').document(job_id).set(
                    {'usage': data['by_model'][model]}, merge=True,
                )
            if batch_id:
                db.collection('photo_batches').document(batch_id).set(
                    {'usage': data['by_model'][model]}, merge=True,
                )
        except (GoogleAPIError, OSError, ValueError, TypeError, AttributeError) as exc:
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
