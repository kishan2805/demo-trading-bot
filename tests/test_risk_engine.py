import unittest, math
from src.risk_engine import validate_and_size
CANDLE={"low":95.0,"high":105.0}
class RiskTests(unittest.TestCase):
    def proposal(self,**kw):
        d={"action":"LONG","confidence":0.8,"stop_price":94.0,"target_price":112.0}; d.update(kw); return d
    def validate(self, proposal=None, **kw):
        args={"symbol":"BTC","price":100,"previous_candle":CANDLE,"equity":1000,"cash":1000}; args.update(kw)
        return validate_and_size(proposal or self.proposal(), **args)
    def test_risk_and_equity_caps(self):
        v=self.validate(); self.assertTrue(v.accepted,v.reason); self.assertLessEqual(v.risk_usd,5+1e-8); self.assertLessEqual(v.notional,1000+1e-8)
    def test_bad_stop_rejected(self): self.assertFalse(self.validate(self.proposal(stop_price=96)).accepted)
    def test_short_disabled(self): self.assertFalse(self.validate(self.proposal(action="SHORT",stop_price=106,target_price=90)).accepted)
    def test_no_add(self): self.assertFalse(self.validate(has_open_position=True).accepted)
    def test_cooldown(self): self.assertFalse(self.validate(in_cooldown=True).accepted)
    def test_reward_risk_rejected(self):
        v=self.validate(self.proposal(target_price=108)); self.assertFalse(v.accepted); self.assertIn("reward/risk",v.reason.lower())
    def test_nan_stop_rejected(self): self.assertFalse(self.validate(self.proposal(stop_price=math.nan)).accepted)
    def test_infinite_target_rejected(self): self.assertFalse(self.validate(self.proposal(target_price=math.inf)).accepted)
    def test_nan_confidence_rejected(self): self.assertFalse(self.validate(self.proposal(confidence=math.nan)).accepted)
    def test_invalid_previous_candle_rejected(self): self.assertFalse(self.validate(previous_candle={"low":110,"high":105}).accepted)
    def test_invalid_cash_rejected(self): self.assertFalse(self.validate(cash=-1).accepted)
if __name__=="__main__": unittest.main()
