import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from src.market_data import CoinGeckoClient, MarketDataError


class FakeResponse:
    def __init__(self, data, status_code=200, text=""):
        self._data, self.status_code, self.text = data, status_code, text
        self.ok = 200 <= status_code < 400
    def json(self):
        return self._data


class MarketDataTests(unittest.TestCase):
    def client(self, get_side_effect):
        session = Mock()
        session.headers = {}
        session.get.side_effect = get_side_effect
        return CoinGeckoClient(api_key="demo", base_url="https://example.test", session=session), session

    def test_current_price_uses_simple_price_endpoint(self):
        client, session = self.client([FakeResponse({"bitcoin": {"usd": 123.5, "usd_24h_change": 2.1}})])
        self.assertEqual(client.current_price("bitcoin"), (123.5, 2.1))
        self.assertEqual(session.get.call_args.args[0], "https://example.test/simple/price")

    def test_daily_candles_excludes_current_utc_date_and_aggregates(self):
        today = datetime.now(timezone.utc).date()
        yesterday = datetime.combine(today - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        two_days = datetime.combine(today - timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)
        rows = [
            [two_days.timestamp()*1000, 100, 110, 95, 105],
            [two_days.timestamp()*1000 + 4*3600*1000, 105, 115, 101, 112],
            [yesterday.timestamp()*1000, 112, 120, 108, 118],
            [datetime.now(timezone.utc).timestamp()*1000, 118, 130, 117, 129],
        ]
        client, session = self.client([FakeResponse(rows)])
        candles = client.daily_candles("bitcoin", days=30)
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0]["open"], 100)
        self.assertEqual(candles[0]["high"], 115)
        self.assertEqual(candles[0]["low"], 95)
        self.assertEqual(candles[0]["close"], 112)
        self.assertEqual(candles[-1]["date"], (today-timedelta(days=1)).isoformat())
        self.assertEqual(session.get.call_args.kwargs["params"]["days"], 30)

    def test_invalid_days_rejected_without_request(self):
        client, session = self.client([])
        with self.assertRaises(ValueError):
            client.daily_candles("bitcoin", days=35)
        session.get.assert_not_called()

    def test_http_error_is_sanitized(self):
        client, _ = self.client([FakeResponse({}, status_code=400, text="bad request")])
        with self.assertRaisesRegex(MarketDataError, "HTTP 400"):
            client.current_price("bitcoin")


if __name__ == "__main__":
    unittest.main()
