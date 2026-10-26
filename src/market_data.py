import math
import os
from datetime import datetime, timedelta, timezone

import requests


class MarketDataError(RuntimeError):
    pass


class CoinGeckoClient:
    """Small CoinGecko API client with validated USD prices and completed daily OHLC."""

    VALID_OHLC_DAYS = (1, 7, 14, 30, 90, 180, 365)

    def __init__(self, api_key=None, base_url=None, session=None):
        self.api_key = api_key or os.getenv("COINGECKO_API_KEY", "")
        self.base_url = (
            base_url or os.getenv("COINGECKO_BASE_URL", "https://api.coingecko.com/api/v3")
        ).rstrip("/")
        self.session = session or requests.Session()
        if self.api_key:
            self.session.headers.update({"x-cg-demo-api-key": self.api_key})

    def _get(self, path, params=None):
        try:
            response = self.session.get(self.base_url + path, params=params, timeout=25)
        except requests.RequestException as exc:
            raise MarketDataError(f"CoinGecko request failed: {type(exc).__name__}") from exc
        if not response.ok:
            raise MarketDataError(f"CoinGecko HTTP {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise MarketDataError("CoinGecko returned invalid JSON") from exc

    def current_price(self, coin_id):
        data = self._get(
            "/simple/price",
            {"ids": coin_id, "vs_currencies": "usd", "include_24hr_change": "true"},
        )
        try:
            price = float(data[coin_id]["usd"])
            change = data[coin_id].get("usd_24h_change")
            if not math.isfinite(price) or price <= 0:
                raise ValueError("invalid price")
            if change is not None:
                change = float(change)
                if not math.isfinite(change):
                    change = None
            return price, change
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise MarketDataError(f"Missing or invalid price for {coin_id}") from exc

    def daily_candles(self, coin_id, days=30):
        try:
            days = int(days)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("days must be an integer") from exc
        if days not in self.VALID_OHLC_DAYS:
            raise ValueError(f"days must be one of {self.VALID_OHLC_DAYS}")

        data = self._get(f"/coins/{coin_id}/ohlc", {"vs_currency": "usd", "days": days})
        if not isinstance(data, list):
            raise MarketDataError("CoinGecko OHLC response must be a list")

        today_utc = datetime.now(timezone.utc).date()
        # Exclude the current UTC date entirely. CoinGecko's latest daily bucket may
        # still be forming, so it must never drive exits or model decisions.
        last_completed_date = today_utc - timedelta(days=1)
        buckets = {}

        for row in data:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            try:
                ts = datetime.fromtimestamp(float(row[0]) / 1000, tz=timezone.utc)
                candle_date = ts.date()
                values = [float(x) for x in row[1:5]]
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            if candle_date > last_completed_date:
                continue
            if not all(math.isfinite(x) and x > 0 for x in values):
                continue
            open_price, high, low, close = values
            if low > min(open_price, close) or high < max(open_price, close) or low > high:
                continue

            key = candle_date.isoformat()
            if key not in buckets:
                buckets[key] = {
                    "date": key, "open": open_price, "high": high,
                    "low": low, "close": close,
                }
            else:
                candle = buckets[key]
                candle["high"] = max(candle["high"], high)
                candle["low"] = min(candle["low"], low)
                candle["close"] = close

        return [buckets[key] for key in sorted(buckets)]
