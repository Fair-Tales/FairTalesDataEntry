"""Messages used by the offline pipeline runner and structured parsers."""

BILLING_UNVERIFIED = (
    "Paid extraction requires confirmed subscription-credit billing, organisation, "
    "project, and the SHA-256 fingerprint of the dedicated evaluation key."
)
BILLING_KEY_MISSING = "The dedicated evaluation key is absent; no alternate key will be used."
BILLING_KEY_MISMATCH = "The evaluation key does not match the confirmed billing account; dispatch refused."
BILLING_EXHAUSTED = (
    "Subscription credits are exhausted or billing access was rejected. "
    "The run has stopped; completed work is preserved and remaining work is pending. "
    "No key switch or automatic billing retry is permitted."
)
CONTEXT_INVALID = "Context classification must contain exactly one valid decision for every supplied page."
CONTEXT_LIMIT = "The complete book exceeds a pinned context request limit; provisional classification retained."
CONTEXT_PENDING = "Waiting for all page stages before whole-book context classification."
PAGE_SCHEMA_INVALID = "Expected has_text/is_story_page booleans and usable text"
PAGE_REPLY_INVALID = "Model reply could not be parsed as usable JSON: {snippet}"
PAGE_REPLY_EMPTY = "Model returned no usable reply."
CONTEXT_TARGET = "\nTarget ordered images and OCR (image_position is one-based):\n{pages_json}"
RUN_STOPPED = "Run stopped ({reason}): {detail}"
