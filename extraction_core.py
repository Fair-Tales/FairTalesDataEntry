"""Storage-free entry points using the application's extraction computations.

Inputs are local bytes, predicted text, and explicit runtime dependencies. No
Firestore model objects, page upload wrappers, S3 clients, or reference labels
are accepted. Imports of application helpers are lazy so offline scorers never
need to initialize the application's dependency graph.
"""

from ai_runtime import extraction_runtime


def attempt_page_extraction(image_bytes, client, ai_settings, *, label,
                            vision_json_call=None, strict=False):
    """Shared OCR computation; the app wrapper adds its error-log writes.

    Returns the application's existing ``('ok', (text, story, type))`` or
    ``('error', (error_type, message))`` contract. ``vision_json_call`` keeps the
    UI wrapper's injectable helper boundary without duplicating the algorithm.
    """
    import anthropic
    from text_content import AIPrompts

    if vision_json_call is None:
        from utilities import vision_json
        vision_json_call = vision_json
    try:
        data, raw = vision_json_call(
            client, [image_bytes], AIPrompts.page_extraction, downscale=True,
            model=ai_settings.get('extraction_model'),
            max_edge=ai_settings.get('extraction_max_edge'),
            max_tokens=ai_settings.get('extraction_max_tokens'),
            label=label,
        )
    except anthropic.AnthropicError as exc:
        return 'error', (type(exc).__name__, str(exc))

    if not isinstance(data, dict):
        snippet = (raw or '').strip()
        message = (
            f'Model reply could not be parsed as usable JSON: {snippet[:500]}'
            if snippet else 'Model returned no usable reply.'
        )
        return 'error', ('parse_error', message)

    raw_text = data.get('text')
    if strict and (
        not isinstance(data.get('has_text'), bool)
        or not isinstance(data.get('is_story_page'), bool)
        or not (isinstance(raw_text, str)
                or (data.get('has_text') is False and raw_text is None))
    ):
        return 'error', ('parse_error',
                         'Expected has_text/is_story_page booleans and usable text')
    text = raw_text.strip() if isinstance(raw_text, str) else ''
    if not bool(data.get('has_text', True)):
        text = ''
    return 'ok', (
        text, bool(data.get('is_story_page', False)), data.get('page_type', ''),
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
    else:
        result.update(text=None, contains_story=None,
                      error_type=payload[0], error=payload[1])
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
        text = page.get('text')
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
        )
