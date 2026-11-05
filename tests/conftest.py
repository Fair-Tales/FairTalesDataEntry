"""Tests may use in-memory fakes and the local Streamlit server only."""
import ipaddress
import socket

import pytest


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
