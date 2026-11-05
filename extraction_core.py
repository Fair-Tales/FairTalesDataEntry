"""Storage-free entry points using the application's extraction computations.

Inputs are local bytes, predicted text, and explicit runtime dependencies. No
Firestore model objects, page upload wrappers, S3 clients, or reference labels
are accepted. Imports of application helpers are lazy so offline scorers never
need to initialize the application's dependency graph.
"""

from ai_runtime import extraction_runtime


class PageExtractionPayload(tuple):
    """Legacy three-item result with additive archival/projection evidence."""

    def __new__(cls, text, contains_story, page_type, details=None):
        result = super().__new__(cls, (text, contains_story, page_type))
        result.details = details or {}
        return result


class ProcessedPagePayload(tuple):
    """Five-item uploader result, retaining the OCR evidence sidecar."""

    def __new__(cls, payload, corrected, rotation_uncertain):
        result = super().__new__(cls, (*payload, corrected, rotation_uncertain))
        result.details = getattr(payload, 'details', {})
        return result


class PageFailurePayload(tuple):
    """Failure remains a two-item tuple while retaining rejected model output."""

    def __new__(cls, error_type, message, details=None):
        result = super().__new__(cls, (error_type, message))
        result.details = details or {}
        return result


def attempt_page_extraction(image_bytes, client, ai_settings, *, label,
                            vision_json_call=None, strict=False):
    """OCR one page image and return a classified outcome — with NO Firestore
    logging or session access, so it is callable from the background
    page-processing worker (#179), standalone evaluation, and
    :func:`pages.uploader.extract_page_info` (which adds logging + raise).

    Returns ``('ok', (text, is_story_page, page_type))`` on success, or
    ``('error', (error_type, error_message))`` on an Anthropic API error or a
    reply that cannot be parsed as usable JSON.

    ``ai_settings`` is a plain validated settings dict, read by the CALLER (the
    worker thread must not touch the ``get_ai_settings`` Streamlit cache, #179).
    Required keys are ``extraction_model``, ``extraction_max_edge``, and
    ``extraction_max_tokens``; a missing key raises ``KeyError`` before the
    vision call in both application and evaluation paths.

    Uses the shared ``vision_json`` helper (#129) with the admin-configured
    extraction model/resolution/tokens (#135). The image is downscaled for the
    vision call and JPEG re-encoded below Claude's 10 MB per-image limit (#134).
    ``vision_json_call`` keeps the UI wrapper's injectable helper boundary
    without duplicating the algorithm.

    A contradictory ``has_text=false`` never clears nonempty transcription.
    Preserve the raw response, flag the inconsistency and derive analytical
    text separately. Legacy tuple consumers still receive the same three fields.
    """
    import anthropic
    # Lazy to avoid loading the data_structures package's application dependency
    # graph when offline scorers import this module (#129).
    from data_structures.extraction_error_log import ExtractionErrorLog
    from text_content.pipeline_prompts import prompt_for, prompt_request_manifest
    from text_content.pipeline_runtime import (
        PAGE_SCHEMA_INVALID, PAGE_REPLY_INVALID, PAGE_REPLY_EMPTY,
    )

    if vision_json_call is None:
        from utilities import vision_json
        vision_json_call = vision_json
    try:
        prompt_variant = ai_settings.get('prompt_variant', 'rules-plus-icl')
        prompt = prompt_for('page_extraction', prompt_variant)
        data, raw = vision_json_call(
            client, [image_bytes], prompt, downscale=True,
            model=ai_settings['extraction_model'],
            max_edge=ai_settings['extraction_max_edge'],
            max_tokens=ai_settings['extraction_max_tokens'],
            label=label, reject_partial=True,
        )
    except anthropic.AnthropicError as exc:
        return 'error', (type(exc).__name__, str(exc))

    if not isinstance(data, dict):
        snippet = (raw or '').strip()
        message = (
            PAGE_REPLY_INVALID.format(snippet=snippet[:500])
            if snippet else PAGE_REPLY_EMPTY
        )
        return 'error', PageFailurePayload(ExtractionErrorLog.ERROR_PARSE, message, {
            'raw_page_output': data, 'raw_response': raw,
            'failure_kind': getattr(raw, 'failure_kind', None),
        })

    raw_text = data.get('text')
    if strict and (
        not isinstance(data.get('has_text'), bool)
        or not isinstance(data.get('is_story_page'), bool)
        or not (isinstance(raw_text, str)
                or (data.get('has_text') is False and raw_text is None))
    ):
        return 'error', PageFailurePayload(ExtractionErrorLog.ERROR_PARSE,
                         PAGE_SCHEMA_INVALID, {'raw_page_output': data, 'raw_response': raw})
    text = raw_text.strip() if isinstance(raw_text, str) else ''
    from conventions import convention_metadata, project_analysis_text
    flags = []
    if data.get('has_text') is False and text:
        flags.append('has_text_conflict')
    regions = data.get('regions')
    valid_regions = isinstance(regions, list) and all(
        isinstance(region, dict) and isinstance(region.get('kind'), str) and region.get('kind') in {
            'narrative', 'informational', 'illustration_lettering', 'paratext'
        } and isinstance(region.get('text'), str) for region in regions
    )
    if valid_regions and '\n\n'.join(region.get('text', '') for region in regions).strip() != text:
        flags.append('region_text_conflict')
        valid_regions = False
    if regions is not None and not valid_regions:
        flags.append('invalid_regions')
    page_schema = ai_settings.get('page_schema_version', 'legacy-compatible-page-v1')
    page_types = {'cover', 'title', 'copyright', 'dedication', 'contents', 'narrative',
                  'informational', 'wordless_narrative', 'endpaper', 'blank',
                  'glossary_index', 'supplemental_activity', 'other_back_matter', 'uncertain'}
    if (page_schema == 'page-regions-v1' and
            (not valid_regions or not isinstance(data.get('page_type'), str)
             or data.get('page_type') not in page_types
             or type(data.get('story_needs_context')) is not bool)):
        return 'error', PageFailurePayload(ExtractionErrorLog.ERROR_PARSE,
            PAGE_SCHEMA_INVALID, {'raw_page_output': data, 'raw_response': raw,
                                  'validation_flags': flags, 'page_schema_version': page_schema})
    details = {
        'page_schema_version': page_schema,
        'raw_page_output': data, 'raw_response': raw,
        'regions': regions if valid_regions else [],
        'regions_status': 'complete' if valid_regions else 'unavailable',
        'has_text': bool(text), 'model_has_text': data.get('has_text'),
        'story_needs_context': data.get('story_needs_context') is True,
        'provisional_classification': {
            'contains_story': bool(data.get('is_story_page', False)),
            'page_type': data.get('page_type', ''),
        },
        'validation_flags': flags,
        'conventions': convention_metadata(),
        'prompt_manifest': prompt_request_manifest('page_extraction', prompt_variant, prompt),
    }
    details.update(project_analysis_text({'text': text, **details}))
    return 'ok', PageExtractionPayload(
        text, bool(data.get('is_story_page', False)), data.get('page_type', ''), details,
    )


