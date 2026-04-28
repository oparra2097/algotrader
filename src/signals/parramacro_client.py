"""HTTP client for the ParraMacro v1 API.

Bearer-token auth, exponential-backoff retries on 5xx, and a small
typed surface for the endpoints the trader needs. Keeps the JSON shape
contract close to the docs the user provided.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import requests


class ParraMacroError(RuntimeError):
    pass


class StaleDataError(ParraMacroError):
    pass


class ParraMacroClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        if not base_url:
            raise ValueError("base_url required")
        if not api_key:
            raise ValueError("api_key required (set PARRAMACRO_API_KEY)")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "algotrader/0.1",
        })

    @classmethod
    def from_env(cls, base_url: str, api_key_env: str = "PARRAMACRO_API_KEY",
                 **kwargs) -> "ParraMacroClient":
        key = os.environ.get(api_key_env, "")
        return cls(base_url=base_url, api_key=key, **kwargs)

    # ---- low-level GET with retries ----

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self._session.get(url, params=params, timeout=self.timeout)
                if 200 <= r.status_code < 300:
                    return r.json()
                if r.status_code in (401, 403):
                    raise ParraMacroError(f"auth failed ({r.status_code}) on {path}")
                if r.status_code == 404:
                    raise ParraMacroError(f"not found: {path}")
                if 500 <= r.status_code < 600 and attempt < self.max_retries:
                    last_err = ParraMacroError(f"{r.status_code} on {path}")
                    time.sleep(self.backoff_base * (2 ** attempt))
                    continue
                raise ParraMacroError(f"{r.status_code} on {path}: {r.text[:200]}")
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(self.backoff_base * (2 ** attempt))
                    continue
                raise ParraMacroError(f"network error on {path}: {e}") from e
        raise ParraMacroError(f"exhausted retries on {path}: {last_err}")

    # ---- typed endpoints ----

    def health(self) -> dict[str, Any]:
        """Return the platform health snapshot.

        Expected shape:
            {"as_of": iso8601,
             "freshest_data": {"commodities": iso8601, "georisk": iso8601, ...},
             "stale": [list of stale series names]}
        """
        return self._get("/api/v1/health")

    def commodity_forecast(self, commodity: str,
                           as_of: str | None = None) -> dict[str, Any]:
        """GET /api/v1/forecasts?commodity=<>[&as_of=YYYY-MM-DD]

        Expected shape:
            {"as_of": iso8601, "commodity": "gold",
             "spot": 2080.5, "spot_as_of": iso8601,
             "fan": [{"quarter": "2026Q3", "p2_5": ..., "p10": ...,
                      "p50": ..., "p90": ..., "p97_5": ...}, ...]}
        """
        params: dict[str, Any] = {"commodity": commodity}
        if as_of:
            params["as_of"] = as_of
        return self._get("/api/v1/forecasts", params=params)

    def hotspots(self, threshold: float = 70.0) -> list[dict[str, Any]]:
        """GET /api/v1/hotspots?threshold=70 -> list of country dicts."""
        data = self._get("/api/v1/hotspots", params={"threshold": threshold})
        return data.get("hotspots", []) if isinstance(data, dict) else data

    # ---- helpers ----

    @staticmethod
    def parse_iso(ts: str) -> datetime:
        # accept Z-suffix or +00:00
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).astimezone(timezone.utc)

    def assert_fresh(
        self,
        series: str,
        max_hours: float,
        health: dict[str, Any] | None = None,
    ) -> datetime:
        """Raise StaleDataError if a named series is too old."""
        h = health or self.health()
        freshest = h.get("freshest_data", {})
        if series not in freshest:
            raise StaleDataError(f"no freshness info for {series!r} in /health")
        as_of = self.parse_iso(freshest[series])
        age_h = (datetime.now(timezone.utc) - as_of).total_seconds() / 3600
        if age_h > max_hours:
            raise StaleDataError(
                f"{series} is {age_h:.1f}h old (max {max_hours}h)"
            )
        return as_of
