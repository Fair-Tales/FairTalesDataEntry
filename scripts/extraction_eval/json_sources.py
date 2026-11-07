"""Locate JSON source records without deserializing their content.

Offsets refer to a read-only memory map. Callers can inspect identity fields,
apply the tuning exclusion check, then decode only the permitted record.
"""
from __future__ import annotations

import json
import mmap
from pathlib import Path
import re

from text_content.tuning import TuningErrors as E

_STRING = re.compile(rb'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"')
_SCALAR = re.compile(rb'(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)')
_SPACE = re.compile(rb'[ \t\r\n]*')


class JsonSources:
    """Context-managed JSON structure; spans are ``(start, end)`` byte offsets."""

    def __init__(self, path):
        self.path = Path(path)
        self._data = None

    def __enter__(self):
        self._file = self.path.open('rb')
        try:
            self._data = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            self.root = self._scan(0)
            if self._space(self.root[1]) != len(self._data):
                raise ValueError(E.trailing_json.format(path=self.path))
        except (OSError, ValueError):
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._data is not None:
            self._data.close()
        self._file.close()

    def _space(self, offset):
        return _SPACE.match(self._data, offset).end()

    def _token(self, pattern, offset):
        match = pattern.match(self._data, offset)
        if match is None:
            raise ValueError(E.malformed_json.format(offset=offset, path=self.path))
        return match.end()

    def _scan(self, offset):
        start = offset = self._space(offset)
        kind = self._data[offset:offset + 1]
        if kind == b'"':
            return start, self._token(_STRING, offset)
        if kind not in (b'{', b'['):
            return start, self._token(_SCALAR, offset)
        closing = b'}' if kind == b'{' else b']'
        offset = self._space(offset + 1)
        if self._data[offset:offset + 1] == closing:
            return start, offset + 1
        while True:
            if kind == b'{':
                offset = self._space(self._token(_STRING, offset))
                if self._data[offset:offset + 1] != b':':
                    raise ValueError(E.missing_colon.format(offset=offset, path=self.path))
                offset += 1
            _, offset = self._scan(offset)
            offset = self._space(offset)
            separator = self._data[offset:offset + 1]
            if separator == closing:
                return start, offset + 1
            if separator != b',':
                raise ValueError(E.missing_separator.format(offset=offset, path=self.path))
            offset = self._space(offset + 1)

    def members(self, span):
        """Return object keys and value spans; only the keys are decoded."""
        start, end = span
        if self._data[start:start + 1] != b'{':
            raise ValueError(E.expected_object)
        result = {}
        offset = self._space(start + 1)
        while offset < end - 1:
            key_end = self._token(_STRING, offset)
            key = json.loads(self._data[offset:key_end])
            offset = self._space(key_end) + 1  # Colon validated by _scan.
            value = self._scan(offset)
            if key in result:
                raise ValueError(E.duplicate_member.format(key=key, path=self.path))
            result[key] = value
            offset = self._space(value[1])
            if offset < end - 1:
                offset = self._space(offset + 1)
        return result

    def items(self, span):
        """Return array item spans without decoding their values."""
        start, end = span
        if self._data[start:start + 1] != b'[':
            raise ValueError(E.expected_array)
        result = []
        offset = self._space(start + 1)
        while offset < end - 1:
            value = self._scan(offset)
            result.append(value)
            offset = self._space(value[1])
            if offset < end - 1:
                offset = self._space(offset + 1)
        return result

    def decode(self, span):
        """Deserialize a value only after the caller has checked its source."""
        start, end = span
        return json.loads(self._data[start:end])
