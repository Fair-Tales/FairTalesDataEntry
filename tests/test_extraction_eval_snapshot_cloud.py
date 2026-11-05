"""Firestore adapter contracts use fake SDK queries and never create a client."""

from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
import socket

from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable
from google.cloud.firestore_v1.base_query import FieldFilter
import pytest

from scripts.extraction_eval.snapshot import ReadOnlyCloudBackend


@dataclass(frozen=True)
class FakeReference:
    path: str
    database: object

    @property
    def id(self):
        return self.path.rsplit("/", 1)[-1]

    def get(self, **kwargs):
        self.database.get_calls.append((self.path, kwargs))
        if self.database.get_error is not None:
            raise self.database.get_error
        return FakeSnapshot(self, {})


class FakeSnapshot:
    exists = True
    update_time = "frozen-update-time"

    def __init__(self, reference, data):
        self.reference, self.id, self.data = reference, reference.id, data

    def to_dict(self):
        return deepcopy(self.data)


class FakeQuery:
    def __init__(self, database, collection, filter):
        self.database, self.collection, self.filter = database, collection, filter
        self.order, self.after = None, None

    def order_by(self, name):
        self.order = name
        return self

    def start_after(self, fields):
        assert set(fields) == {"__name__"}
        reference = fields.get("__name__")
        assert isinstance(reference, FakeReference)
        assert reference.path.startswith(f"{self.collection}/")
        self.after = reference.id
        return self

    def stream(self, **kwargs):
        assert self.order == "__name__"
        retry = kwargs.get("retry")
        assert retry is not None
        assert retry._predicate(ResourceExhausted("daily quota exhausted")) is False
        assert retry._predicate(ServiceUnavailable("unavailable")) is False
        assert self.filter.op_string == "=="
        value = self.filter.value
        variant = ("reference", value.path) if isinstance(value, FakeReference) else ("string", value)
        self.database.query_calls.append({"collection": self.collection, "field": self.filter.field_path,
                                          "variant": variant, "after": self.after})
        documents = self.database.results.get((self.collection, variant), [])
        for identifier in sorted(documents):
            if self.after is not None and identifier <= self.after:
                continue
            if self.database.fail_after == len(self.database.reads):
                raise ResourceExhausted("daily quota exhausted")
            path = f"{self.collection}/{identifier}"
            self.database.reads.append(path)
            yield FakeSnapshot(FakeReference(path, self.database), {"frozen": identifier})


class FakeCollection:
    def __init__(self, database, name):
        self.database, self.name = database, name

    def document(self, identifier):
        return FakeReference(f"{self.name}/{identifier}", self.database)

    def where(self, *, filter):
        assert isinstance(filter, FieldFilter)
        return FakeQuery(self.database, self.name, filter)


class FakeDatabase:
    def __init__(self):
        self.results, self.query_calls, self.reads, self.get_calls = {}, [], [], []
        self.fail_after = self.get_error = None

    def collection(self, name):
        return FakeCollection(self, name)

    def document(self, path):
        return FakeReference(path, self)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Cloud adapter contract test attempted a network connection")

    monkeypatch.setattr(socket, "socket", no_network)


@pytest.mark.parametrize("kind, interrupted_variant", [
    ("books", 0), ("books", 1), ("related", 0), ("related", 1), ("related", 2),
])
@pytest.mark.parametrize("captured_in_variant", [0, 1])
def test_ordered_cursor_resumes_every_reference_variant_without_rereads(kind, interrupted_variant, captured_in_variant):
    database = FakeDatabase()
    backend = ReadOnlyCloudBackend(database, SimpleNamespace())
    if kind == "books":
        collection, field = "books", "entered_by"
        variants = [("string", "pilot_import"), ("reference", "users/pilot_import")]

        def stream(cursor, checkpoint):
            return backend.iter_books_resumable("pilot_import", cursor, checkpoint)
    else:
        collection, field = "pages", "book"
        variants = [("reference", "books/story"), ("string", "books/story"), ("string", "story")]

        def stream(cursor, checkpoint):
            return backend.iter_related_resumable("pages", "story", cursor, checkpoint)

    # Later variants deliberately contain smaller IDs: start_after must reset
    # between variants, rather than skipping their documents with an old cursor.
    for index, variant in enumerate(variants):
        prefix = len(variants) - index
        database.results[(collection, variant)] = [f"{prefix}-a", f"{prefix}-b"]
    database.fail_after = 2 * interrupted_variant + captured_in_variant
    cursor, captured, checkpoints = {}, {}, []

    def checkpoint():
        checkpoints.append({"cursor": deepcopy(cursor), "documents": deepcopy(captured)})

    with pytest.raises(ResourceExhausted, match="daily quota"):
        try:
            for document in stream(cursor, checkpoint):
                captured[document.get("path")] = document
                checkpoint()
        finally:
            checkpoint()
    assert len(database.reads) == database.fail_after
    assert cursor.get("value_index", 0) == interrupted_variant
    if captured_in_variant:
        assert cursor.get("last_id", "").endswith("-a")
    else:
        assert cursor.get("last_id") is None
    saved = checkpoints[-1]
    cursor = deepcopy(saved.get("cursor", {}))
    captured = deepcopy(saved.get("documents", {}))
    previous_query_count = len(database.query_calls)
    database.fail_after = None
    for document in stream(cursor, checkpoint):
        captured[document.get("path")] = document
        checkpoint()
    resumed_queries = database.query_calls[previous_query_count:]
    assert resumed_queries[0].get("variant") == variants[interrupted_variant]
    assert resumed_queries[0].get("after") == saved.get("cursor", {}).get("last_id")
    assert all(query.get("after") is None for query in resumed_queries[1:])
    assert all(query.get("field") == field for query in database.query_calls)
    assert len(database.reads) == 2 * len(variants)
    assert len(set(database.reads)) == len(database.reads)
    assert set(captured) == set(database.reads)
    assert cursor == {"value_index": len(variants), "last_id": None}
    assert database.get_calls == []  # Cursor restore must never read a document.
    completed_query_count = len(database.query_calls)
    assert list(stream(cursor, checkpoint)) == []
    assert len(database.query_calls) == completed_query_count


def test_document_get_disables_retry_and_surfaces_exhausted_quota_once():
    database = FakeDatabase()
    backend = ReadOnlyCloudBackend(database, SimpleNamespace())
    document = backend.get_document("authors/one")
    assert document.get("path") == "authors/one"
    assert database.get_calls == [("authors/one", {"retry": None})]
    database.get_error = ResourceExhausted("daily quota exhausted")
    with pytest.raises(ResourceExhausted, match="daily quota"):
        backend.get_document("authors/two")
    assert database.get_calls == [("authors/one", {"retry": None}), ("authors/two", {"retry": None})]
