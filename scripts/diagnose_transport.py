"""Measure TLS/upload/response timing without credentials or database writes.

The deliberately invalid bearer must receive 401. A pass verifies the unauthenticated
HTTP path only; it does not qualify SDK queries or server capacity.
"""
import argparse
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import platform
import socket
import ssl
import time
from urllib.parse import urlsplit


def probe(url, padding, timeout):
    target = urlsplit(url)
    body = json.dumps({"sql": "SELECT 7 AS value" + " " * padding, "params": []}).encode()
    result = {"padding_bytes": padding, "body_bytes": len(body), "passed": False}
    connection = http.client.HTTPSConnection(target.hostname, target.port or 443,
                                            timeout=timeout, context=ssl.create_default_context())
    started = time.perf_counter()
    stage = "connect_tls"
    try:
        connection.connect()
        result["connect_tls_ms"] = (time.perf_counter() - started) * 1000
        result["peer_ip"] = connection.sock.getpeername()[0]
        result["tls"] = connection.sock.version()
        stage = "upload"
        before = time.perf_counter()
        connection.putrequest("POST", target.path)
        connection.putheader("Authorization", "Bearer sdk-invalid-transport-control")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body)))
        connection.putheader("Connection", "close")
        connection.endheaders()
        connection.send(body)
        result["upload_ms"] = (time.perf_counter() - before) * 1000
        stage = "response_headers"
        before = time.perf_counter()
        response = connection.getresponse()
        result["response_headers_ms"] = (time.perf_counter() - before) * 1000
        result["status"] = response.status
        stage = "response_body"
        before = time.perf_counter()
        raw = response.read(65537)
        result["response_body_ms"] = (time.perf_counter() - before) * 1000
        result["response_bytes"] = len(raw)
        result["passed"] = response.status == 401 and len(raw) <= 65536
    except (OSError, http.client.HTTPException) as error:
        # Exception messages may embed response content. Keep stable types/codes only.
        result.update(failed_stage=stage, error_type=type(error).__name__,
                      errno=getattr(error, "errno", None), winerror=getattr(error, "winerror", None))
    finally:
        connection.close()
    result["total_ms"] = (time.perf_counter() - started) * 1000
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="HTTPS tenant SQL endpoint; no query or userinfo")
    parser.add_argument("--output", default="transport-report.json")
    parser.add_argument("--repetitions", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--timeout", type=int, choices=range(1, 31), default=10)
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not parsed.path.endswith("/sql")):
        parser.error("Use an HTTPS SQL endpoint without credentials, query or fragment")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema": 1, "started_at": datetime.now(timezone.utc).isoformat(),
              "os": platform.platform(), "python": platform.python_version(),
              "openssl": ssl.OPENSSL_VERSION, "source": "Python standard library; no SDK",
              "credential_used": False, "writes": False, "checks": [], "passed": False}
    try:
        report["dns_addresses"] = sorted({r[4][0] for r in socket.getaddrinfo(parsed.hostname, 443)})
        for repetition in range(args.repetitions):
            for padding in (0, 1024, 16384, 65536, 320000):
                item = dict(repetition=repetition + 1, **probe(args.url, padding, args.timeout))
                report["checks"].append(item)
                output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(item), flush=True)
        report["passed"] = all(item["passed"] for item in report["checks"])
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