def extract_page(image_bytes, client, settings, usage_sink):
    """Correct and OCR a local photograph with no application storage access."""
    from image_processing import correct_page_image, exif_transpose_bytes

    with extraction_runtime(client=client, settings=settings, usage_sink=usage_sink):
        normalized = exif_transpose_bytes(image_bytes)
        image, corrected, method, uncertain = correct_page_image(
            normalized, client, settings,
        )
        outcome, payload = attempt_page_extraction(
            image, client, settings, label='page_extraction', strict=True,
        )
    result = {
        'status': 'completed' if outcome == 'ok' else 'failed',
        'method': method,
        'rotation_uncertain': uncertain,
    }
    if corrected is not None:
        result['corrected_bytes'] = corrected
    if outcome == 'ok':
        result.update(text=payload[0], contains_story=payload[1], page_type=payload[2])
        result.update(getattr(payload, 'details', {}))
    else:
        result.update(text=None, contains_story=None,
                      error_type=payload[0], error=payload[1])
        result.update(getattr(payload, 'details', {}))
    return result


def extract_metadata(pages, client, settings, usage_sink, isbn_lookup):
    """Run photo-first metadata on original ordered photos, with cached ISBN IO.

    ``pages`` contains actual ``(page_number, bytes)`` pairs. The production
    locator uses positions; returned source attributions additionally retain
    actual page numbers so gaps in the photographed sequence are not hidden.
    An explicit lookup callable is required even when ISBN lookup is disabled
    (pass a local callable returning ``None`` in a named no-lookup variant).
    """
    if not callable(isbn_lookup):
        raise TypeError('Metadata extraction requires an explicit isbn_lookup')
    pages = sorted(list(pages), key=lambda page: page[0])
    if not pages:
        raise ValueError('Metadata extraction requires at least one image')
    with extraction_runtime(client=client, settings=settings, usage_sink=usage_sink):
        from utilities import extract_photo_first_metadata
        metadata = extract_photo_first_metadata(
            pages, client, isbn_lookup=isbn_lookup,
        )
    for source in metadata.get('sources', {}).values():
        position = source.get('page_position')
        if isinstance(position, int) and 1 <= position <= len(pages):
            source['page_number'] = pages[position - 1][0]
    return metadata


