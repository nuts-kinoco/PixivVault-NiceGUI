"""Measure the real managed reader against a verified HTTPS loopback only."""

import argparse
from collections import defaultdict
import http.client
import json
import math
import os
from pathlib import Path
import socket
import ssl
import statistics
from threading import Thread
from time import monotonic, perf_counter_ns, sleep
from unittest.mock import patch
from urllib.request import OpenerDirector

from pytest import MonkeyPatch

from d6_api_tls_fixture import EXTRA, loopback, write_cookie
from pixivvault_nicegui import owned_https, pixiv_read_client as reads
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
from pixivvault_nicegui.managed_network_stop_reader import create_exact_managed_read_client


def summarize(samples):
    values = sorted(samples)
    return {
        "n": len(values),
        "median_ms": round(statistics.median(values) / 1e6, 4),
        "p95_ms": round(values[math.ceil(.95 * len(values)) - 1] / 1e6, 4),
    }


def measure(root, count, pace_ms=0):
    phases = defaultdict(int)
    rows = []
    original_connect = socket.socket.connect

    def timed(target, name, phase, *, enabled=lambda *_a, **_kw: True):
        action = getattr(target, name)

        def record(*args, **kwargs):
            if not enabled(*args, **kwargs):
                return action(*args, **kwargs)
            start = perf_counter_ns()
            try:
                return action(*args, **kwargs)
            finally:
                phases[phase] += perf_counter_ns() - start

        return patch.object(target, name, record)

    root.mkdir()
    gate = ManagedNetworkStopGate(root / "gate.sqlite")
    with MonkeyPatch.context() as monkey, loopback(root, monkey, original_connect,
                                                 use_system_ca=True) as server:
        with (timed(ssl, "create_default_context", "ssl_context"),
              timed(socket, "getaddrinfo", "dns"),
              timed(Thread, "start", "thread_start"),
              timed(owned_https.HTTPSOwner, "addresses", "resolve_total"),
              timed(socket.socket, "connect", "tcp"),
              timed(ssl.SSLSocket, "do_handshake", "tls",
                    enabled=lambda sock, *a, **kw: not sock.server_side),
              timed(http.client.HTTPConnection, "request", "request_total"),
              timed(http.client.HTTPConnection, "send", "send_total"),
              timed(http.client.HTTPConnection, "getresponse", "headers"),
              timed(OpenerDirector, "open", "opener_total"),
              timed(reads, "_read_response", "read_total"),
              timed(ManagedNetworkStopGate, "_read_locked", "gate_check"),
              timed(json, "loads", "json", enabled=lambda *a, **kw:
                    kw.get("object_pairs_hook") is reads._payload_unique_keys),
              create_exact_managed_read_client(write_cookie(root), gate) as client):
            last_end = None
            spacing_ns = pace_ms * 1_000_000
            for _ in range(count):
                phases.clear()
                start = perf_counter_ns()
                if spacing_ns and last_end is not None:
                    while (remaining := last_end + spacing_ns - perf_counter_ns()) > 0:
                        sleep(remaining / 1e9)
                    start = perf_counter_ns()
                    phases["pace"] = start - last_end
                assert len(client._json("identity", EXTRA, deadline=monotonic() + 3)) == 65536
                last_end = perf_counter_ns()
                phases["total"] = last_end - start
                # Resolve wall time includes DNS plus thread launch/scheduling.
                phases["dns_thread_overhead"] = max(0, phases["resolve_total"] - phases["dns"])
                phases["send"] = phases["send_total"] - phases["resolve_total"] - phases["tcp"] - phases["tls"]
                phases["read_body"] = phases["read_total"] - phases["opener_total"]
                rows.append(dict(phases))
            assert client._owned_transport.active_count == 0
        observations = {
            "connections": server.connections,
            "requests": len(server.requests),
            "gzip_requests": sum(row[-1] == "gzip" for row in server.requests),
        }
    keys = sorted(set().union(*rows))
    return {
        "pid": os.getpid(), **observations, "network": "verified_loopback_only",
        "pacing_ms": pace_ms, "body_bytes": 65561,
        "all": {key: summarize([row.get(key, 0) for row in rows]) for key in keys},
        "warm": {key: summarize([row.get(key, 0) for row in rows[1:]]) for key in keys},
        "samples_ns": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scratch", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--count", type=int, default=41)
    parser.add_argument("--pace-ms", type=int, default=0)
    args = parser.parse_args()
    if (any(p.resolve().drive.upper() != "S:" for p in (args.scratch, args.output))
            or args.count < 31 or args.pace_ms < 0):
        parser.error("use S: and at least 31 samples")
    result = measure(args.scratch, args.count, args.pace_ms)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="ascii")
    print(json.dumps({k: v for k, v in result.items() if k != "samples_ns"}))
