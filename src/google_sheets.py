import base64, json, os
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADERS = {
    "Overview": ["run_date","initial_capital_usd","cash_usd","equity_usd","realized_pnl_usd","unrealized_pnl_usd","return_pct","BTC_equity","ETH_equity","SOL_equity","SUI_equity"],
    "Decisions": ["run_date","symbol","price_usd","change_24h_pct","action","confidence","accepted","reason","quantity","notional_usd","risk_usd","model_reason","decision_id"],
    "Positions": ["symbol","side","entry_date","entry_price","quantity","stop_price","target_price","last_price","unrealized_pnl_usd","subwallet_equity_usd"],
    "Trades": ["symbol","side","opened_on","closed_on","entry_price","exit_price","quantity","gross_pnl","fees","net_pnl","exit_reason","trade_id"],
    "DailyRuns": ["run_date","status","total_equity_usd","total_realized_pnl_usd","total_unrealized_pnl_usd","notes","state_json"],
}

def _credentials():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64")
    if not raw and b64:
        raw = base64.b64decode(b64).decode("utf-8")
    if not raw:
        raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_JSON_BASE64")
    return Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)

class SheetsStore:
    def __init__(self, spreadsheet_id=None):
        self.spreadsheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEET_ID")
        if not self.spreadsheet_id:
            raise RuntimeError("GOOGLE_SHEET_ID is not set")
        self.client = gspread.authorize(_credentials())
        self.book = self.client.open_by_key(self.spreadsheet_id)
        existing = {w.title for w in self.book.worksheets()}
        for title, headers in HEADERS.items():
            if title not in existing:
                self.book.add_worksheet(title=title, rows=3000, cols=max(16, len(headers)))
            ws = self.book.worksheet(title)
            values = ws.get_all_values()
            if not values:
                ws.append_row(headers)
            elif values[0] != headers:
                # Migrate older tabs by appending new header columns, never clearing history.
                old = values[0]
                merged = old + [h for h in headers if h not in old]
                if merged != old:
                    ws.update(range_name=f"A1:{gspread.utils.rowcol_to_a1(1, len(merged))}", values=[merged])

    def append(self, tab, row):
        self.book.worksheet(tab).append_row(row, value_input_option="USER_ENTERED")

    def upsert_daily_run(self, row):
        """Keep one DailyRuns row per UTC date; retries update the existing row."""
        ws = self.book.worksheet("DailyRuns")
        values = ws.get_all_values()
        for idx, existing in enumerate(values[1:], start=2):
            if existing and existing[0] == str(row[0]):
                ws.update(
                    range_name=f"A{idx}:{gspread.utils.rowcol_to_a1(idx, len(row))}",
                    values=[row],
                    value_input_option="USER_ENTERED",
                )
                return
        ws.append_row(row, value_input_option="USER_ENTERED")

    def append_decision(self, row):
        """Upsert by UTC date + symbol so a retry cannot duplicate a decision."""
        ws = self.book.worksheet("Decisions")
        vals = ws.get_all_values()
        headers = vals[0] if vals else HEADERS["Decisions"]
        decision_id = f"{row[0]}:{row[1]}"
        values = list(row[:12])
        if "decision_id" in headers:
            values.append(decision_id)
        for idx, existing in enumerate(vals[1:], start=2):
            if len(existing) > 1 and existing[0] == str(row[0]) and existing[1] == str(row[1]):
                # Pad to the existing schema width to avoid leaving stale trailing cells.
                width = max(len(headers), len(values))
                values = values + [""] * (width - len(values))
                ws.update(
                    range_name=f"A{idx}:{gspread.utils.rowcol_to_a1(idx, width)}",
                    values=[values],
                    value_input_option="USER_ENTERED",
                )
                return
        ws.append_row(values, value_input_option="USER_ENTERED")

    def append_trade(self, trade):
        ws = self.book.worksheet("Trades")
        vals = ws.get_all_values()
        headers = vals[0] if vals else HEADERS["Trades"]
        trade_id = trade.get("trade_id")
        if not trade_id:
            raise ValueError("Closed trade missing deterministic trade_id")
        if "trade_id" not in headers:
            # Add the key at the end so historical columns stay aligned.
            headers = headers + ["trade_id"]
            ws.update(range_name=f"A1:{gspread.utils.rowcol_to_a1(1, len(headers))}", values=[headers])
            vals = ws.get_all_values()
        col = headers.index("trade_id")
        if any(len(r) > col and r[col] == trade_id for r in vals[1:]):
            return
        row = [trade.get(k, "") for k in headers]
        ws.append_row(row, value_input_option="USER_ENTERED")

    def replace_positions(self, rows):
        ws = self.book.worksheet("Positions")
        ws.clear()
        ws.append_row(HEADERS["Positions"])
        if rows:
            ws.append_rows(rows, value_input_option="USER_ENTERED")

    def update_overview(self, row):
        ws = self.book.worksheet("Overview")
        ws.clear()
        ws.append_row(HEADERS["Overview"])
        ws.append_row(row, value_input_option="USER_ENTERED")
