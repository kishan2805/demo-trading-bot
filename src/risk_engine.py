from dataclasses import dataclass
import math

@dataclass
class Validation:
    accepted: bool
    reason: str
    quantity: float = 0.0
    notional: float = 0.0
    risk_usd: float = 0.0

def validate_and_size(proposal, *, symbol, price, previous_candle, equity, cash,
                      risk_pct=0.005, stop_buffer_pct=0.002, reward_risk_ratio=2.0,
                      allow_short=False, has_open_position=False, in_cooldown=False):
    def reject(msg): return Validation(False, msg)
    if in_cooldown: return reject("Coin subwallet is in cooldown")
    if has_open_position: return reject("A position already exists; no adding or stacking")
    try:
        price, equity, cash = float(price), float(equity), float(cash)
        risk_pct, stop_buffer_pct, reward_risk_ratio = float(risk_pct), float(stop_buffer_pct), float(reward_risk_ratio)
        if not all(math.isfinite(x) for x in (price,equity,cash,risk_pct,stop_buffer_pct,reward_risk_ratio)):
            return reject("Non-finite account or configuration value")
        if price <= 0 or equity <= 0 or cash < 0 or cash > equity + 1e-8:
            return reject("Invalid price or account state")
        if not 0 < risk_pct <= .05: return reject("Risk percentage must be >0 and <=5%")
        if not 0 <= stop_buffer_pct < 1: return reject("Invalid stop buffer")
        if reward_risk_ratio < 1: return reject("Reward/risk ratio must be at least 1")
        if not isinstance(proposal, dict): return reject("Proposal must be an object")
        action = str(proposal.get("action", "HOLD")).upper()
        if action == "HOLD": return reject("LLM chose HOLD")
        if action not in ("LONG", "SHORT"): return reject("Unsupported action")
        if action == "SHORT" and not allow_short: return reject("Shorts disabled by configuration")
        stop, target, confidence = float(proposal["stop_price"]), float(proposal["target_price"]), float(proposal["confidence"])
        prev_low, prev_high = float(previous_candle["low"]), float(previous_candle["high"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return reject("Missing or invalid proposal, candle, or account values")
    if not all(math.isfinite(x) for x in (stop,target,confidence,prev_low,prev_high)):
        return reject("Non-finite stop, target, confidence, or candle value")
    if prev_low <= 0 or prev_high < prev_low: return reject("Invalid previous candle")
    if not 0 <= confidence <= 1: return reject("Confidence must be between 0 and 1")
    if action == "LONG":
        if not (0 < stop < price and stop <= prev_low*(1-stop_buffer_pct)*1.000001): return reject("Long stop must be below previous daily low with configured buffer")
        if target <= price: return reject("Long target must be above entry")
        risk, reward = price-stop, target-price
    else:
        if not (stop > price and stop >= prev_high*(1+stop_buffer_pct)*.999999): return reject("Short stop must be above previous daily high with configured buffer")
        if target >= price: return reject("Short target must be below entry")
        risk, reward = stop-price, price-target
    if risk <= 0 or reward <= 0: return reject("Risk and reward must be positive")
    if reward < risk*reward_risk_ratio: return reject("Target below configured reward/risk ratio")
    quantity = min(equity*risk_pct/risk, cash/price, equity/price)
    notional, risk_usd = quantity*price, quantity*risk
    if not all(math.isfinite(x) for x in (quantity,notional,risk_usd)) or quantity <= 0: return reject("Position size is zero or invalid")
    return Validation(True, "Accepted by deterministic risk engine", quantity, notional, risk_usd)
