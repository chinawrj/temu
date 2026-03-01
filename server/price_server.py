#!/usr/bin/env python3
"""
Price Server — 根据商品参数散列生成 USD 价格

POST /price
  Body (JSON): { "product_id": "...", "skc_id": "...", "sku_id": "...", "platform_sku": "..." }
  Response:    { "usd_price": 12.34, "product_id": "...", "skc_id": "...", "sku_id": "...", "platform_sku": "..." }

价格 = hash(product_id + skc_id + sku_id + platform_sku) 映射到 [0.01, 99.99]
相同参数永远返回相同价格（确定性散列）。
"""

import hashlib
import json
import struct
from http.server import HTTPServer, BaseHTTPRequestHandler

HOST = "127.0.0.1"
PORT = 18234

REQUIRED_FIELDS = ["product_id", "skc_id", "sku_id", "platform_sku"]


def compute_price(product_id: str, skc_id: str, sku_id: str, platform_sku: str) -> float:
    """Hash inputs → deterministic USD price in [0.01, 99.99]."""
    raw = f"{product_id}|{skc_id}|{sku_id}|{platform_sku}"
    digest = hashlib.sha256(raw.encode()).digest()
    # Take first 4 bytes as unsigned int
    val = struct.unpack(">I", digest[:4])[0]
    # Map to [0.01, 99.99]
    price = 0.01 + (val / 0xFFFFFFFF) * 99.98
    return round(price, 2)


class PriceHandler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path != "/price":
            self._json_response(404, {"error": "Not found. Use POST /price"})
            return

        # Read body
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._json_response(400, {"error": "Empty body"})
            return

        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._json_response(400, {"error": f"Invalid JSON: {e}"})
            return

        # Validate fields
        missing = [f for f in REQUIRED_FIELDS if not body.get(f)]
        if missing:
            self._json_response(400, {"error": f"Missing fields: {missing}"})
            return

        pid = str(body["product_id"]).strip()
        skc = str(body["skc_id"]).strip()
        sku = str(body["sku_id"]).strip()
        psku = str(body["platform_sku"]).strip()

        price = compute_price(pid, skc, sku, psku)

        self._json_response(200, {
            "usd_price": price,
            "product_id": pid,
            "skc_id": skc,
            "sku_id": sku,
            "platform_sku": psku,
        })

    def log_message(self, fmt, *args):
        print(f"[PriceServer] {args[0]}" if args else fmt)


def main():
    server = HTTPServer((HOST, PORT), PriceHandler)
    print(f"🚀 Price Server listening on http://{HOST}:{PORT}/price")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n⏹  Shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
