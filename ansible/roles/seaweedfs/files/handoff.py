#!/usr/bin/env python3
"""One-time hand-over of the back-up login to app01.

Serves ONE request (GET /) from the allowed network, with the S3 login of the "kopia" account, the Kopia
repository password and the bucket settings as JSON. Then it writes /etc/seaweedfs/handoff-done and stops.
Requests from anywhere else are refused. Start it again with `translog-handoff` when app01 was rebuilt.

This way no secret is ever typed in, stored in Git or put in EduCloud user data.
"""
import argparse
import ipaddress
import json
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def secrets():
    out = {}
    for line in pathlib.Path("/etc/seaweedfs/secrets.env").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--allow", required=True)
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--days", type=int, required=True)
    ap.add_argument("--prefix", default="app01/")
    a = ap.parse_args()
    allowed = ipaddress.ip_network(a.allow)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            client = ipaddress.ip_address(self.client_address[0])
            if client not in allowed:
                self.send_error(403)
                return
            s = secrets()
            body = json.dumps({"endpoint": a.endpoint, "bucket": a.bucket, "prefix": a.prefix, "retention_days": a.days,
                               "access_key": s["KOPIA_KEY"], "secret_key": s["KOPIA_SECRET"],
                               "repo_password": s["KOPIA_REPO_PASSWORD"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            pathlib.Path("/etc/seaweedfs/handoff-done").write_text(f"{client}\n")
            print(f"hand-over done to {client}", flush=True)
            threading.Thread(target=self.server.shutdown).start()

        def log_message(self, *args):
            pass

    print(f"waiting for one hand-over request from {allowed} on port {a.port}", flush=True)
    HTTPServer(("0.0.0.0", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
