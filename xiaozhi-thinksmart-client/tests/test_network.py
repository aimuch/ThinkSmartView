import socket

from thinksmart_client import app


def test_local_ip_is_the_route_source_address(monkeypatch):
    class Sock:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def connect(self, addr): pass
        def getsockname(self): return ('192.168.1.23', 40000)
    monkeypatch.setattr(socket, 'socket', Sock)
    assert app.local_ip() == '192.168.1.23'


def test_local_ip_offline(monkeypatch):
    class Sock(socket.socket):
        def connect(self, addr): raise OSError('Network is unreachable')
    monkeypatch.setattr(socket, 'socket', Sock)
    assert app.local_ip() is None
