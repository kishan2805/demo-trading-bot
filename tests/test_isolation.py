import unittest

def create_wallets(symbols, capital):
    allocation=capital/len(symbols)
    return {s:{"symbol":s,"cash":allocation,"positions":[],"closed_trades":[],"loss_streak":0,"cooldown_until":"","last_processed_candle":""} for s in symbols}

class IsolationTests(unittest.TestCase):
    def setUp(self): self.wallets=create_wallets(("BTC","ETH","SOL","SUI"),10000)
    def test_equal_four_way_split(self):
        self.assertAlmostEqual(sum(w["cash"] for w in self.wallets.values()),10000); self.assertTrue(all(w["cash"]==2500 for w in self.wallets.values()))
    def test_independent_positions(self):
        self.wallets["BTC"]["positions"].append({"symbol":"BTC"}); self.assertEqual(self.wallets["ETH"]["positions"],[])
    def test_independent_cooldowns(self):
        self.wallets["BTC"]["cooldown_until"]="2026-10-29"; self.assertEqual(self.wallets["ETH"]["cooldown_until"],"")
    def test_independent_trade_history(self):
        self.wallets["ETH"]["closed_trades"].append({"net_pnl":-10}); self.assertEqual(self.wallets["BTC"]["closed_trades"],[])
    def test_independent_loss_streak(self):
        self.wallets["SOL"]["loss_streak"]=2; self.assertEqual(self.wallets["BTC"]["loss_streak"],0)
if __name__=="__main__": unittest.main()
