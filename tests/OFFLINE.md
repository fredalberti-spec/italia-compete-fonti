# Offline test suite

Run the complete suite, including `test_offclass.py`, with:

```sh
python tests/run_offline.py
```

Install the declared requirements into a local virtual environment beforehand.
The runner clears the inherited environment, uses a temporary HOME, and installs
a process-wide audit hook before importing application code. No Telegram session,
real API credential, or production service is needed.

Every test module also installs the guard before importing application code, so
direct unittest discovery and individual test module invocations retain the block.
The guard rejects IP sockets, DNS, all socket connections, UDP sends and child
processes. Local socket pairs needed by asyncio remain available. There is no
network opt-out. Mock external clients explicitly (e.g. FakeGraph, fake Telegram
clients, fake Dropbox clients, or the application's HTTP request boundary).
The optional urllib3 IPv6 import-time capability probe is explicitly disabled.

A missing mock raises `NetworkAccessBlocked`, which inherits from BaseException
so an application's ordinary error handling cannot turn a network leak into a
passing test. Guard regression tests verify that forbidden operations are stopped
before external access, and that mock cleanup cannot remove the guard.

This is a test guard for trusted project tests, not a security sandbox for hostile
native extensions. Do not run tests in a production shell or provide live secrets.
