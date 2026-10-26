"""Verify Google Sheets credentials and write one clearly labeled test row."""
import hashlib
from datetime import datetime, timezone
from src.google_sheets import SheetsStore


def main():
    store = SheetsStore()
    title = "SystemChecks"
    existing = {ws.title for ws in store.book.worksheets()}
    if title not in existing:
        store.book.add_worksheet(title=title, rows=100, cols=4)
    ws = store.book.worksheet(title)
    if not ws.get_all_values():
        ws.append_row(["check_id", "timestamp_utc", "status", "message"])
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    check_id = hashlib.sha256(f"sheets-smoke:{now}".encode()).hexdigest()[:16]
    ws.append_row([check_id, now, "PASS", "Paper-trader Sheets connectivity test; no trades executed"])
    print(f"Google Sheets write test PASSED. Spreadsheet: {store.book.title}; tab: {title}; check_id: {check_id}")

if __name__ == "__main__":
    main()
