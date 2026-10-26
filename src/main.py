import json
import math
from datetime import date, datetime, timedelta, timezone

from src.config import load_settings
from src.market_data import CoinGeckoClient
from src.llm_trader import LLMTrader
from src.risk_engine import validate_and_size
from src.paper_broker import process_exits, open_position, equity, unrealized_pnl
from src.google_sheets import SheetsStore


def _initial_wallets(symbols, capital):
    if not symbols:
        raise RuntimeError("No trading symbols configured")
    allocation = float(capital) / len(symbols)
    return {
        symbol: {
            "symbol": symbol, "cash": allocation, "positions": [],
            "closed_trades": [], "loss_streak": 0, "cooldown_until": "",
            "last_processed_candle": "",
        }
        for symbol in symbols
    }


def _recover_state(run_rows, symbols, capital, run_date):
    """Recover latest wallet state and same-day checkpoint metadata."""
    for row in reversed(run_rows[1:]):
        if len(row) <= 6 or not row[6]:
            continue
        try:
            saved = json.loads(row[6])
            if not isinstance(saved, dict):
                continue
            if isinstance(saved.get("wallets"), dict):
                wallets = saved["wallets"]
                meta = saved.get("_meta", {})
            else:  # backward compatibility with earlier state_json rows
                wallets, meta = saved, {}
            if not all(s in wallets and isinstance(wallets[s], dict) for s in symbols):
                continue
            recovered = {}
            for symbol in symbols:
                w = dict(wallets[symbol])
                w.setdefault("last_processed_candle", "")
                w.setdefault("closed_trades", [])
                w.setdefault("positions", [])
                w.setdefault("loss_streak", 0)
                w.setdefault("cooldown_until", "")
                recovered[symbol] = w
            if row[0] == run_date:
                return recovered, {
                    "processed_symbols": list(meta.get("processed_symbols", [])),
                    "decision_rows": dict(meta.get("decision_rows", {})),
                    "run_date": run_date,
                }
            return recovered, {"processed_symbols": [], "decision_rows": {}, "run_date": run_date}
        except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
            continue
    return _initial_wallets(symbols, capital), {
        "processed_symbols": [], "decision_rows": {}, "run_date": run_date
    }


def _state_payload(wallets, meta):
    return json.dumps({"wallets": wallets, "_meta": meta}, separators=(",", ":"), sort_keys=True)


def _portfolio_totals(wallets):
    total_equity = sum(equity(w) for w in wallets.values())
    total_cash = sum(float(w["cash"]) for w in wallets.values())
    total_realized = sum(
        float(t.get("net_pnl", 0))
        for w in wallets.values() for t in w.get("closed_trades", [])
    )
    total_unrealized = sum(unrealized_pnl(w) for w in wallets.values())
    return total_equity, total_cash, total_realized, total_unrealized


def _checkpoint(store, run_date, capital, wallets, meta, status="RUNNING", notes="Checkpoint"):
    total_equity, _, total_realized, total_unrealized = _portfolio_totals(wallets)
    store.upsert_daily_run([
        run_date, status, total_equity, total_realized, total_unrealized,
        notes, _state_payload(wallets, meta),
    ])


