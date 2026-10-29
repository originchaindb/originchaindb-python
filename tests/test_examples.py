"""Run the documented CLI through the real client and a loopback server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def test_read_examples_use_sdk_requests_and_return_server_values():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append((self.command, urlsplit(self.path), dict(self.headers),
                             json.loads(raw) if raw else None))
            if self.path.endswith("/sql"):
                payload = {"kind": "select", "rows": [{"message": "Hello from OriginChainDB"}]}
            elif "/vector/" in self.path:
                payload = [{"vec_id": "item-1", "score": 0.9}]
            elif "/fts/" in self.path:
                payload = [{"doc_id": "item-1", "score": 1.5}]
            else:
                payload = ["neighbor-1"]
            denied = self.headers.get("Authorization") == "Bearer rejected-fixture-token"
            if denied:
                payload = {"error": {"code": "unauthorized", "message": "fixture-private-row-marker"}}
            data = json.dumps(payload).encode()
            self.send_response(401 if denied else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {key: value for key, value in os.environ.items() if not key.startswith("OC_")}
    env.update(OC_BASE_URL=f"http://127.0.0.1:{server.server_port}", OC_TENANT="fixture",
               OC_BEARER="fixture-token", OC_DB_USER="fixture-user", OC_DB_KEY="fixture-key",
               OC_TABLE="sample.items", OC_FTS_FIELD="body", OC_QUERY="tea & mug",
               OC_QUERY_VECTOR="[1,0]", OC_RELATION="related", OC_NODE_ID="item/1")
    script = Path(__file__).resolve().parents[1] / "examples/query.py"

    def run(operation, **changes):
        return subprocess.run([sys.executable, str(script), operation], env={**env, **changes},
                              capture_output=True, text=True, timeout=15)

    try:
        outputs = {}
        for operation in ["sql", "fts", "vector", "graph"]:
            result = run(operation)
            assert result.returncode == 0, result.stderr
            outputs[operation] = json.loads(result.stdout)
        assert outputs["sql"]["rows"] == [{"message": "Hello from OriginChainDB"}]
        assert outputs["fts"]["hits"][0]["doc_id"] == "item-1"
        assert outputs["fts"]["hits"][0]["score"] == 1.5
        assert outputs["vector"][0]["vec_id"] == "item-1"
        assert outputs["vector"][0]["score"] == 0.9
        assert outputs["graph"][0]["pk"] == "neighbor-1"
        assert len(requests) == 4
        assert [r[0] for r in requests] == ["POST", "GET", "POST", "GET"]
        assert [r[1].path for r in requests] == [
            "/v1/tenants/fixture/sql", "/v1/tenants/fixture/fts/sample.items/body",
            "/v1/tenants/fixture/vector/sample.items/topk",
            "/v1/tenants/fixture/graph/sample.items/neighbors",
        ]
        assert requests[0][3] == {"sql": "SELECT $1 AS message", "params": ["Hello from OriginChainDB"]}
        assert parse_qs(requests[1][1].query) == {"q": ["tea & mug"], "mode": ["bm25"], "k": ["5"]}
        assert requests[2][3] == {"query": [1, 0], "dim": 2, "k": 5, "metric": "cosine"}
        assert parse_qs(requests[3][1].query) == {"rel": ["related"], "pk": ["item/1"]}
        for _, _, headers, _ in requests:
            normalized = {key.lower(): value for key, value in headers.items()}
            assert normalized["authorization"] == "Bearer fixture-token"
            assert normalized["x-oc-db-user"] == "fixture-user"
            assert normalized["x-oc-db-key"] == "fixture-key"
        assert run("--help").returncode == 0
        assert run("vector", OC_QUERY_VECTOR='["bad"]').returncode != 0
        assert run("sql", OC_BEARER="").returncode != 0
        assert len(requests) == 4
        denied = run("sql", OC_BEARER="rejected-fixture-token")
        assert denied.returncode != 0
        assert "Query failed" in denied.stderr
        assert "fixture-private-row-marker" not in denied.stdout + denied.stderr
        assert "rejected-fixture-token" not in denied.stdout + denied.stderr
        assert len(requests) == 5
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    test_read_examples_use_sdk_requests_and_return_server_values()
    print("Installed wheel: four executable read examples and input checks passed")
