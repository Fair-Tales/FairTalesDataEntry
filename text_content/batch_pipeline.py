"""Text for durable bulk upload, recovery and progress."""
class BatchPipeline:
    rollout_disabled = 'Durable batch upload is awaiting pilot verification. An administrator can enable BATCH_UPLOAD_DURABLE_ENABLED after completing the deployment checks.'
    resume_header = 'Resume a saved batch'
    resume_label = 'Saved batch'
    resume_option = '{batch_id} — {state} — {count} photos'
    resume_button = 'Open saved batch'
    new_batch = 'Start a new batch'
    saved = 'Batch {batch_id}. Accepted chunks are preserved so you can return later.'
    save_failed = 'This step could not finish. Your saved progress is retained. Please retry. Details: {error}'
    detecting_resume = 'The sources are saved. Continue splitting and metadata extraction.'
    continue_detection = 'Continue detection'
    boundaries = 'Photos {first}–{last}. Check the first and last photo belong to the same book.'
    review_title = 'Book title'
    review_accept = 'I checked the book boundaries and titles above.'
    metadata_pending = 'Metadata may be incomplete or the previous call was interrupted. Review the title and details before creating this book.'
    isbn_lookup_failures = '{count} books had no ISBN enrichment because the lookup failed. Review their details and fill in any gaps.'
    progress_header = 'Batch processing'
    progress_intro = 'Pages and whole-book character and alias suggestions are processed automatically. Leave this page open for finalization, or reopen the saved batch later.'
    progress_row = '{title}: {state}; pages {done}/{total}, failed {failed}; characters: {characters}; saved pages: {finalization}'
    refresh = 'Refresh progress'
    resume_work = 'Resume unfinished work'
    retry_book = 'Retry failed stages'
    cancel_book = 'Cancel processing'
    review_book = 'Review this book and its characters'
    unknown_response = 'A previous paid call may have completed before its result was saved. Retrying may incur another charge.'
    not_available = 'not available'
    state_labels = {
        'uploading': 'uploading', 'detecting': 'detecting books', 'review': 'awaiting confirmation',
        'processing': 'processing', 'pending': 'pending', 'queued': 'queued',
        'waiting': 'awaiting unfinished stages', 'suggestions': 'suggestions ready', 'running': 'processing', 'staging': 'saving source photos', 'failed': 'failed',
        'blocked': 'blocked', 'partial': 'needs attention', 'complete': 'complete',
        'done': 'complete', 'cancelled': 'cancelled', 'finalized': 'saved',
        'success': 'suggestions ready', 'empty': 'no characters found',
        'no_text': 'no usable story text', 'skipped': 'no usable story text',
        'incomplete': 'incomplete coverage', 'stale': 'outdated suggestions',
    }