def run():
    cfg = load_settings()
    run_date = datetime.now(timezone.utc).date().isoformat()
    capital = float(cfg["initial_capital_usd"])
    symbols = cfg["symbols"]
    if not symbols:
        raise RuntimeError("No trading symbols configured")

    store = SheetsStore()
    market = CoinGeckoClient()
    trader = LLMTrader()
    run_rows = store.book.worksheet("DailyRuns").get_all_values()

    if any(r and r[0] == run_date and len(r) > 1 and r[1] == "SUCCESS" for r in run_rows[1:]):
        print(f"Run for {run_date} already completed; skipping.")
        return

    wallets, meta = _recover_state(run_rows, list(symbols), capital, run_date)
    # Old PARTIAL rows did not have checkpoints. Recover completed decisions from
    # the decision ledger so retries don't execute a second same-day entry.
    decision_values = store.book.worksheet("Decisions").get_all_values()
    existing_today = {}
    if decision_values:
        for row in decision_values[1:]:
            if len(row) > 7 and row[0] == run_date and row[1]:
                existing_today[row[1]] = row
    # Repair legacy records where provider failure was incorrectly saved as HOLD.
    for symbol in list(meta["processed_symbols"]):
        saved = meta["decision_rows"].get(symbol) or existing_today.get(symbol)
        if (
            saved
            and len(saved) > 11
            and str(saved[11]).startswith("All configured LLM providers failed:")
        ):
            meta["processed_symbols"].remove(symbol)
            meta["decision_rows"].pop(symbol, None)
            print(f"{symbol}: legacy inference failure detected; retry enabled")

    for symbol, row in existing_today.items():
        model_reason = str(row[11]) if len(row) > 11 else ""
        if (
            symbol not in meta["processed_symbols"]
            and not str(row[7]).startswith("Screening error:")
            and not model_reason.startswith("All configured LLM providers failed:")
        ):
            meta["processed_symbols"].append(symbol)
            meta["decision_rows"].setdefault(symbol, row[:12])

    _checkpoint(store, run_date, capital, wallets, meta, notes="Run started / recovered")

    failures = []
    for symbol, coin_meta in symbols.items():
        if symbol in meta["processed_symbols"]:
            # Repair a decision write if the process stopped after checkpointing state.
            saved_decision = meta["decision_rows"].get(symbol)
            if saved_decision:
                store.append_decision(saved_decision)
            continue

        wallet = wallets[symbol]
        try:
            price, change = market.current_price(coin_meta["coingecko_id"])
            price = float(price)
            if not math.isfinite(price) or price <= 0:
                raise RuntimeError("Invalid market price")

            candles = market.daily_candles(coin_meta["coingecko_id"], days=30)
            if len(candles) < 3:
                raise RuntimeError("Insufficient completed daily candles")

            # Reject incomplete sequences instead of silently skipping a missed stop/target.
            candle_dates = [date.fromisoformat(c["date"]) for c in candles]
            if candle_dates != sorted(set(candle_dates)):
                raise RuntimeError("Candle dates are duplicated or unsorted")
            if any((b - a).days != 1 for a, b in zip(candle_dates, candle_dates[1:])):
                raise RuntimeError("Candle history contains a date gap; refusing to trade")
            cursor = wallet.get("last_processed_candle", "")
            if cursor:
                cursor_date = date.fromisoformat(cursor)
                if candle_dates[0] > cursor_date + timedelta(days=1):
                    raise RuntimeError(
                        f"Missed-candle gap: saved cursor {cursor}, oldest available {candles[0]['date']}"
                    )
                pending = [c for c in candles if c["date"] > cursor]
            else:
                # Start the paper account now; do not simulate pre-launch history.
                pending = [candles[-1]]

            closed_now = []
            for candle in pending:
                closed_now.extend(process_exits(
                    wallet, candle, fee_rate=cfg["fee_rate"],
                    slippage_pct=cfg["slippage_pct"], run_date=candle["date"],
                ))
                wallet["last_processed_candle"] = candle["date"]

            for position in wallet["positions"]:
                position["last_price"] = price

            # Only one strategy evaluation / entry per symbol per UTC day.
            latest, previous = candles[-1], candles[-2]
            cooldown = bool(wallet.get("cooldown_until") and run_date < wallet["cooldown_until"])
            proposal = trader.decide(symbol, price, change, candles[-5:])

