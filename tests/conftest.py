"""Tests may use in-memory fakes and the local Streamlit server only."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket

import pytest


def load_dev20_predictions():
    """Load the unchanged dev20 evidence kept outside the public repository."""
    from scripts.extraction_eval.tuning import DEFAULT_FREEZE_DIR

    evidence_dir = Path(os.environ.get('FAIRTALES_EVIDENCE_DIR', DEFAULT_FREEZE_DIR / 'evidence'))
    path = evidence_dir / 'dev20-run' / 'predictions.json'
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        pytest.skip(f'Dev20 evidence file is absent: {path}')
    expected = '7976646dec39ab8798684894d0db64a3cf2e30affdd4e185ec6e4a2e807199cf'
    if hashlib.sha256(data).hexdigest() != expected:
        pytest.fail(f'Dev20 evidence SHA-256 mismatch: {path}')
    return json.loads(data)


@pytest.fixture(autouse=True)
def no_live_services(monkeypatch):
    """Fail before any test can open a live Firestore, S3, or AI connection."""
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def check(address):
        if not isinstance(address, tuple):  # local Unix-domain sockets
            return
        host = address[0]
        if host == 'localhost':
            return
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local:
            raise AssertionError(f'Live network access is forbidden in tests: {host}')

    def local_connect(sock, address):
        check(address)
        return connect(sock, address)

    def local_connect_ex(sock, address):
        check(address)
        return connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', local_connect_ex)
