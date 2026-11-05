"""Strict generations, chunk ordering and recovery, with in-memory storage only."""
import json
from io import BytesIO

import pytest

from photo_upload import (
    append_batch_upload_chunk, build_batch_uploader_html,
    finish_batch_upload, get_batch_upload_state, manifest_matches,
    strict_manifest_matches,
)


class MemoryFile(BytesIO):
    def __init__(self, fs, path):
        super().__init__()
        self.fs, self.path = fs, path

    def close(self):
        if not self.closed:
            self.fs.files[self.path] = self.getvalue()
        super().close()


class MemoryFS:
    def __init__(self):
        self.files = {}

    def open(self, path, mode="rb"):
        if mode == "wb":
            return MemoryFile(self, path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return BytesIO(self.files[path])

    def ls(self, path, **kwargs):
        return [key for key in self.files if key.rsplit("/", 1)[0] == path]


def complete_chunk(fs, index, offset, count, *, failed=0, state="complete"):
    generation = f"session/chunks/{index:04d}"
    prefix = f"sawimages/uploads/batch/{generation}"
    expected = [f"page_{i}.jpg" for i in range(offset + 1, offset + count + 1)]
    manifest = {"version": 2, "generation": generation, "state": state,
                "expected": expected, "uploaded": expected, "count": count, "failed": failed}
    fs.files[f"{prefix}/manifest.json"] = json.dumps(manifest).encode()
    for name in expected:
        fs.files[f"{prefix}/{name}"] = b"photo"
    return manifest, [f"{prefix}/{name}" for name in expected]


def test_failed_legacy_manifest_and_holes_never_count_as_ready():
    assert not manifest_matches({"uploaded": ["page_1.jpg"], "failed": 1}, ["a/page_1.jpg"])
    assert not manifest_matches({"uploaded": ["page_1.jpg", "page_3.jpg"]}, ["a/page_1.jpg", "a/page_3.jpg"])
    assert not manifest_matches({"uploaded": ["page_1.jpg"], "state": "uploading"}, ["a/page_1.jpg"])


def test_strict_generation_cannot_reuse_old_complete_manifest():
    fs = MemoryFS()
    manifest, keys = complete_chunk(fs, 0, 0, 2)
    assert strict_manifest_matches(manifest, keys, generation="session/chunks/0000")
    assert not strict_manifest_matches(manifest, keys, generation="session/chunks/0001")
    assert not strict_manifest_matches(manifest, keys[:1], generation="session/chunks/0000")
    manifest["state"] = "uploading"
    assert not strict_manifest_matches(manifest, keys, generation="session/chunks/0000")


def test_a_failed_separator_blocks_full_batch_even_when_listing_is_stable():
    fs = MemoryFS()
    get_batch_upload_state(fs, "session")
    manifest, keys = complete_chunk(fs, 0, 0, 3, failed=1)
    del fs.files[keys[1]]
    with pytest.raises(ValueError, match="failed, missing"):
        finish_batch_upload(fs, "session")
    with pytest.raises(ValueError, match="failed, missing"):
        append_batch_upload_chunk(fs, "session")
    assert not get_batch_upload_state(fs, "session")["finished"]


def test_multiple_chunks_preserve_global_order_and_recover_without_browser_state():
    fs = MemoryFS()
    state = get_batch_upload_state(fs, "session")
    complete_chunk(fs, 0, 0, 60)
    staged = {}

    def stage(index, sources):
        staged[index] = sources

    state = append_batch_upload_chunk(fs, "session", on_chunk=stage)
    assert state["chunks"][-1] == {"index": 1, "offset": 60}
    assert len(staged[0]) == 60
    complete_chunk(fs, 1, 60, 3)
    sources = finish_batch_upload(fs, "session", on_chunk=stage)
    assert [s["position"] for s in sources] == list(range(1, 64))
    assert [s["name"] for s in sources][-3:] == ["page_61.jpg", "page_62.jpg", "page_63.jpg"]
    assert sources == finish_batch_upload(fs, "session", on_chunk=stage)
    assert get_batch_upload_state(fs, "session")["finished"]
    with pytest.raises(ValueError):
        append_batch_upload_chunk(fs, "session")


def test_durable_staging_failure_prevents_chunk_advancement():
    fs = MemoryFS()
    get_batch_upload_state(fs, "session")
    complete_chunk(fs, 0, 0, 2)

    def fail_stage(index, sources):
        raise OSError("copy failed")

    with pytest.raises(OSError, match="copy failed"):
        append_batch_upload_chunk(fs, "session", on_chunk=fail_stage)
    assert len(get_batch_upload_state(fs, "session")["chunks"]) == 1
    assert not get_batch_upload_state(fs, "session")["finished"]


def test_chunk_holes_and_duplicate_manifest_entries_are_rejected():
    fs = MemoryFS()
    manifest, keys = complete_chunk(fs, 0, 0, 2)
    manifest["uploaded"] = ["page_1.jpg", "page_1.jpg"]
    assert not strict_manifest_matches(manifest, keys, generation="session/chunks/0000")
    manifest["uploaded"] = manifest["expected"]
    manifest["expected"] = ["page_1.jpg", "page_3.jpg"]
    assert not strict_manifest_matches(manifest, keys, generation="session/chunks/0000")


def test_html_writes_pending_before_upload_and_requires_finish():
    html = build_batch_uploader_html(["https://fake/1"], "https://fake/manifest", generation="test")
    assert 'await save("uploading")' in html
    assert 'await save("complete")' in html
    assert 'selected.length > URLS.length' in html
    assert 'JSON.stringify(next) !== JSON.stringify(descriptors)' in html
    assert 'sequence.catch' in html
    assert 'batch-upload-finish' in html
    assert '__' not in html


def test_accepted_chunks_resume_after_temporary_upload_expiry():
    fs = MemoryFS()
    get_batch_upload_state(fs, "session")
    complete_chunk(fs, 0, 0, 2)
    staged = []
    append_batch_upload_chunk(fs, "session", on_chunk=lambda index, sources: staged.append(index))
    fs.files = {key: value for key, value in fs.files.items() if "/uploads/" not in key}
    complete_chunk(fs, 1, 2, 1)
    sources = finish_batch_upload(fs, "session", on_chunk=lambda index, sources: staged.append(index))
    assert [source["position"] for source in sources] == [1, 2, 3]
    assert staged == [0, 1]
    fs.files = {key: value for key, value in fs.files.items() if "/uploads/" not in key}
    assert finish_batch_upload(fs, "session") == sources
    assert "sawimages/batch_sources/session/upload_state.json" in fs.files


def test_phone_finish_does_not_claim_durable_staging_without_callback():
    fs = MemoryFS()
    get_batch_upload_state(fs, "session")
    complete_chunk(fs, 0, 0, 2)
    finish_batch_upload(fs, "session")
    state = get_batch_upload_state(fs, "session")
    assert "accepted_sources" not in state["chunks"][0]
    calls = []
    finish_batch_upload(fs, "session", on_chunk=lambda index, sources: calls.append(index))
    assert calls == [0]
    assert state["finished"]
    assert get_batch_upload_state(fs, "session")["chunks"][0]["accepted_sources"]


def test_capacity_limit_does_not_allocate_an_unfinishable_chunk():
    fs = MemoryFS()
    get_batch_upload_state(fs, "session")
    for index in range(20):
        complete_chunk(fs, index, index * 60, 60)
        if index < 19:
            append_batch_upload_chunk(fs, "session", on_chunk=lambda *_: None)
    with pytest.raises(ValueError, match="selection limit"):
        append_batch_upload_chunk(fs, "session", on_chunk=lambda *_: None)
    assert len(finish_batch_upload(fs, "session", on_chunk=lambda *_: None)) == 1200
