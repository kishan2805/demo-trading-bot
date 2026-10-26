from pathlib import Path
import os, yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

def load_settings():
    with open(ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.getenv("INITIAL_CAPITAL"):
        cfg["initial_capital_usd"] = float(os.environ["INITIAL_CAPITAL"])
    return cfg
