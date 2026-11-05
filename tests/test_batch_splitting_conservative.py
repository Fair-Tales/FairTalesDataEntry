"""Conservative separator decisions, corrupt input, and bounded AI fallback."""
from io import BytesIO

import pytest
from PIL import Image

from batch_splitting import BatchSplitError, split_photo_batch, split_source_manifest


def picture(colour=255, *, bright_pixel=False):
    image = Image.new("RGB", (100, 100), (colour,) * 3)
    if bright_pixel:
        image.putpixel((50, 50), (255, 255, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("levels, lengths", [
    ([0, 255, 0, 255, 0], [1, 1]),
    ([255, 0, 0, 255], [1, 1]),
    ([255, 255], [2]),
])
def test_edge_and_consecutive_separators_do_not_create_empty_books(levels, lengths):
    result = split_photo_batch([(str(i), picture(level)) for i, level in enumerate(levels)])
    assert [len(group) for group in result["groups"]] == lengths


def test_all_black_input_is_a_visible_failure():
    with pytest.raises(BatchSplitError, match="only black"):
        split_photo_batch([("separator", picture(0))])


def test_corrupt_photo_blocks_before_any_cover_call():
    def forbidden(*args):
        pytest.fail("AI must not see corrupt input")

    with pytest.raises(BatchSplitError, match="cannot be decoded"):
        split_photo_batch([("broken", b"corrupt")], object(), cover_locator=forbidden)


def test_tiny_bright_title_on_black_cover_cannot_silently_split():
    with pytest.raises(BatchSplitError, match="not an unambiguous"):
        split_photo_batch([("dark-cover", picture(0, bright_pixel=True))])


def test_near_black_photo_requires_review():
    with pytest.raises(BatchSplitError, match="very dark"):
        split_photo_batch([("dim", picture(8))])


@pytest.mark.parametrize("boundaries", [[0], [3], [True], [1.5], "1"])
def test_invalid_fallback_boundaries_fail_loudly(boundaries):
    with pytest.raises(BatchSplitError, match="invalid boundaries"):
        split_photo_batch([("page", picture())], object(), cover_locator=lambda *_: boundaries)


def test_large_batch_requires_separators_before_ai_fallback():
    with pytest.raises(BatchSplitError, match="limited to 60"):
        split_photo_batch([(str(i), picture()) for i in range(61)], object())


def test_streaming_split_reads_each_original_once_and_returns_references():
    sources = [{"position": i, "name": f"page_{i}.jpg", "key": f"source/{i}"} for i in range(1, 65)]
    reads = []

    def read(source):
        reads.append(source["position"])
        return picture(0 if source["position"] == 62 else 255)

    result = split_source_manifest(sources, read, client=object())
    assert reads == list(range(1, 65))
    assert [len(group) for group in result["groups"]] == [61, 2]
    assert result["separators"] == [62]
    assert result["groups"][0][0] is sources[0]


def test_streaming_fallback_uses_existing_injected_locator():
    sources = [{"position": i, "name": str(i)} for i in range(1, 4)]
    called = []

    def locator(pages, client):
        called.append(pages)
        return [1, 3]

    result = split_source_manifest(sources, lambda _: picture(), object(), cover_locator=locator)
    assert [len(group) for group in result["groups"]] == [2, 1]
    assert result["method"] == "cover_page"
    assert len(called) == 1


def test_truncated_but_identifiable_jpeg_is_rejected():
    image = Image.new("RGB", (100, 100), "white")
    output = BytesIO()
    image.save(output, format="JPEG")
    truncated = output.getvalue()[:-40]
    with pytest.raises(BatchSplitError, match="cannot be decoded"):
        split_photo_batch([("truncated.jpg", truncated)])
