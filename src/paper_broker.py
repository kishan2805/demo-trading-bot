from datetime import date, timedelta
import hashlib
import math


def open_position(wallet, *, symbol, action, price, quantity, stop, target,
                  fee_rate, slippage_pct, run_date):
    action = str(action).upper()
    if action not in ("LONG", "SHORT"):
        raise ValueError("action must be LONG or SHORT")
    values = [price, quantity, stop, target, fee_rate, slippage_pct]
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError("non-finite order value")
    price, quantity = float(price), float(quantity)
    if price <= 0 or quantity <= 0 or fee_rate < 0 or not 0 <= slippage_pct < 1:
        raise ValueError("invalid order values")
    # Shorts remain unsupported by default in the risk engine. Their accounting
    # uses collateral-style cash treatment if enabled explicitly.
    fill = price * (1 + slippage_pct if action == "LONG" else 1 - slippage_pct)
    notional, fee = fill * quantity, fill * quantity * fee_rate
    if action == "LONG":
        required = notional + fee
        if required > float(wallet["cash"]) + 1e-8:
            return None
        wallet["cash"] -= required
    else:
        # Reserve sale proceeds in cash; equity formula below treats this as
        # collateral accounting. Do not enable shorts without additional tests.
        required = fee
        if required > float(wallet["cash"]) + 1e-8:
            return None
        wallet["cash"] -= fee
        wallet["cash"] += notional
    p = {"symbol": symbol, "side": action, "entry_price": fill,
         "quantity": quantity, "stop_price": float(stop),
         "target_price": float(target), "entry_fee": fee,
         "opened_on": run_date, "last_price": price}
    wallet.setdefault("positions", []).append(p)
    return p


def close_position(wallet, p, price, *, reason, fee_rate, slippage_pct, run_date):
    side, qty = p["side"], float(p["quantity"])
    price = float(price)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("invalid exit price")
    # A gap-aware caller supplies the candle open/trigger price as appropriate.
    fill = price * (1 - slippage_pct if side == "LONG" else 1 + slippage_pct)
    fee = fill * qty * fee_rate
    entry = float(p["entry_price"])
    if side == "LONG":
        wallet["cash"] += fill * qty - fee
        gross = (fill - entry) * qty
    else:
        # Sale proceeds were credited on entry; buy back the position on exit.
        wallet["cash"] -= fill * qty + fee
        gross = (entry - fill) * qty
    entry_fee = float(p.get("entry_fee", 0))
    net = gross - entry_fee - fee
    identity = "|".join([str(p.get("symbol", "")), str(p.get("opened_on", "")),
                         side, str(run_date), str(reason), str(entry), str(qty)])
    trade_id = hashlib.sha256(identity.encode()).hexdigest()[:20]
    trade = {"trade_id": trade_id, "symbol": p["symbol"], "side": side,
             "opened_on": p["opened_on"], "closed_on": run_date,
             "entry_price": entry, "exit_price": fill, "quantity": qty,
             "gross_pnl": gross, "fees": entry_fee + fee, "net_pnl": net,
             "exit_reason": reason}
    wallet["positions"].remove(p)
    closed = wallet.setdefault("closed_trades", [])
    if not any(t.get("trade_id") == trade_id for t in closed):
        closed.append(trade)
    if net < 0:
        wallet["loss_streak"] = wallet.get("loss_streak", 0) + 1
        if wallet["loss_streak"] >= 3:
            wallet["cooldown_until"] = (date.fromisoformat(run_date) + timedelta(days=3)).isoformat()
    else:
        wallet["loss_streak"] = 0
    return trade


def process_exits(wallet, candle, *, fee_rate, slippage_pct, run_date):
    """Process one completed OHLC candle; stop wins if both levels are touched.

    If price gaps through a stop at the open, use the worse opening price.
    Targets crossed at the open are filled at the target level (conservative).
    """
    closed = []
    o, h, l, c = [float(candle[k]) for k in ("open", "high", "low", "close")]
    if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)) or l > min(o, c) or h < max(o, c) or l > h:
        raise ValueError("invalid OHLC candle")
    for p in list(wallet.get("positions", [])):
        p["last_price"] = c
        side = p["side"]
        stop, target = float(p["stop_price"]), float(p["target_price"])
        if side == "LONG":
            stop_hit, target_hit = l <= stop, h >= target
            stop_fill = min(o, stop) if stop_hit else stop
            target_fill = min(o, target) if target_hit else target
        else:
            stop_hit, target_hit = h >= stop, l <= target
            stop_fill = max(o, stop) if stop_hit else stop
            target_fill = max(o, target) if target_hit else target
        if stop_hit:
            closed.append(close_position(wallet, p, stop_fill, reason="STOP", fee_rate=fee_rate, slippage_pct=slippage_pct, run_date=run_date))
        elif target_hit:
            closed.append(close_position(wallet, p, target_fill, reason="TARGET", fee_rate=fee_rate, slippage_pct=slippage_pct, run_date=run_date))
    return closed


def unrealized_pnl(wallet):
    total = 0.0
    for p in wallet.get("positions", []):
        mark, entry, qty = float(p.get("last_price", p["entry_price"])), float(p["entry_price"]), float(p["quantity"])
        total += (mark - entry) * qty if p["side"] == "LONG" else (entry - mark) * qty
    return total


def equity(wallet):
    # Cash accounting for LONG: cash excludes cost basis; add marked position value.
    # SHORTs use sale proceeds held in cash, so subtract liability at mark.
    value = float(wallet["cash"])
    for p in wallet.get("positions", []):
        mark, entry, qty = float(p.get("last_price", p["entry_price"])), float(p["entry_price"]), float(p["quantity"])
        value += qty * mark if p["side"] == "LONG" else -qty * mark
    return value
