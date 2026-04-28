#!/usr/bin/env python3
"""Mock ParraMacro server for local development.

Stands up the v1 surface algotrader expects, with deterministic data
controlled by env vars so the validator can exercise both "long signal"
and "no signal" paths.

Usage:
    PARRAMACRO_API_KEY=devkey \
    MOCK_GOLD_SPOT=1900 \
    python dev/mock_parramacro.py

Then point algotrader at http://localhost:5050.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request


app = Flask(__name__)
EXPECTED_KEY = os.environ.get("PARRAMACRO_API_KEY", "devkey")


def _check_auth():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "missing bearer token"}), 401
    if auth.split(" ", 1)[1] != EXPECTED_KEY:
        return jsonify({"error": "bad bearer token"}), 401
    return None


@app.before_request
def _auth_gate():
    if request.path == "/healthz":
        return None
    err = _check_auth()
    if err is not None:
        return err


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/api/v1/health")
def v1_health():
    now = datetime.now(timezone.utc)
    return jsonify({
        "as_of": now.isoformat(),
        "freshest_data": {
            "commodities": (now - timedelta(hours=1)).isoformat(),
            "georisk": (now - timedelta(minutes=10)).isoformat(),
            "macro_model": (now - timedelta(days=1)).isoformat(),
        },
        "stale": [],
    })


@app.route("/api/v1/forecasts")
def v1_forecasts():
    commodity = request.args.get("commodity", "gold")
    spot_env = f"MOCK_{commodity.upper()}_SPOT"
    spot = float(os.environ.get(spot_env, "2050"))
    now = datetime.now(timezone.utc)
    # static-ish fan around the model's prior; quarters mid-range so a
    # spot of 1850 triggers a long, 2300 triggers a close, 2050 -> nothing.
    fan = [
        {"quarter": "2026Q3", "p2_5": 1800, "p10": 1900, "p50": 2050,
         "p90": 2200, "p97_5": 2300},
        {"quarter": "2026Q4", "p2_5": 1780, "p10": 1880, "p50": 2080,
         "p90": 2240, "p97_5": 2350},
    ]
    return jsonify({
        "as_of": now.isoformat(),
        "commodity": commodity,
        "spot": spot,
        "spot_as_of": now.isoformat(),
        "fan": fan,
        "model_version": "mock-1.0",
    })


@app.route("/api/v1/hotspots")
def v1_hotspots():
    threshold = float(request.args.get("threshold", "70"))
    now = datetime.now(timezone.utc)
    sample = [
        {"cc": "UA", "name": "Ukraine", "score": 95.0, "delta_24h": 0.5},
        {"cc": "IL", "name": "Israel",  "score": 88.0, "delta_24h": -1.2},
        {"cc": "TR", "name": "Turkey",  "score": 65.0, "delta_24h": 4.0},
    ]
    return jsonify({
        "as_of": now.isoformat(),
        "threshold": threshold,
        "hotspots": [h for h in sample if h["score"] >= threshold],
    })


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "5050"))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
