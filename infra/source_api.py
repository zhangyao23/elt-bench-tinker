"""Serve the pinned official shipping truck data, with the upstream route and JSON semantics."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


class ShippingAPI(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/shipping/truck":
            self.send_error(404)
            return
        frame = pd.read_csv(ROOT / "data/official/shipping/source_snapshot/truck.csv")
        payload = json.dumps(frame.where(frame.notna(), None).to_dict(orient="records")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print("Pinned official API: http://127.0.0.1:58505/shipping/truck")
    ThreadingHTTPServer(("127.0.0.1", 58505), ShippingAPI).serve_forever()
