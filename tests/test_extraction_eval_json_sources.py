"""Offline checks for reading identities before source content is decoded."""

import json

import pytest

from scripts.extraction_eval.json_sources import JsonSources


def test_source_ids_can_be_read_without_deserializing_content(tmp_path, monkeypatch):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'books': [
        {'content': 'PRIVATE DENIED CONTENT', 'id': 'denied'},
        {'id': 'allowed', 'content': {'text': 'permitted', 'labels': [True, None, 2]}},
    ]}), encoding='utf-8')
    loads = json.loads
    decoded = []

    def checked_loads(value):
        assert b'PRIVATE DENIED CONTENT' not in value
        decoded.append(value)
        return loads(value)

    monkeypatch.setattr(json, 'loads', checked_loads)
    with JsonSources(path) as source:
        # Opening, locating arrays and locating records do not decode content.
        assert decoded == []
        records = source.items(source.members(source.root)['books'])
        permitted = []
        for record in records:
            fields = source.members(record)
            if source.decode(fields['id']) == 'allowed':
                permitted.append(source.decode(record))
    assert permitted == [{'id': 'allowed', 'content': {'text': 'permitted', 'labels': [True, None, 2]}}]


def test_nested_json_escaping_whitespace_and_scalars(tmp_path):
    value = {'escaped"key': ['a \\" } ] , : \n café', {}, [], -12.5e30, False, None]}
    path = tmp_path / 'nested.json'
    path.write_text(' \n' + json.dumps(value, ensure_ascii=False, indent=2) + '\t', encoding='utf-8')
    with JsonSources(path) as source:
        fields = source.members(source.root)
        assert list(fields) == ['escaped"key']
        assert [source.decode(item) for item in source.items(fields['escaped"key'])] == value['escaped"key']
        assert source.decode(source.root) == value


@pytest.mark.parametrize('raw', [
    b'', b' ', b'{', b'[1,', b'{"id" "x"}', b'{"id":}', b'[1 2]',
    b'{"id":"x",}', b'[1,]', b'{} []', b'01', b'NaN', b'"unterminated',
    b'"bad\\q"', b'"bad\\u123"', b'"bad\x00"',
])
def test_malformed_or_truncated_json_is_rejected(tmp_path, raw):
    path = tmp_path / 'bad.json'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        with JsonSources(path):
            pytest.fail('Malformed JSON was accepted')


def test_duplicate_identity_keys_are_rejected(tmp_path):
    path = tmp_path / 'duplicate.json'
    path.write_text('{"id":"allowed","id":"denied"}', encoding='utf-8')
    with JsonSources(path) as source, pytest.raises(ValueError, match='Duplicate JSON member'):
        source.members(source.root)


def test_container_type_mismatch_is_visible(tmp_path):
    path = tmp_path / 'types.json'
    path.write_text('{"values":[]}', encoding='utf-8')
    with JsonSources(path) as source:
        with pytest.raises(ValueError, match='array'):
            source.items(source.root)
        with pytest.raises(ValueError, match='object'):
            source.members(source.members(source.root)['values'])
