"""Fail closed on real network or child-process access during tests.

Audit hooks cannot be removed by an individual test's mock cleanup. Socket pairs
remain available for asyncio's local wake-up mechanism; IP sockets, DNS, socket
connections (including Unix/proxy sockets), and subprocesses are denied. External
clients must be replaced with explicit mocks at their application boundary.
"""
import socket
import sys
from unittest.mock import patch


class NetworkAccessBlocked(BaseException):
    """BaseException prevents application 'except Exception' from hiding leaks."""


_installed = False
_ipv6_probe = None
_blocked_events = frozenset({
    'socket.connect', 'socket.bind', 'socket.getaddrinfo',
    'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.getnameinfo',
    'socket.sendto', 'socket.sendmsg',
    'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec', 'os.fork',
    'os.forkpty',
})


def _audit(event, args):
    if event in _blocked_events:
        # Do not include addresses, command arguments, URLs or credentials.
        raise NetworkAccessBlocked(f'Offline tests blocked {event}; use an explicit mock')
    if event == 'socket.__new__' and args[1] in (socket.AF_INET, socket.AF_INET6):
        raise NetworkAccessBlocked('Offline tests blocked an IP socket; use an explicit mock')


def install():
    global _installed, _ipv6_probe
    if not _installed:
        # urllib3 probes IPv6 by creating and binding a real loopback socket at
        # import time. Explicitly disable that optional probe in offline tests.
        _ipv6_probe = patch.object(socket, 'has_ipv6', False)
        _ipv6_probe.start()
        sys.addaudithook(_audit)
        _installed = True
