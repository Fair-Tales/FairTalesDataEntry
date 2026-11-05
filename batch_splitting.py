"""Conservative validation around the existing batch separator/cover helpers.

A false negative stops for review; a false positive silently invents half-books.
Keep the older detector for compatibility while requiring every pixel in a new
batch separator to be dark. All retained pages must decode before any AI work.
"""

from io import BytesIO

from PIL import Image, UnidentifiedImageError

from image_processing import is_black_frame
from text_content.batch_upload import BatchUpload

SPLIT_VERSION = "conservative-black-v1"
MAX_COVER_FALLBACK_PAGES = 60


class BatchSplitError(ValueError):
    """The source set cannot safely be split without researcher intervention."""


def _is_separator(name, data):
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise BatchSplitError(BatchUpload.corrupt.format(name=name)) from exc
    if is_black_frame(data, mean_threshold=5.0, percentile=100, percentile_threshold=18.0):
        return True
    if is_black_frame(data):
        raise BatchSplitError(BatchUpload.ambiguous.format(name=name))
    return False


def split_photo_batch(pages, client=None, *, cover_locator=None):
    """Compatibility adapter for callers holding ``(name, image_bytes)`` pairs."""
    sources = [
        {"name": name, "position": position, "image": data}
        for position, (name, data) in enumerate(pages, 1)
    ]
    result = split_source_manifest(
        sources, lambda source: source.get("image"), client, cover_locator=cover_locator,
    )
    names = {source.get("position"): source.get("name") for source in sources}
    return {
        **result,
        "groups": [[(source.get("name"), source.get("image")) for source in group]
                   for group in result.get("groups", [])],
        "separators": [names.get(position) for position in result.get("separators", [])],
    }


def split_source_manifest(sources, read_bytes, client=None, *, cover_locator=None):
    """Stream original images once; return groups containing only source refs.

    At most 60 small thumbnails are retained for the existing cover fallback.
    A detected separator discards those thumbnails because fallback is unneeded.
    ``read_bytes(source)`` is injected, keeping tests entirely offline.
    """
    from image_processing import downscale_for_vision

    sources = list(sources)
    groups, current, separators, thumbnails = [], [], [], []
    for source in sources:
        name = source.get("name", str(source.get("position", "")))
        data = read_bytes(source)
        if _is_separator(name, data):
            separators.append(source.get("position"))
            thumbnails.clear()
            if current:
                groups.append(current)
                current = []
        else:
            current.append(source)
            if client is not None and not separators and len(sources) <= MAX_COVER_FALLBACK_PAGES:
                thumbnails.append((name, downscale_for_vision(data, max_edge=512)))
    if current:
        groups.append(current)
    if separators:
        if not groups:
            raise BatchSplitError(BatchUpload.all_black)
        return {"groups": groups, "method": "black_frame", "separators": separators, "version": SPLIT_VERSION}
    if not sources:
        return {"groups": [], "method": "single", "separators": [], "version": SPLIT_VERSION}
    if client is None:
        return {"groups": [sources], "method": "single", "separators": [], "version": SPLIT_VERSION}
    if len(sources) > MAX_COVER_FALLBACK_PAGES:
        raise BatchSplitError(BatchUpload.fallback_limit.format(limit=MAX_COVER_FALLBACK_PAGES))
    if cover_locator is None:
        from utilities import locate_cover_pages
        cover_locator = locate_cover_pages
    covers = cover_locator(thumbnails, client)
    if not isinstance(covers, list) or any(type(value) is not int or not 1 <= value <= len(sources) for value in covers):
        raise BatchSplitError(BatchUpload.bad_covers)
    boundaries = sorted({1, *covers})
    groups = [sources[start - 1:end - 1] for start, end in zip(boundaries, boundaries[1:] + [len(sources) + 1])]
    return {"groups": groups, "method": "cover_page" if len(groups) > 1 else "single", "separators": [], "version": SPLIT_VERSION}
