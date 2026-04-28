#!/usr/bin/env python3
"""Mock ParraMacro v1 server matching the real JSON shapes.

Mirrors the production endpoints algotrader consumes:
  GET /api/v1/health                                      (public)
  GET /api/v1/commodities/list
  GET /api/v1/commodities/forecasts?commodity=<>[&as_of=]
  GET /api/v1/georisk/hotspots?threshold=<>

Bearer-auth check against PARRAMACRO_API_KEY (default "devkey").
Spot price is configurable via MOCK_<COMMODITY>_SPOT env vars
(e.g. MOCK_GOLD_SPOT=1850).

Usage:
    PARRAMACRO_API_KEY=devkey \
    MOCK_GOLD_SPOT=1850 \
    python dev/mock_parramacro.py
"""
from __future__ import annotations

import os
import re
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
    if request.path in ("/healthz", "/api/v1/health"):
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
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "freshest_data": {
            "georisk":     (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            "commodities": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "macro_model": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        },
        "stale": [],
        "ok": True,
    })


@app.route("/api/v1/commodities/list")
def v1_commodities_list():
    return jsonify({
        "as_of": datetime.now(timezone.utc).isoformat(),
        "commodities": [
            "Aluminum", "Brent Crude", "Cocoa", "Coffee", "Copper",
            "Gold", "Natural Gas (HH)", "Platinum", "Silver",
        ],
    })


def _spot_for(commodity: str, default: float) -> float:
    # MOCK_GOLD_SPOT, MOCK_BRENT_CRUDE_SPOT, MOCK_NATURAL_GAS_HH_SPOT, ...
    key = "MOCK_" + re.sub(r"[^A-Za-z0-9]+", "_", commodity).strip("_").upper() + "_SPOT"
    return float(os.environ.get(key, default))


@app.route("/api/v1/commodities/forecasts")
def v1_commodities_forecasts():
    commodity = request.args.get("commodity", "Gold")
    as_of_param = request.args.get("as_of")
    spot = _spot_for(commodity, default=2050.0)
    now = datetime.now(timezone.utc)

    quarters = {
        "Q+1": {"label": "Q3 2026", "median": 2050.0,
                "p2_5": 1800.0, "p10": 1900.0,
                "p90": 2200.0, "p97_5": 2300.0},
        "Q+2": {"label": "Q4 2026", "median": 2080.0,
                "p2_5": 1780.0, "p10": 1880.0,
                "p90": 2240.0, "p97_5": 2350.0},
        "Q+3": {"label": "Q1 2027", "median": 2110.0,
                "p2_5": 1750.0, "p10": 1860.0,
                "p90": 2280.0, "p97_5": 2400.0},
        "Q+4": {"label": "Q2 2027", "median": 2140.0,
                "p2_5": 1720.0, "p10": 1840.0,
                "p90": 2320.0, "p97_5": 2450.0},
    }

    summary = {
        "name": commodity,
        "ticker": "GC=F",
        "drivers": ["DXY", "TIPS_10Y:lead2"],
        "n_obs": 312,
        "rmse": 0.043,
        "fit_at": (now - timedelta(hours=1)).isoformat(),
        "fit_error": None,
        "last_price": spot,
        "garch": True,
    }

    if as_of_param:
        # backtest path: flat quarters, no nowcast/last_price (no live data)
        return jsonify({
            "as_of": now.isoformat(),
            "as_of_param": as_of_param,
            "commodity": commodity,
            "forecast": quarters,
            "model_summary": {**summary, "last_price": None},
        })

    # cached path: nested with anchoring + nowcast + summary
    return jsonify({
        "as_of": now.isoformat(),
        "as_of_param": None,
        "commodity": commodity,
        "forecast": {
            "forecast": quarters,
            "model_only": quarters,
            "forward_curve": None,
            "long_run_trend": None,
            "nowcast": spot,
            "summary": summary,
            "exog_columns": ["DXY", "TIPS_10Y_lead2"],
        },
    })


@app.route("/api/v1/georisk/hotspots")
def v1_georisk_hotspots():
    threshold = float(request.args.get("threshold", "70"))
    now = datetime.now(timezone.utc)
    sample = [
        {"country_code": "UA", "country_name": "Ukraine",
         "composite": 95.0, "base_score": 80.0, "news_score": 99.0,
         "indicators": {"military_conflict": 99, "political_stability": 88,
                         "economic_sanctions": 90, "protests_civil_unrest": 70,
                         "terrorism": 75, "diplomatic_tensions": 95},
         "headline_count": 50, "gdelt_event_count": 250, "avg_tone": -8.4,
         "updated_at": now.isoformat(), "trend": [90, 92, 94, 95]},
        {"country_code": "IL", "country_name": "Israel",
         "composite": 88.0, "base_score": 70.0, "news_score": 95.0,
         "indicators": {}, "headline_count": 30, "gdelt_event_count": 120,
         "avg_tone": -6.1, "updated_at": now.isoformat(),
         "trend": [82, 85, 87, 88]},
        {"country_code": "TR", "country_name": "Turkey",
         "composite": 65.0, "base_score": 60.0, "news_score": 67.0,
         "indicators": {}, "headline_count": 12, "gdelt_event_count": 30,
         "avg_tone": -2.0, "updated_at": now.isoformat(),
         "trend": [60, 62, 64, 65]},
    ]
    return jsonify({
        "as_of": now.isoformat(),
        "threshold": threshold,
        "hotspots": [h for h in sample if h["composite"] >= threshold],
    })


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "5050"))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
