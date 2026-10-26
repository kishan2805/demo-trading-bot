import unittest
from src.paper_broker import process_exits, open_position, close_position, equity

class BrokerTests(unittest.TestCase):
    def test_stop_first_when_both_hit(self):
        w={"symbol":"BTC","cash":900,"positions":[{"symbol":"BTC","side":"LONG","entry_price":100,"quantity":1,"stop_price":90,"target_price":110,"entry_fee":0,"opened_on":"2026-01-01","last_price":100}],"loss_streak":0}
        t=process_exits(w,{"open":100,"low":89,"high":111,"close":100},fee_rate=0,slippage_pct=0,run_date="2026-01-02")
        self.assertEqual(t[0]["exit_reason"],"STOP")
    def test_open_deducts_cash(self):
        w={"symbol":"BTC","cash":1000,"positions":[]}
        p=open_position(w,symbol="BTC",action="LONG",price=100,quantity=2,stop=90,target=120,fee_rate=.001,slippage_pct=0,run_date="2026-01-01")
        self.assertIsNotNone(p); self.assertAlmostEqual(w["cash"],799.8)
    def test_long_close_accounts_for_fees(self):
        w={"cash":799.8,"positions":[]}; p={"symbol":"BTC","side":"LONG","entry_price":100,"quantity":2,"entry_fee":.2,"opened_on":"2026-01-01"}; w["positions"].append(p)
        t=close_position(w,p,110,reason="TARGET",fee_rate=.001,slippage_pct=0,run_date="2026-01-02")
        self.assertAlmostEqual(t["gross_pnl"],20); self.assertAlmostEqual(t["fees"],.42); self.assertAlmostEqual(t["net_pnl"],19.58); self.assertAlmostEqual(w["cash"],1019.58)
    def test_gap_through_long_stop_fills_at_open(self):
        w={"cash":0,"positions":[{"symbol":"BTC","side":"LONG","entry_price":100,"quantity":1,"stop_price":90,"target_price":120,"entry_fee":0,"opened_on":"2026-01-01","last_price":100}],"loss_streak":0}
        t=process_exits(w,{"open":80,"high":85,"low":75,"close":82},fee_rate=0,slippage_pct=0,run_date="2026-01-02")
        self.assertEqual(t[0]["exit_reason"],"STOP"); self.assertEqual(t[0]["exit_price"],80)
    def test_long_equity_reconciles(self):
        w={"cash":899,"positions":[{"side":"LONG","entry_price":100,"quantity":1,"last_price":110}]}
        self.assertEqual(equity(w),1009)
    def test_trade_id_is_deterministic(self):
        def close_once():
            w={"cash":0,"positions":[],"closed_trades":[]}; p={"symbol":"BTC","side":"LONG","entry_price":100,"quantity":1,"entry_fee":0,"opened_on":"2026-01-01"}; w["positions"].append(p)
            return close_position(w,p,90,reason="STOP",fee_rate=0,slippage_pct=0,run_date="2026-01-02")["trade_id"]
        self.assertEqual(close_once(),close_once())
    def test_loss_streak_triggers_cooldown(self):
        w={"cash":100,"positions":[],"loss_streak":2,"closed_trades":[]}; p={"symbol":"BTC","side":"LONG","entry_price":100,"quantity":1,"entry_fee":0,"opened_on":"2026-01-01"}; w["positions"].append(p)
        close_position(w,p,90,reason="STOP",fee_rate=0,slippage_pct=0,run_date="2026-01-03")
        self.assertEqual(w["loss_streak"],3); self.assertEqual(w["cooldown_until"],"2026-01-06")
if __name__=="__main__": unittest.main()
