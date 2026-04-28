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
        """GET /api/v1/health (public).

        Real shape:
            {"as_of": iso8601,
             "freshest_data": {"georisk": iso8601|null,
                               "commodities": iso8601|null,
                               "macro_model": iso8601|null},
             "stale": [list of stale series names],
             "ok": bool}
        """
        return self._get("/api/v1/health")

    def commodity_forecast(self, commodity: str,
                           as_of: str | None = None) -> dict[str, Any]:
        """GET /api/v1/commodities/forecasts?commodity=<>[&as_of=YYYY-MM-DD]

        Returns a normalized shape regardless of cached vs backtest path:
            {"as_of": iso8601,
             "as_of_param": "YYYY-MM-DD" or None,
             "commodity": str (verbatim, e.g. "Gold"),
             "spot": float | None,        # None on backtest path
             "fan": [{"key": "Q+1", "label": "Q3 2026",
                      "p2_5", "p10", "p50", "p90", "p97_5"}, ...],
             "summary": {raw model summary dict}}
        """
        params: dict[str, Any] = {"commodity": commodity}
        if as_of:
            params["as_of"] = as_of
        raw = self._get("/api/v1/commodities/forecasts", params=params)
        return self._normalize_commodity_forecast(raw)

    def commodities_list(self) -> list[str]:
        """GET /api/v1/commodities/list -> ['Aluminum', 'Brent Crude', ...]"""
        data = self._get("/api/v1/commodities/list")
        return data.get("commodities", []) if isinstance(data, dict) else data

    def hotspots(self, threshold: float = 70.0) -> list[dict[str, Any]]:
        """GET /api/v1/georisk/hotspots?threshold=<>

        Returns the hotspots array. Each item has:
            {"country_code", "country_name", "composite", "base_score",
             "news_score", "indicators": {...}, "headline_count",
             "gdelt_event_count", "avg_tone", "updated_at", "trend": [...]}
        Sorted desc by composite.
        """
        data = self._get("/api/v1/georisk/hotspots",
                         params={"threshold": threshold})
        return data.get("hotspots", []) if isinstance(data, dict) else data

    # ---- normalizers ----

    @staticmethod
    def _normalize_commodity_forecast(raw: dict[str, Any]) -> dict[str, Any]:
        """Collapse cached and backtest payloads into one consistent shape."""
        as_of_param = raw.get("as_of_param")
        outer = raw.get("forecast", {})

        if as_of_param:
            # backtest path: forecast = {"Q+1": {...}, ...} directly
            quarters = outer if isinstance(outer, dict) else {}
            summary = raw.get("model_summary", {}) or {}
            spot: float | None = None       # caller must supply historical spot
        else:
            # cached path: forecast.forecast is the anchored quarters dict
            quarters = outer.get("forecast", {}) if isinstance(outer, dict) else {}
            summary = outer.get("summary", {}) if isinstance(outer, dict) else {}
            nowcast = outer.get("nowcast") if isinstance(outer, dict) else None
            last_price = summary.get("last_price") if isinstance(summary, dict) else None
            spot = nowcast if nowcast is not None else last_price

        fan: list[dict[str, Any]] = []
        # quarter keys are "Q+1", "Q+2", ...; sort numerically
        def _qord(k: str) -> int:
            try:
                return int(k.split("+", 1)[1])
            except Exception:
                return 99
        for k in sorted(quarters, key=_qord):
            q = quarters[k] or {}
            fan.append({
                "key": k,
                "label": q.get("label", k),
                "p2_5": q.get("p2_5"),
                "p10": q.get("p10"),
                "p50": q.get("median"),     # real uses "median"
                "p90": q.get("p90"),
                "p97_5": q.get("p97_5"),
            })

        return {
            "as_of": raw.get("as_of"),
            "as_of_param": as_of_param,
            "commodity": raw.get("commodity"),
            "spot": float(spot) if spot is not None else None,
            "fan": fan,
            "summary": summary,
        }

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
        """Raise StaleDataError if a named series is too old or unavailable.

        Per the parramacro v1 contract, freshest_data values are
        `string | null` - null means the underlying product has no
        cached run yet. Treat that as stale rather than crashing.
        """
        h = health or self.health()
        freshest = h.get("freshest_data", {})
        if series not in freshest:
            raise StaleDataError(f"no freshness info for {series!r} in /health")
        ts = freshest[series]
        if ts is None:
            raise StaleDataError(f"{series} has no cached data (freshest_data is null)")
        as_of = self.parse_iso(ts)
        age_h = (datetime.now(timezone.utc) - as_of).total_seconds() / 3600
        if age_h > max_hours:
            raise StaleDataError(
                f"{series} is {age_h:.1f}h old (max {max_hours}h)"
            )
        return as_of
