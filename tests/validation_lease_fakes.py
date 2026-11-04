"""Versioned transactional datastore shared by validation model/page tests.

The production callback runs unchanged. Writes remain staged until commit;
reads record snapshot versions, and a conflicting commit replays the callback.
``before_commit`` is a one-shot hook for an actual competing model operation.
No Firestore client is constructed.
"""

from copy import deepcopy

from google.api_core.exceptions import Aborted, NotFound


class AttrDict(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class FakeSnapshot:
    def __init__(self, reference, data, version):
        self.reference = reference
        self.id = reference.id
        self.exists = data is not None
        self._data = deepcopy(data)
        self.version = version

    def to_dict(self):
        return deepcopy(self._data)


class FakeReference:
    def __init__(self, store, path):
        self.store = store
        self.path = path
        self.id = path.rsplit("/", 1)[-1]

    def __deepcopy__(self, memo):
        return self

    def get(self, transaction=None):
        self.store.reads += 1
        snapshot = FakeSnapshot(
            self, self.store.documents.get(self.path),
            self.store.versions.get(self.path, 0),
        )
        if transaction is not None:
            transaction.snapshots[self.path] = snapshot
        return snapshot


class FakeCollection:
    def __init__(self, store, name):
        self.store = store
        self.name = name

    def document(self, doc_id):
        return FakeReference(self.store, f"{self.name}/{doc_id}")


class FakeTransaction:
    def __init__(self, store):
        self.store = store
        self.snapshots = {}
        self.staged = []

    def update(self, ref, patch):
        self.staged.append((ref.path, deepcopy(patch)))

    def commit(self):
        hook, self.store.before_commit = self.store.before_commit, None
        if hook is not None:
            hook(self)
        failure, self.store.failure = self.store.failure, None
        if failure is not None:
            raise failure
        for path, snapshot in self.snapshots.items():
            if self.store.versions.get(path, 0) != snapshot.version:
                self.store.conflicts += 1
                raise Aborted("Snapshot changed before commit")
        for path, _ in self.staged:
            if path not in self.store.documents:
                raise NotFound("Cannot update a missing document")
        for path, patch in self.staged:
            self.store._commit_patch(path, patch)


class FakeFirestore:
    def __init__(self, store):
        self.store = store

    def connect_book(self):
        return self.store

    def get_by_reference(self, collection, document_ref):
        return self.store.collection(collection).document(document_ref).get()

    def username_to_doc_ref(self, username):
        return self.store.collection("users").document(username)

    def get_all_documents_stream(self, collection, select=None):
        return iter([
            FakeReference(self.store, path).get()
            for path in self.store.documents if path.startswith(f"{collection}/")
        ])

    def update_field(self, **kwargs):
        raise AssertionError(f"Unexpected descriptor write: {kwargs}")


class FakeLeaseStore:
    def __init__(self, data=None, book_id="the_test_book"):
        self.book_id = book_id
        self.documents = {
            f"books/{book_id}": {
                "title": book_id.replace("_", " ").title(),
                "is_registered": True,
                **(data or {}),
            }
        }
        self.versions = {f"books/{book_id}": 1}
        self.writes = []
        self.reads = 0
        self.attempts = 0
        self.conflicts = 0
        self.max_attempts = 5
        self.before_commit = None
        self.failure = None
        self.firestore = FakeFirestore(self)

    def collection(self, name):
        return FakeCollection(self, name)

    def reference(self, book_id=None):
        return self.collection("books").document(book_id or self.book_id)

    def make_book(self, book_id=None):
        from data_structures.book import Book

        return Book(db_object=self.reference(book_id).get().to_dict())

    def transaction(self):
        return FakeTransaction(self)

    def transactional(self, callback):
        def run(transaction):
            for _ in range(self.max_attempts):
                self.attempts += 1
                transaction.snapshots.clear()
                transaction.staged.clear()
                result = callback(transaction)
                try:
                    transaction.commit()
                except Aborted as exc:
                    last_abort = exc
                    continue
                return result
            raise ValueError("Transaction retries exhausted") from last_abort

        return run

    def _commit_patch(self, path, patch):
        self.documents[path].update(deepcopy(patch))
        self.versions[path] = self.versions.get(path, 0) + 1
        self.writes.append((path, deepcopy(patch)))

    def write(self, patch, book_id=None):
        """Apply an immediate competing write and advance its document version."""
        path = self.reference(book_id).path
        if path not in self.documents:
            raise NotFound("Cannot update a missing document")
        self._commit_patch(path, patch)

    def delete(self, book_id=None):
        path = self.reference(book_id).path
        self.documents.pop(path, None)
        self.versions[path] = self.versions.get(path, 0) + 1