# Provider outage is a screening failure, not a model HOLD.
# Leave this symbol unprocessed so a later run can retry it.
            if proposal.get("_inference_failed"):
                raise RuntimeError(
                    "LLM inference unavailable; symbol remains retryable. "
                    + str(proposal.get("reason", "No provider succeeded"))
                )

            validation = validate_and_size(
                proposal, symbol=symbol, price=price, previous_candle=previous,
                equity=equity(wallet), cash=float(wallet["cash"]),
                risk_pct=cfg["risk_per_trade_pct"], stop_buffer_pct=cfg["stop_buffer_pct"],
                reward_risk_ratio=cfg["reward_risk_ratio"], allow_short=cfg["allow_short"],
                has_open_position=bool(wallet["positions"]), in_cooldown=cooldown,
            )

            if validation.accepted:
                side = str(proposal["action"]).upper()
                stop, target = float(proposal["stop_price"]), float(proposal["target_price"])
                risk = price - stop if side == "LONG" else stop - price
                reward = target - price if side == "LONG" else price - target
                if not (
                    math.isfinite(risk) and math.isfinite(reward) and risk > 0
                    and reward >= risk * float(cfg["reward_risk_ratio"])
                ):
                    validation.accepted = False
                    validation.reason = "Target below configured reward/risk ratio or invalid risk/reward values"

            if validation.accepted:
                position = open_position(
                    wallet, symbol=symbol, action=str(proposal["action"]).upper(),
                    price=price, quantity=validation.quantity,
                    stop=float(proposal["stop_price"]), target=float(proposal["target_price"]),
                    fee_rate=cfg["fee_rate"], slippage_pct=cfg["slippage_pct"], run_date=run_date,
                )
                if position is None:
                    validation.accepted = False
                    validation.reason = "Insufficient cash after fees/slippage"
                else:
                    position["last_price"] = price

            decision = [
                run_date, symbol, price, change if change is not None else "",
                proposal.get("action", ""), proposal.get("confidence", ""),
                validation.accepted, validation.reason,
                validation.quantity if validation.accepted else 0,
                validation.notional if validation.accepted else 0,
                validation.risk_usd if validation.accepted else 0,
                proposal.get("reason", ""),
            ]
            meta["processed_symbols"].append(symbol)
            meta["decision_rows"][symbol] = decision
            # Persist state before ledger writes. On restart, processed symbols will not
            # execute a second time; the decision and closed-trade ledgers are repaired.
            _checkpoint(store, run_date, capital, wallets, meta, notes=f"Checkpoint after {symbol}")
            store.append_decision(decision)

        except Exception as exc:
            # If this symbol was checkpointed, the exception happened during a
            # persistence operation. Fail the job so a rerun can repair the saved
            # decision/ledger; do not mislabel a committed trade as a screening error.
            if symbol in meta["processed_symbols"]:
                raise
            message = f"{symbol}: {type(exc).__name__}: {exc}"
            print(f"Screening failed: {message}")
            failures.append(message)
            decision = [
                run_date, symbol, "", "", "", "", False,
                f"Screening error: {message}", 0, 0, 0, "",
            ]
            # Failed symbols remain retryable. Upsert the error row, then checkpoint
            # the unchanged/partially processed wallet state.
            meta["decision_rows"][symbol] = decision
            store.append_decision(decision)
            _checkpoint(store, run_date, capital, wallets, meta, notes=f"Partial failure: {message}")

    # Reconcile all saved closed trades on every run; trade_id deduplication repairs
    # a crash between wallet checkpoint and ledger append.
    for wallet in wallets.values():
        for trade in wallet.get("closed_trades", []):
            store.append_trade(trade)

    positions = []
    for symbol, wallet in wallets.items():
        for position in wallet.get("positions", []):
            mark = float(position.get("last_price", position["entry_price"]))
            entry, quantity = float(position["entry_price"]), float(position["quantity"])
            upnl = (mark - entry) * quantity if position["side"] == "LONG" else (entry - mark) * quantity
            positions.append([
                symbol, position["side"], position["opened_on"], entry, quantity,
                position["stop_price"], position["target_price"], mark, upnl, equity(wallet),
            ])
    store.replace_positions(positions)

    total_equity, total_cash, total_realized, total_unrealized = _portfolio_totals(wallets)
    store.update_overview([
        run_date, capital, total_cash, total_equity, total_realized, total_unrealized,
        (total_equity / capital - 1) * 100,
        *[equity(wallets[symbol]) for symbol in symbols],
    ])

    status = "PARTIAL" if failures else "SUCCESS"
    _checkpoint(
        store, run_date, capital, wallets, meta, status=status,
        notes="Paper trading only; isolated per-coin wallets. " +
              ("Failures: " + " | ".join(failures) if failures else "All coins screened"),
    )
    print(f"Paper run {status}: {run_date}; equity=${total_equity:.2f}; failures={len(failures)}")


if __name__ == "__main__":
    run()
