#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=18080)
parser.add_argument("--ready-delay", type=float, default=0.0)
parser.add_argument("--busy", action="store_true")
args = parser.parse_args()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            body = {"status": "ok"}
        elif path == "/v1/models":
            body = {"data": [{"id": "fixture-model"}]}
        elif path == "/slots":
            body = [{
                "id": 0,
                "is_processing": args.busy,
                "n_ctx": 32768,
                "n_prompt_tokens": 128,
            }]
        else:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return


if args.ready_delay:
    time.sleep(args.ready_delay)
ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