def extract_characters(pages, client, settings, usage_sink):
    """Detect entities from successful *predicted* story text only.

    The absence of predicted story text yields no entities without a paid call,
    matching the background pipeline. Reference labels/text never enter here.
    """
    story_pages = []
    for page in pages:
        from conventions import project_analysis_text
        text = project_analysis_text(page).get('analysis_text')
        if (page.get('status', 'completed') == 'completed'
                and page.get('contains_story') is True
                and isinstance(text, str) and text.strip()):
            number = page.get('page_number')
            if not isinstance(number, int) or isinstance(number, bool):
                raise ValueError('Predicted page_number must be an integer')
            story_pages.append((number, text))
    if not story_pages:
        return []
    with extraction_runtime(client=client, settings=settings, usage_sink=usage_sink):
        from utilities import detect_book_characters
        return detect_book_characters(
            sorted(story_pages), client,
            model=settings.get('character_detection_model'),
            prompt_variant=settings.get('prompt_variant', 'rules-plus-icl'),
        )


def classify_book_context(pages, images, client, settings, usage_sink):
    """One bounded classification pass; OCR and provisional labels are immutable.

    ``pages`` is predicted OCR only; ``images`` contains all actual numbered
    photographs. A malformed or oversized response records an explicit fallback.
    A valid null decision remains unresolved, excluding the page from characters.
    """
    import json
    import anthropic
    from text_content.pipeline_prompts import prompt_for, prompt_request_manifest
    from text_content.pipeline_runtime import CONTEXT_INVALID, CONTEXT_LIMIT, CONTEXT_TARGET
    from conventions import convention_metadata
    from utilities import vision_json

    pages = sorted(pages, key=lambda page: page.get('page_number', 0))
    images = sorted(images, key=lambda row: row[0])
    numbers = [page.get('page_number') for page in pages]
    result = {'conventions': convention_metadata(), 'pages': [], 'fallback': True}
    if (any(type(number) is not int for number in numbers)
            or len(set(numbers)) != len(numbers)
            or [number for number, _ in images] != numbers):
        return {**result, 'status': 'failed', 'error_type': 'context_input_coverage',
                'error': CONTEXT_INVALID}
    variant = settings.get('prompt_variant', 'rules-plus-icl')
    rows = [{'page_number': page.get('page_number'), 'image_position': index + 1,
             'text': page.get('text'), 'ocr_status': page.get('status', 'completed')}
            for index, page in enumerate(pages)]
    prompt = prompt_for('story_classification', variant) + CONTEXT_TARGET.format(
        pages_json=json.dumps(rows, ensure_ascii=False))
    result['prompt_manifest'] = prompt_request_manifest('story_classification', variant, prompt)
    model = settings.get('story_context_model', 'claude-sonnet-5')
    max_tokens = settings.get('story_context_max_tokens', 8192)
    limits = getattr(client, 'prices', {}).get(model) or settings.get('story_context_limits')

    class ContextLimit(ValueError):
        pass

    def validate(request):
        if not isinstance(limits, dict) or not all(
                isinstance(limits.get(key), int) and limits.get(key) > 0
                for key in ('max_images', 'max_request_bytes', 'max_input_tokens', 'max_output_tokens')):
            raise ContextLimit(CONTEXT_LIMIT)
        # UTF-8 byte count bounds text tokens conservatively; each 784px image
        # reserves 1,568 tokens. Reject before dispatch instead of truncating.
        input_bound = len(prompt.encode('utf-8')) + len(images) * 1568 + 1024
        if (len(images) > min(100, limits.get('max_images'))
                or len(json.dumps(request, ensure_ascii=False).encode('utf-8'))
                > min(32_000_000, limits.get('max_request_bytes'))
                or input_bound + max_tokens > limits.get('max_input_tokens')
                or max_tokens > limits.get('max_output_tokens')):
            raise ContextLimit(CONTEXT_LIMIT)
        result['request_input_token_upper_bound'] = input_bound

    try:
        with extraction_runtime(client=client, settings=settings, usage_sink=usage_sink):
            data, raw = vision_json(client, [image for _, image in images], prompt,
                                    model=model, max_tokens=max_tokens, max_edge=784,
                                    label='story_context', request_validator=validate,
                                    reject_partial=True)
    except ContextLimit as exc:
        return {**result, 'status': 'failed', 'error_type': 'context_request_limit', 'error': str(exc)}
    except anthropic.AnthropicError as exc:
        return {**result, 'status': 'failed', 'error_type': type(exc).__name__, 'error': str(exc)}
    result.update(raw_response=raw, raw_context_output=data)
    decisions = data.get('pages') if isinstance(data, dict) else None
    page_types = {'cover', 'title', 'copyright', 'dedication', 'contents', 'narrative',
                  'informational', 'wordless_narrative', 'endpaper', 'blank',
                  'glossary_index', 'supplemental_activity', 'other_back_matter', 'uncertain'}
    valid = isinstance(decisions, list) and len(decisions) == len(numbers)
    if valid:
        seen = []
        for decision in decisions:
            if not isinstance(decision, dict):
                valid = False
                break
            number = decision.get('page_number')
            label = decision.get('contains_story')
            state = decision.get('decision')
            if (type(number) is not int or number not in numbers or number in seen
                    or not isinstance(decision.get('page_type'), str)
                    or decision.get('page_type') not in page_types
                    or not isinstance(decision.get('evidence'), str)
                    or not ((state == 'resolved' and type(label) is bool)
                            or (state == 'review_required' and label is None))):
                valid = False
                break
            seen.append(number)
    if not valid:
        return {**result, 'status': 'failed', 'error_type': 'context_invalid_output', 'error': CONTEXT_INVALID}
    return {**result, 'status': 'completed', 'fallback': False, 'pages': decisions,
            'unresolved_pages': sum(row.get('contains_story') is None for row in decisions)}


def apply_context_classification(pages, context):
    """Return copies; never delete or revise OCR while replacing labels."""
    decisions = {row.get('page_number'): row for row in context.get('pages', [])}
    valid = context.get('status') == 'completed' and context.get('fallback') is False
    result = []
    for page in pages:
        updated = dict(page)
        updated.setdefault('provisional_classification', {
            'contains_story': page.get('contains_story'), 'page_type': page.get('page_type')})
        decision = decisions.get(page.get('page_number')) if valid else None
        if decision is not None:
            updated.update(contains_story=decision.get('contains_story'),
                           page_type=decision.get('page_type'), context_classification=dict(decision),
                           classification_source='book_context')
        else:
            updated['classification_source'] = 'provisional_fallback'
        result.append(updated)
    return result


def reconcile_metadata(metadata, pages, images, client, settings, usage_sink):
    """Defer evidence-ranked fallback until predicted OCR is available."""
    from utilities import finalize_photo_metadata
    with extraction_runtime(client=client, settings=settings, usage_sink=usage_sink):
        return finalize_photo_metadata(metadata, pages, client, images_by_number=dict(images))
