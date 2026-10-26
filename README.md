# LLM Crypto Paper Trader

Paper-trading-only daily agent for BTC, ETH, SOL and SUI. Each coin has an isolated virtual subwallet funded with 25% of initial capital. Google Sheets gives a combined view of equity, P&L, decisions, positions and trades. No exchange API, wallet, private key or real order execution is included.

## Architecture
1. Fetch CoinGecko data for each coin.
2. Ask the NVIDIA-hosted LLM for a strict JSON proposal (`LONG`, `SHORT`, `HOLD`).
3. Deterministic Python validates the proposal and sizes positions.
4. Virtual broker simulates positions, exits, fees, slippage and P&L.
5. Google Sheets persists the state and ledger.
6. GitHub Actions runs daily or manually.

## Four isolated subwallets
- Initial capital is split 25% each across BTC, ETH, SOL and SUI.
- Cash, equity, positions, loss streak and cooldown are separate per coin.
- Unused funds are not transferred between coins.
- Combined portfolio P&L is the sum of all four subwallets.
- One position per coin; no adding to a losing position.
- Shorts are disabled by default.

## Five rules encoded
1. Risk at most 0.5% of that coin subwallet's equity per trade.
2. Long stop below previous completed daily candle low; short stop above its high, with configurable buffer.
3. Never add to a losing position.
4. Three consecutive losing closed trades trigger a three-calendar-day cooldown for that coin.
5. Position notional cannot exceed that subwallet's equity.

The supplied rule document also mentions one market/chart and one new trade per day. This implementation screens all four requested markets once per UTC daily run and allows at most one new entry per coin per run. The LLM is advisory; deterministic code has final authority.

## Google Sheets tabs
- `Overview`: combined equity, cash, realized/unrealized P&L and per-coin equity.
- `Decisions`: each coin's model proposal, validation and reason.
- `Positions`: current virtual positions.
- `Trades`: closed trades, fees, P&L and exit reason.
- `DailyRuns`: run ledger and persistent JSON state.

## Setup
### Local
Python 3.11+ recommended.
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m unittest discover -s tests -v
python -m src.main
```
For Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

### Google Sheets one-time authorization
1. Create/select a project in Google Cloud Console.
2. Enable Google Sheets API.
3. Create a service account and download its JSON key.
4. Create a spreadsheet and share it with the service-account email as Editor.
5. Set the spreadsheet ID and credentials in `.env` or GitHub Actions Secrets.
6. Scheduled runs use the service account; no daily interactive login is needed.

For stronger credential security, consider Workload Identity Federation instead of a long-lived JSON key. Never commit credentials.

### GitHub Actions
Push the repo to GitHub. Under **Settings → Secrets and variables → Actions**, add:
- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64` (base64-encoded service-account JSON)
- `NVIDIA_API_KEY`
- `COINGECKO_API_KEY`

Optional repository variables:
- `NVIDIA_MODEL` (default `deepseek-ai/deepseek-v4.1-flash`)
- `INITIAL_CAPITAL` (default 10000 USD)

Encode credentials:
- macOS/Linux: `base64 -i service-account.json | tr -d '\n'`
- PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("service-account.json"))`

Then go to **Actions → Daily Paper Trader → Run workflow**. Default schedule is 00:25 UTC (05:55 India time); GitHub may delay scheduled jobs.

## Assumptions and limitations
- Confirm current NVIDIA model availability and CoinGecko plan/endpoint limits before deployment.
- CoinGecko IDs: `bitcoin`, `ethereum`, `solana`, `sui`; quotes are USD.
- Candle timestamps are normalized to UTC. If data is incomplete, that coin is skipped.
- If a daily candle touches both stop and target, the broker assumes the stop happened first.
- Daily OHLC cannot model exact intraday execution; gaps, fees and slippage can change results.
- Invalid LLM responses or API errors mean no new trade for that coin.
- Google Sheets is not a transactional database. The workflow serializes scheduled runs and checks the daily run ledger.
- The historical return stated in the supplied Google Doc is not independently verified and is not assumed by this code.

## Layout
```text
.github/workflows/daily-paper-trader.yml
config/settings.yaml
prompts/trader.md
src/config.py
src/market_data.py
src/llm_trader.py
src/risk_engine.py
src/paper_broker.py
src/google_sheets.py
src/main.py
tests/
```

## Pre-deployment verification (no trades)
Run unit tests:
```bash
python -m unittest discover -s tests -v
```
Verify Google Sheets read/write access without running the strategy or creating any paper positions:
```bash
python -m src.sheets_smoke_test
```
This creates a `SystemChecks` tab if missing and appends one explicitly labeled connectivity-test row. It does not place orders or modify trading tabs.

### Recovery and candle processing notes
- The runner persists a per-wallet `last_processed_candle` cursor and processes all returned completed candles newer than that cursor. CoinGecko only returns a bounded OHLC history; outages longer than the returned history window cannot be fully reconstructed automatically. Review stale cursors and logs after a prolonged outage.
- Closed trades receive deterministic IDs and are deduplicated in the `Trades` sheet. Daily decisions are upserted by UTC date and symbol. Google Sheets is not transactional, so a network failure during a multi-tab update can still leave tabs temporarily inconsistent; inspect `DailyRuns` and the `SystemChecks` tab after an interrupted run.
- Stop gaps use the candle open when it is worse than the stop level; if stop and target are both touched within a candle, the stop is assumed first.
- Shorts remain disabled by default. Keep `allow_short: false` until short behavior has been independently reviewed and tested.
- This is a local paper-trading prototype, not investment advice or production trading software. No real exchange execution is implemented.
