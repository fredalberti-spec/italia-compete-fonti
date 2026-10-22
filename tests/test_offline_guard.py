from tests.offline_guard import install, NetworkAccessBlocked
install()

import asyncio
import os
import socket
import subprocess
import unittest
import urllib.request
from unittest.mock import patch


class OfflineGuard(unittest.TestCase):
    def test_ipv4_and_ipv6_are_blocked_before_socket_creation(self):
        for family in (socket.AF_INET, socket.AF_INET6):
            with self.subTest(family=family), self.assertRaises(NetworkAccessBlocked):
                socket.socket(family, socket.SOCK_STREAM)

    def test_dns_and_http_are_blocked(self):
        with self.assertRaises(NetworkAccessBlocked):
            socket.getaddrinfo('offline-test.invalid', 443)
        with self.assertRaises(NetworkAccessBlocked):
            urllib.request.urlopen('https://offline-test.invalid/')

    def test_requests_and_whatsapp_transport_cannot_reach_network(self):
        import requests
        from papa_whatsapp import Graph
        with self.assertRaises(NetworkAccessBlocked):
            requests.get('https://offline-test.invalid/', timeout=1)
        # Direct transport test, independent of notice dates or enabled flags.
        with self.assertRaises(NetworkAccessBlocked):
            asyncio.run(Graph('fixture-not-a-credential').call('GET', 'fixture'))

    def test_unix_proxy_connections_are_blocked(self):
        with socket.socket(socket.AF_UNIX) as client:
            with self.assertRaises(NetworkAccessBlocked):
                client.connect('/nonexistent-offline-proxy')

    def test_child_process_network_escape_is_blocked(self):
        with self.assertRaises(NetworkAccessBlocked):
            subprocess.run(['curl', 'https://offline-test.invalid/'])
        with self.assertRaises(NetworkAccessBlocked):
            os.system('curl https://offline-test.invalid/')

    def test_mock_cleanup_cannot_disable_audit_guard(self):
        with patch('socket.getaddrinfo', return_value=[]):
            self.assertEqual(socket.getaddrinfo('fixture', 443), [])
        with self.assertRaises(NetworkAccessBlocked):
            socket.getaddrinfo('offline-test.invalid', 443)

    def test_application_exception_handler_cannot_swallow_denial(self):
        def application():
            try:
                socket.getaddrinfo('offline-test.invalid', 443)
            except Exception:
                return 'hidden'
        with self.assertRaises(NetworkAccessBlocked):
            application()

    def test_asyncio_wakeup_socketpair_still_works(self):
        async def fixture():
            await asyncio.sleep(0)
            return 'offline'
        self.assertEqual(asyncio.run(fixture()), 'offline')
