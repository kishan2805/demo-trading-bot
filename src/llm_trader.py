
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import (
    OpenAI,
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)
PROMPT = (ROOT / "prompts" / "trader.md").read_text(encoding="utf-8")


class LLMTrader:
    """NVIDIA first, Gemini fallback, bounded retries, fail-closed."""

    def __init__(self, api_key=None, model=None):
        self.nvidia_key = api_key or os.getenv("NVIDIA_API_KEY", "")
        self.nvidia_model = model or os.getenv(
            "NVIDIA_MODEL", "deepseek-ai/deepseek-v4.1-flash"
        )
        self.gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
        self.max_attempts = max(
            1, min(3, int(os.getenv("LLM_MAX_ATTEMPTS", "2")))
        )
        self.provider = os.getenv("LLM_PROVIDER", "nvidia").lower()

        self.client = (
            OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=self.nvidia_key,
                timeout=self.timeout,
                max_retries=0,
            )
            if self.nvidia_key
            else None
        )

        if not self.client and not self.gemini_key:
            raise RuntimeError("Set NVIDIA_API_KEY and/or GEMINI_API_KEY")

    @staticmethod
    def _parse(text):
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise ValueError("LLM response must be a JSON object")

        action = str(obj.get("action", "HOLD")).upper()
        if action not in ("LONG", "SHORT", "HOLD"):
            raise ValueError("Invalid action in LLM response")

        obj["action"] = action
        obj["confidence"] = float(obj.get("confidence", 0))

        if not 0 <= obj["confidence"] <= 1:
            raise ValueError("Invalid confidence")

        for key in ("stop_price", "target_price"):
            if key not in obj and action != "HOLD":
                raise ValueError(f"Missing {key}")

        obj.setdefault("reason", "No reason provided")
        return obj

    @staticmethod
    def _transient_nvidia(exc):
        if isinstance(
            exc,
            (APITimeoutError, APIConnectionError, RateLimitError,
             InternalServerError, requests.exceptions.Timeout),
        ):
            return True

        status = getattr(exc, "status_code", None)
        return status in (408, 409, 429, 500, 502, 503, 504)

    @staticmethod
    def _transient_gemini_status(status):
        return status in (408, 409, 429, 500, 502, 503, 504)

    def _retry(self, provider, operation, is_transient):
        for attempt in range(1, self.max_attempts + 1):
            try:
                return operation()
            except Exception as exc:
                retry = is_transient(exc) and attempt < self.max_attempts
                if not retry:
                    raise

                delay = min(8, 2 ** (attempt - 1))
                print(
                    f"{provider} transient failure "
                    f"({type(exc).__name__}); retry "
                    f"{attempt + 1}/{self.max_attempts} in {delay}s"
                )
                time.sleep(delay)

    def _nvidia(self, payload):
        if not self.client:
            raise RuntimeError("NVIDIA_API_KEY not configured")

        def call():
            response = self.client.chat.completions.create(
                model=self.nvidia_model,
                messages=[
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": json.dumps(payload)},
                ],
                temperature=0.1,
                max_tokens=700,
            )
            content = response.choices[0].message.content
            return self._parse(content), self.nvidia_model

        return self._retry(
            "nvidia", call, self._transient_nvidia
        )

    def _gemini(self, payload):
        if not self.gemini_key:
            raise RuntimeError("GEMINI_API_KEY not configured")

        url = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{self.gemini_model}:generateContent"
        )
        body = {
            "systemInstruction": {"parts": [{"text": PROMPT}]},
            "contents": [
                {"role": "user", "parts": [{"text": json.dumps(payload)}]}
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1200,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }

        def call():
            response = requests.post(
                url,
                params={"key": self.gemini_key},
                json=body,
                timeout=(10, self.timeout),
            )

            if not response.ok:
                # Avoid logging response bodies that might contain sensitive data.
                error = RuntimeError(
                    f"Gemini HTTP {response.status_code}"
                )
                error.status_code = response.status_code
                raise error

            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError(
                    "Gemini returned no candidates; response may have "
                    "been blocked or truncated"
                )

            parts = candidates[0].get("content", {}).get("parts", [])
            text = next(
                (part["text"] for part in parts if "text" in part), None
            )
            if not text:
                raise ValueError("Gemini returned no text content")

            return self._parse(text), self.gemini_model

        def transient(exc):
            if isinstance(
                exc,
                (requests.exceptions.Timeout,
                 requests.exceptions.ConnectionError),
            ):
                return True
            return self._transient_gemini_status(
                getattr(exc, "status_code", None)
            )

        return self._retry("gemini", call, transient)

    def decide(self, symbol, price, change_24h, candles):
        payload = {
            "symbol": symbol,
            "current_price_usd": price,
            "change_24h_pct": change_24h,
            "daily_candles_utc": candles[-5:],
        }

        providers = (
            ["gemini", "nvidia"]
            if self.provider == "gemini"
            else ["nvidia", "gemini"]
        )
        errors = []

        for provider in providers:
            try:
                if provider == "nvidia":
                    result, model = self._nvidia(payload)
                else:
                    result, model = self._gemini(payload)

                result["_provider"] = provider
                result["_model"] = model
                return result

            except Exception as exc:
                status = getattr(exc, "status_code", None)
                message = (
                    f"{provider}: {type(exc).__name__}"
                    + (f" HTTP {status}" if status else "")
                )
                errors.append(message)
                print(f"{symbol} {message}; trying fallback if available")

        # Fail closed. Do not fabricate a trading signal.
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "stop_price": 0.0,
            "target_price": 0.0,
            "reason": "Inference unavailable: " + "; ".join(errors),
            "_provider": "none",
            "_model": "none",
            "_inference_failed": True,
        }
