# -*- coding: utf-8 -*-
"""
FastAPI Backend for Trading Bot Control
"""

import os, json, asyncio, time, math, hashlib, platform, secrets, subprocess, uuid, statistics
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List, Tuple
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from cryptography.fernet import Fernet
import base64
from pathlib import Path
from datetime import datetime
from ccxt_compat import ccxtpro, get_ccxt_backend_name

from bot_core import TradingBot
from ai_agent import AITradingAgent, AIProvider, MarketContext
from liquidation_hunter import LiquidationHunter

# Import license system
from license import validate_license, get_all_licenses, create_license, is_local_license_active
SECURE_LICENSE = False

# Import secure storage and risk calculator
from secure_storage import secure_storage
from risk_calculator import RiskCalculator


app = FastAPI(title="Trading Bot API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Root endpoint
@app.get("/")
async def root():
    """API root endpoint"""
    return {
        "name": "modAI Trader API",
        "version": "2.0",
        "status": "running",
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "api": "/api/*"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "bots_running": len(bot_instances),
        "ai_agent_active": ai_agent_active,
        "ai_agent_configured": ai_agent is not None,
        "ccxt_backend": get_ccxt_backend_name()
    }


@app.get("/favicon.ico")
async def favicon():
    """Favicon endpoint - returns 🤖 emoji as response"""
    return {"emoji": "🤖"}

# Global state
bot_instances: Dict[str, TradingBot] = {}  # symbol -> bot instance
bot_tasks: Dict[str, asyncio.Task] = {}  # symbol -> task
credentials_store = {}
config_store = {}

# AI Agent
ai_agent: Optional[AITradingAgent] = None
ai_agent_active = False

POPULAR_SCANNER_SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOGE/USDT', 'DOT/USDT', 'AVAX/USDT', 'LINK/USDT',
    'POL/USDT', 'UNI/USDT', 'LTC/USDT', 'ATOM/USDT', 'XLM/USDT',
    'NEAR/USDT', 'APT/USDT', 'ARB/USDT', 'OP/USDT', 'SUI/USDT'
]

SYMBOL_CACHE_TTL_SECONDS = 300
_symbol_catalog_cache: List[Dict[str, str]] = []
_symbol_catalog_cache_ts: float = 0.0
TRADE_HISTORY_CACHE_TTL_SECONDS = 6
_trade_history_cache: Dict[str, Dict[str, Any]] = {}
TICKER_CACHE_TTL_SECONDS = 2
_ticker_cache: Dict[str, Dict[str, Any]] = {}
LIQ_SNAPSHOT_CACHE_TTL_SECONDS = 12
_liq_snapshot_cache: Dict[str, Dict[str, Any]] = {}
_last_stale_conditionals_cleanup_ts: float = 0.0
STALE_CONDITIONALS_CLEANUP_INTERVAL_SECONDS = 45
_stale_conditionals_task: Optional[asyncio.Task] = None
SIGNAL_STATE_TTL_SECONDS = 3600
_signal_governor_state: Dict[str, Dict[str, Any]] = {}

LICENSE_CHECK_CACHE_SECONDS = 20
_license_check_cache: Dict[str, Any] = {"ts": 0.0, "valid": False}
_device_fingerprint_cache: Optional[str] = None
LICENSE_GUARD_EXEMPT_PATHS = {
    "/api/license/activate",
    "/api/license/check",
    "/api/license/device-id",
    "/api/license/status",
}


# Paths
BASE_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
CREDENTIALS_FILE = BASE_DIR / ".credentials.enc"
KEY_FILE = BASE_DIR / ".key"

TEMPLATES_DIR.mkdir(exist_ok=True)


def _read_machine_hint() -> str:
    for machine_id_path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            if os.path.exists(machine_id_path):
                value = Path(machine_id_path).read_text(encoding="utf-8").strip()
                if value:
                    return value
        except Exception:
            pass

    if platform.system().lower() == "windows":
        try:
            output = subprocess.check_output(
                [
                    "reg",
                    "query",
                    r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Cryptography",
                    "/v",
                    "MachineGuid",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
            for line in output.splitlines():
                if "MachineGuid" in line:
                    parts = line.split()
                    if parts:
                        return parts[-1].strip()
        except Exception:
            pass

    if platform.system().lower() == "darwin":
        try:
            output = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
            for line in output.splitlines():
                if "IOPlatformUUID" in line and '"' in line:
                    return line.split('"')[-2].strip()
        except Exception:
            pass

    return ""


def get_local_device_id() -> str:
    global _device_fingerprint_cache
    if _device_fingerprint_cache:
        return _device_fingerprint_cache

    raw_parts = [
        platform.system(),
        platform.release(),
        platform.machine(),
        platform.node(),
        hex(uuid.getnode()),
        _read_machine_hint(),
    ]
    digest = hashlib.sha256("|".join(raw_parts).encode("utf-8")).hexdigest()
    _device_fingerprint_cache = f"hw_{digest[:48]}"
    return _device_fingerprint_cache


def _set_license_cache(valid: bool) -> None:
    _license_check_cache["ts"] = time.time()
    _license_check_cache["valid"] = bool(valid)


def is_license_active_for_device(force_refresh: bool = False) -> bool:
    now = time.time()
    cache_age = now - float(_license_check_cache.get("ts", 0.0))
    if not force_refresh and cache_age < LICENSE_CHECK_CACHE_SECONDS:
        return bool(_license_check_cache.get("valid", False))

    current_device = get_local_device_id()
    try:
        valid = bool(is_local_license_active(current_device))
    except Exception:
        valid = False

    _set_license_cache(valid)
    return valid


def require_admin_token(request: Request) -> None:
    expected = os.getenv("MODAI_ADMIN_TOKEN", "").strip()
    provided = str(request.headers.get("x-admin-token") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Admin operations are disabled for this runtime.",
        )
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")


@app.middleware("http")
async def enforce_license_guard(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if path in LICENSE_GUARD_EXEMPT_PATHS:
        return await call_next(request)
    if is_license_active_for_device():
        return await call_next(request)
    return JSONResponse(
        status_code=403,
        content={"detail": "License is not active on this device. Activate your license to continue."},
    )


def resolve_log_file_path(raw_path: str) -> str:
    value = str(raw_path or "").strip() or "logs/bot.jsonl"
    if os.path.isabs(value):
        return value

    data_dir = os.getenv("MODAI_DATA_DIR", "").strip()
    if data_dir:
        try:
            target_dir = Path(data_dir) / "logs"
            target_dir.mkdir(parents=True, exist_ok=True)
            return str((target_dir / Path(value).name).resolve())
        except Exception:
            pass

    return str((BASE_DIR / value).resolve())


# Encryption helpers
def get_or_create_key():
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    return key


def encrypt_credentials(api_key: str, secret_key: str) -> bytes:
    key = get_or_create_key()
    f = Fernet(key)
    data = json.dumps({"api_key": api_key, "secret_key": secret_key})
    return f.encrypt(data.encode())


def decrypt_credentials() -> Dict[str, str]:
    """Load credentials from secure storage"""
    creds = secure_storage.load_credentials()
    if not creds:
        return {}
    return {
        'api_key': creds.get('api_key', ''),
        'secret_key': creds.get('secret_key', ''),
        'is_testnet': creds.get('is_testnet', True)
    }


def _normalize_to_futures_symbol(raw: str) -> str:
    value = str(raw or "").strip().upper().replace(" ", "")
    if not value:
        return ""
    # CCXT futures symbols can arrive as BASE/USDT:USDT; normalize to BASE/USDT.
    if ":" in value:
        value = value.split(":", 1)[0]
    if "/" in value:
        base, quote = value.split("/", 1)
        quote = quote.split(":", 1)[0] if ":" in quote else quote
        return f"{base}/{quote or 'USDT'}"
    if value.endswith("USDT"):
        return f"{value[:-4]}/USDT"
    return f"{value}/USDT"


def _futures_symbol_code(raw: str) -> str:
    normalized = _normalize_to_futures_symbol(raw)
    if not normalized:
        return ""
    base, quote = normalized.split("/", 1)
    return f"{base}{quote}"


def _normalize_timeframe(raw: str) -> str:
    value = str(raw or "1h").strip().lower()
    allowed = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}
    return value if value in allowed else "1h"


def _resolve_micro_vol_threshold(config: Dict[str, Any], timeframe: str) -> float:
    tf = _normalize_timeframe(timeframe)
    base = _safe_float(config.get("micro_vol_threshold_bps"), 10.0)
    if tf == "1m":
        return _safe_float(config.get("micro_vol_threshold_bps_1m"), base)
    if tf == "5m":
        return _safe_float(config.get("micro_vol_threshold_bps_5m"), base)
    if tf == "15m":
        return _safe_float(config.get("micro_vol_threshold_bps_15m"), base)
    return base


def _parse_klines_rows(rows: Any) -> List[List[float]]:
    parsed: List[List[float]] = []
    if not isinstance(rows, list):
        return parsed
    for row in rows:
        try:
            parsed.append([
                int(row[0]),
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            ])
        except Exception:
            continue
    return parsed


def _ohlcv_from_rows(rows: List[List[float]]) -> Dict[str, List[float]]:
    return {
        "open": [candle[1] for candle in rows],
        "high": [candle[2] for candle in rows],
        "low": [candle[3] for candle in rows],
        "close": [candle[4] for candle in rows],
        "volume": [candle[5] for candle in rows],
    }


async def _fetch_futures_ohlcv_fast(exchange: Any, raw_symbol: str, timeframe: str, limit: int) -> List[List[float]]:
    normalized_symbol = _normalize_to_futures_symbol(raw_symbol)
    if not normalized_symbol:
        return []

    safe_limit = max(30, min(int(limit), 1000))
    interval = _normalize_timeframe(timeframe)
    symbol_code = _futures_symbol_code(normalized_symbol)

    # Primary path: raw futures klines endpoint (no load_markets call).
    try:
        raw_rows = await exchange.fapiPublicGetKlines({
            "symbol": symbol_code,
            "interval": interval,
            "limit": safe_limit
        })
        parsed = _parse_klines_rows(raw_rows)
        if parsed:
            return parsed
    except Exception:
        pass

    # Fallback to ccxt fetch_ohlcv (may load markets depending on backend).
    try:
        rows = await exchange.fetch_ohlcv(normalized_symbol, timeframe=interval, limit=safe_limit)
        return _parse_klines_rows(rows)
    except Exception:
        return []


async def _fetch_futures_ticker_fast(exchange: Any, raw_symbol: str) -> Dict[str, Any]:
    normalized_symbol = _normalize_to_futures_symbol(raw_symbol)
    if not normalized_symbol:
        return {"symbol": "", "last": 0.0, "percentage": 0.0, "quoteVolume": 0.0}

    symbol_code = _futures_symbol_code(normalized_symbol)

    try:
        row = await exchange.fapiPublicGetTicker24hr({"symbol": symbol_code})
        if isinstance(row, dict):
            return {
                "symbol": normalized_symbol,
                "last": _safe_float(row.get("lastPrice"), 0.0),
                "percentage": _safe_float(row.get("priceChangePercent"), 0.0),
                "quoteVolume": _safe_float(row.get("quoteVolume"), 0.0),
            }
    except Exception:
        pass

    try:
        ticker = await exchange.fetch_ticker(normalized_symbol)
        return {
            "symbol": normalized_symbol,
            "last": _safe_float(ticker.get("last"), 0.0),
            "percentage": _safe_float(ticker.get("percentage"), 0.0),
            "quoteVolume": _safe_float(ticker.get("quoteVolume"), 0.0),
        }
    except Exception:
        return {"symbol": normalized_symbol, "last": 0.0, "percentage": 0.0, "quoteVolume": 0.0}


async def _fetch_futures_book_ticker_fast(exchange: Any, raw_symbol: str) -> Dict[str, Any]:
    normalized_symbol = _normalize_to_futures_symbol(raw_symbol)
    if not normalized_symbol:
        return {"symbol": "", "bid": 0.0, "ask": 0.0, "spread_bps": 0.0}

    symbol_code = _futures_symbol_code(normalized_symbol)
    bid = 0.0
    ask = 0.0

    try:
        row = await exchange.fapiPublicGetTickerBookTicker({"symbol": symbol_code})
        if isinstance(row, dict):
            bid = _safe_float(row.get("bidPrice"), 0.0)
            ask = _safe_float(row.get("askPrice"), 0.0)
    except Exception:
        pass

    if bid <= 0 or ask <= 0:
        try:
            order_book = await exchange.fetch_order_book(normalized_symbol, limit=5)
            bids = order_book.get("bids") or []
            asks = order_book.get("asks") or []
            if bids:
                bid = _safe_float(bids[0][0], 0.0)
            if asks:
                ask = _safe_float(asks[0][0], 0.0)
        except Exception:
            bid = 0.0
            ask = 0.0

    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
    spread_bps = ((ask - bid) / mid * 10000.0) if mid > 0 else 0.0

    return {
        "symbol": normalized_symbol,
        "bid": bid,
        "ask": ask,
        "spread_bps": spread_bps
    }


async def _fetch_funding_rate_fast(exchange: Any, raw_symbol: str) -> Dict[str, Any]:
    normalized_symbol = _normalize_to_futures_symbol(raw_symbol)
    if not normalized_symbol:
        return {"fundingRate": 0.0}
    symbol_code = _futures_symbol_code(normalized_symbol)

    try:
        row = await exchange.fapiPublicGetPremiumIndex({"symbol": symbol_code})
        return {"fundingRate": _safe_float(row.get("lastFundingRate"), 0.0)}
    except Exception:
        pass

    try:
        funding = await exchange.fetch_funding_rate(normalized_symbol)
        return {"fundingRate": _safe_float(funding.get("fundingRate"), 0.0)}
    except Exception:
        return {"fundingRate": 0.0}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _safe_mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / float(len(values))


def _safe_median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    clamped_pct = _clamp(pct, 0.0, 100.0)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (clamped_pct / 100.0)
    lower_idx = int(math.floor(position))
    upper_idx = int(math.ceil(position))
    if lower_idx == upper_idx:
        return ordered[lower_idx]
    weight = position - lower_idx
    return ordered[lower_idx] * (1.0 - weight) + ordered[upper_idx] * weight


def _stddev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    try:
        return float(statistics.pstdev(values))
    except Exception:
        return 0.0


def _timeframe_to_minutes(timeframe: str) -> int:
    normalized = _normalize_timeframe(timeframe)
    mapping = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "2h": 120,
        "4h": 240,
        "6h": 360,
        "8h": 480,
        "12h": 720,
        "1d": 1440,
    }
    return mapping.get(normalized, 60)


def _timeframe_weight(timeframe: str) -> float:
    minutes = _timeframe_to_minutes(timeframe)
    if minutes <= 5:
        return 1.0
    if minutes <= 15:
        return 1.2
    if minutes <= 30:
        return 1.35
    if minutes <= 60:
        return 1.55
    if minutes <= 240:
        return 1.8
    return 2.2


def _direction_from_action(action: str, suggested_side: Optional[str] = None) -> str:
    suggested = str(suggested_side or "").upper()
    if suggested in {"LONG", "SHORT"}:
        return suggested
    value = str(action or "").upper()
    if any(tag in value for tag in ("BUY", "LONG", "OPEN_LONG")):
        return "LONG"
    if any(tag in value for tag in ("SELL", "SHORT", "OPEN_SHORT")):
        return "SHORT"
    return "NEUTRAL"


def _signal_cooldown_seconds(timeframe: str) -> int:
    minutes = _timeframe_to_minutes(timeframe)
    if minutes <= 1:
        return 15
    if minutes <= 5:
        return 22
    if minutes <= 15:
        return 45
    if minutes <= 30:
        return 70
    if minutes <= 60:
        return 95
    if minutes <= 240:
        return 160
    return 300


def _cleanup_signal_governor_state(now_ts: Optional[float] = None) -> None:
    if not _signal_governor_state:
        return
    timestamp = now_ts or time.time()
    stale_keys = [
        key
        for key, row in _signal_governor_state.items()
        if (timestamp - _safe_float(row.get("ts"), 0.0)) > SIGNAL_STATE_TTL_SECONDS
    ]
    for key in stale_keys:
        _signal_governor_state.pop(key, None)


def _apply_signal_governor(
    symbol: str,
    timeframe: str,
    strategy_id: str,
    action: str,
    direction: str,
    confidence: float,
    reasoning: str,
) -> Dict[str, Any]:
    now_ts = time.time()
    if len(_signal_governor_state) > 600:
        _cleanup_signal_governor_state(now_ts)

    normalized_symbol = _normalize_to_futures_symbol(symbol) or str(symbol or "").upper()
    normalized_timeframe = _normalize_timeframe(timeframe)
    normalized_strategy = str(strategy_id or "momentum").strip().lower()
    signal_action = str(action or "HOLD").upper()
    signal_direction = _direction_from_action(signal_action, direction)
    raw_confidence = _clamp(_safe_float(confidence, 0.0), 0.0, 100.0)
    signal_reasoning = str(reasoning or "")
    directional_signal = signal_direction in {"LONG", "SHORT"}
    key = f"{normalized_symbol}|{normalized_timeframe}|{normalized_strategy}"
    cooldown_seconds = _signal_cooldown_seconds(normalized_timeframe)

    previous = _signal_governor_state.get(key) or {}
    previous_ts = _safe_float(previous.get("ts"), 0.0)
    elapsed = max(0.0, now_ts - previous_ts)
    same_signal = (
        previous.get("action") == signal_action
        and previous.get("direction") == signal_direction
        and directional_signal
    )
    confidence_shift = abs(raw_confidence - _safe_float(previous.get("raw_confidence"), 0.0))
    repeat_count = int(previous.get("repeat_count", 0)) + 1 if same_signal else 0

    throttled = False
    cooldown_remaining_ms = 0
    if same_signal and elapsed < cooldown_seconds and confidence_shift < 5.0:
        throttled = True
        cooldown_remaining_ms = int((cooldown_seconds - elapsed) * 1000)

    decay_penalty = 0.0
    if same_signal:
        decay_penalty += min(35.0, repeat_count * 3.8)
        if throttled:
            decay_penalty += min(14.0, (max(0.0, cooldown_seconds - elapsed) / max(cooldown_seconds, 1)) * 11.0)

    adjusted_confidence = _clamp(raw_confidence - decay_penalty, 0.0, 100.0)
    final_action = signal_action
    final_direction = signal_direction
    quality_blocked = False
    if directional_signal and repeat_count >= 2 and adjusted_confidence < 52.0:
        final_action = "HOLD"
        final_direction = "NEUTRAL"
        quality_blocked = True
        signal_reasoning = (
            f"{signal_reasoning} | Signal decay applied: repeated {signal_direction} setup "
            f"lost edge in cooldown window."
        ).strip(" |")

    should_alert = bool(
        final_direction in {"LONG", "SHORT"}
        and not throttled
        and adjusted_confidence >= 63.0
    )

    _signal_governor_state[key] = {
        "action": signal_action,
        "direction": signal_direction,
        "raw_confidence": raw_confidence,
        "repeat_count": repeat_count,
        "ts": now_ts,
    }

    return {
        "action": final_action,
        "direction": final_direction,
        "confidence": round(adjusted_confidence, 2),
        "reasoning": signal_reasoning,
        "meta": {
            "key": key,
            "throttled": throttled,
            "repeat_count": repeat_count,
            "decay_penalty": round(decay_penalty, 2),
            "cooldown_remaining_ms": max(0, cooldown_remaining_ms),
            "should_alert": should_alert,
            "quality_blocked": quality_blocked,
        },
    }


def _returns_from_close(close_values: List[float]) -> List[float]:
    returns: List[float] = []
    for idx in range(1, len(close_values)):
        previous = _safe_float(close_values[idx - 1], 0.0)
        current = _safe_float(close_values[idx], 0.0)
        if previous <= 0 or current <= 0:
            continue
        returns.append((current - previous) / previous)
    return returns


def _pearson_corr(left: List[float], right: List[float]) -> float:
    usable = min(len(left), len(right))
    if usable < 3:
        return 0.0
    left_series = left[-usable:]
    right_series = right[-usable:]
    mean_left = _safe_mean(left_series)
    mean_right = _safe_mean(right_series)
    numerator = 0.0
    left_var = 0.0
    right_var = 0.0
    for l_value, r_value in zip(left_series, right_series):
        dl = l_value - mean_left
        dr = r_value - mean_right
        numerator += dl * dr
        left_var += dl * dl
        right_var += dr * dr
    denominator = math.sqrt(left_var * right_var)
    if denominator <= 1e-12:
        return 0.0
    return _clamp(numerator / denominator, -1.0, 1.0)


def _derive_market_regimes(ohlcv_data: Dict[str, List[float]], timeframe: str) -> List[str]:
    close_series = list(ohlcv_data.get("close", []))
    high_series = list(ohlcv_data.get("high", []))
    low_series = list(ohlcv_data.get("low", []))
    count = len(close_series)
    if count == 0:
        return []

    minutes = _timeframe_to_minutes(timeframe)
    if minutes <= 5:
        vol_threshold = 0.0045
        range_threshold = 0.012
        move_threshold = 0.0035
    elif minutes <= 15:
        vol_threshold = 0.006
        range_threshold = 0.016
        move_threshold = 0.005
    elif minutes <= 60:
        vol_threshold = 0.009
        range_threshold = 0.022
        move_threshold = 0.008
    else:
        vol_threshold = 0.013
        range_threshold = 0.032
        move_threshold = 0.012

    regime_window = max(16, min(42, int(24 * max(1.0, minutes / 15.0))))
    labels: List[str] = ["RANGE"] * count
    for idx in range(regime_window, count):
        start_idx = idx - regime_window
        closes = close_series[start_idx : idx + 1]
        highs = high_series[start_idx : idx + 1] if len(high_series) > idx else []
        lows = low_series[start_idx : idx + 1] if len(low_series) > idx else []
        if len(closes) < 6:
            continue

        returns = _returns_from_close(closes)
        realized_vol = _stddev(returns)
        close_now = _safe_float(closes[-1], 0.0)
        step_sum = sum(abs(closes[j] - closes[j - 1]) for j in range(1, len(closes)))
        net_move = abs(closes[-1] - closes[0])
        trend_efficiency = (net_move / step_sum) if step_sum > 1e-12 else 0.0
        move_ratio = (net_move / close_now) if close_now > 0 else 0.0
        if highs and lows and close_now > 0:
            range_ratio = (max(highs) - min(lows)) / close_now
        else:
            range_ratio = 0.0

        if realized_vol >= vol_threshold or range_ratio >= range_threshold:
            labels[idx] = "VOLATILE"
        elif trend_efficiency >= 0.45 and move_ratio >= move_threshold:
            labels[idx] = "TREND"
        else:
            labels[idx] = "RANGE"

    if count > regime_window:
        fill_value = labels[regime_window]
        for idx in range(regime_window):
            labels[idx] = fill_value
    return labels


def _compute_max_drawdown(equity_curve: List[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = ((peak - equity) / peak) * 100.0
            if drawdown > max_dd:
                max_dd = drawdown
    return max_dd


def _performance_score(win_rate: float, roi: float, max_drawdown: float, profit_factor: float) -> float:
    win_component = _clamp(win_rate, 0.0, 100.0) * 0.42
    roi_component = _clamp((roi + 40.0) * 1.1, 0.0, 100.0) * 0.33
    drawdown_component = _clamp(100.0 - (max_drawdown * 1.4), 0.0, 100.0) * 0.15
    pf_component = _clamp(profit_factor * 25.0, 0.0, 100.0) * 0.10
    return round(_clamp(win_component + roi_component + drawdown_component + pf_component, 0.0, 100.0), 2)


def _summarize_backtest_trades(
    trades: List[Dict[str, Any]],
    initial_balance: float,
    final_balance: float,
    equity_curve: List[float],
) -> Dict[str, Any]:
    wins = [trade for trade in trades if _safe_float(trade.get("pnl_amount"), 0.0) > 0]
    losses = [trade for trade in trades if _safe_float(trade.get("pnl_amount"), 0.0) < 0]
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0
    total_pnl = sum(_safe_float(trade.get("pnl_amount"), 0.0) for trade in trades)
    avg_win = _safe_mean([_safe_float(trade.get("pnl_amount"), 0.0) for trade in wins])
    avg_loss = _safe_mean([_safe_float(trade.get("pnl_amount"), 0.0) for trade in losses])
    total_win = sum(_safe_float(trade.get("pnl_amount"), 0.0) for trade in wins)
    total_loss = abs(sum(_safe_float(trade.get("pnl_amount"), 0.0) for trade in losses))
    profit_factor = (total_win / total_loss) if total_loss > 1e-9 else (99.0 if total_win > 0 else 0.0)
    max_drawdown = _compute_max_drawdown(equity_curve)
    roi = ((final_balance - initial_balance) / initial_balance * 100.0) if initial_balance > 0 else 0.0

    trade_returns = [(_safe_float(trade.get("pnl_pct"), 0.0) / 100.0) for trade in trades]
    mean_return = _safe_mean(trade_returns)
    vol_return = _stddev(trade_returns)
    sharpe_ratio = (mean_return / vol_return * math.sqrt(len(trade_returns))) if vol_return > 1e-12 else 0.0
    score = _performance_score(win_rate, roi, max_drawdown, profit_factor)

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "final_balance": round(final_balance, 2),
        "roi": round(roi, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe_ratio, 3),
        "max_drawdown": round(max_drawdown, 2),
        "score": score,
    }


def _run_backtest_window(
    ohlcv_data: Dict[str, List[float]],
    timestamps: List[int],
    strategy_id: str,
    timeframe: str,
    start_index: int,
    end_index: int,
    initial_balance: float = 10000.0,
    fee_bps: float = 4.0,
    slippage_bps: float = 1.5,
    regime_labels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    close_series = list(ohlcv_data.get("close", []))
    if not close_series:
        return {"trades": [], "final_balance": initial_balance, "equity_curve": [initial_balance]}

    series_len = len(close_series)
    warmup_floor = min(60, series_len - 1)
    safe_start = max(warmup_floor, min(start_index, series_len - 1))
    safe_end = max(safe_start + 1, min(end_index, series_len))
    if safe_start >= series_len:
        return {"trades": [], "final_balance": initial_balance, "equity_curve": [initial_balance]}

    trades: List[Dict[str, Any]] = []
    balance = float(initial_balance)
    equity_curve: List[float] = [balance]
    position: Optional[Dict[str, Any]] = None

    for idx in range(safe_start, safe_end):
        window_data = {
            "open": ohlcv_data["open"][: idx + 1],
            "high": ohlcv_data["high"][: idx + 1],
            "low": ohlcv_data["low"][: idx + 1],
            "close": ohlcv_data["close"][: idx + 1],
            "volume": ohlcv_data["volume"][: idx + 1],
        }
        current_price = _safe_float(close_series[idx], 0.0)
        if current_price <= 0:
            equity_curve.append(balance)
            continue

        micro_vol_threshold = _resolve_micro_vol_threshold((config_store or {}), timeframe)
        result = strategy_manager.analyze_with_strategy(
            window_data,
            strategy_id,
            None,
            timeframe,
            micro_vol_threshold,
        )
        signal = str(result.get("signal", "HOLD")).upper()
        confidence = _safe_float(result.get("confidence"), 0.0)
        leverage = max(1, int(_safe_float(result.get("leverage"), 1)))
        candle_ts = int(timestamps[idx]) if idx < len(timestamps) else 0
        regime = (
            str(regime_labels[idx]).upper()
            if regime_labels and idx < len(regime_labels)
            else "RANGE"
        )

        if position is None:
            if signal in {"BUY", "SELL"}:
                side = "LONG" if signal == "BUY" else "SHORT"
                slip = slippage_bps / 10000.0
                entry_exec = current_price * (1.0 + slip) if side == "LONG" else current_price * (1.0 - slip)
                position = {
                    "type": side,
                    "entry": max(entry_exec, 1e-9),
                    "take_profit": _safe_float(result.get("take_profit"), current_price),
                    "stop_loss": _safe_float(result.get("stop_loss"), current_price),
                    "leverage": leverage,
                    "entry_index": idx,
                    "entry_ts": candle_ts,
                    "entry_confidence": confidence,
                    "entry_regime": regime,
                }
        else:
            side = str(position.get("type") or "LONG")
            exit_trade = False
            exit_reason = ""
            take_profit = _safe_float(position.get("take_profit"), 0.0)
            stop_loss = _safe_float(position.get("stop_loss"), 0.0)
            if side == "LONG":
                if take_profit > 0 and current_price >= take_profit:
                    exit_trade = True
                    exit_reason = "TAKE_PROFIT"
                elif stop_loss > 0 and current_price <= stop_loss:
                    exit_trade = True
                    exit_reason = "STOP_LOSS"
                elif signal == "SELL" and confidence >= max(55.0, _safe_float(position.get("entry_confidence"), 0.0) - 8.0):
                    exit_trade = True
                    exit_reason = "SIGNAL_FLIP"
            else:
                if take_profit > 0 and current_price <= take_profit:
                    exit_trade = True
                    exit_reason = "TAKE_PROFIT"
                elif stop_loss > 0 and current_price >= stop_loss:
                    exit_trade = True
                    exit_reason = "STOP_LOSS"
                elif signal == "BUY" and confidence >= max(55.0, _safe_float(position.get("entry_confidence"), 0.0) - 8.0):
                    exit_trade = True
                    exit_reason = "SIGNAL_FLIP"

            if exit_trade:
                slip = slippage_bps / 10000.0
                exit_exec = current_price * (1.0 - slip) if side == "LONG" else current_price * (1.0 + slip)
                entry_exec = _safe_float(position.get("entry"), current_price)
                if entry_exec <= 0:
                    entry_exec = current_price
                if side == "LONG":
                    raw_move_pct = ((exit_exec - entry_exec) / entry_exec) * 100.0
                else:
                    raw_move_pct = ((entry_exec - exit_exec) / entry_exec) * 100.0
                gross_pct = raw_move_pct * _safe_float(position.get("leverage"), 1.0)
                fee_pct = ((fee_bps * 2.0) / 10000.0) * _safe_float(position.get("leverage"), 1.0) * 100.0
                net_pct = gross_pct - fee_pct
                pnl_amount = balance * (net_pct / 100.0)
                balance += pnl_amount

                trades.append({
                    "type": side,
                    "entry": round(entry_exec, 8),
                    "exit": round(exit_exec, 8),
                    "entry_index": int(position.get("entry_index", idx)),
                    "exit_index": idx,
                    "entry_ts": int(position.get("entry_ts", 0)),
                    "exit_ts": candle_ts,
                    "entry_regime": str(position.get("entry_regime", regime)),
                    "exit_regime": regime,
                    "pnl_pct": round(net_pct, 3),
                    "pnl_amount": round(pnl_amount, 4),
                    "exit_reason": exit_reason,
                    "leverage": int(_safe_float(position.get("leverage"), 1)),
                    "confidence": round(_safe_float(position.get("entry_confidence"), 0.0), 2),
                })
                position = None

        equity_curve.append(balance)

    if position is not None and safe_end > safe_start:
        last_idx = safe_end - 1
        last_price = _safe_float(close_series[last_idx], 0.0)
        if last_price > 0:
            side = str(position.get("type") or "LONG")
            slip = slippage_bps / 10000.0
            exit_exec = last_price * (1.0 - slip) if side == "LONG" else last_price * (1.0 + slip)
            entry_exec = _safe_float(position.get("entry"), last_price)
            if entry_exec <= 0:
                entry_exec = last_price
            if side == "LONG":
                raw_move_pct = ((exit_exec - entry_exec) / entry_exec) * 100.0
            else:
                raw_move_pct = ((entry_exec - exit_exec) / entry_exec) * 100.0
            gross_pct = raw_move_pct * _safe_float(position.get("leverage"), 1.0)
            fee_pct = ((fee_bps * 2.0) / 10000.0) * _safe_float(position.get("leverage"), 1.0) * 100.0
            net_pct = gross_pct - fee_pct
            pnl_amount = balance * (net_pct / 100.0)
            balance += pnl_amount
            trades.append({
                "type": side,
                "entry": round(entry_exec, 8),
                "exit": round(exit_exec, 8),
                "entry_index": int(position.get("entry_index", last_idx)),
                "exit_index": last_idx,
                "entry_ts": int(position.get("entry_ts", 0)),
                "exit_ts": int(timestamps[last_idx]) if last_idx < len(timestamps) else 0,
                "entry_regime": str(position.get("entry_regime", "RANGE")),
                "exit_regime": str(regime_labels[last_idx]).upper() if regime_labels and last_idx < len(regime_labels) else "RANGE",
                "pnl_pct": round(net_pct, 3),
                "pnl_amount": round(pnl_amount, 4),
                "exit_reason": "END_OF_WINDOW",
                "leverage": int(_safe_float(position.get("leverage"), 1)),
                "confidence": round(_safe_float(position.get("entry_confidence"), 0.0), 2),
            })
            equity_curve.append(balance)

    return {
        "trades": trades,
        "final_balance": balance,
        "equity_curve": equity_curve,
    }


def _regime_scores_from_trades(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    regime_labels = ("TREND", "RANGE", "VOLATILE")
    total_count = max(1, len(trades))
    payload: Dict[str, Dict[str, Any]] = {}
    for label in regime_labels:
        subset = [trade for trade in trades if str(trade.get("entry_regime", "RANGE")).upper() == label]
        if not subset:
            payload[label.lower()] = {
                "regime": label,
                "trades": 0,
                "win_rate": 0.0,
                "net_pnl": 0.0,
                "roi": 0.0,
                "max_drawdown": 0.0,
                "profit_factor": 0.0,
                "score": 0.0,
                "trade_share_pct": 0.0,
            }
            continue
        balance = 10000.0
        eq_curve = [balance]
        for row in subset:
            balance += _safe_float(row.get("pnl_amount"), 0.0)
            eq_curve.append(balance)
        summary = _summarize_backtest_trades(subset, 10000.0, balance, eq_curve)
        payload[label.lower()] = {
            "regime": label,
            "trades": len(subset),
            "win_rate": summary["win_rate"],
            "net_pnl": summary["total_pnl"],
            "roi": summary["roi"],
            "max_drawdown": summary["max_drawdown"],
            "profit_factor": summary["profit_factor"],
            "score": summary["score"],
            "trade_share_pct": round((len(subset) / total_count) * 100.0, 2),
        }
    return payload


def _walk_forward_backtest(
    ohlcv_data: Dict[str, List[float]],
    timestamps: List[int],
    strategy_id: str,
    timeframe: str,
    initial_balance: float = 10000.0,
    folds: int = 4,
) -> Dict[str, Any]:
    close_series = list(ohlcv_data.get("close", []))
    count = len(close_series)
    if count < 140:
        return {
            "enabled": False,
            "folds": [],
            "fold_count": 0,
            "out_of_sample_score": 0.0,
            "consistency_score": 0.0,
            "message": "Not enough candles for walk-forward segmentation.",
        }

    regime_labels = _derive_market_regimes(ohlcv_data, timeframe)
    warmup = max(80, min(140, count // 3))
    available = count - warmup
    fold_count = max(2, min(int(folds), 6))
    test_span = max(40, available // (fold_count + 1))
    fold_rows: List[Dict[str, Any]] = []

    for fold_idx in range(fold_count):
        train_end = warmup + (fold_idx * test_span)
        test_start = train_end
        test_end = min(count, test_start + test_span)
        if test_end - test_start < 32:
            continue

        window = _run_backtest_window(
            ohlcv_data=ohlcv_data,
            timestamps=timestamps,
            strategy_id=strategy_id,
            timeframe=timeframe,
            start_index=test_start,
            end_index=test_end,
            initial_balance=initial_balance,
            regime_labels=regime_labels,
        )
        trades = list(window.get("trades", []))
        final_balance = _safe_float(window.get("final_balance"), initial_balance)
        equity_curve = list(window.get("equity_curve", [])) or [initial_balance, final_balance]
        summary = _summarize_backtest_trades(trades, initial_balance, final_balance, equity_curve)
        fold_rows.append({
            "fold": fold_idx + 1,
            "train_range": [0, test_start - 1],
            "test_range": [test_start, test_end - 1],
            "train_candles": test_start,
            "test_candles": test_end - test_start,
            "results": summary,
            "regime_scores": _regime_scores_from_trades(trades),
            "trades": trades[-8:],
        })

    if not fold_rows:
        return {
            "enabled": False,
            "folds": [],
            "fold_count": 0,
            "out_of_sample_score": 0.0,
            "consistency_score": 0.0,
            "message": "Unable to build walk-forward folds with current dataset.",
        }

    oos_scores = [_safe_float(row.get("results", {}).get("score"), 0.0) for row in fold_rows]
    oos_win_rates = [_safe_float(row.get("results", {}).get("win_rate"), 0.0) for row in fold_rows]
    oos_rois = [_safe_float(row.get("results", {}).get("roi"), 0.0) for row in fold_rows]
    consistency = _clamp(100.0 - (_stddev(oos_scores) * 2.2), 0.0, 100.0)
    out_of_sample = _safe_mean(oos_scores)
    robustness = round((out_of_sample * 0.7) + (consistency * 0.3), 2)

    return {
        "enabled": True,
        "folds": fold_rows,
        "fold_count": len(fold_rows),
        "out_of_sample_score": round(out_of_sample, 2),
        "consistency_score": round(consistency, 2),
        "robustness_score": robustness,
        "avg_win_rate": round(_safe_mean(oos_win_rates), 2),
        "avg_roi": round(_safe_mean(oos_rois), 2),
    }


def _prepare_position_snapshot(position: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw_symbol = (
        position.get("symbol")
        or position.get("pair")
        or position.get("id")
        or ""
    )
    symbol = _normalize_to_futures_symbol(raw_symbol)
    if not symbol:
        return None
    side = str(position.get("side") or "").upper()
    if side not in {"LONG", "SHORT"}:
        amount_raw = _safe_float(position.get("amount"), _safe_float(position.get("positionAmt"), 0.0))
        side = "LONG" if amount_raw >= 0 else "SHORT"
    amount = abs(_safe_float(position.get("amount"), _safe_float(position.get("positionAmt"), 0.0)))
    mark_price = _safe_float(position.get("markPrice"), _safe_float(position.get("price"), 0.0))
    notional = abs(_safe_float(position.get("notional"), 0.0))
    if notional <= 0 and amount > 0 and mark_price > 0:
        notional = amount * mark_price
    leverage = int(max(1, _safe_float(position.get("leverage"), 1.0)))
    if notional <= 0:
        return None
    return {
        "symbol": symbol,
        "side": side,
        "amount": amount,
        "markPrice": mark_price,
        "notional": notional,
        "leverage": leverage,
    }


def _estimate_target_notional(balance: float, leverage: int, config: Dict[str, Any]) -> float:
    if balance <= 0:
        return max(0.0, _safe_float(config.get("min_notional"), 120.0))
    lev = max(1, int(leverage))
    sizing_k = _safe_float(config.get("sizing_k"), 5.0)
    sizing_p = _safe_float(config.get("sizing_p"), 1.699)
    max_margin_fraction = _safe_float(config.get("max_margin_fraction"), 0.20)
    min_margin_usdt = _safe_float(config.get("min_margin_usdt"), 10.0)
    max_notional_abs = _safe_float(config.get("max_notional_abs"), 20000.0)
    min_notional = _safe_float(config.get("min_notional"), 120.0)

    margin_fraction = sizing_k / (float(lev) ** max(0.1, sizing_p))
    margin_fraction = min(max_margin_fraction, max(0.0, margin_fraction))
    margin = max(balance * margin_fraction, min_margin_usdt)
    notional = margin * lev
    if max_notional_abs > 0:
        notional = min(notional, max_notional_abs)
    return max(min_notional, notional)


def _empty_correlation_engine(balance: float = 0.0) -> Dict[str, Any]:
    return {
        "correlation_risk_score": 0.0,
        "average_abs_correlation": 0.0,
        "weighted_abs_correlation": 0.0,
        "concentration_index": 0.0,
        "exposure_percentage": 0.0,
        "current_exposure_percentage": 0.0,
        "projected_exposure_percentage": 0.0,
        "recommended_max_exposure_pct": 85.0 if balance > 0 else 0.0,
        "exposure_breach": False,
        "position_count": 0,
        "symbols": [],
        "top_pairs": [],
        "recommendations": ["Portfolio correlation risk is low with current exposure."],
    }


async def _compute_portfolio_correlation_engine(
    exchange: Any,
    positions: List[Dict[str, Any]],
    balance: float,
    candidate_position: Optional[Dict[str, Any]] = None,
    timeframe: str = "1h",
    lookback: int = 180,
) -> Dict[str, Any]:
    snapshots: List[Dict[str, Any]] = []
    for row in positions:
        snapshot = _prepare_position_snapshot(row)
        if snapshot:
            snapshots.append(snapshot)

    candidate_notional = 0.0
    if candidate_position:
        candidate_snapshot = _prepare_position_snapshot(candidate_position)
        if candidate_snapshot:
            candidate_notional = _safe_float(candidate_snapshot.get("notional"), 0.0)
            snapshots.append(candidate_snapshot)

    if balance <= 0 or not snapshots:
        return _empty_correlation_engine(balance)

    total_notional = sum(abs(_safe_float(item.get("notional"), 0.0)) for item in snapshots)
    if total_notional <= 0:
        return _empty_correlation_engine(balance)

    symbol_weights: Dict[str, float] = {}
    for row in snapshots:
        symbol = str(row.get("symbol") or "")
        weight = abs(_safe_float(row.get("notional"), 0.0)) / total_notional
        symbol_weights[symbol] = symbol_weights.get(symbol, 0.0) + weight

    symbol_returns: Dict[str, List[float]] = {}
    for symbol in symbol_weights.keys():
        rows = await _fetch_futures_ohlcv_fast(exchange, symbol, timeframe, lookback)
        if not rows:
            continue
        closes = [_safe_float(item[4], 0.0) for item in rows if len(item) >= 5]
        returns = _returns_from_close(closes)
        if len(returns) >= 30:
            symbol_returns[symbol] = returns

    pair_rows: List[Dict[str, Any]] = []
    abs_corr_values: List[float] = []
    weighted_corr_sum = 0.0
    weighted_pair_weight = 0.0
    symbols = sorted(symbol_weights.keys())
    for left_idx in range(len(symbols)):
        for right_idx in range(left_idx + 1, len(symbols)):
            left_symbol = symbols[left_idx]
            right_symbol = symbols[right_idx]
            left_returns = symbol_returns.get(left_symbol)
            right_returns = symbol_returns.get(right_symbol)
            if not left_returns or not right_returns:
                continue
            corr = _pearson_corr(left_returns, right_returns)
            abs_corr = abs(corr)
            abs_corr_values.append(abs_corr)
            pair_weight = symbol_weights[left_symbol] * symbol_weights[right_symbol]
            weighted_corr_sum += abs_corr * pair_weight
            weighted_pair_weight += pair_weight
            pair_rows.append({
                "left": left_symbol,
                "right": right_symbol,
                "correlation": round(corr, 4),
                "abs_correlation": round(abs_corr, 4),
                "pair_weight": round(pair_weight, 4),
            })

    average_abs_corr = _safe_mean(abs_corr_values)
    weighted_abs_corr = (
        (weighted_corr_sum / weighted_pair_weight)
        if weighted_pair_weight > 1e-12
        else average_abs_corr
    )
    hhi = sum(weight * weight for weight in symbol_weights.values())
    concentration_index = hhi * 100.0

    projected_exposure = (total_notional / balance) * 100.0 if balance > 0 else 0.0
    current_exposure = (
        ((total_notional - candidate_notional) / balance) * 100.0
        if balance > 0 and candidate_notional > 0
        else projected_exposure
    )

    correlation_component = weighted_abs_corr * 100.0
    recommended_max_exposure = _clamp(
        90.0 - (correlation_component * 0.45) - (concentration_index * 0.35),
        25.0,
        90.0,
    )
    exposure_overshoot = max(0.0, projected_exposure - recommended_max_exposure)
    risk_score = _clamp(
        (correlation_component * 0.65)
        + (concentration_index * 0.25)
        + (exposure_overshoot * 0.7),
        0.0,
        100.0,
    )
    exposure_breach = bool(
        projected_exposure > recommended_max_exposure
        and (risk_score >= 55.0 or weighted_abs_corr >= 0.55)
    )

    recommendations: List[str] = []
    if exposure_breach:
        recommendations.append(
            f"⚠️ Portfolio exposure ({projected_exposure:.1f}%) exceeds correlation-adjusted limit ({recommended_max_exposure:.1f}%)."
        )
    if weighted_abs_corr >= 0.65:
        recommendations.append(
            f"⚠️ High cross-symbol correlation ({weighted_abs_corr:.2f}). Reduce same-direction concentration."
        )
    if concentration_index >= 45.0:
        recommendations.append(
            f"⚠️ Position concentration is elevated (HHI {concentration_index:.1f}). Diversify symbols/timeframes."
        )
    if not recommendations:
        recommendations.append("✅ Correlation risk and concentration are within acceptable limits.")

    pair_rows.sort(key=lambda row: row.get("abs_correlation", 0.0), reverse=True)
    return {
        "correlation_risk_score": round(risk_score, 2),
        "average_abs_correlation": round(average_abs_corr, 4),
        "weighted_abs_correlation": round(weighted_abs_corr, 4),
        "concentration_index": round(concentration_index, 2),
        "exposure_percentage": round(projected_exposure, 2),
        "current_exposure_percentage": round(current_exposure, 2),
        "projected_exposure_percentage": round(projected_exposure, 2),
        "recommended_max_exposure_pct": round(recommended_max_exposure, 2),
        "exposure_breach": exposure_breach,
        "position_count": len(snapshots),
        "symbols": list(symbol_weights.keys()),
        "top_pairs": pair_rows[:6],
        "recommendations": recommendations,
    }


def _reference_price_from_rows(rows: List[List[float]], timestamp_ms: int) -> float:
    if not rows:
        return 0.0
    if timestamp_ms <= 0:
        return _safe_float(rows[-1][4], 0.0) if len(rows[-1]) > 4 else 0.0

    target_minute = (timestamp_ms // 60000) * 60000
    close_map = {
        int(_safe_float(item[0], 0.0)): _safe_float(item[4], 0.0)
        for item in rows
        if len(item) > 4
    }
    for offset_minutes in (0, -1, 1, -2, 2, -3, 3):
        key = target_minute + (offset_minutes * 60000)
        if key in close_map and close_map[key] > 0:
            return close_map[key]

    nearest = min(rows, key=lambda item: abs(int(_safe_float(item[0], 0.0)) - target_minute))
    return _safe_float(nearest[4], 0.0) if len(nearest) > 4 else 0.0


def _execution_quality_score(avg_effective_bps: float, p90_slippage_bps: float) -> float:
    penalty = (avg_effective_bps * 1.9) + (p90_slippage_bps * 1.25)
    return round(_clamp(100.0 - penalty, 0.0, 100.0), 2)


def _extract_trade_pnl(trade: Dict[str, Any]) -> float:
    info = trade.get("info") if isinstance(trade.get("info"), dict) else {}
    candidates = [
        trade.get("pnl"),
        trade.get("realizedPnl"),
        info.get("realizedPnl"),
        info.get("realizedPNL"),
        info.get("realizedProfit"),
        info.get("pnl")
    ]
    for value in candidates:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except Exception:
            continue
    return 0.0


async def _resolve_exchange_symbol(exchange: Any, raw_symbol: str) -> str:
    """Resolve a normalized futures symbol to the exact market symbol used by the exchange."""
    normalized = _normalize_to_futures_symbol(raw_symbol)
    if not normalized:
        return ""

    base, quote = normalized.split("/", 1)
    target_code = f"{base}{quote}"

    try:
        if not getattr(exchange, "markets", None):
            await exchange.load_markets()
        markets = getattr(exchange, "markets", {}) or {}
    except Exception:
        markets = {}

    for market_symbol, market in markets.items():
        market_id = str((market or {}).get("id") or "").upper().replace("/", "").replace(":", "")
        market_code = str(market_symbol or "").upper().replace("/", "").replace(":", "")
        market_base = str((market or {}).get("base") or "").upper()
        market_quote = str((market or {}).get("quote") or "").upper()
        if market_id == target_code or market_code == target_code:
            return str(market_symbol)
        if market_base == base and market_quote == quote:
            return str(market_symbol)

    return normalized


async def _fetch_my_trades_safe(exchange: Any, raw_symbol: str, limit: int) -> List[Dict[str, Any]]:
    """
    Fetch trades with robust symbol fallbacks.
    Handles BASE/USDT, BASE/USDT:USDT and compact BASEUSDT forms.
    """
    normalized = _normalize_to_futures_symbol(raw_symbol)
    if not normalized:
        return []
    safe_limit = max(1, min(int(limit), 1000))

    symbol_code = _futures_symbol_code(normalized)
    try:
        raw_rows = await exchange.fapiPrivateGetUserTrades({
            "symbol": symbol_code,
            "limit": safe_limit
        })
        if isinstance(raw_rows, list) and raw_rows:
            converted: List[Dict[str, Any]] = []
            for row in raw_rows:
                if not isinstance(row, dict):
                    continue
                timestamp = int(_safe_float(row.get("time"), 0.0))
                side = "buy" if _to_bool(row.get("buyer")) else "sell"
                amount = _safe_float(row.get("qty"), 0.0)
                price = _safe_float(row.get("price"), 0.0)
                cost = _safe_float(row.get("quoteQty"), amount * price)
                fee_cost = abs(_safe_float(row.get("commission"), 0.0))
                trade_id = row.get("id") or row.get("tradeId") or row.get("orderId")
                converted.append({
                    "id": str(trade_id) if trade_id is not None else "",
                    "symbol": normalized,
                    "side": side,
                    "price": price,
                    "amount": amount,
                    "cost": cost,
                    "timestamp": timestamp,
                    "datetime": datetime.utcfromtimestamp(max(timestamp, 0) / 1000).isoformat() if timestamp else "",
                    "fee": {"cost": fee_cost, "currency": row.get("commissionAsset", "USDT")},
                    "info": row,
                    "pnl": _safe_float(row.get("realizedPnl"), 0.0),
                    "realizedPnl": _safe_float(row.get("realizedPnl"), 0.0),
                })
            if converted:
                return converted
    except Exception:
        pass

    base = normalized.split("/", 1)[0]
    candidates: List[str] = [
        await _resolve_exchange_symbol(exchange, normalized),
        normalized,
        f"{base}/USDT:USDT",
        f"{base}USDT",
    ]

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            rows = await exchange.fetch_my_trades(candidate, limit=safe_limit)
            if isinstance(rows, list):
                return rows
        except Exception:
            continue

    return []


def _fallback_symbol_catalog() -> List[Dict[str, str]]:
    seen = set()
    catalog: List[Dict[str, str]] = []
    for symbol in POPULAR_SCANNER_SYMBOLS:
        normalized = _normalize_to_futures_symbol(symbol)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        base = normalized.split("/")[0]
        catalog.append({
            "symbol": normalized,
            "base": base,
            "quote": "USDT"
        })
    return catalog


async def _load_symbol_catalog(force_refresh: bool = False) -> List[Dict[str, str]]:
    global _symbol_catalog_cache, _symbol_catalog_cache_ts
    now = time.time()
    if (
        not force_refresh
        and _symbol_catalog_cache
        and (now - _symbol_catalog_cache_ts) < SYMBOL_CACHE_TTL_SECONDS
    ):
        return _symbol_catalog_cache

    import aiohttp

    url = "https://testnet.binancefuture.com/fapi/v1/exchangeInfo"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
            if response.status != 200:
                raise HTTPException(status_code=500, detail="Failed to fetch symbols")
            data = await response.json()

    symbols = data.get("symbols", [])
    catalog: List[Dict[str, str]] = []
    for item in symbols:
        if (
            item.get("quoteAsset") == "USDT"
            and item.get("contractType") == "PERPETUAL"
            and item.get("status") == "TRADING"
        ):
            base = str(item.get("baseAsset", "")).upper()
            if not base:
                continue
            catalog.append({
                "symbol": f"{base}/USDT",
                "base": base,
                "quote": "USDT"
            })

    _symbol_catalog_cache = catalog
    _symbol_catalog_cache_ts = now
    return catalog


# Models
class Credentials(BaseModel):
    api_key: str
    secret_key: str
    is_testnet: bool = True  # Default to testnet for safety


class BotConfig(BaseModel):
    symbol: str = "BTC/USDT"
    hedge_mode: bool = True
    leverage: int = 20
    quote_interval_ms: int = 400
    order_ttl_ms: int = 2000
    requote_move_bps: float = 0.9
    max_open_entry_orders_per_side: int = 1
    entry_offset_bps: float = 1.5
    min_spread_bps: float = 1.2
    spread_guard_mode: str = "soft"
    spread_guard_cooldown_ms: int = 2500
    micro_vol_threshold_bps: float = 10.0
    micro_vol_threshold_bps_1m: float = 12.0
    micro_vol_threshold_bps_5m: float = 11.0
    micro_vol_threshold_bps_15m: float = 10.0
    tp_bps: float = 2.0
    sl_bps: float = 3.0
    be_trigger_bps: float = 1.2
    be_pad_bps: float = 0.6
    trail_start_bps: float = 2.0
    trail_dist_bps: float = 1.2
    anti_wick_bps: float = 8.0
    trail_activation_hold_ms: int = 1500
    min_notional: float = 120.0
    max_notional_abs: float = 20000.0
    max_margin_fraction: float = 0.20
    min_margin_usdt: float = 10.0
    sizing_k: float = 5.0
    sizing_p: float = 1.699
    use_available_balance: bool = True
    reserve_balance_fraction: float = 0.10
    max_fills_per_min: int = 20
    cooldown_after_fill_ms: int = 350
    max_loss_streak: int = 3
    cooldown_after_loss_ms: int = 15000
    cooldown_after_loss_streak_ms: int = 60000
    kill_switch_max_drawdown_usdt: float = 150.0
    maker_fee_bps: float = 0.2
    taker_fee_bps: float = 0.4
    slippage_bps: float = 0.5
    funding_buffer_bps: float = 0.1
    portfolio_risk_guard_mode: str = "soft"
    portfolio_risk_max_score: float = 78.0
    log_file: str = Field(default_factory=lambda: resolve_log_file_path("logs/bot.jsonl"))
    indicator_strategy: Dict[str, Any] = Field(default_factory=lambda: {
        "mode": "confluence",
        "min_confidence": 60,
        "contrarian_mode": False,
        "ai_assist": False,
        "strategy_id": "",
        "strategy_name": "Custom Blend",
        "indicators": [
            "ATR",
            "OBV",
            "MACD",
            "RSI",
            "WILLIAMS_R",
            "BOLLINGER",
            "PIVOT",
            "MA9",
            "MA20",
            "MA100",
            "MA200",
            "MOMENTUM"
        ],
        "weights": {
            "ATR": 20,
            "OBV": 60,
            "MACD": 70,
            "RSI": 70,
            "WILLIAMS_R": 50,
            "BOLLINGER": 60,
            "PIVOT": 40,
            "MA9": 40,
            "MA20": 60,
            "MA100": 50,
            "MA200": 40,
            "MOMENTUM": 60
        }
    })


class Template(BaseModel):
    name: str
    config: BotConfig





# API Endpoints
@app.post("/api/credentials")
async def save_credentials(creds: Credentials):
    """Save API credentials securely using AES-256-GCM encryption"""
    try:
        # Save using secure storage (AES-256-GCM)
        success = secure_storage.save_credentials(
            api_key=creds.api_key,
            secret_key=creds.secret_key,
            is_testnet=creds.is_testnet,
            additional_data={
                'saved_at': datetime.now().isoformat(),
                'version': '2.0'
            }
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save credentials to secure storage")
        
        # Store in memory with testnet flag
        credentials_store["api_key"] = creds.api_key
        credentials_store["secret_key"] = creds.secret_key
        credentials_store["is_testnet"] = creds.is_testnet
        
        return {
            "status": "success",
            "message": f"Credentials saved securely with AES-256-GCM encryption ({'Testnet' if creds.is_testnet else 'Mainnet'} mode)",
            "mode": "testnet" if creds.is_testnet else "mainnet"
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"Error saving credentials: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to save credentials: {str(e)}")


@app.post("/api/credentials/test")
async def test_credentials(creds: Credentials):
    """Test API credentials"""
    try:
        exchange = ccxtpro.binance({
            "apiKey": creds.api_key,
            "secret": creds.secret_key,
            "enableRateLimit": True,
            "options": {
                "defaultType": "future"
            },
        })
        
        # Enable testnet or mainnet based on flag
        if creds.is_testnet:
            exchange.enable_demo_trading(True)
        
        # Test connection
        balance = await exchange.fetch_balance({"type": "future"})
        await exchange.close()
        
        return {
            "status": "success",
            "message": f"{'Testnet' if creds.is_testnet else 'Mainnet'} connection successful",
            "demo_mode": creds.is_testnet,
            "balance": balance.get("USDT", {}).get("total", 0)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")


@app.get("/api/credentials/status")
async def credentials_status():
    """Check if credentials are saved"""
    return {
        "saved": secure_storage.credentials_exist(),
        "mode": secure_storage.get_mode()
    }


@app.post("/api/config")
async def save_config(config: BotConfig):
    """Save bot configuration"""
    payload = config.model_dump()
    payload["log_file"] = resolve_log_file_path(payload.get("log_file", "logs/bot.jsonl"))
    config_store.update(payload)
    return {"status": "success"}


@app.get("/api/config")
async def get_config():
    """Get current configuration"""
    if not config_store:
        return BotConfig().model_dump()
    config_store["log_file"] = resolve_log_file_path(config_store.get("log_file", "logs/bot.jsonl"))
    return config_store


@app.post("/api/bot/start")
async def start_bot():
    """Start the trading bot (legacy - uses default symbol)"""
    # Load credentials
    if not credentials_store:
        creds = decrypt_credentials()
        if not creds:
            raise HTTPException(status_code=400, detail="No credentials found")
        credentials_store.update(creds)
    
    # Load config
    if not config_store:
        config_store.update(BotConfig().model_dump())
    config_store["log_file"] = resolve_log_file_path(config_store.get("log_file", "logs/bot.jsonl"))
    
    symbol = config_store.get("symbol", "BTC/USDT")
    
    # Clean up any stopped bot instances first
    if symbol in bot_instances:
        bot = bot_instances[symbol]
        if not bot.running:
            # Bot exists but not running - clean it up
            if symbol in bot_tasks:
                task = bot_tasks[symbol]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                del bot_tasks[symbol]
            del bot_instances[symbol]
        else:
            # Bot is actually running
            raise HTTPException(status_code=400, detail=f"Bot already running for {symbol}")
    
    try:
        bot = TradingBot(
            config=config_store,
            api_key=credentials_store["api_key"],
            secret_key=credentials_store["secret_key"]
        )
        bot_instances[symbol] = bot
        
        # Create task with cleanup callback
        async def run_with_cleanup():
            try:
                await bot.run()
            except Exception as e:
                print(f"Bot {symbol} crashed: {e}")
            finally:
                # Clean up on crash or completion
                if symbol in bot_instances:
                    del bot_instances[symbol]
                if symbol in bot_tasks:
                    del bot_tasks[symbol]
        
        bot_tasks[symbol] = asyncio.create_task(run_with_cleanup())
        return {"status": "success", "message": f"Bot started for {symbol}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bot/stop")
async def stop_bot():
    """Stop all trading bots"""
    if not bot_instances:
        raise HTTPException(status_code=400, detail="No bots running")
    
    stopped_symbols = []
    errors = []
    
    try:
        for symbol, bot in list(bot_instances.items()):
            try:
                # Stop the bot
                await bot.stop()
                stopped_symbols.append(symbol)
                
                # Cancel the task
                if symbol in bot_tasks:
                    task = bot_tasks[symbol]
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                    del bot_tasks[symbol]
                
                # Remove from instances
                del bot_instances[symbol]
                
            except Exception as e:
                import traceback
                error_detail = f"{symbol}: {type(e).__name__}: {str(e)}"
                print(f"Error stopping bot {symbol}:")
                print(traceback.format_exc())
                errors.append(error_detail)
                
                # Still try to clean up
                try:
                    if symbol in bot_tasks:
                        bot_tasks[symbol].cancel()
                        del bot_tasks[symbol]
                    if symbol in bot_instances:
                        del bot_instances[symbol]
                except:
                    pass
        
        if errors:
            return {
                "status": "partial",
                "message": f"Stopped {len(stopped_symbols)} bots with {len(errors)} errors",
                "stopped": stopped_symbols,
                "errors": errors
            }
        
        return {
            "status": "success",
            "message": f"All bots stopped ({len(stopped_symbols)} bots)",
            "stopped": stopped_symbols
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bot/panic")
async def panic_bot():
    """Emergency stop - cancel all orders for all bots"""
    if not bot_instances:
        raise HTTPException(status_code=400, detail="No bots running")
    
    panicked_symbols = []
    errors = []
    
    try:
        for symbol, bot in bot_instances.items():
            try:
                await bot.panic()
                panicked_symbols.append(symbol)
            except Exception as e:
                errors.append(f"{symbol}: {str(e)}")
        
        if errors:
            return {
                "status": "partial",
                "message": f"Panic executed for {len(panicked_symbols)} bots with {len(errors)} errors",
                "panicked": panicked_symbols,
                "errors": errors
            }
        
        return {
            "status": "success",
            "message": f"Panic executed - all orders canceled ({len(panicked_symbols)} bots)",
            "panicked": panicked_symbols
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class StartTradeRequest(BaseModel):
    symbol: str
    leverage: int
    strategy_name: str
    strategy_id: Optional[str] = None
    timeframe: str = "1h"
    action: Optional[str] = None
    confidence: Optional[float] = None
    force_entry: Optional[bool] = None
    preferred_side: Optional[str] = None
    entry_price: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    tp_bps: Optional[float] = None
    sl_bps: Optional[float] = None
    atr_bps: Optional[float] = None
    micro_volatility: Optional[bool] = None


@app.post("/api/bot/start-trade")
async def start_trade(request: StartTradeRequest):
    """Start a new trade with specified symbol and strategy"""
    symbol = _normalize_to_futures_symbol(request.symbol) or request.symbol
    action_value = str(request.action or "").strip().upper()
    directional_actions = {"OPEN_LONG", "BUY", "LONG", "STRONG_BUY", "OPEN_SHORT", "SELL", "SHORT", "STRONG_SELL"}
    force_entry_requested = request.force_entry if request.force_entry is not None else (action_value in directional_actions)
    preferred_side: Optional[str] = None
    allowed_sides = ["LONG", "SHORT"]
    requested_side = str(request.preferred_side or "").strip().upper()

    symbol_config = config_store.copy() if config_store else BotConfig().model_dump()
    symbol_config['log_file'] = resolve_log_file_path(symbol_config.get('log_file', "logs/bot.jsonl"))
    spread_guard_mode = str(symbol_config.get("spread_guard_mode", "soft")).strip().lower()
    min_spread_bps = _safe_float(symbol_config.get("min_spread_bps"), 1.2)
    spread_guard = {"triggered": False, "mode": spread_guard_mode, "spread_bps": 0.0, "min_spread_bps": min_spread_bps}
    portfolio_guard_mode = str(symbol_config.get("portfolio_risk_guard_mode", "soft")).strip().lower()
    portfolio_guard_max_score = _safe_float(symbol_config.get("portfolio_risk_max_score"), 78.0)
    portfolio_risk_guard: Dict[str, Any] = {
        "mode": portfolio_guard_mode,
        "triggered": False,
        "risk_score": 0.0,
        "recommended_max_exposure_pct": 0.0,
        "projected_exposure_pct": 0.0,
        "details": {},
        "message": "",
    }

    if spread_guard_mode in {"soft", "hard"} and min_spread_bps > 0:
        exchange_public = await get_public_exchange()
        try:
            book_ticker = await _fetch_futures_book_ticker_fast(exchange_public, symbol)
            spread_bps = _safe_float(book_ticker.get("spread_bps"), 0.0)
            spread_guard["spread_bps"] = round(spread_bps, 2)
            spread_guard["triggered"] = bool(spread_bps > 0 and spread_bps < min_spread_bps)
            if spread_guard["triggered"] and spread_guard_mode == "hard":
                raise HTTPException(
                    status_code=409,
                    detail=f"Spread guard: {spread_bps:.2f} bps below minimum {min_spread_bps:.2f} bps"
                )
            if spread_guard["triggered"] and spread_guard_mode == "soft":
                action_value = "HOLD"
                force_entry_requested = False
                preferred_side = None
                allowed_sides = ["LONG", "SHORT"]
        finally:
            try:
                await exchange_public.close()
            except Exception:
                pass

    if requested_side in ("LONG", "SHORT"):
        preferred_side = requested_side
        allowed_sides = [requested_side]

    if action_value in ("OPEN_LONG", "BUY", "LONG", "STRONG_BUY"):
        preferred_side = "LONG"
        allowed_sides = ["LONG"]
    elif action_value in ("OPEN_SHORT", "SELL", "SHORT", "STRONG_SELL"):
        preferred_side = "SHORT"
        allowed_sides = ["SHORT"]
    elif action_value in ("HOLD", "NEUTRAL") and not force_entry_requested:
        # Run in monitor/signal mode with two-sided quoting.
        if preferred_side is None:
            allowed_sides = ["LONG", "SHORT"]

    if force_entry_requested and preferred_side is None:
        preferred_side = "LONG"
        allowed_sides = ["LONG"]

    # Optional TP/SL overrides from strategy analysis
    def _clamp_bps(value: Optional[float], min_bps: float, max_bps: float) -> Optional[float]:
        try:
            val = float(value)
        except Exception:
            return None
        if not (val > 0):
            return None
        return max(min_bps, min(val, max_bps))

    def _derive_bps(entry: float, tp: float, sl: float, side: str):
        if entry <= 0:
            return None, None
        if side == "SHORT":
            tp_bps = (entry - tp) / entry * 10000.0
            sl_bps = (sl - entry) / entry * 10000.0
        else:
            tp_bps = (tp - entry) / entry * 10000.0
            sl_bps = (entry - sl) / entry * 10000.0
        if tp_bps <= 0 or sl_bps <= 0:
            return None, None
        return tp_bps, sl_bps

    # Clean up any stopped bot instances first
    if symbol in bot_instances:
        bot = bot_instances[symbol]
        if not bot.running:
            # Bot exists but not running - clean it up
            if symbol in bot_tasks:
                task = bot_tasks[symbol]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                del bot_tasks[symbol]
            del bot_instances[symbol]
        else:
            # Bot is actually running - return success (idempotent)
            return {
                "message": f"Bot already running for {symbol}",
                "symbol": symbol,
                "leverage": bot.config.get('leverage', request.leverage),
                "status": "running"
            }
    
    # Load credentials
    creds = decrypt_credentials()
    if not creds:
        raise HTTPException(status_code=400, detail="Credentials not configured")

    # Opportunistic stale conditional cleanup:
    # - full-scope cleanup periodically
    # - symbol-scope cleanup on each start request
    global _last_stale_conditionals_cleanup_ts
    try:
        now_ts = time.time()
        full_scope = (now_ts - _last_stale_conditionals_cleanup_ts) >= 90
        cleanup_exchange = await get_exchange()
        try:
            await _cleanup_stale_conditionals(
                cleanup_exchange,
                symbol_filter=None if full_scope else symbol,
                dry_run=False
            )
            if full_scope:
                _last_stale_conditionals_cleanup_ts = now_ts
        finally:
            await cleanup_exchange.close()
    except Exception as cleanup_error:
        # Non-fatal; trade start should still proceed.
        print(f"stale conditional cleanup skipped: {cleanup_error}")
    
    # Create config for this symbol
    symbol_config['symbol'] = symbol
    symbol_config['leverage'] = request.leverage
    symbol_config['allowed_sides'] = allowed_sides
    symbol_config['strategy_name'] = request.strategy_name
    symbol_config['strategy_id'] = request.strategy_id or request.strategy_name
    symbol_config['timeframe'] = request.timeframe
    symbol_config['signal_action'] = action_value
    symbol_config['signal_confidence'] = float(request.confidence or 0.0)
    symbol_config['initial_entry_side'] = preferred_side if force_entry_requested else None
    symbol_config['force_entry'] = bool(force_entry_requested and preferred_side is not None)
    symbol_config['is_testnet'] = bool(creds.get('is_testnet', True))

    indicator_strategy = dict(symbol_config.get('indicator_strategy') or {})
    if request.strategy_id:
        indicator_strategy['strategy_id'] = request.strategy_id
    if request.strategy_name:
        indicator_strategy['strategy_name'] = request.strategy_name
    if request.confidence is not None:
        indicator_strategy['min_confidence'] = max(40.0, min(95.0, float(request.confidence)))
    symbol_config['indicator_strategy'] = indicator_strategy

    strategy_key = str(symbol_config.get('strategy_id') or request.strategy_name or "").lower()
    side_for_risk: Optional[str] = preferred_side
    if side_for_risk is None:
        if action_value in ("OPEN_LONG", "BUY", "LONG", "STRONG_BUY"):
            side_for_risk = "LONG"
        elif action_value in ("OPEN_SHORT", "SELL", "SHORT", "STRONG_SELL"):
            side_for_risk = "SHORT"

    derived_tp_bps: Optional[float] = None
    derived_sl_bps: Optional[float] = None
    try:
        entry_price = float(request.entry_price or 0.0)
        tp_price = float(request.take_profit or 0.0)
        sl_price = float(request.stop_loss or 0.0)
        if side_for_risk and entry_price > 0 and tp_price > 0 and sl_price > 0:
            derived_tp_bps, derived_sl_bps = _derive_bps(entry_price, tp_price, sl_price, side_for_risk)
    except Exception:
        derived_tp_bps, derived_sl_bps = None, None

    requested_tp_bps = _clamp_bps(request.tp_bps, 1.0, 500.0)
    requested_sl_bps = _clamp_bps(request.sl_bps, 1.0, 500.0)
    if "scalp" in strategy_key:
        if not symbol_config['force_entry']:
            symbol_config['allowed_sides'] = ["LONG", "SHORT"]

        # Scalp profile: faster quote cycle, tighter trailing updates, single entry per side.
        symbol_config['quote_interval_ms'] = int(min(symbol_config.get('quote_interval_ms', 400), 320))
        symbol_config['order_ttl_ms'] = int(min(symbol_config.get('order_ttl_ms', 2000), 1400))
        symbol_config['max_open_entry_orders_per_side'] = 1
        symbol_config['trail_update_cooldown_ms'] = int(min(symbol_config.get('trail_update_cooldown_ms', 2500), 1200))
        symbol_config['trail_start_bps'] = float(min(symbol_config.get('trail_start_bps', 25.0), 16.0))
        symbol_config['trail_dist_bps'] = float(max(4.0, min(symbol_config.get('trail_dist_bps', 12.0), 10.0)))
        symbol_config['disable_tp_on_trailing'] = True
        symbol_config['entry_offset_bps'] = float(max(0.4, min(symbol_config.get('entry_offset_bps', 1.5), 1.2)))
        symbol_config['max_loss_streak'] = int(max(2, min(symbol_config.get('max_loss_streak', 3), 4)))
        symbol_config['cooldown_after_loss_ms'] = int(min(symbol_config.get('cooldown_after_loss_ms', 15000), 8000))
        symbol_config['cooldown_after_loss_streak_ms'] = int(min(symbol_config.get('cooldown_after_loss_streak_ms', 60000), 30000))
    
    # Adjust TP/SL based on leverage
    if request.leverage >= 100:
        symbol_config['tp_bps'] = 15.0
        symbol_config['sl_bps'] = 8.0
    elif request.leverage >= 50:
        symbol_config['tp_bps'] = 25.0
        symbol_config['sl_bps'] = 12.0
    elif request.leverage >= 20:
        symbol_config['tp_bps'] = 35.0
        symbol_config['sl_bps'] = 18.0
    else:
        symbol_config['tp_bps'] = 50.0
        symbol_config['sl_bps'] = 25.0

    # Apply analysis-driven TP/SL if provided
    is_scalp = "scalp" in strategy_key
    timeframe_value = _normalize_timeframe(request.timeframe)
    short_timeframe = timeframe_value in {"1m", "3m", "5m", "15m"}
    micro_volatility = bool(request.micro_volatility)
    if not micro_volatility:
        atr_bps_hint = _safe_float(request.atr_bps, 0.0)
        micro_vol_threshold = _resolve_micro_vol_threshold(symbol_config, request.timeframe)
        micro_volatility = bool(short_timeframe and atr_bps_hint > 0 and atr_bps_hint < micro_vol_threshold)

    min_tp_bps = 6.0 if is_scalp else 10.0
    min_sl_bps = 5.0 if is_scalp else 8.0
    if is_scalp and micro_volatility:
        min_tp_bps = max(min_tp_bps, 10.0)
        min_sl_bps = max(min_sl_bps, 8.0)
    max_tp_bps = 120.0 if is_scalp else 260.0
    max_sl_bps = 80.0 if is_scalp else 180.0

    candidate_tp_bps = requested_tp_bps if requested_tp_bps is not None else derived_tp_bps
    candidate_sl_bps = requested_sl_bps if requested_sl_bps is not None else derived_sl_bps

    if candidate_tp_bps:
        symbol_config['tp_bps'] = _clamp_bps(candidate_tp_bps, min_tp_bps, max_tp_bps) or symbol_config['tp_bps']
    if candidate_sl_bps:
        symbol_config['sl_bps'] = _clamp_bps(candidate_sl_bps, min_sl_bps, max_sl_bps) or symbol_config['sl_bps']

    if is_scalp:
        try:
            liq_snapshot = await _get_liquidation_snapshot(symbol)
        except Exception:
            liq_snapshot = None

        if liq_snapshot:
            total_notional = _safe_float(liq_snapshot.get("total_notional"), 0.0)
            if total_notional > 0:
                scale = max(0.0, min(1.0, (math.log10(total_notional + 1) - 4.0) / 2.0))
                symbol_config['tp_bps'] = min(max_tp_bps, symbol_config['tp_bps'] * (1.0 + scale * 0.18))
                symbol_config['sl_bps'] = min(max_sl_bps, symbol_config['sl_bps'] * (1.0 + scale * 0.12))

    # Enforce RR ratio safety
    try:
        if symbol_config['tp_bps'] < symbol_config['sl_bps'] * 1.1:
            symbol_config['tp_bps'] = symbol_config['sl_bps'] * 1.2
    except Exception:
        pass

    symbol_config['min_tp_bps'] = min_tp_bps
    symbol_config['min_sl_bps'] = min_sl_bps

    # Scalp: align trailing start/dist to TP range for cleaner exits.
    if is_scalp:
        try:
            tp_bps = float(symbol_config.get('tp_bps', 12.0))
            symbol_config['trail_start_bps'] = float(max(6.0, min(tp_bps * 0.6, 22.0)))
            symbol_config['trail_dist_bps'] = float(max(3.0, min(tp_bps * 0.35, 14.0)))
        except Exception:
            pass

        try:
            sl_bps = float(symbol_config.get('sl_bps', min_sl_bps))
            symbol_config['anti_wick_bps'] = float(max(4.0, min(sl_bps * 0.7, 20.0)))
            symbol_config['trail_activation_hold_ms'] = int(
                max(800, min(symbol_config.get('trail_activation_hold_ms', 1500), 4000))
            )
        except Exception:
            pass

    if portfolio_guard_mode not in {"off", "soft", "hard"}:
        portfolio_guard_mode = "soft"
        portfolio_risk_guard["mode"] = portfolio_guard_mode
    is_directional_start = bool(symbol_config.get("force_entry") and preferred_side in {"LONG", "SHORT"})
    if portfolio_guard_mode != "off" and is_directional_start:
        try:
            account_response = await get_account_info()
            balance_total = _safe_float(account_response.get("balance", {}).get("total"), 0.0)
            positions_payload = await get_all_positions()
            active_positions = positions_payload.get("positions", [])
            estimated_notional = _estimate_target_notional(balance_total, request.leverage, symbol_config)
            candidate_snapshot = {
                "symbol": symbol,
                "side": preferred_side or "LONG",
                "amount": 0.0,
                "markPrice": _safe_float(request.entry_price, 0.0),
                "notional": estimated_notional,
                "leverage": request.leverage,
            }
            risk_exchange = await get_public_exchange()
            try:
                engine = await _compute_portfolio_correlation_engine(
                    exchange=risk_exchange,
                    positions=active_positions,
                    balance=balance_total,
                    candidate_position=candidate_snapshot,
                    timeframe="1h",
                    lookback=180,
                )
            finally:
                try:
                    await risk_exchange.close()
                except Exception:
                    pass

            risk_score = _safe_float(engine.get("correlation_risk_score"), 0.0)
            projected_exposure = _safe_float(engine.get("projected_exposure_percentage"), 0.0)
            recommended_exposure = _safe_float(engine.get("recommended_max_exposure_pct"), 0.0)
            breach = bool(engine.get("exposure_breach", False)) or risk_score >= portfolio_guard_max_score
            portfolio_risk_guard = {
                "mode": portfolio_guard_mode,
                "triggered": breach,
                "risk_score": round(risk_score, 2),
                "max_score": round(portfolio_guard_max_score, 2),
                "recommended_max_exposure_pct": round(recommended_exposure, 2),
                "projected_exposure_pct": round(projected_exposure, 2),
                "details": engine,
                "message": (
                    f"Projected exposure {projected_exposure:.1f}% vs limit {recommended_exposure:.1f}% "
                    f"(risk score {risk_score:.1f})."
                ),
            }
            if breach and portfolio_guard_mode == "hard":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Portfolio risk guard blocked trade: projected exposure {projected_exposure:.1f}% "
                        f"exceeds limit {recommended_exposure:.1f}% (risk score {risk_score:.1f})."
                    ),
                )
            if breach and portfolio_guard_mode == "soft":
                action_value = "HOLD"
                force_entry_requested = False
                preferred_side = None
                allowed_sides = ["LONG", "SHORT"]
                symbol_config["force_entry"] = False
                symbol_config["initial_entry_side"] = None
                symbol_config["allowed_sides"] = allowed_sides
                symbol_config["signal_action"] = action_value
        except HTTPException:
            raise
        except Exception as guard_error:
            portfolio_risk_guard = {
                "mode": portfolio_guard_mode,
                "triggered": False,
                "risk_score": 0.0,
                "recommended_max_exposure_pct": 0.0,
                "projected_exposure_pct": 0.0,
                "details": {},
                "message": f"Portfolio risk guard unavailable: {guard_error}",
            }
    
    try:
        # Create new bot instance for this symbol
        bot = TradingBot(
            config=symbol_config,
            api_key=creds['api_key'],
            secret_key=creds['secret_key']
        )
        
        bot_instances[symbol] = bot
        
        # Create task with cleanup callback
        async def run_with_cleanup():
            try:
                await bot.run()
            except Exception as e:
                print(f"Bot {symbol} crashed: {e}")
            finally:
                # Clean up on crash or completion
                if symbol in bot_instances:
                    del bot_instances[symbol]
                if symbol in bot_tasks:
                    del bot_tasks[symbol]
        
        bot_tasks[symbol] = asyncio.create_task(run_with_cleanup())
        guard_note = ""
        if portfolio_guard_mode == "soft" and bool(portfolio_risk_guard.get("triggered")):
            guard_note = " | portfolio-risk soft guard downgraded entry to signal mode"
        
        return {
            "status": "success",
            "message": (
                f"Trade started: {request.strategy_name} for {symbol} at {request.leverage}x leverage "
                f"({'/'.join(allowed_sides)} mode, {'force entry' if symbol_config['force_entry'] else 'signal-driven'})"
                f"{guard_note}"
            ),
            "symbol": symbol,
            "strategy_id": symbol_config['strategy_id'],
            "allowed_sides": allowed_sides,
            "force_entry": symbol_config['force_entry'],
            "initial_entry_side": symbol_config['initial_entry_side'],
            "spread_guard": spread_guard,
            "portfolio_risk_guard": portfolio_risk_guard
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/bot/status")
async def bot_status():
    """Get status of all running bots"""
    if not bot_instances:
        return {
            "bots": [],
            "total_running": 0
        }
    
    bots_status = []
    
    # Create a copy to avoid "dictionary changed size during iteration" error
    for symbol, bot in list(bot_instances.items()):
        try:
            status = bot.get_status()
            
            # Get positions if bot is running
            if bot.running:
                from dataclasses import asdict
                positions = await bot.fetch_positions_map()
                status["positions"] = {
                    "LONG": asdict(positions["LONG"]) if positions["LONG"] else None,
                    "SHORT": asdict(positions["SHORT"]) if positions["SHORT"] else None
                }
                
                # Get all open orders from exchange
                try:
                    open_orders = await bot.exchange.fetch_open_orders(symbol)
                    status["open_orders"] = [
                        {
                            "id": o["id"],
                            "type": o["type"],
                            "side": o["side"],
                            "price": o.get("price"),
                            "stopPrice": o.get("stopPrice"),
                            "amount": o["amount"],
                            "filled": o.get("filled", 0),
                            "remaining": o.get("remaining", o["amount"]),
                            "status": o["status"],
                            "timestamp": o.get("timestamp"),
                            "info": {
                                "positionSide": o.get("info", {}).get("positionSide"),
                                "reduceOnly": o.get("reduceOnly", False)
                            }
                        }
                        for o in open_orders
                    ]
                except:
                    status["open_orders"] = []
            else:
                status["positions"] = {"LONG": None, "SHORT": None}
                status["open_orders"] = []
            
            bots_status.append(status)
        except Exception:
            # Fallback to basic status
            status = bot.get_status()
            if "positions" not in status:
                status["positions"] = {"LONG": None, "SHORT": None}
            if "open_orders" not in status:
                status["open_orders"] = []
            bots_status.append(status)
    
    return {
        "bots": bots_status,
        "total_running": sum(1 for bot in bot_instances.values() if bot.running)
    }


@app.get("/api/logs/trades")
async def get_local_trade_history(limit: int = 50):
    """Get trade history aggregated from all local bot log files."""
    safe_limit = max(1, min(int(limit), 500))

    def _parse_ts(ts_value: Any) -> float:
        if not ts_value:
            return 0.0
        text = str(ts_value)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    try:
        log_candidates: List[Path] = []
        preferred_log = Path(resolve_log_file_path(config_store.get("log_file", "logs/bot.jsonl")))
        log_candidates.append(preferred_log)

        logs_dir = BASE_DIR / "logs"
        if logs_dir.exists():
            for file_path in logs_dir.glob("*.jsonl"):
                log_candidates.append(file_path)

        data_dir = os.getenv("MODAI_DATA_DIR", "").strip()
        if data_dir:
            data_logs_dir = Path(data_dir) / "logs"
            if data_logs_dir.exists():
                for file_path in data_logs_dir.glob("*.jsonl"):
                    log_candidates.append(file_path)

        # De-duplicate file list while preserving order.
        unique_paths: List[Path] = []
        seen_paths = set()
        for path_item in log_candidates:
            resolved = str(path_item.resolve()) if path_item.exists() else str(path_item)
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            unique_paths.append(path_item)

        trades: List[Dict[str, Any]] = []
        for log_path in unique_paths:
            if not log_path.exists():
                continue

            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            entry = json.loads(line)
                            event = entry.get("event", "")
                            if event not in ("fill_entry", "fill_tp", "fill_sl"):
                                continue

                            trade_type = "ENTRY" if event == "fill_entry" else ("TP" if event == "fill_tp" else "SL")
                            trades.append({
                                "timestamp": entry.get("timestamp"),
                                "type": trade_type,
                                "symbol": entry.get("symbol"),
                                "side": entry.get("side", "UNKNOWN"),
                                "price": entry.get("price", 0),
                                "amount": entry.get("amount", 0),
                                "pnl": entry.get("pnl"),
                                "order_id": entry.get("order_id", ""),
                                "source_log": log_path.name
                            })
                        except Exception:
                            continue
            except Exception:
                continue

        # Normalize and dedupe trades
        normalized: List[Dict[str, Any]] = []
        seen_keys = set()
        for trade in trades:
            symbol_value = str(trade.get("symbol") or "").upper()
            if symbol_value and "/" not in symbol_value and symbol_value.endswith("USDT"):
                symbol_value = f"{symbol_value[:-4]}/USDT"
            trade["symbol"] = symbol_value or "N/A"

            dedupe_key = (
                str(trade.get("order_id") or ""),
                str(trade.get("timestamp") or ""),
                str(trade.get("type") or ""),
                str(trade.get("symbol") or "")
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            normalized.append(trade)

        normalized.sort(key=lambda item: _parse_ts(item.get("timestamp")), reverse=True)
        return {"trades": normalized[:safe_limit]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs/tail")
async def tail_logs(lines: int = 50):
    """Get recent log entries"""
    log_file = resolve_log_file_path(config_store.get("log_file", "logs/bot.jsonl"))
    log_path = Path(log_file)
    
    if not log_path.exists():
        return {"logs": []}
    
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            recent = all_lines[-lines:]
            logs = [json.loads(line) for line in recent if line.strip()]
            return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Templates
@app.post("/api/templates")
async def save_template(template: Template):
    """Save a configuration template"""
    template_file = TEMPLATES_DIR / f"{template.name}.json"
    template_file.write_text(json.dumps(template.dict(), indent=2))
    return {"status": "success", "message": f"Template '{template.name}' saved"}


@app.get("/api/templates")
async def list_templates():
    """List all templates"""
    templates = []
    for file in TEMPLATES_DIR.glob("*.json"):
        try:
            data = json.loads(file.read_text())
            templates.append({"name": data["name"], "config": data["config"]})
        except Exception:
            pass
    return {"templates": templates}


@app.get("/api/templates/{name}")
async def get_template(name: str):
    """Get a specific template"""
    # Name'i dosya adına çevir: "Contrarian Scalper" -> "contrarian_scalper"
    file_name = name.lower().replace(" ", "_")
    template_file = TEMPLATES_DIR / f"{file_name}.json"
    
    if not template_file.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {name}")
    
    data = json.loads(template_file.read_text())
    return data


@app.delete("/api/templates/{name}")
async def delete_template(name: str):
    """Delete a template"""
    # Name'i dosya adına çevir
    file_name = name.lower().replace(" ", "_")
    template_file = TEMPLATES_DIR / f"{file_name}.json"
    
    if not template_file.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {name}")
    
    template_file.unlink()
    return {"status": "success", "message": f"Template '{name}' deleted"}





# WebSocket for live updates
@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    """WebSocket endpoint for live updates"""
    await websocket.accept()
    
    try:
        while True:
            if bot_instances:
                bots_status = []
                
                # Create a copy to avoid "dictionary changed size during iteration" error
                for symbol, bot in list(bot_instances.items()):
                    try:
                        from dataclasses import asdict
                        status = bot.get_status()
                        
                        # Add positions
                        positions = await bot.fetch_positions_map()
                        status["positions"] = {
                            "LONG": asdict(positions["LONG"]) if positions["LONG"] else None,
                            "SHORT": asdict(positions["SHORT"]) if positions["SHORT"] else None
                        }
                        
                        bots_status.append(status)
                    except Exception:
                        # Send basic status on error
                        status = bot.get_status()
                        if "positions" not in status:
                            status["positions"] = {"LONG": None, "SHORT": None}
                        bots_status.append(status)
                
                await websocket.send_json({
                    "bots": bots_status,
                    "total_running": sum(1 for bot in bot_instances.values() if bot.running)
                })
            else:
                # No bots running
                await websocket.send_json({
                    "bots": [],
                    "total_running": 0
                })
            
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


# ============================================================================
# BINANCE ACCOUNT ENDPOINTS - Direct Binance API Access
# ============================================================================

async def get_exchange():
    """Get authenticated exchange instance"""
    creds = decrypt_credentials()
    if not creds:
        raise HTTPException(status_code=400, detail="Credentials not configured")
    
    is_testnet = creds.get("is_testnet", True)  # Default to testnet for safety
    
    exchange = ccxtpro.binance({
        "apiKey": creds["api_key"],
        "secret": creds["secret_key"],
        "enableRateLimit": True,
        "options": {"defaultType": "future"}
    })
    
    # Enable demo trading based on saved mode
    if is_testnet:
        exchange.enable_demo_trading(True)
    
    return exchange


async def get_public_exchange():
    """Get public futures exchange instance (no credentials required)."""
    creds = decrypt_credentials()
    is_testnet = bool(creds.get("is_testnet", True)) if creds else True
    exchange = ccxtpro.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "future"}
    })
    if is_testnet:
        exchange.enable_demo_trading(True)
    return exchange


async def _get_liquidation_snapshot(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch recent liquidation snapshot with short-lived cache."""
    normalized_symbol = _normalize_to_futures_symbol(symbol or "")
    if not normalized_symbol:
        return None

    now_ts = time.time()
    cached = _liq_snapshot_cache.get(normalized_symbol)
    if cached and (now_ts - cached.get("ts", 0.0)) <= LIQ_SNAPSHOT_CACHE_TTL_SECONDS:
        return cached.get("payload")

    exchange = None
    try:
        exchange = await get_exchange()
        hunter = LiquidationHunter(exchange)
        payload = await hunter.get_liquidation_snapshot(normalized_symbol)
        _liq_snapshot_cache[normalized_symbol] = {"ts": now_ts, "payload": payload}
        return payload
    except Exception:
        return None
    finally:
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass


async def _cleanup_stale_conditionals(
    exchange: Any,
    symbol_filter: Optional[str] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Cancel stale reduce-only conditional orders that no longer have a backing position.
    Keeps active protective conditionals for existing positions.
    """
    normalized_symbol = _normalize_to_futures_symbol(symbol_filter or "")
    symbol_code = normalized_symbol.replace("/", "") if normalized_symbol else ""
    query_params: Dict[str, Any] = {"symbol": symbol_code} if symbol_code else {}

    open_orders_raw = await exchange.fapiPrivateGetOpenOrders(query_params) if query_params else await exchange.fapiPrivateGetOpenOrders()
    positions_raw = await exchange.fapiPrivateV2GetPositionRisk(query_params) if query_params else await exchange.fapiPrivateV2GetPositionRisk()

    position_map: Dict[str, Dict[str, float]] = {}
    for row in positions_raw:
        sym = str(row.get("symbol") or "")
        amt = _safe_float(row.get("positionAmt"), 0.0)
        pos_side = str(row.get("positionSide") or "").upper()
        bucket = position_map.setdefault(sym, {"LONG": 0.0, "SHORT": 0.0, "BOTH": 0.0})
        if pos_side == "LONG" and amt > 0:
            bucket["LONG"] = abs(amt)
        elif pos_side == "SHORT" and amt < 0:
            bucket["SHORT"] = abs(amt)
        elif abs(amt) > 0:
            bucket["BOTH"] = abs(amt)
            if amt > 0:
                bucket["LONG"] = abs(amt)
            else:
                bucket["SHORT"] = abs(amt)

    conditional_types = {
        "STOP",
        "STOP_MARKET",
        "TAKE_PROFIT",
        "TAKE_PROFIT_MARKET",
        "TRAILING_STOP_MARKET",
    }

    scanned = 0
    stale_candidates = 0
    canceled = 0
    cancel_errors = 0
    canceled_orders: List[Dict[str, Any]] = []

    for order in open_orders_raw:
        scanned += 1
        order_type = str(order.get("type") or "").upper()
        if order_type not in conditional_types:
            continue

        reduce_only = _to_bool(order.get("reduceOnly")) or _to_bool(order.get("closePosition"))
        if not reduce_only:
            continue

        symbol_raw = str(order.get("symbol") or "")
        position_side = str(order.get("positionSide") or "BOTH").upper()
        position_bucket = position_map.get(symbol_raw, {"LONG": 0.0, "SHORT": 0.0, "BOTH": 0.0})

        has_position = False
        if position_side == "LONG":
            has_position = position_bucket.get("LONG", 0.0) > 0
        elif position_side == "SHORT":
            has_position = position_bucket.get("SHORT", 0.0) > 0
        else:
            has_position = (
                position_bucket.get("LONG", 0.0) > 0
                or position_bucket.get("SHORT", 0.0) > 0
                or position_bucket.get("BOTH", 0.0) > 0
            )

        if has_position:
            continue

        stale_candidates += 1
        order_id = str(order.get("orderId") or order.get("id") or "")
        if not order_id:
            continue

        if dry_run:
            continue

        try:
            symbol_pair = _normalize_to_futures_symbol(symbol_raw) or symbol_raw
            await exchange.cancel_order(order_id, symbol_pair)
            canceled += 1
            canceled_orders.append({
                "orderId": order_id,
                "symbol": symbol_raw,
                "type": order_type,
                "positionSide": position_side
            })
        except Exception:
            try:
                await exchange.fapiPrivateDeleteOrder({
                    "symbol": symbol_raw,
                    "orderId": order_id
                })
                canceled += 1
                canceled_orders.append({
                    "orderId": order_id,
                    "symbol": symbol_raw,
                    "type": order_type,
                    "positionSide": position_side
                })
            except Exception:
                cancel_errors += 1

    return {
        "status": "success",
        "symbol_filter": normalized_symbol or None,
        "dry_run": bool(dry_run),
        "scanned": scanned,
        "stale_candidates": stale_candidates,
        "canceled": canceled,
        "cancel_errors": cancel_errors,
        "canceled_orders": canceled_orders[:25],
    }


async def _stale_conditionals_maintenance_loop() -> None:
    """Periodically clean stale reduce-only conditionals across all symbols."""
    # Give backend startup and first UI boot traffic time to settle.
    await asyncio.sleep(20)
    while True:
        try:
            creds = decrypt_credentials()
            if not creds or not creds.get("api_key") or not creds.get("secret_key"):
                await asyncio.sleep(STALE_CONDITIONALS_CLEANUP_INTERVAL_SECONDS)
                continue

            exchange = await get_exchange()
            try:
                result = await _cleanup_stale_conditionals(exchange, symbol_filter=None, dry_run=False)
                canceled_count = int(result.get("canceled", 0))
                if canceled_count > 0:
                    print(f"stale-conditional-cleanup: canceled {canceled_count} orders", flush=True)
            finally:
                await exchange.close()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"stale-conditional-cleanup: skipped ({exc})", flush=True)

        await asyncio.sleep(STALE_CONDITIONALS_CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _stale_conditionals_task
    if _stale_conditionals_task is None or _stale_conditionals_task.done():
        _stale_conditionals_task = asyncio.create_task(_stale_conditionals_maintenance_loop())
    try:
        yield
    finally:
        if _stale_conditionals_task and not _stale_conditionals_task.done():
            _stale_conditionals_task.cancel()
            try:
                await _stale_conditionals_task
            except asyncio.CancelledError:
                pass
        _stale_conditionals_task = None


app.router.lifespan_context = lifespan


@app.get("/api/binance/account")
async def get_account_info():
    """Get Binance account information"""
    exchange = await get_exchange()
    try:
        balance = await exchange.fetch_balance({"type": "future"})
        account_info = await exchange.fapiPrivateV2GetAccount()
        
        await exchange.close()
        
        return {
            "balance": {
                "total": balance.get("USDT", {}).get("total", 0),
                "free": balance.get("USDT", {}).get("free", 0),
                "used": balance.get("USDT", {}).get("used", 0)
            },
            "account": {
                "totalWalletBalance": float(account_info.get("totalWalletBalance", 0)),
                "totalUnrealizedProfit": float(account_info.get("totalUnrealizedProfit", 0)),
                "totalMarginBalance": float(account_info.get("totalMarginBalance", 0)),
                "availableBalance": float(account_info.get("availableBalance", 0)),
                "maxWithdrawAmount": float(account_info.get("maxWithdrawAmount", 0))
            }
        }
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/binance/positions")
async def get_all_positions():
    """Get all open positions from Binance"""
    exchange = await get_exchange()
    try:
        positions = await exchange.fapiPrivateV2GetPositionRisk()
        await exchange.close()
        
        # Filter only positions with non-zero amount
        active_positions = []
        for pos in positions:
            amount = float(pos.get("positionAmt", 0))
            if amount != 0:
                raw_symbol = str(pos.get("symbol") or "")
                normalized_symbol = _normalize_to_futures_symbol(raw_symbol) or raw_symbol
                entry_price = float(pos.get("entryPrice", 0))
                mark_price = float(pos.get("markPrice", 0))
                unrealized_pnl = float(pos.get("unRealizedProfit", 0))
                
                # Calculate PnL percentage
                pnl_percent = 0
                if entry_price > 0:
                    if amount > 0:  # LONG
                        pnl_percent = ((mark_price - entry_price) / entry_price) * 100
                    else:  # SHORT
                        pnl_percent = ((entry_price - mark_price) / entry_price) * 100
                
                active_positions.append({
                    "symbol": normalized_symbol,
                    "side": "LONG" if amount > 0 else "SHORT",
                    "amount": abs(amount),
                    "entryPrice": entry_price,
                    "markPrice": mark_price,
                    "leverage": int(pos.get("leverage", 1)),
                    "unrealizedPnl": unrealized_pnl,
                    "pnlPercent": pnl_percent,
                    "liquidationPrice": float(pos.get("liquidationPrice", 0)),
                    "marginType": pos.get("marginType"),
                    "isolatedMargin": float(pos.get("isolatedMargin", 0)),
                    "positionSide": pos.get("positionSide")
                })
        
        return {"positions": active_positions}
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/binance/orders")
async def get_open_orders(symbol: str):
    """Get all open orders for a symbol"""
    exchange = await get_exchange()
    try:
        normalized_symbol = _normalize_to_futures_symbol(symbol)
        if not normalized_symbol:
            raise HTTPException(status_code=400, detail="Invalid symbol")

        resolved_symbol = await _resolve_exchange_symbol(exchange, normalized_symbol)
        orders: List[Dict[str, Any]] = []
        candidates = [resolved_symbol, normalized_symbol]
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                orders = await exchange.fetch_open_orders(candidate)
                if orders:
                    break
            except Exception:
                continue

        if not orders:
            try:
                raw_orders = await exchange.fapiPrivateGetOpenOrders({
                    "symbol": normalized_symbol.replace("/", "")
                })
                orders = raw_orders if isinstance(raw_orders, list) else []
            except Exception:
                orders = []

        await exchange.close()
        
        formatted_orders = []
        for o in orders:
            if isinstance(o, dict) and "orderId" in o:
                raw_symbol = str(o.get("symbol") or "")
                amount = _safe_float(o.get("origQty"), 0.0)
                filled = _safe_float(o.get("executedQty"), 0.0)
                formatted_orders.append({
                    "id": str(o.get("orderId") or ""),
                    "symbol": _normalize_to_futures_symbol(raw_symbol) or normalized_symbol,
                    "type": o.get("type"),
                    "side": str(o.get("side") or "").lower(),
                    "price": _safe_float(o.get("price"), 0.0),
                    "stopPrice": _safe_float(o.get("stopPrice"), 0.0),
                    "amount": amount,
                    "filled": filled,
                    "remaining": max(0.0, amount - filled),
                    "status": o.get("status"),
                    "timestamp": int(_safe_float(o.get("time"), 0.0)),
                    "positionSide": o.get("positionSide"),
                    "reduceOnly": _to_bool(o.get("reduceOnly")) or _to_bool(o.get("closePosition")),
                    "timeInForce": o.get("timeInForce"),
                })
            else:
                output_symbol = _normalize_to_futures_symbol(o.get("symbol") or normalized_symbol) or normalized_symbol
                info = o.get("info", {}) if isinstance(o.get("info"), dict) else {}
                amount = _safe_float(o.get("amount"), 0.0)
                filled = _safe_float(o.get("filled"), 0.0)
                formatted_orders.append({
                    "id": str(o.get("id") or info.get("orderId") or ""),
                    "symbol": output_symbol,
                    "type": o.get("type"),
                    "side": o.get("side"),
                    "price": _safe_float(o.get("price"), 0.0),
                    "stopPrice": _safe_float(o.get("stopPrice"), 0.0),
                    "amount": amount,
                    "filled": filled,
                    "remaining": _safe_float(o.get("remaining"), max(0.0, amount - filled)),
                    "status": o.get("status"),
                    "timestamp": o.get("timestamp"),
                    "positionSide": info.get("positionSide"),
                    "reduceOnly": bool(o.get("reduceOnly", False)),
                    "timeInForce": o.get("timeInForce")
                })
        
        return {"orders": formatted_orders}
    except HTTPException:
        await exchange.close()
        raise
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/binance/cleanup-stale-conditionals")
async def cleanup_stale_conditionals(symbol: Optional[str] = None, dry_run: bool = False):
    """Cleanup stale reduce-only conditional orders without matching open positions."""
    exchange = await get_exchange()
    try:
        result = await _cleanup_stale_conditionals(exchange, symbol_filter=symbol, dry_run=dry_run)
        await exchange.close()
        return result
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/binance/order-history")
async def get_order_history(symbol: str, limit: int = 80):
    """Get historical orders (open + closed) for a symbol."""
    exchange = await get_exchange()
    try:
        safe_limit = max(1, min(int(limit), 500))
        normalized_symbol = _normalize_to_futures_symbol(symbol)
        if not normalized_symbol:
            raise HTTPException(status_code=400, detail="Invalid symbol")

        all_orders: List[Dict[str, Any]] = []

        try:
            resolved_symbol = await _resolve_exchange_symbol(exchange, normalized_symbol)
            candidates = [resolved_symbol, normalized_symbol]
            seen: set[str] = set()
            for candidate in candidates:
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                try:
                    all_orders = await exchange.fetch_orders(candidate, limit=safe_limit)
                    if all_orders:
                        break
                except Exception:
                    continue
            if not all_orders:
                raise RuntimeError("fetch_orders returned no rows; fallback to raw endpoint")
        except Exception:
            raw_orders = await exchange.fapiPrivateGetAllOrders({
                "symbol": normalized_symbol.replace("/", ""),
                "limit": safe_limit
            })
            all_orders = raw_orders if isinstance(raw_orders, list) else []

        await exchange.close()

        formatted_orders: List[Dict[str, Any]] = []
        for order in all_orders:
            if isinstance(order, dict) and "orderId" in order:
                raw_symbol = str(order.get("symbol") or "")
                formatted_orders.append({
                    "id": str(order.get("orderId") or ""),
                    "symbol": _normalize_to_futures_symbol(raw_symbol) or raw_symbol,
                    "type": order.get("type"),
                    "side": str(order.get("side") or "").lower(),
                    "price": _safe_float(order.get("price"), 0.0),
                    "avgPrice": _safe_float(order.get("avgPrice"), 0.0),
                    "amount": _safe_float(order.get("origQty"), 0.0),
                    "filled": _safe_float(order.get("executedQty"), 0.0),
                    "remaining": max(
                        0.0,
                        _safe_float(order.get("origQty"), 0.0) - _safe_float(order.get("executedQty"), 0.0),
                    ),
                    "status": order.get("status"),
                    "timestamp": int(_safe_float(order.get("time"), 0)),
                    "positionSide": order.get("positionSide"),
                    "reduceOnly": str(order.get("reduceOnly", "false")).lower() == "true",
                    "timeInForce": order.get("timeInForce")
                })
            else:
                info = order.get("info", {}) if isinstance(order, dict) else {}
                raw_symbol = str(order.get("symbol") or info.get("symbol") or "")
                amount = _safe_float(order.get("amount"), 0.0)
                filled = _safe_float(order.get("filled"), 0.0)
                formatted_orders.append({
                    "id": str(order.get("id") or info.get("orderId") or ""),
                    "symbol": _normalize_to_futures_symbol(raw_symbol) or raw_symbol,
                    "type": order.get("type"),
                    "side": str(order.get("side") or "").lower(),
                    "price": _safe_float(order.get("price"), 0.0),
                    "avgPrice": _safe_float(order.get("average"), 0.0),
                    "amount": amount,
                    "filled": filled,
                    "remaining": _safe_float(order.get("remaining"), max(0.0, amount - filled)),
                    "status": order.get("status"),
                    "timestamp": int(_safe_float(order.get("timestamp"), 0)),
                    "positionSide": info.get("positionSide"),
                    "reduceOnly": bool(order.get("reduceOnly", info.get("reduceOnly", False))),
                    "timeInForce": order.get("timeInForce", info.get("timeInForce"))
                })

        formatted_orders.sort(key=lambda item: int(item.get("timestamp") or 0), reverse=True)
        return {"orders": formatted_orders[:safe_limit]}
    except HTTPException:
        await exchange.close()
        raise
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/binance/order")
async def cancel_order(symbol: str, order_id: str):
    """Cancel a specific order"""
    exchange = await get_exchange()
    try:
        result = await exchange.cancel_order(order_id, symbol)
        await exchange.close()
        return {"status": "success", "message": f"Order {order_id} canceled", "result": result}
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/binance/orders/all")
async def cancel_all_orders(symbol: str):
    """Cancel all orders for a symbol"""
    exchange = await get_exchange()
    try:
        result = await exchange.cancel_all_orders(symbol)
        await exchange.close()
        return {"status": "success", "message": f"All orders canceled for {symbol}", "count": len(result)}
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/binance/orders/all-symbols")
async def cancel_all_orders_all_symbols():
    """Cancel ALL orders for ALL symbols (emergency cleanup)"""
    exchange = await get_exchange()
    try:
        # Get ALL open orders directly
        all_open_orders = await exchange.fapiPrivateGetOpenOrders()
        
        # Group by symbol
        symbols_with_orders = {}
        for order in all_open_orders:
            symbol = order.get("symbol", "")
            if symbol not in symbols_with_orders:
                symbols_with_orders[symbol] = []
            symbols_with_orders[symbol].append(order)
        
        total_canceled = 0
        results = []
        
        # Cancel all orders for each symbol
        for symbol, orders in symbols_with_orders.items():
            try:
                # Convert BTCUSDT -> BTC/USDT
                symbol_formatted = symbol.replace("USDT", "/USDT") if "/" not in symbol else symbol
                
                # Cancel all orders for this symbol
                result = await exchange.cancel_all_orders(symbol_formatted)
                canceled_count = len(orders)
                total_canceled += canceled_count
                results.append({
                    "symbol": symbol_formatted,
                    "canceled": canceled_count,
                    "orders": [o.get("orderId") for o in orders]
                })
            except Exception as e:
                results.append({"symbol": symbol, "error": str(e), "order_count": len(orders)})
        
        await exchange.close()
        
        return {
            "status": "success",
            "message": f"Canceled {total_canceled} orders across {len(symbols_with_orders)} symbols",
            "total_canceled": total_canceled,
            "total_symbols": len(symbols_with_orders),
            "results": results
        }
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


class ClosePositionRequest(BaseModel):
    symbol: str
    side: str  # LONG or SHORT


@app.post("/api/binance/close-position")
async def close_position(request: ClosePositionRequest):
    """Close a position by market order"""
    exchange = await get_exchange()
    try:
        # Get current position
        positions = await exchange.fapiPrivateV2GetPositionRisk({"symbol": request.symbol.replace("/", "")})
        
        position = None
        for pos in positions:
            pos_side = pos.get("positionSide")
            amount = float(pos.get("positionAmt", 0))
            
            if request.side == "LONG" and pos_side == "LONG" and amount > 0:
                position = pos
                break
            elif request.side == "SHORT" and pos_side == "SHORT" and amount < 0:
                position = pos
                break
        
        if not position:
            await exchange.close()
            raise HTTPException(status_code=404, detail=f"No {request.side} position found for {request.symbol}")
        
        amount = abs(float(position.get("positionAmt", 0)))
        
        # Place market order to close
        side = "sell" if request.side == "LONG" else "buy"
        order = await exchange.create_order(
            request.symbol,
            "MARKET",
            side,
            amount,
            None,
            {"positionSide": request.side, "reduceOnly": True}
        )
        
        await exchange.close()
        return {
            "status": "success",
            "message": f"Closed {request.side} position for {request.symbol}",
            "order": order
        }
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/binance/trade-history")
async def get_binance_trade_history(limit: int = 100, symbol: Optional[str] = None):
    """Get recent trade history from Binance"""
    try:
        safe_limit = max(1, min(int(limit), 500))
        normalized_symbol = _normalize_to_futures_symbol(symbol or "")
        cache_key = f"{normalized_symbol or 'ALL'}:{safe_limit}"
        cached = _trade_history_cache.get(cache_key)
        now_ts = time.time()
        if cached and (now_ts - cached.get("ts", 0.0)) <= TRADE_HISTORY_CACHE_TTL_SECONDS:
            return cached.get("payload", {"trades": []})

        exchange = await get_exchange()

        all_trades: List[Dict[str, Any]] = []

        # Fetch selected symbol only when provided; otherwise use a bounded, high-signal symbol set
        # to keep response time stable for analytics/dashboard calls.
        curated_symbols = [
            'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT',
            'DOGE/USDT', 'POL/USDT', 'XRP/USDT', 'AVAX/USDT'
        ]
        if normalized_symbol:
            symbols = [normalized_symbol]
        else:
            symbols = []
            # Prioritize currently running bot symbols first.
            for running_symbol in list(bot_instances.keys()):
                normalized_running = _normalize_to_futures_symbol(running_symbol)
                if normalized_running and normalized_running not in symbols:
                    symbols.append(normalized_running)
            for candidate_symbol in curated_symbols:
                if candidate_symbol not in symbols:
                    symbols.append(candidate_symbol)
            symbols = symbols[:8]

        per_symbol_limit = max(
            20,
            min(90, safe_limit if normalized_symbol else (safe_limit // max(1, len(symbols))) + 25)
        )

        for pair in symbols:
            try:
                trades = await _fetch_my_trades_safe(exchange, pair, per_symbol_limit)
                all_trades.extend(trades)
                if len(all_trades) >= safe_limit * 2:
                    break
            except:
                continue
        
        await exchange.close()
        
        # Sort by timestamp (newest first)
        all_trades.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        
        # Format trades
        formatted_trades = []
        for trade in all_trades[:safe_limit]:
            pnl = _extract_trade_pnl(trade)
            normalized_trade_symbol = (
                _normalize_to_futures_symbol(trade.get('symbol'))
                or _normalize_to_futures_symbol((trade.get('info') or {}).get('symbol'))
                or str(trade.get('symbol') or '')
            )
            raw_type = trade.get('type') or (trade.get('info') or {}).get('type') or 'TRADE'
            trade_type = str(raw_type).upper() if raw_type else 'TRADE'
            formatted_trades.append({
                'id': trade.get('id'),
                'symbol': normalized_trade_symbol,
                'side': str(trade.get('side') or '').upper(),
                'type': trade_type,
                'price': _safe_float(trade.get('price', 0)),
                'amount': _safe_float(trade.get('amount', 0)),
                'cost': _safe_float(trade.get('cost', 0)),
                'fee': _safe_float(trade.get('fee', {}).get('cost', 0) if isinstance(trade.get('fee'), dict) else 0),
                'timestamp': int(_safe_float(trade.get('timestamp', 0))),
                'datetime': trade.get('datetime', ''),
                'pnl': pnl,
                'realizedPnl': pnl,
                'source': 'trade'
            })

        payload = {'trades': formatted_trades, 'items': formatted_trades}
        _trade_history_cache[cache_key] = {"ts": now_ts, "payload": payload}
        return payload
        
    except Exception as e:
        if "exchange" in locals():
            await exchange.close()
        print(f"Trade history error: {e}")
        return {'trades': []}


@app.get("/api/binance/income-history")
async def get_binance_income_history(limit: int = 200, symbol: Optional[str] = None):
    """Get Binance futures income history (commission, realized pnl, funding, etc.)."""
    exchange = await get_exchange()
    try:
        safe_limit = max(1, min(int(limit), 1000))
        params: Dict[str, Any] = {"limit": safe_limit}
        normalized_symbol = _normalize_to_futures_symbol(symbol or "")
        if normalized_symbol:
            params["symbol"] = normalized_symbol.replace("/", "")

        income_items = await exchange.fapiPrivateGetIncome(params)
        await exchange.close()

        rows: List[Dict[str, Any]] = []
        for idx, item in enumerate(income_items):
            raw_symbol = str(item.get("symbol") or "")
            norm_symbol = _normalize_to_futures_symbol(raw_symbol)
            income_type = str(item.get("incomeType") or "")
            normalized_type = income_type.upper().replace(" ", "_")
            income_value = _safe_float(item.get("income"), 0.0)
            timestamp_ms = int(_safe_float(item.get("time"), 0.0))
            datetime_iso = datetime.fromtimestamp(timestamp_ms / 1000).isoformat() if timestamp_ms else ""
            is_commission = "COMMISSION" in normalized_type
            rows.append({
                "id": item.get("tranId") or item.get("tradeId") or f"{raw_symbol}:{timestamp_ms}:{idx}",
                "symbol": norm_symbol or raw_symbol,
                "incomeType": income_type,
                "type": normalized_type,
                "side": "REALIZED_GAIN" if income_value >= 0 else "REALIZED_LOSS",
                "price": 0.0,
                "amount": 0.0,
                "cost": 0.0,
                "fee": abs(income_value) if is_commission else 0.0,
                "pnl": income_value,
                "income": income_value,
                "asset": item.get("asset"),
                "info": item.get("info"),
                "tranId": item.get("tranId"),
                "tradeId": item.get("tradeId"),
                "time": timestamp_ms,
                "timestamp": timestamp_ms,
                "datetime": datetime_iso,
                "source": "income"
            })

        rows.sort(key=lambda x: int(x.get("time") or 0), reverse=True)
        return {"items": rows[:safe_limit]}
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ledger/events")
async def get_ledger_events(limit: int = 200, symbol: Optional[str] = None):
    """
    Unified ledger events (bot logs + trade history + income history).
    """
    safe_limit = max(1, min(int(limit), 500))
    normalized_symbol = _normalize_to_futures_symbol(symbol or "")

    events: List[Dict[str, Any]] = []

    try:
        local = await get_local_trade_history(limit=safe_limit)
        for row in local.get("trades", []):
            sym = _normalize_to_futures_symbol(row.get("symbol")) or row.get("symbol")
            if normalized_symbol and sym != normalized_symbol:
                continue
            events.append({
                "id": row.get("order_id") or row.get("id") or row.get("timestamp"),
                "symbol": sym,
                "type": str(row.get("type") or "BOT").upper(),
                "side": str(row.get("side") or "").upper(),
                "price": _safe_float(row.get("price"), 0.0),
                "amount": _safe_float(row.get("amount"), 0.0),
                "fee": _safe_float(row.get("fee"), 0.0),
                "pnl": _safe_float(row.get("pnl"), 0.0),
                "timestamp": int(_safe_float(row.get("timestamp"), 0.0)),
                "datetime": row.get("datetime") or "",
                "source": "bot_log"
            })
    except Exception:
        pass

    try:
        trade_payload = await get_binance_trade_history(limit=safe_limit, symbol=normalized_symbol or None)
        for row in trade_payload.get("trades", []):
            sym = _normalize_to_futures_symbol(row.get("symbol")) or row.get("symbol")
            if normalized_symbol and sym != normalized_symbol:
                continue
            events.append({
                "id": row.get("id") or row.get("order_id") or row.get("timestamp"),
                "symbol": sym,
                "type": str(row.get("type") or "TRADE").upper(),
                "side": str(row.get("side") or "").upper(),
                "price": _safe_float(row.get("price"), 0.0),
                "amount": _safe_float(row.get("amount"), 0.0),
                "fee": _safe_float(row.get("fee"), 0.0),
                "pnl": _safe_float(row.get("pnl"), 0.0),
                "timestamp": int(_safe_float(row.get("timestamp"), 0.0)),
                "datetime": row.get("datetime") or "",
                "source": "trade"
            })
    except Exception:
        pass

    try:
        income_payload = await get_binance_income_history(limit=safe_limit, symbol=normalized_symbol or None)
        for row in income_payload.get("items", []):
            sym = _normalize_to_futures_symbol(row.get("symbol")) or row.get("symbol")
            if normalized_symbol and sym != normalized_symbol:
                continue
            events.append({
                "id": row.get("id") or row.get("tranId") or row.get("tradeId") or row.get("time"),
                "symbol": sym,
                "type": str(row.get("type") or row.get("incomeType") or "INCOME").upper(),
                "side": str(row.get("side") or "").upper(),
                "price": _safe_float(row.get("price"), 0.0),
                "amount": _safe_float(row.get("amount"), 0.0),
                "fee": _safe_float(row.get("fee"), 0.0),
                "pnl": _safe_float(row.get("pnl") or row.get("income"), 0.0),
                "timestamp": int(_safe_float(row.get("timestamp") or row.get("time"), 0.0)),
                "datetime": row.get("datetime") or "",
                "source": "income"
            })
    except Exception:
        pass

    # Deduplicate
    deduped: List[Dict[str, Any]] = []
    seen: set = set()
    for event in events:
        key = (
            event.get("source"),
            event.get("id"),
            event.get("symbol"),
            event.get("timestamp")
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)

    deduped.sort(key=lambda x: int(x.get("timestamp") or 0), reverse=True)
    return {
        "events": deduped[:safe_limit],
        "count": min(len(deduped), safe_limit)
    }


def _ledger_summary_from_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_realized = 0.0
    total_commission = 0.0
    wins = 0
    losses = 0

    for row in events:
        source = str(row.get("source") or "").lower()
        event_type = str(row.get("type") or row.get("incomeType") or "").upper()
        pnl = _safe_float(row.get("pnl"), 0.0)
        fee = _safe_float(row.get("fee"), 0.0)

        if "COMMISSION" in event_type:
            total_commission += abs(pnl) if pnl != 0 else abs(fee)

        realized_event = (
            source == "income"
            or "REALIZED" in event_type
            or (source in {"trade", "bot_log"} and abs(pnl) > 1e-12)
        )
        if realized_event:
            total_realized += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0

    return {
        "totalRealizedPnl": total_realized,
        "totalUnrealizedPnl": 0.0,
        "totalPnl": total_realized,
        "totalCommission": total_commission,
        "winningTrades": wins,
        "losingTrades": losses,
        "totalTrades": total_trades,
        "winRate": win_rate
    }


@app.get("/api/ledger/summary")
async def get_ledger_summary(limit: int = 300, symbol: Optional[str] = None):
    """Summary metrics derived from ledger events (trade/income/bot logs)."""
    payload = await get_ledger_events(limit=limit, symbol=symbol)
    events = payload.get("events", []) if isinstance(payload, dict) else []
    summary = _ledger_summary_from_events(events)
    return {"summary": summary, "count": len(events)}


@app.get("/api/binance/pnl-stats")
async def get_pnl_stats():
    """Get PnL statistics"""
    exchange = await get_exchange()
    try:
        # Get account info
        account_info = await exchange.fapiPrivateV2GetAccount()
        
        # Get income history (last 7 days)
        income = await exchange.fapiPrivateGetIncome({
            "limit": 1000
        })
        
        # Calculate stats
        total_realized_pnl = 0
        total_commission = 0
        winning_trades = 0
        losing_trades = 0

        realized_type_tokens = {
            "REALIZED_PNL",
            "REALIZEDPNL",
            "REALIZED_PROFIT",
            "REALIZEDPROFIT",
        }
        commission_type_tokens = {
            "COMMISSION",
            "TRADING_FEE",
            "FEE",
        }
        
        for item in income:
            income_type_raw = str(item.get("incomeType", "") or "")
            income_type = (
                income_type_raw.upper()
                .replace("-", "_")
                .replace(" ", "_")
            )
            amount = _safe_float(item.get("income"), 0.0)

            if income_type in realized_type_tokens or "REALIZED" in income_type:
                total_realized_pnl += amount
                if amount > 0:
                    winning_trades += 1
                elif amount < 0:
                    losing_trades += 1
            elif income_type in commission_type_tokens or "COMMISSION" in income_type:
                total_commission += abs(amount)

        # Fallback: derive counts/PnL from trade history if income endpoint is sparse.
        if (winning_trades + losing_trades) == 0:
            fallback_pairs = [
                "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
                "AVAX/USDT", "DOGE/USDT", "ARB/USDT", "OP/USDT",
                "POL/USDT", "ADA/USDT", "DOT/USDT", "LINK/USDT",
                "XRP/USDT", "NEAR/USDT", "CAKE/USDT",
            ]
            fallback_realized = 0.0
            fallback_wins = 0
            fallback_losses = 0
            for pair in fallback_pairs:
                try:
                    trades = await _fetch_my_trades_safe(exchange, pair, 60)
                except Exception:
                    continue
                for trade in trades:
                    pnl = _extract_trade_pnl(trade)
                    if pnl > 0:
                        fallback_wins += 1
                        fallback_realized += pnl
                    elif pnl < 0:
                        fallback_losses += 1
                        fallback_realized += pnl

            if fallback_wins + fallback_losses > 0:
                winning_trades = fallback_wins
                losing_trades = fallback_losses
                # Use fallback realized only when income realized is unavailable.
                if abs(total_realized_pnl) < 1e-9:
                    total_realized_pnl = fallback_realized
        
        total_trades = winning_trades + losing_trades
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        await exchange.close()
        
        return {
            "totalRealizedPnl": total_realized_pnl,
            "totalUnrealizedPnl": float(account_info.get("totalUnrealizedProfit", 0)),
            "totalPnl": total_realized_pnl + float(account_info.get("totalUnrealizedProfit", 0)),
            "totalCommission": total_commission,
            "winningTrades": winning_trades,
            "losingTrades": losing_trades,
            "totalTrades": total_trades,
            "winRate": win_rate
        }
    except Exception as e:
        await exchange.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/portfolio/state")
async def get_portfolio_state():
    """
    Unified runner + exchange portfolio snapshot for UI consistency.
    """
    exchange = await get_exchange()
    try:
        account_info = await exchange.fapiPrivateV2GetAccount()
        positions_raw = await exchange.fapiPrivateV2GetPositionRisk()
        open_orders_raw = await exchange.fapiPrivateGetOpenOrders()

        active_positions: List[Dict[str, Any]] = []
        for pos in positions_raw:
            amount = _safe_float(pos.get("positionAmt"), 0.0)
            if abs(amount) <= 0:
                continue
            raw_symbol = str(pos.get("symbol") or "")
            normalized_symbol = _normalize_to_futures_symbol(raw_symbol) or raw_symbol
            entry_price = _safe_float(pos.get("entryPrice"), 0.0)
            mark_price = _safe_float(pos.get("markPrice"), 0.0)
            unrealized_pnl = _safe_float(pos.get("unRealizedProfit"), 0.0)
            pnl_percent = 0.0
            if entry_price > 0:
                if amount > 0:
                    pnl_percent = ((mark_price - entry_price) / entry_price) * 100.0
                else:
                    pnl_percent = ((entry_price - mark_price) / entry_price) * 100.0

            active_positions.append({
                "symbol": normalized_symbol,
                "side": "LONG" if amount > 0 else "SHORT",
                "amount": abs(amount),
                "entryPrice": entry_price,
                "markPrice": mark_price,
                "leverage": int(_safe_float(pos.get("leverage"), 1)),
                "unrealizedPnl": unrealized_pnl,
                "pnlPercent": pnl_percent,
            })

        open_orders_summary: Dict[str, Dict[str, Any]] = {}
        open_orders_sample: List[Dict[str, Any]] = []
        for order in open_orders_raw or []:
            raw_symbol = str(order.get("symbol") or "")
            normalized_symbol = _normalize_to_futures_symbol(raw_symbol) or raw_symbol
            if not normalized_symbol:
                continue
            summary = open_orders_summary.setdefault(
                normalized_symbol,
                {"symbol": normalized_symbol, "count": 0, "reduceOnly": 0, "types": {}}
            )
            summary["count"] += 1
            is_reduce = bool(order.get("reduceOnly") or (order.get("info") or {}).get("reduceOnly"))
            if is_reduce:
                summary["reduceOnly"] += 1
            otype = str(order.get("type") or (order.get("info") or {}).get("type") or "UNKNOWN").upper()
            summary["types"][otype] = summary["types"].get(otype, 0) + 1
            if len(open_orders_sample) < 25:
                open_orders_sample.append({
                    "symbol": normalized_symbol,
                    "type": otype,
                    "side": str(order.get("side") or "").upper(),
                    "price": _safe_float(order.get("price"), 0.0),
                    "stopPrice": _safe_float(order.get("stopPrice"), 0.0),
                    "amount": _safe_float(order.get("amount"), 0.0),
                    "reduceOnly": is_reduce,
                    "timestamp": int(_safe_float(order.get("timestamp"), 0.0))
                })

        running_bots = []
        for symbol_key, bot in list(bot_instances.items()):
            if not bot.running:
                continue
            running_bots.append({
                "symbol": _normalize_to_futures_symbol(symbol_key) or symbol_key,
                "leverage": int(bot.config.get("leverage", 1)),
                "last_mid": _safe_float(getattr(bot, "last_mid", 0.0), 0.0),
            })

        payload = {
            "timestamp": datetime.now().isoformat(),
            "runner": {
                "active_count": len(running_bots),
                "active_bots": running_bots,
            },
            "exchange": {
                "open_positions_count": len(active_positions),
                "positions": active_positions,
                "open_orders_count": len(open_orders_raw or []),
                "open_orders_summary": list(open_orders_summary.values()),
                "open_orders_sample": open_orders_sample,
            },
            "account": {
                "totalWalletBalance": _safe_float(account_info.get("totalWalletBalance"), 0.0),
                "totalUnrealizedProfit": _safe_float(account_info.get("totalUnrealizedProfit"), 0.0),
                "totalMarginBalance": _safe_float(account_info.get("totalMarginBalance"), 0.0),
                "availableBalance": _safe_float(account_info.get("availableBalance"), 0.0),
            }
        }
        return payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await exchange.close()


class UploadedTrade(BaseModel):
    symbol: Optional[str] = ""
    side: Optional[str] = ""
    price: float = 0.0
    amount: float = 0.0
    pnl: Optional[float] = None
    timestamp: Optional[int] = None
    datetime: Optional[str] = ""
    trade_type: Optional[str] = ""


class UploadedTradeAnalysisRequest(BaseModel):
    trades: List[UploadedTrade]


@app.post("/api/analytics/analyze-trades")
async def analyze_uploaded_trades(request: UploadedTradeAnalysisRequest):
    """
    Analyze uploaded trade records (CSV/XML parsed in frontend) and return
    performance metrics with actionable recommendations.
    """
    try:
        records = [trade.model_dump() for trade in request.trades]
        if not records:
            raise HTTPException(status_code=400, detail="No trades provided")

        with_pnl = [item for item in records if item.get("pnl") is not None]
        if not with_pnl:
            return {
                "summary": {
                    "total_trades": len(records),
                    "trades_with_pnl": 0,
                    "win_rate": 0.0,
                    "profit_factor": 0.0,
                    "net_pnl": 0.0,
                    "avg_pnl": 0.0,
                    "best_trade": 0.0,
                    "worst_trade": 0.0
                },
                "recommendations": [
                    "Uploaded file has no realized PnL fields. Export with pnl column for full analysis.",
                    "Keep symbol, side, amount, price, pnl columns in export files."
                ]
            }

        pnl_values = [float(item.get("pnl") or 0.0) for item in with_pnl]
        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p < 0]
        win_count = len(wins)
        loss_count = len(losses)
        total_count = len(with_pnl)
        net_pnl = sum(pnl_values)
        avg_pnl = net_pnl / total_count if total_count else 0.0
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        win_rate = (win_count / total_count * 100.0) if total_count else 0.0

        # Consecutive loss streak
        max_loss_streak = 0
        current_loss_streak = 0
        for pnl in pnl_values:
            if pnl < 0:
                current_loss_streak += 1
                max_loss_streak = max(max_loss_streak, current_loss_streak)
            else:
                current_loss_streak = 0

        recommendations: List[str] = []
        if win_rate < 45:
            recommendations.append("Win rate is below 45%. Increase entry confidence and reduce low-quality setups.")
        if profit_factor < 1.2:
            recommendations.append("Profit factor is weak. Tighten stop placement and let winners run with trailing stops.")
        if avg_pnl <= 0:
            recommendations.append("Average trade is non-positive. Reduce overtrading and focus on high-liquidity symbols.")
        if max_loss_streak >= 4:
            recommendations.append("Detected long loss streak. Add a session loss limit and cooldown after consecutive losses.")
        if not recommendations:
            recommendations.append("Performance is stable. Maintain discipline and continue collecting more samples.")

        return {
            "summary": {
                "total_trades": len(records),
                "trades_with_pnl": total_count,
                "win_rate": round(win_rate, 2),
                "profit_factor": round(profit_factor, 2),
                "net_pnl": round(net_pnl, 4),
                "avg_pnl": round(avg_pnl, 4),
                "best_trade": round(max(pnl_values), 4),
                "worst_trade": round(min(pnl_values), 4),
                "max_loss_streak": max_loss_streak
            },
            "recommendations": recommendations
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Trade analysis failed: {str(e)}")


# ============================================================================
# RISK MANAGEMENT ENDPOINTS
# ============================================================================

@app.get("/api/risk/metrics")
async def get_risk_metrics():
    """Get portfolio risk metrics"""
    try:
        # Get account info
        account_response = await get_account_info()
        balance = _safe_float(account_response.get('balance', {}).get('total', 0), 0.0)
        
        # Get positions
        positions_response = await get_all_positions()
        positions = positions_response.get('positions', [])
        
        # Calculate risk
        calculator = RiskCalculator(balance)
        metrics = calculator.calculate_portfolio_risk(positions)
        base_score = _safe_float(metrics.portfolio_risk_score, 0.0)

        portfolio_engine = _empty_correlation_engine(balance)
        exchange_public = await get_public_exchange()
        try:
            portfolio_engine = await _compute_portfolio_correlation_engine(
                exchange=exchange_public,
                positions=positions,
                balance=balance,
                timeframe="1h",
                lookback=180,
            )
        finally:
            try:
                await exchange_public.close()
            except Exception:
                pass

        corr_score = _safe_float(portfolio_engine.get("correlation_risk_score"), 0.0)
        blended_score = _clamp((base_score * 0.68) + (corr_score * 0.32), 0.0, 100.0)
        if bool(portfolio_engine.get("exposure_breach")):
            blended_score = max(blended_score, 65.0)

        if blended_score < 30:
            risk_level = "LOW"
        elif blended_score < 60:
            risk_level = "MEDIUM"
        else:
            risk_level = "HIGH"

        combined_recommendations: List[str] = []
        for row in list(metrics.recommendations) + list(portfolio_engine.get("recommendations", [])):
            text = str(row or "").strip()
            if text and text not in combined_recommendations:
                combined_recommendations.append(text)
        
        return {
            "risk_score": round(blended_score, 2),
            "risk_level": risk_level,
            "total_exposure": metrics.total_exposure,
            "exposure_percentage": metrics.exposure_percentage,
            "max_loss_potential": metrics.max_loss_potential,
            "diversification_score": metrics.diversification_score,
            "leverage_risk": metrics.leverage_risk,
            "avg_leverage": metrics.avg_leverage,
            "position_count": metrics.position_count,
            "recommendations": combined_recommendations,
            "portfolio_risk_engine": portfolio_engine,
            "correlation_risk_score": corr_score,
            "average_abs_correlation": portfolio_engine.get("average_abs_correlation", 0.0),
            "weighted_abs_correlation": portfolio_engine.get("weighted_abs_correlation", 0.0),
            "concentration_index": portfolio_engine.get("concentration_index", 0.0),
            "recommended_max_exposure_pct": portfolio_engine.get("recommended_max_exposure_pct", 0.0),
            "exposure_breach": bool(portfolio_engine.get("exposure_breach", False)),
            "timestamp": metrics.timestamp
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PositionSizeRequest(BaseModel):
    risk_percentage: float
    entry_price: float
    stop_loss_price: float
    leverage: int = 1


@app.post("/api/risk/calculate-position-size")
async def calculate_position_size(request: PositionSizeRequest):
    """Calculate optimal position size based on risk"""
    try:
        # Get balance
        account_response = await get_account_info()
        balance = account_response.get('balance', {}).get('total', 0)
        
        calculator = RiskCalculator(balance)
        result = calculator.calculate_position_size(
            risk_percentage=request.risk_percentage,
            entry_price=request.entry_price,
            stop_loss_price=request.stop_loss_price,
            leverage=request.leverage
        )
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# LICENSE ENDPOINTS
# ============================================================================

class LicenseActivateRequest(BaseModel):
    key: str
    device_id: Optional[str] = None


class LicenseCreateRequest(BaseModel):
    payment_tx: str
    crypto: str
    amount: float
    device_id: Optional[str] = None
    license_kind: str = Field(default="paid")
    trial_days: int = Field(default=30, ge=1, le=365)


@app.post("/api/license/activate")
async def activate_license(request: LicenseActivateRequest):
    """Activate license with device binding"""
    device_id = get_local_device_id()
    result = validate_license(request.key, device_id)
    
    if result['valid']:
        _set_license_cache(True)
        return {
            "valid": True,
            "message": result['message'],
            "device_id": device_id,
            "license": {
                "key": result['license']['key'],
                "created_at": result['license']['created_at'],
                "activated_at": result['license']['activated_at'],
                "license_kind": result['license'].get('license_kind', 'paid'),
                "expires_at": result['license'].get('expires_at'),
                "trial_days": result['license'].get('trial_days')
            }
        }
    else:
        _set_license_cache(False)
        raise HTTPException(status_code=400, detail=result['message'])


@app.post("/api/license/create")
async def create_new_license(request: LicenseCreateRequest, http_request: Request):
    """Create new license after payment (admin only)"""
    require_admin_token(http_request)
    normalized_kind = str(request.license_kind or "paid").strip().lower()
    if normalized_kind in {"trial", "free", "test"}:
        normalized_kind = "trial"
    else:
        normalized_kind = "paid"

    license_key = create_license(
        payment_tx=request.payment_tx,
        crypto=request.crypto,
        amount=request.amount,
        device_id=request.device_id,
        license_kind=normalized_kind,
        trial_days=request.trial_days,
    )
    
    return {
        "success": True,
        "license_key": license_key,
        "license_kind": normalized_kind,
        "trial_days": request.trial_days if normalized_kind == "trial" else None,
        "message": "Trial license created successfully" if normalized_kind == "trial" else "License created successfully"
    }


@app.get("/api/license/check")
async def check_license(key: str, device_id: str):
    """Check if license is valid"""
    _ = device_id  # client-provided device id is ignored in production
    local_device = get_local_device_id()
    result = validate_license(key, local_device)
    if result['valid']:
        _set_license_cache(True)
    return {
        "valid": result['valid'],
        "message": result['message'],
        "device_id": local_device
    }


@app.get("/api/license/device-id")
async def get_device_id():
    """Return the backend-derived stable device identifier"""
    return {"device_id": get_local_device_id()}


@app.get("/api/license/status")
async def get_license_status():
    """Return whether this device currently has an active bound license"""
    return {
        "licensed": is_license_active_for_device(force_refresh=True),
        "device_id": get_local_device_id()
    }


@app.get("/api/license/all")
async def list_all_licenses(http_request: Request):
    """List all licenses (admin only)"""
    require_admin_token(http_request)
    licenses = get_all_licenses()
    return {"licenses": licenses, "count": len(licenses)}


# ============================================================================
# AI AGENT ENDPOINTS - MUST BE BEFORE if __name__
# ============================================================================

class AIAgentConfig(BaseModel):
    provider: str
    model: str
    api_key: Optional[str] = None


@app.post("/api/ai-agent/configure")
async def configure_ai_agent(config: AIAgentConfig):
    """AI Agent yapılandır"""
    global ai_agent
    try:
        provider = AIProvider(config.provider.lower())
        ai_agent = AITradingAgent(provider=provider, model=config.model)
        return {
            "status": "success",
            "message": f"AI Agent configured: {config.provider} - {config.model}",
            "provider": config.provider,
            "model": config.model
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ai-agent/models")
async def get_available_models():
    """Mevcut AI modellerini listele"""
    try:
        ollama_models = []
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get("http://localhost:11434/api/tags", timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        data = await response.json()
                        ollama_models = [model["name"] for model in data.get("models", [])]
        except Exception as e:
            print(f"Ollama connection error: {e}")
        
        return {
            "ollama": ollama_models if ollama_models else ["modai", "modai:latest", "llama3.2"],
            "groq": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
            "openai": ["gpt-4", "gpt-3.5-turbo"],
            "anthropic": ["claude-3-opus-20240229", "claude-3-sonnet-20240229"],
            "google": ["gemini-pro"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ai-agent/start")
async def start_ai_agent():
    global ai_agent_active
    if not ai_agent:
        raise HTTPException(status_code=400, detail="AI Agent not configured")
    ai_agent_active = True
    return {"status": "success"}


@app.post("/api/ai-agent/stop")
async def stop_ai_agent():
    global ai_agent_active
    ai_agent_active = False
    return {"status": "success"}


@app.get("/api/ai-agent/status")
async def get_ai_agent_status():
    return {
        "active": ai_agent_active,
        "configured": ai_agent is not None,
        "provider": ai_agent.provider.value if ai_agent else None,
        "model": ai_agent.model if ai_agent else None
    }


@app.get("/api/ai-agent/decisions")
async def get_ai_decisions(limit: int = 5):
    if not ai_agent:
        return {"decisions": []}
    safe_limit = max(1, min(int(limit or 5), 50))
    return {"decisions": ai_agent.get_recent_decisions(limit=safe_limit)}


@app.post("/api/ai-agent/monitor-positions")
async def monitor_positions_with_ai(request: Optional[Dict[str, Any]] = None):
    if not ai_agent:
        raise HTTPException(status_code=400, detail="AI Agent not configured")

    payload = request or {}
    raw_symbols = payload.get("symbols")
    symbol = payload.get("symbol")
    timeframe = str(payload.get("timeframe", "5m") or "5m")
    strategy_id = str(payload.get("strategy_id", "momentum") or "momentum")
    limit = max(60, min(int(payload.get("limit", 120) or 120), 300))
    max_symbols = max(1, min(int(payload.get("max_symbols", 6) or 6), 16))

    symbols: List[str] = []
    if isinstance(raw_symbols, list):
        symbols.extend([str(item) for item in raw_symbols if str(item).strip()])
    if symbol and str(symbol).strip():
        symbols.append(str(symbol))

    if not symbols:
        for running_symbol in bot_instances.keys():
            symbols.append(str(running_symbol))

    deduped: List[str] = []
    seen = set()
    for item in symbols:
        normalized = _normalize_to_futures_symbol(item)
        if normalized and normalized not in seen:
            deduped.append(normalized)
            seen.add(normalized)
        if len(deduped) >= max_symbols:
            break

    if not deduped:
        return {"status": "success", "message": "No symbols to monitor", "decisions": []}

    exchange = await get_public_exchange()
    try:
        hunter = LiquidationHunter(exchange)
        decisions: List[Dict[str, Any]] = []
        for monitor_symbol in deduped:
            try:
                analyzed = await _analyze_symbol_with_exchange(
                    exchange,
                    monitor_symbol,
                    timeframe,
                    strategy_id,
                    limit,
                    hunter
                )
                decisions.append({
                    "symbol": analyzed.get("symbol", monitor_symbol),
                    "action": analyzed.get("action", "HOLD"),
                    "direction": analyzed.get("direction", "NEUTRAL"),
                    "confidence": analyzed.get("confidence", 0.0),
                    "reasoning": analyzed.get("reasoning", ""),
                    "risk_level": analyzed.get("risk_level", "MEDIUM"),
                    "suggested_leverage": analyzed.get("suggested_leverage", 1),
                    "strategy_id": analyzed.get("strategy_id", strategy_id),
                    "strategy_used": analyzed.get("strategy_used", strategy_id),
                    "timeframe": analyzed.get("timeframe_used", timeframe),
                    "signal_governor": analyzed.get("signal_governor", {}),
                    "should_alert": bool((analyzed.get("signal_governor") or {}).get("should_alert", False)),
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as monitor_error:
                decisions.append({
                    "symbol": monitor_symbol,
                    "action": "HOLD",
                    "direction": "NEUTRAL",
                    "confidence": 0.0,
                    "reasoning": f"Monitor error: {str(monitor_error)}",
                    "risk_level": "HIGH",
                    "suggested_leverage": 1,
                    "strategy_id": strategy_id,
                    "strategy_used": strategy_id,
                    "timeframe": timeframe,
                    "signal_governor": {},
                    "should_alert": False,
                    "timestamp": datetime.now().isoformat()
                })

        return {
            "status": "success",
            "message": f"Monitored {len(decisions)} symbol(s)",
            "count": len(decisions),
            "decisions": decisions
        }
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


class ValidateKeyRequest(BaseModel):
    provider: str
    api_key: str


@app.post("/api/ai-agent/validate-key")
async def validate_api_key(request: ValidateKeyRequest):
    """API key validation"""
    try:
        models = []
        if request.provider == "groq":
            import aiohttp
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {request.api_key}"}
                async with session.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        models = [m["id"] for m in data.get("data", [])]
                    else:
                        raise HTTPException(status_code=400, detail="Invalid API key")
        elif request.provider == "openai":
            models = ["gpt-4", "gpt-3.5-turbo"]
        elif request.provider == "anthropic":
            models = ["claude-3-opus-20240229", "claude-3-sonnet-20240229"]
        elif request.provider == "google":
            models = ["gemini-pro"]
        else:
            raise HTTPException(status_code=400, detail=f"Provider {request.provider} not supported")
        
        return {"valid": True, "models": models, "provider": request.provider}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/ai-agent/analyze")
async def analyze_with_ai(symbol: str):
    """AI ile piyasa analizi"""
    if not ai_agent:
        raise HTTPException(status_code=400, detail="AI Agent not configured")
    
    try:
        # Symbol formatını düzelt
        symbol = symbol.strip().upper()
        if '/' not in symbol:
            if symbol.endswith('USDT'):
                base = symbol[:-4]
                symbol = f"{base}/USDT"
            else:
                symbol = f"{symbol}/USDT"
        
        exchange = await get_exchange()
        ticker = await _fetch_futures_ticker_fast(exchange, symbol)
        funding = await _fetch_funding_rate_fast(exchange, symbol)
        
        hunter = LiquidationHunter(exchange)
        liq_signal = await hunter.get_liquidation_signal(symbol)
        
        context = MarketContext(
            symbol=symbol,
            current_price=ticker['last'],
            price_change_24h=ticker.get('percentage', 0),
            volume_24h=ticker.get('quoteVolume', 0),
            funding_rate=funding.get('fundingRate', 0) * 100,
            open_interest=0,
            liquidation_signal=liq_signal
        )
        
        decision = await ai_agent.analyze_market(context)
        await exchange.close()
        
        return {
            "status": "success",
            "decision": {
                "action": decision.action,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning,
                "suggested_leverage": decision.suggested_leverage,
                "risk_level": decision.risk_level,
                "timestamp": decision.timestamp.isoformat()
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/binance/tickers")
async def get_all_tickers(symbols: Optional[str] = None):
    """Get ticker data for popular pairs or for an explicit symbol list."""
    exchange = await get_public_exchange()
    try:
        requested_symbols: List[str] = []
        if symbols:
            requested_symbols = [
                _normalize_to_futures_symbol(item)
                for item in symbols.split(",")
                if item.strip()
            ]

        target_symbols = requested_symbols if requested_symbols else POPULAR_SCANNER_SYMBOLS
        deduped_symbols: List[str] = []
        seen = set()
        for symbol in target_symbols:
            normalized = _normalize_to_futures_symbol(symbol)
            if normalized and normalized not in seen:
                deduped_symbols.append(normalized)
                seen.add(normalized)
        deduped_symbols = deduped_symbols[:60]
        symbol_codes = {_futures_symbol_code(item): item for item in deduped_symbols}
        cache_key = ",".join(sorted(symbol_codes.keys())) if symbol_codes else "__popular__"
        now_ts = time.time()
        cached = _ticker_cache.get(cache_key)
        if cached and (now_ts - cached.get("ts", 0.0)) <= TICKER_CACHE_TTL_SECONDS:
            return cached.get("payload", [])

        # First try the batch endpoint with explicit symbol list (no load_markets).
        rows: List[Dict[str, Any]] = []
        raw_batch: Any = None
        if symbol_codes:
            try:
                raw_batch = await exchange.fapiPublicGetTicker24hr({
                    "symbols": json.dumps(sorted(symbol_codes.keys()))
                })
            except Exception:
                raw_batch = None

        if isinstance(raw_batch, dict):
            rows = [raw_batch]
        elif isinstance(raw_batch, list):
            rows = [row for row in raw_batch if isinstance(row, dict)]

        # Fallback: fetch each symbol quickly via raw endpoint.
        if not rows:
            semaphore = asyncio.Semaphore(8)

            async def fetch_one(symbol: str):
                symbol_code = _futures_symbol_code(symbol)
                try:
                    async with semaphore:
                        row = await exchange.fapiPublicGetTicker24hr({"symbol": symbol_code})
                    if isinstance(row, dict):
                        return {
                            "symbol": symbol,
                            "last": _safe_float(row.get("lastPrice"), 0.0),
                            "percentage": _safe_float(row.get("priceChangePercent"), 0.0),
                            "quoteVolume": _safe_float(row.get("quoteVolume"), 0.0),
                        }
                except Exception:
                    pass
                return None

            results = await asyncio.gather(
                *(fetch_one(symbol) for symbol in deduped_symbols),
                return_exceptions=False
            )
            payload = [item for item in results if item is not None]
            _ticker_cache[cache_key] = {"ts": now_ts, "payload": payload}
            return payload

        payload: List[Dict[str, Any]] = []
        for row in rows:
            code = str(row.get("symbol") or "").upper()
            normalized = symbol_codes.get(code)
            if not normalized:
                normalized = _normalize_to_futures_symbol(code)
            if not normalized:
                continue
            if symbol_codes and code not in symbol_codes:
                continue
            payload.append({
                "symbol": normalized,
                "last": _safe_float(row.get("lastPrice"), 0.0),
                "percentage": _safe_float(row.get("priceChangePercent"), 0.0),
                "quoteVolume": _safe_float(row.get("quoteVolume"), 0.0),
            })

        if not payload and deduped_symbols:
            # Last-resort fallback for scanner continuity.
            payload = [
                {
                    "symbol": symbol,
                    "last": 0.0,
                    "percentage": 0.0,
                    "quoteVolume": 0.0,
                }
                for symbol in deduped_symbols
            ]

        _ticker_cache[cache_key] = {"ts": now_ts, "payload": payload}
        return payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


async def _analyze_symbol_with_exchange(
    exchange_instance,
    symbol_value: str,
    timeframe_value: str,
    strategy_value: str,
    limit_value: int,
    hunter_instance: Optional[LiquidationHunter] = None
) -> Dict[str, Any]:
    normalized_symbol = _normalize_to_futures_symbol(symbol_value)
    if not normalized_symbol:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol_value} not found")

    ticker = await _fetch_futures_ticker_fast(exchange_instance, normalized_symbol)
    last_price = _safe_float(ticker.get("last"), 0.0)

    def _confidence_to_risk(confidence_value: float) -> str:
        if confidence_value >= 80:
            return "LOW"
        if confidence_value >= 60:
            return "MEDIUM"
        return "HIGH"

    def _risk_rank_value(risk_level: str) -> int:
        return {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(str(risk_level).upper(), 2)

    ohlcv = await _fetch_futures_ohlcv_fast(exchange_instance, normalized_symbol, timeframe_value, limit_value)
    if not ohlcv:
        raise HTTPException(status_code=404, detail=f"No OHLCV for {normalized_symbol}")
    ohlcv_data = _ohlcv_from_rows(ohlcv)

    liq_snapshot = None
    if hunter_instance is not None:
        try:
            liq_snapshot = await hunter_instance.get_liquidation_snapshot(normalized_symbol)
        except Exception:
            liq_snapshot = None

    micro_vol_threshold = _resolve_micro_vol_threshold((config_store or {}), timeframe_value)
    strategy_used = strategy_value
    try:
        strategy_result = strategy_manager.analyze_with_strategy(
            ohlcv_data, strategy_value, liq_snapshot, timeframe_value, micro_vol_threshold
        )
    except Exception:
        strategy_used = "momentum"
        strategy_result = strategy_manager.analyze_with_strategy(
            ohlcv_data, strategy_used, liq_snapshot, timeframe_value, micro_vol_threshold
        )

    strategy_action = str(strategy_result.get('signal', 'HOLD')).upper()
    strategy_direction = _direction_from_action(strategy_action, strategy_result.get("suggested_side"))
    strategy_confidence = float(strategy_result.get('confidence', 0.0))
    strategy_leverage = int(strategy_result.get('leverage', 1))
    strategy_reasoning = str(strategy_result.get('reasoning') or 'Rule-based strategy analysis')
    strategy_name = str(strategy_result.get('strategy_name') or strategy_used)
    strategy_risk = _confidence_to_risk(strategy_confidence)

    if ai_agent is None:
        governed = _apply_signal_governor(
            symbol=normalized_symbol,
            timeframe=timeframe_value,
            strategy_id=strategy_used,
            action=strategy_action,
            direction=strategy_direction,
            confidence=strategy_confidence,
            reasoning=strategy_reasoning,
        )
        governed_action = str(governed.get("action", strategy_action))
        governed_direction = str(governed.get("direction", strategy_direction))
        governed_confidence = _safe_float(governed.get("confidence"), strategy_confidence)
        governed_reasoning = str(governed.get("reasoning", strategy_reasoning))
        governed_risk = _confidence_to_risk(governed_confidence)
        final_leverage = strategy_leverage if governed_direction in {"LONG", "SHORT"} else 1
        return {
            'symbol': normalized_symbol,
            'action': governed_action,
            'direction': governed_direction,
            'confidence': round(governed_confidence, 2),
            'reasoning': governed_reasoning,
            'risk_level': governed_risk or strategy_risk,
            'suggested_leverage': final_leverage,
            'strategy_used': strategy_name,
            'strategy_id': strategy_used,
            'timeframe_used': timeframe_value,
            'signal_governor': governed.get("meta", {}),
            'price': last_price,
            'change_24h': ticker.get('percentage', 0),
            'volume_24h': ticker.get('quoteVolume', 0)
        }

    funding = await _fetch_funding_rate_fast(exchange_instance, normalized_symbol)
    hunter = hunter_instance or LiquidationHunter(exchange_instance)
    liq_signal = await hunter.get_liquidation_signal(normalized_symbol)

    context = MarketContext(
        symbol=normalized_symbol,
        current_price=last_price,
        price_change_24h=ticker.get('percentage', 0),
        volume_24h=ticker.get('quoteVolume', 0),
        funding_rate=funding.get('fundingRate', 0) * 100,
        open_interest=0,
        liquidation_signal=liq_signal
    )

    decision = await ai_agent.analyze_market(context)
    ai_direction = _direction_from_action(decision.action)
    ai_confidence = float(decision.confidence or 0.0)
    ai_risk = str(decision.risk_level or "MEDIUM").upper()

    blended_confidence = (strategy_confidence * 0.72) + (ai_confidence * 0.28)
    if strategy_direction != "NEUTRAL" and strategy_direction == ai_direction:
        blended_confidence += 6.0
    elif strategy_direction != "NEUTRAL" and ai_direction != "NEUTRAL" and strategy_direction != ai_direction:
        blended_confidence -= 9.0
    elif strategy_direction == "NEUTRAL" and ai_direction != "NEUTRAL":
        blended_confidence += 3.0
    blended_confidence = max(0.0, min(100.0, blended_confidence))

    final_action = strategy_action
    final_direction = strategy_direction
    final_leverage = strategy_leverage
    if final_direction == "NEUTRAL" and ai_direction != "NEUTRAL" and ai_confidence >= 82 and strategy_confidence >= 55:
        final_action = "BUY" if ai_direction == "LONG" else "SELL"
        final_direction = ai_direction
        final_leverage = max(1, int(decision.suggested_leverage or strategy_leverage))

    blended_risk = _confidence_to_risk(blended_confidence)
    if _risk_rank_value(ai_risk) > _risk_rank_value(blended_risk):
        blended_risk = ai_risk

    overlay_reason = (
        f"AI overlay ({decision.action} {ai_confidence:.1f}%): {decision.reasoning}"
        if decision.reasoning
        else f"AI overlay ({decision.action} {ai_confidence:.1f}%)"
    )
    reasoning = f"{strategy_reasoning} | {overlay_reason}"
    governed = _apply_signal_governor(
        symbol=normalized_symbol,
        timeframe=timeframe_value,
        strategy_id=strategy_used,
        action=final_action,
        direction=final_direction,
        confidence=blended_confidence,
        reasoning=reasoning,
    )
    governed_action = str(governed.get("action", final_action))
    governed_direction = str(governed.get("direction", final_direction))
    governed_confidence = _safe_float(governed.get("confidence"), blended_confidence)
    governed_reasoning = str(governed.get("reasoning", reasoning))
    governed_risk = _confidence_to_risk(governed_confidence)
    if _risk_rank_value(ai_risk) > _risk_rank_value(governed_risk):
        governed_risk = ai_risk
    if governed_direction not in {"LONG", "SHORT"}:
        final_leverage = 1

    return {
        'symbol': normalized_symbol,
        'action': governed_action,
        'direction': governed_direction,
        'confidence': round(governed_confidence, 2),
        'reasoning': governed_reasoning,
        'risk_level': governed_risk or blended_risk,
        'suggested_leverage': final_leverage,
        'strategy_used': strategy_name,
        'strategy_id': strategy_used,
        'timeframe_used': timeframe_value,
        'strategy_confidence': round(strategy_confidence, 2),
        'ai_confidence': round(ai_confidence, 2),
        'signal_governor': governed.get("meta", {}),
        'price': last_price,
        'change_24h': ticker.get('percentage', 0),
        'volume_24h': ticker.get('quoteVolume', 0)
    }


@app.post("/api/ai-agent/analyze-symbol")
async def analyze_single_symbol(request: dict):
    """Analyze a single symbol with AI when available, otherwise strategy fallback."""
    raw_symbol = request.get("symbol")
    if not raw_symbol:
        raise HTTPException(status_code=400, detail="Symbol required")

    symbol = _normalize_to_futures_symbol(raw_symbol)
    timeframe = str(request.get("timeframe", "1h") or "1h")
    strategy_id = str(request.get("strategy_id", "momentum") or "momentum")
    try:
        limit = int(request.get("limit", 120))
    except Exception:
        limit = 120
    limit = max(60, min(300, limit))

    exchange = await get_public_exchange()
    try:
        hunter = LiquidationHunter(exchange) if ai_agent is not None else None
        result = await _analyze_symbol_with_exchange(exchange, symbol, timeframe, strategy_id, limit, hunter)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


class AnalyzeBatchRequest(BaseModel):
    symbols: List[str]
    timeframe: str = "1h"
    strategy_id: str = "momentum"
    limit: int = 150
    max_concurrency: int = Field(default=4, ge=1, le=8)


@app.post("/api/ai-agent/analyze-batch")
async def analyze_symbols_batch(request: AnalyzeBatchRequest):
    """Analyze multiple symbols in one backend roundtrip for faster scanner performance."""
    normalized_symbols: List[str] = []
    seen = set()
    for raw_symbol in request.symbols:
        normalized = _normalize_to_futures_symbol(raw_symbol)
        if normalized and normalized not in seen:
            normalized_symbols.append(normalized)
            seen.add(normalized)

    if not normalized_symbols:
        raise HTTPException(status_code=400, detail="No valid symbols provided")

    limit = max(60, min(300, int(request.limit)))
    timeframe = str(request.timeframe or "1h")
    strategy_id = str(request.strategy_id or "momentum")
    semaphore = asyncio.Semaphore(request.max_concurrency)

    exchange = await get_public_exchange()
    try:
        hunter = LiquidationHunter(exchange) if ai_agent is not None else None

        async def run_single(symbol_value: str):
            async with semaphore:
                try:
                    result = await _analyze_symbol_with_exchange(
                        exchange,
                        symbol_value,
                        timeframe,
                        strategy_id,
                        limit,
                        hunter
                    )
                    return ("ok", result)
                except HTTPException as exc:
                    return ("error", {"symbol": symbol_value, "status": exc.status_code, "error": str(exc.detail)})
                except Exception as exc:
                    return ("error", {"symbol": symbol_value, "status": 500, "error": str(exc)})

        raw_results = await asyncio.gather(
            *(run_single(symbol_value) for symbol_value in normalized_symbols),
            return_exceptions=False
        )

        results: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        for status, payload in raw_results:
            if status == "ok":
                results.append(payload)
            else:
                errors.append(payload)

        return {
            "success": True,
            "requested": len(normalized_symbols),
            "count": len(results),
            "results": results,
            "errors": errors
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


@app.post("/api/ai-agent/compare-tokens")
async def compare_tokens(request: dict):
    """Compare token analyses; uses AI when available, deterministic fallback otherwise."""
    tokens = request.get('tokens', [])
    if len(tokens) < 2:
        raise HTTPException(status_code=400, detail="At least 2 tokens required")

    try:
        timeframe = str(request.get("timeframe", "1h") or "1h")
        strategy_id = str(request.get("strategy_id", "momentum") or "momentum")

        def safe_analysis(token: Dict[str, Any]) -> Dict[str, Any]:
            return token.get('analysis', {}) if isinstance(token.get('analysis'), dict) else {}

        def risk_rank(risk: str) -> int:
            return {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}.get(str(risk).upper(), 2)

        def direction_from_action(action: str) -> str:
            value = str(action or "").upper()
            if any(tag in value for tag in ("BUY", "LONG", "OPEN_LONG")):
                return "LONG"
            if any(tag in value for tag in ("SELL", "SHORT", "OPEN_SHORT")):
                return "SHORT"
            return "NEUTRAL"

        def direction_rank(action: str) -> int:
            direction = direction_from_action(action)
            if direction == "LONG":
                return 2
            if direction == "SHORT":
                return 1
            return 0

        directional_tokens = [
            token for token in tokens
            if direction_rank(safe_analysis(token).get('action', '')) > 0
        ]
        candidate_tokens = directional_tokens if directional_tokens else tokens

        best_short_term = max(
            candidate_tokens,
            key=lambda x: (
                float(safe_analysis(x).get('confidence', 0.0)),
                direction_rank(safe_analysis(x).get('action', ''))
            )
        )
        safest = min(
            tokens,
            key=lambda x: (
                risk_rank(safe_analysis(x).get('risk_level', 'MEDIUM')),
                -float(safe_analysis(x).get('confidence', 0.0)),
                -direction_rank(safe_analysis(x).get('action', ''))
            )
        )
        highest_potential = max(
            candidate_tokens,
            key=lambda x: (
                float(safe_analysis(x).get('confidence', 0.0)) * max(1.0, float(safe_analysis(x).get('suggested_leverage', 1))),
                float(safe_analysis(x).get('confidence', 0.0)),
                int(safe_analysis(x).get('suggested_leverage', 1))
            )
        )

        best_action = safe_analysis(best_short_term).get('action', 'HOLD')
        safe_action = safe_analysis(safest).get('action', 'HOLD')
        potential_action = safe_analysis(highest_potential).get('action', 'HOLD')

        best_direction = direction_from_action(str(best_action))
        safest_direction = direction_from_action(str(safe_action))
        potential_direction = direction_from_action(str(potential_action))

        recommendation: str
        if ai_agent is not None:
            comparison_text = (
                "You are a professional crypto trading advisor. Compare these opportunities and return a concise recommendation.\n\n"
            )
            comparison_text += f"Context: timeframe={timeframe}, strategy={strategy_id}\n"
            for token in tokens:
                analysis = safe_analysis(token)
                comparison_text += (
                    f"{token.get('symbol')}: action={analysis.get('action')}, "
                    f"confidence={analysis.get('confidence')}%, risk={analysis.get('risk_level')}, "
                    f"leverage={analysis.get('suggested_leverage')}x, "
                    f"change24h={token.get('change_24h')}, volume={token.get('volume_24h')}.\n"
                )
            comparison_text += "Return: best short-term, safest, highest potential, include LONG/SHORT direction, and one execution note."
            recommendation = await ai_agent._call_ollama(comparison_text)
        else:
            recommendation = (
                f"Best short-term: {best_short_term.get('symbol')} ({best_direction}) | "
                f"Safest: {safest.get('symbol')} ({safest_direction}) | "
                f"Highest potential: {highest_potential.get('symbol')} ({potential_direction}). "
                f"Strategy={strategy_id}, timeframe={timeframe}. Fallback comparison mode is active (AI Agent disabled)."
            )

        return {
            'recommendation': recommendation,
            'context': {
                'strategy_id': strategy_id,
                'timeframe': timeframe
            },
            'best_short_term': {
                'symbol': best_short_term.get('symbol'),
                'action': best_action,
                'direction': best_direction,
                'leverage': safe_analysis(best_short_term).get('suggested_leverage', 1),
                'confidence': safe_analysis(best_short_term).get('confidence', 0),
            },
            'safest': {
                'symbol': safest.get('symbol'),
                'action': safe_action,
                'direction': safest_direction,
                'leverage': safe_analysis(safest).get('suggested_leverage', 1),
                'confidence': safe_analysis(safest).get('confidence', 0),
                'risk': safe_analysis(safest).get('risk_level', 'MEDIUM'),
            },
            'highest_potential': {
                'symbol': highest_potential.get('symbol'),
                'action': potential_action,
                'direction': potential_direction,
                'confidence': safe_analysis(highest_potential).get('confidence', 0),
                'leverage': safe_analysis(highest_potential).get('suggested_leverage', 1),
            },
        }
    except Exception as e:
        print(f"❌ Comparison error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ai-agent/scan-opportunities")
async def scan_market_opportunities():
    """AI ile piyasa fırsatlarını tara"""
    print("🔍 Market scan requested")
    
    if not ai_agent:
        print("❌ AI Agent not configured")
        raise HTTPException(status_code=400, detail="AI Agent not configured")
    
    print(f"✅ AI Agent available: {ai_agent.model}")
    
    try:
        exchange = await get_exchange()
        print("📊 Exchange ready for market scan")
        
        # Top volume USDT perpetual pairs (reduced for faster scanning)
        top_symbols = [
            'BTC/USDT', 'ETH/USDT', 'SOL/USDT'
        ]
        
        print(f"🎯 Scanning {len(top_symbols)} symbols...")
        opportunities = []
        hunter = LiquidationHunter(exchange)
        
        for idx, symbol in enumerate(top_symbols, 1):
            try:
                print(f"📊 Scanning {idx}/{len(top_symbols)}: {symbol}")
                ticker = await _fetch_futures_ticker_fast(exchange, symbol)
                funding = await _fetch_funding_rate_fast(exchange, symbol)
                liq_signal = await hunter.get_liquidation_signal(symbol)
                
                context = MarketContext(
                    symbol=symbol,
                    current_price=ticker['last'],
                    price_change_24h=ticker.get('percentage', 0),
                    volume_24h=ticker.get('quoteVolume', 0),
                    funding_rate=funding.get('fundingRate', 0) * 100,
                    open_interest=0,
                    liquidation_signal=liq_signal
                )
                
                print(f"🤖 Analyzing {symbol} with AI...")
                decision = await ai_agent.analyze_market(context)
                print(f"✅ {symbol}: {decision.action} (confidence: {decision.confidence}%)")
                
                # Only include high confidence signals
                if decision.confidence >= 60 and decision.action != 'HOLD':
                    opportunities.append({
                        'symbol': symbol,
                        'action': decision.action,
                        'confidence': decision.confidence,
                        'reasoning': decision.reasoning,
                        'price': ticker['last'],
                        'change_24h': ticker.get('percentage', 0),
                        'volume_24h': ticker.get('quoteVolume', 0),
                        'funding_rate': funding.get('fundingRate', 0) * 100,
                        'risk_level': decision.risk_level,
                        'suggested_leverage': decision.suggested_leverage
                    })
                
                # Small delay to avoid rate limits
                await asyncio.sleep(0.1)
                
            except Exception as e:
                print(f"❌ Error scanning {symbol}: {e}")
                continue
        
        await exchange.close()
        
        # Sort by confidence
        opportunities.sort(key=lambda x: x['confidence'], reverse=True)
        
        print(f"✅ Scan complete: {len(opportunities)} opportunities found")
        
        return {
            "status": "success",
            "opportunities": opportunities,
            "scanned_symbols": len(top_symbols),
            "found_opportunities": len(opportunities),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        print(f"❌ Scan error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/symbols/search")
async def search_symbols(query: str, limit: int = 10):
    """USDT perpetual symbol search for autocomplete."""
    query_upper = str(query or "").strip().upper()
    if not query_upper:
        return {"query": query, "matches": []}

    try:
        catalog = await _load_symbol_catalog()
    except Exception:
        catalog = _fallback_symbol_catalog()

    try:
        matches = []
        for item in catalog:
            base = item.get("base", "")
            if query_upper in base:
                matches.append(item)

        matches.sort(
            key=lambda x: (
                0 if x["base"].startswith(query_upper) else 1,
                0 if x["base"] == query_upper else 1,
                len(x["base"]),
                x["base"]
            )
        )
        safe_limit = max(1, min(30, int(limit)))
        return {"query": query, "matches": matches[:safe_limit]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# INDICATOR & STRATEGY ENDPOINTS
# ============================================================================

# Import indicator and strategy modules
from technical_indicators import TechnicalIndicators
from strategy_manager import StrategyManager

# Initialize indicator and strategy managers
indicator_manager = TechnicalIndicators()
strategy_manager = StrategyManager()


class IndicatorAnalysisRequest(BaseModel):
    """Request model for indicator analysis"""
    symbol: str
    timeframe: str = "1h"
    limit: int = 100


class StrategyAnalysisRequest(BaseModel):
    """Request model for strategy analysis"""
    symbol: str
    strategy_id: str
    timeframe: str = "1h"
    limit: int = 100


class StrategyRecommendationsRequest(BaseModel):
    """Request model for strategy recommendation ranking"""
    symbol: str
    timeframe: str = "1h"
    limit: int = 180
    top_k: int = 4


class StrategyConfluenceRequest(BaseModel):
    """Request model for multi-timeframe strategy confluence."""
    symbol: str
    strategy_id: str = "momentum"
    timeframes: List[str] = Field(default_factory=lambda: ["5m", "15m", "1h"])
    limit: int = 180


@app.post("/api/indicators/analyze")
async def analyze_indicators(request: IndicatorAnalysisRequest):
    """
    Analyze all 46+ indicators for a symbol
    
    Returns comprehensive analysis including:
    - Core indicators (RSI, MACD, BB, MA, OBV, ATR)
    - Oscillators (Williams %R, CCI, ROC, etc.)
    - Trend indicators (SuperTrend, Aroon, Vortex, etc.)
    - Volume indicators (A/D Line, CMF, MFI, etc.)
    - Advanced indicators (Hurst, Fractal Dimension, KAMA, etc.)
    - Institutional indicators (Order Flow, Liquidity Sweeps, FVG, etc.)
    """
    exchange = None
    try:
        # Get OHLCV data from Binance
        exchange = await get_public_exchange()
        
        # Fetch candles (raw futures klines first, ccxt fallback).
        ohlcv = await _fetch_futures_ohlcv_fast(exchange, request.symbol, request.timeframe, request.limit)
        if not ohlcv:
            raise HTTPException(status_code=400, detail="No OHLCV data received")

        # Convert to required format
        ohlcv_data = _ohlcv_from_rows(ohlcv)
        
        # Analyze all indicators
        analysis = indicator_manager.analyze_all_indicators_comprehensive(ohlcv_data)
        
        return {
            'success': True,
            'symbol': request.symbol,
            'timeframe': request.timeframe,
            'timestamp': datetime.now().isoformat(),
            'analysis': analysis
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
    finally:
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass


@app.post("/api/indicators/summary")
async def get_indicator_summary(request: IndicatorAnalysisRequest):
    """
    Get simplified indicator summary for quick decision making
    
    Returns:
    - Overall signal (BUY/SELL/NEUTRAL)
    - Confidence level (0-100)
    - Key indicators
    - Warnings
    - Opportunities
    """
    exchange = None
    try:
        # Get OHLCV data
        exchange = await get_public_exchange()
        
        ohlcv = await _fetch_futures_ohlcv_fast(exchange, request.symbol, request.timeframe, request.limit)
        if not ohlcv:
            raise HTTPException(status_code=400, detail="No OHLCV data received")

        # Convert to required format
        ohlcv_data = _ohlcv_from_rows(ohlcv)
        
        # Get summary
        summary = indicator_manager.get_indicator_summary(ohlcv_data)
        
        return {
            'success': True,
            'symbol': request.symbol,
            'timeframe': request.timeframe,
            'timestamp': datetime.now().isoformat(),
            'summary': summary
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summary failed: {str(e)}")
    finally:
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass


def _safe_get(mapping: Dict[str, Any], *keys: str, default: Any = None):
    """Safely get nested keys from dicts."""
    current = mapping
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _normalize_indicator_weights(indicators: List[str], weights: Dict[str, Any]) -> Dict[str, float]:
    normalized: Dict[str, float] = {}
    for indicator in indicators:
        raw_weight = weights.get(indicator, 50)
        try:
            normalized[indicator] = float(raw_weight)
        except (TypeError, ValueError):
            normalized[indicator] = 50.0
    return normalized


def _signal_direction(signal: str) -> int:
    if not signal:
        return 0
    s = signal.upper()
    if "OVERBOUGHT" in s:
        return -1
    if "OVERSOLD" in s:
        return 1
    if "RESISTANCE" in s:
        return -1
    if "SUPPORT" in s:
        return 1
    if "BEAR" in s and "BULL" not in s:
        return -1
    if "BULL" in s and "BEAR" not in s:
        return 1
    if "SELL" in s and "BUY" not in s:
        return -1
    if "BUY" in s and "SELL" not in s:
        return 1
    if "BELOW" in s:
        return -1
    if "ABOVE" in s:
        return 1
    return 0


def _extract_indicator_snapshot(analysis: Dict[str, Any], indicators: List[str]) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    current_price = _safe_get(analysis, "overall_analysis", "current_price", default=0)
    snapshot["current_price"] = current_price

    core = analysis.get("core_indicators", {})
    osc = analysis.get("oscillators", {})

    if "RSI" in indicators:
        rsi_value = _safe_get(core, "rsi", "value", default=None)
        rsi_signal = "NEUTRAL"
        if rsi_value is not None:
            if rsi_value > 70:
                rsi_signal = "OVERBOUGHT"
            elif rsi_value < 30:
                rsi_signal = "OVERSOLD"
        snapshot["RSI"] = {"value": rsi_value, "signal": rsi_signal}

    if "MACD" in indicators:
        snapshot["MACD"] = {
            "signal": _safe_get(core, "macd", "signal", default="NEUTRAL"),
            "histogram": _safe_get(core, "macd", "histogram", default=None)
        }

    if "OBV" in indicators:
        snapshot["OBV"] = {
            "signal": _safe_get(core, "obv", "signal", default="NEUTRAL"),
            "trend": _safe_get(core, "obv", "trend", default="NEUTRAL")
        }

    if "ATR" in indicators:
        snapshot["ATR"] = {
            "value": _safe_get(core, "atr", "value", default=None),
            "volatility": _safe_get(core, "atr", "volatility", default="MEDIUM")
        }

    if "WILLIAMS_R" in indicators:
        wr = osc.get("williams_r")
        value = None
        signal = "NEUTRAL"
        if isinstance(wr, (list, tuple)) and len(wr) >= 2:
            value, signal = wr[0], wr[1]
        snapshot["WILLIAMS_R"] = {"value": value, "signal": signal}

    if "BOLLINGER" in indicators:
        position = _safe_get(core, "bollinger_bands", "position", default="NEUTRAL")
        bb_signal = "NEUTRAL"
        if isinstance(position, str):
            if "LOWER" in position.upper():
                bb_signal = "OVERSOLD"
            elif "UPPER" in position.upper():
                bb_signal = "OVERBOUGHT"
        snapshot["BOLLINGER"] = {
            "position": position,
            "signal": bb_signal,
            "upper": _safe_get(core, "bollinger_bands", "upper", default=None),
            "lower": _safe_get(core, "bollinger_bands", "lower", default=None)
        }

    if "PIVOT" in indicators:
        pivots = _safe_get(core, "pivot_points", default={})
        pivot_signal = "NEUTRAL"
        nearest = None
        distance_pct = None
        if pivots and current_price:
            levels = {k: v for k, v in pivots.items() if k.startswith("R") or k.startswith("S")}
            if levels:
                nearest = min(levels, key=lambda k: abs(levels[k] - current_price))
                distance = abs(levels[nearest] - current_price)
                distance_pct = (distance / current_price) * 100 if current_price else None
                if distance_pct is not None and distance_pct <= 0.35:
                    if nearest.startswith("S"):
                        pivot_signal = "SUPPORT"
                    elif nearest.startswith("R"):
                        pivot_signal = "RESISTANCE"
        snapshot["PIVOT"] = {
            "nearest_level": nearest,
            "distance_pct": distance_pct,
            "signal": pivot_signal
        }

    ma_values = _safe_get(core, "moving_averages", "values", default={})
    for ma_key in ["MA9", "MA20", "MA100", "MA200"]:
        if ma_key in indicators:
            ma_value = ma_values.get(ma_key)
            ma_signal = "NEUTRAL"
            if ma_value is not None and current_price:
                ma_signal = "ABOVE" if current_price > ma_value else "BELOW"
            snapshot[ma_key] = {"value": ma_value, "signal": ma_signal}

    if "MOMENTUM" in indicators:
        roc = osc.get("roc")
        value = None
        signal = "NEUTRAL"
        if isinstance(roc, (list, tuple)) and len(roc) >= 2:
            value, signal = roc[0], roc[1]
        snapshot["MOMENTUM"] = {"value": value, "signal": signal}

    return snapshot


def _score_indicator_snapshot(snapshot: Dict[str, Any], indicators: List[str], weights: Dict[str, float],
                              mode: str, min_confidence: float, contrarian_mode: bool) -> Dict[str, Any]:
    score = 0.0
    max_score = 0.0
    bullish = 0
    bearish = 0
    reasons: List[str] = []

    for indicator in indicators:
        data = snapshot.get(indicator, {})
        signal = None
        value = None
        if isinstance(data, dict):
            signal = data.get("signal")
            value = data.get("value")
        elif isinstance(data, (list, tuple)) and len(data) >= 2:
            value, signal = data[0], data[1]

        direction = _signal_direction(str(signal) if signal is not None else "")
        weight = weights.get(indicator, 50.0)

        if direction != 0:
            score += direction * weight
            max_score += weight
            if direction > 0:
                bullish += 1
            else:
                bearish += 1
            if value is not None:
                reasons.append(f"{indicator}: {value} → {signal}")
            else:
                reasons.append(f"{indicator}: {signal}")
        else:
            if indicator == "ATR":
                volatility = data.get("volatility", "MEDIUM") if isinstance(data, dict) else "MEDIUM"
                reasons.append(f"ATR: Volatility {volatility}")
            elif signal:
                reasons.append(f"{indicator}: {signal}")

    total_dir = bullish + bearish
    confidence = abs(score) / max_score * 100 if max_score > 0 else 0

    signal = "NEUTRAL"
    if total_dir > 0:
        bullish_ratio = bullish / total_dir
        bearish_ratio = bearish / total_dir
        if mode == "confluence":
            if score > 0 and bullish_ratio >= 0.6:
                signal = "BUY"
            elif score < 0 and bearish_ratio >= 0.6:
                signal = "SELL"
        else:
            if score > 0:
                signal = "BUY"
            elif score < 0:
                signal = "SELL"

    if confidence < min_confidence:
        signal = "NEUTRAL"

    if contrarian_mode:
        if signal == "BUY":
            signal = "SELL"
        elif signal == "SELL":
            signal = "BUY"

    return {
        "signal": signal,
        "confidence": round(confidence, 2),
        "score": round(score, 2),
        "max_score": round(max_score, 2),
        "reasons": reasons,
        "contrarian_mode": contrarian_mode
    }


class IndicatorBlendRequest(BaseModel):
    symbol: str
    timeframe: str = "1h"
    limit: int = 120
    strategy_id: str = ""
    strategy_name: str = "Custom Blend"
    indicators: List[str] = []
    weights: Dict[str, float] = {}
    mode: str = "confluence"
    min_confidence: float = 60
    contrarian_mode: bool = False
    ai_assist: bool = False


@app.post("/api/indicator-blend/preview")
async def preview_indicator_blend(request: IndicatorBlendRequest):
    """
    Preview decision logic for a selected indicator blend.
    Returns rule-based decision and (optional) AI-assisted decision.
    """
    exchange = None
    try:
        exchange = await get_public_exchange()

        ohlcv = await _fetch_futures_ohlcv_fast(exchange, request.symbol, request.timeframe, request.limit)

        if not ohlcv:
            raise HTTPException(status_code=400, detail="No OHLCV data received")

        ohlcv_data = _ohlcv_from_rows(ohlcv)

        analysis = indicator_manager.analyze_all_indicators_comprehensive(ohlcv_data)
        indicators = request.indicators or []
        weights = _normalize_indicator_weights(indicators, request.weights)
        snapshot = _extract_indicator_snapshot(analysis, indicators)
        decision = _score_indicator_snapshot(
            snapshot,
            indicators,
            weights,
            request.mode,
            request.min_confidence,
            request.contrarian_mode
        )

        ai_decision = None
        if request.ai_assist and ai_agent is not None:
            closes = ohlcv_data['close']
            volumes = ohlcv_data['volume']
            current_price = closes[-1]
            if len(closes) >= 25:
                prev_price = closes[-25]
            else:
                prev_price = closes[0]
            price_change_24h = ((current_price - prev_price) / prev_price) * 100 if prev_price else 0
            volume_24h = sum(volumes[-24:]) if len(volumes) >= 24 else sum(volumes)

            context = MarketContext(
                symbol=request.symbol,
                current_price=current_price,
                price_change_24h=price_change_24h,
                volume_24h=volume_24h,
                funding_rate=0.0,
                open_interest=0.0,
                liquidation_signal=None,
                technical_indicators=snapshot
            )
            try:
                ai_result = await ai_agent.analyze_market(context)
                ai_decision = {
                    "action": ai_result.action,
                    "confidence": ai_result.confidence,
                    "reasoning": ai_result.reasoning,
                    "risk_level": ai_result.risk_level
                }
            except Exception as e:
                ai_decision = {"error": str(e)}

        return {
            "success": True,
            "symbol": request.symbol,
            "timeframe": request.timeframe,
            "strategy_id": request.strategy_id,
            "strategy_name": request.strategy_name,
            "timestamp": datetime.now().isoformat(),
            "decision": decision,
            "indicator_snapshot": snapshot,
            "ai_decision": ai_decision
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Indicator blend preview failed: {str(e)}")
    finally:
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass


@app.post("/api/strategy/analyze")
async def analyze_strategy(request: StrategyAnalysisRequest):
    """
    Analyze market with specific strategy
    
    Available strategies:
    - trend_following: MA, MACD, OBV
    - mean_reversion: RSI, Bollinger Bands
    - breakout: BB, ATR, OBV
    - contrarian: RSI, BB, MACD, MA (reverses signals)
    - momentum: MACD, RSI, OBV
    - scalping: RSI, MACD, ATR
    
    Returns:
    - Signal (BUY/SELL/HOLD)
    - Confidence (0-100)
    - Entry price
    - Stop loss
    - Take profit
    - Leverage
    - Reasoning
    """
    exchange = None
    try:
        # Get OHLCV data
        exchange = await get_public_exchange()

        ohlcv = await _fetch_futures_ohlcv_fast(exchange, request.symbol, request.timeframe, request.limit)
        if not ohlcv:
            raise HTTPException(status_code=400, detail="No OHLCV data received")
        
        # Convert to required format
        ohlcv_data = _ohlcv_from_rows(ohlcv)

        liq_snapshot = await _get_liquidation_snapshot(request.symbol)
        book_ticker = await _fetch_futures_book_ticker_fast(exchange, request.symbol)
        spread_bps = _safe_float(book_ticker.get("spread_bps"), 0.0)
        base_config = config_store if config_store else BotConfig().model_dump()
        min_spread_bps = _safe_float(base_config.get("min_spread_bps"), 1.2)
        micro_vol_threshold = _resolve_micro_vol_threshold(base_config, request.timeframe)
        spread_ok = True
        if spread_bps > 0 and min_spread_bps > 0:
            spread_ok = spread_bps >= min_spread_bps

        # Analyze with strategy
        result = strategy_manager.analyze_with_strategy(
            ohlcv_data,
            request.strategy_id,
            liq_snapshot,
            request.timeframe,
            micro_vol_threshold
        )
        result["spread_bps"] = round(spread_bps, 2)
        result["min_spread_bps"] = round(min_spread_bps, 2)
        result["spread_ok"] = spread_ok
        
        return {
            'success': True,
            'symbol': request.symbol,
            'timeframe': request.timeframe,
            'timestamp': datetime.now().isoformat(),
            'strategy': result
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Strategy analysis failed: {str(e)}")
    finally:
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass


@app.get("/api/strategy/list")
async def list_strategies():
    """
    Get list of all available strategies
    
    Returns:
    - Strategy ID
    - Name
    - Type
    - Contrarian mode
    - Indicators used
    - Max leverage
    - Min confidence
    """
    try:
        strategies = strategy_manager.get_all_strategies()
        
        return {
            'success': True,
            'count': len(strategies),
            'strategies': strategies
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list strategies: {str(e)}")


@app.post("/api/strategy/recommendations")
async def recommend_strategies(request: StrategyRecommendationsRequest):
    """
    Rank available strategies for a symbol/timeframe using current market data.
    """
    exchange = None
    try:
        exchange = await get_public_exchange()

        ohlcv = await _fetch_futures_ohlcv_fast(
            exchange,
            request.symbol,
            request.timeframe,
            max(80, min(int(request.limit), 500))
        )

        if not ohlcv:
            raise HTTPException(status_code=400, detail="No OHLCV data received")

        ohlcv_data = _ohlcv_from_rows(ohlcv)

        ranked = []
        liq_snapshot = await _get_liquidation_snapshot(request.symbol)
        for strategy_meta in strategy_manager.get_all_strategies():
            strategy_id = strategy_meta.get('id')
            if not strategy_id:
                continue

            micro_vol_threshold = _resolve_micro_vol_threshold((config_store or {}), request.timeframe)
            result = strategy_manager.analyze_with_strategy(
                ohlcv_data, strategy_id, liq_snapshot, request.timeframe, micro_vol_threshold
            )
            signal = str(result.get('signal', 'HOLD')).upper()
            confidence = float(result.get('confidence', 0.0))

            ranking_score = confidence
            if signal in ('BUY', 'SELL'):
                ranking_score += 18.0
            elif signal == 'HOLD':
                ranking_score -= 12.0

            if signal == 'BUY':
                action = 'OPEN_LONG'
                preferred_side = 'LONG'
            elif signal == 'SELL':
                action = 'OPEN_SHORT'
                preferred_side = 'SHORT'
            else:
                preferred_side = None
                action = 'HOLD'

            ranked.append({
                'id': strategy_id,
                'name': strategy_meta.get('name', strategy_id),
                'type': strategy_meta.get('type', ''),
                'signal': signal,
                'confidence': round(confidence, 2),
                'ranking_score': round(ranking_score, 2),
                'suggested_leverage': int(result.get('leverage', strategy_meta.get('max_leverage', 1))),
                'action': action,
                'preferred_side': preferred_side,
                'reasoning': result.get('reasoning', ''),
                'min_confidence': strategy_meta.get('min_confidence', 60),
                'max_leverage': strategy_meta.get('max_leverage', 20),
                'indicators': strategy_meta.get('indicators', [])
            })

        ranked.sort(key=lambda item: item.get('ranking_score', 0), reverse=True)
        safe_k = max(1, min(int(request.top_k), max(1, len(ranked))))

        return {
            'success': True,
            'symbol': request.symbol,
            'timeframe': request.timeframe,
            'count': safe_k,
            'recommendations': ranked[:safe_k],
            'generated_at': datetime.now().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Strategy recommendation failed: {str(e)}")
    finally:
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass


@app.post("/api/strategy/confluence")
async def strategy_confluence(request: StrategyConfluenceRequest):
    """Score multi-timeframe confluence for a strategy (e.g. 5m+15m+1h)."""
    exchange = None
    try:
        symbol = _normalize_to_futures_symbol(request.symbol) or request.symbol
        if not symbol:
            raise HTTPException(status_code=400, detail="Invalid symbol")
        available_ids = {str(item.get("id")) for item in strategy_manager.get_all_strategies()}
        if request.strategy_id not in available_ids:
            raise HTTPException(status_code=400, detail=f"Unknown strategy_id: {request.strategy_id}")
        requested = request.timeframes or ["5m", "15m", "1h"]
        deduped_timeframes: List[str] = []
        seen = set()
        for raw_tf in requested:
            tf = _normalize_timeframe(raw_tf)
            if tf not in seen:
                deduped_timeframes.append(tf)
                seen.add(tf)
        if not deduped_timeframes:
            deduped_timeframes = ["5m", "15m", "1h"]

        limit = max(80, min(int(request.limit), 400))
        exchange = await get_public_exchange()
        liq_snapshot = await _get_liquidation_snapshot(symbol)

        per_timeframe: List[Dict[str, Any]] = []
        total_weight = 0.0
        weighted_confidence_sum = 0.0
        long_weight = 0.0
        short_weight = 0.0
        errors: List[str] = []

        for tf in deduped_timeframes:
            ohlcv = await _fetch_futures_ohlcv_fast(exchange, symbol, tf, limit)
            if not ohlcv:
                errors.append(f"{tf}: no data")
                continue
            ohlcv_data = _ohlcv_from_rows(ohlcv)
            micro_vol_threshold = _resolve_micro_vol_threshold((config_store or {}), tf)
            result = strategy_manager.analyze_with_strategy(
                ohlcv_data,
                request.strategy_id,
                liq_snapshot,
                tf,
                micro_vol_threshold,
            )
            signal = str(result.get("signal", "HOLD")).upper()
            confidence = _clamp(_safe_float(result.get("confidence"), 0.0), 0.0, 100.0)
            direction = _direction_from_action(signal, result.get("suggested_side"))
            weight = _timeframe_weight(tf)
            total_weight += weight
            weighted_confidence_sum += confidence * weight
            directional_weight = weight * (confidence / 100.0)
            if direction == "LONG":
                long_weight += directional_weight
            elif direction == "SHORT":
                short_weight += directional_weight

            per_timeframe.append({
                "timeframe": tf,
                "signal": signal,
                "direction": direction,
                "confidence": round(confidence, 2),
                "weight": round(weight, 2),
                "weighted_directional_strength": round(directional_weight, 4),
                "risk_reward": round(_safe_float(result.get("risk_reward"), 0.0), 2),
                "reasoning": str(result.get("reasoning", "")),
            })

        if not per_timeframe:
            raise HTTPException(status_code=400, detail="Unable to calculate confluence: no timeframe data")

        directional_sum = long_weight + short_weight
        if directional_sum > 1e-12:
            alignment_ratio = max(long_weight, short_weight) / directional_sum
            net_directional_strength = (long_weight - short_weight) / directional_sum
        else:
            alignment_ratio = 0.0
            net_directional_strength = 0.0

        avg_confidence = weighted_confidence_sum / total_weight if total_weight > 0 else 0.0
        confluence_strength = abs(net_directional_strength)
        confluence_score = _clamp(
            (confluence_strength * 72.0)
            + (alignment_ratio * 18.0)
            + (avg_confidence * 0.10),
            0.0,
            100.0,
        )
        if confluence_score >= 45.0 and net_directional_strength > 0.12:
            dominant_side = "LONG"
            action = "OPEN_LONG"
        elif confluence_score >= 45.0 and net_directional_strength < -0.12:
            dominant_side = "SHORT"
            action = "OPEN_SHORT"
        else:
            dominant_side = "NEUTRAL"
            action = "HOLD"

        return {
            "success": True,
            "symbol": symbol,
            "strategy_id": request.strategy_id,
            "timeframes": deduped_timeframes,
            "timeframe_count": len(per_timeframe),
            "confluence_score": round(confluence_score, 2),
            "alignment_ratio": round(alignment_ratio * 100.0, 2),
            "average_confidence": round(avg_confidence, 2),
            "net_directional_strength": round(net_directional_strength, 4),
            "dominant_side": dominant_side,
            "action": action,
            "details": per_timeframe,
            "errors": errors,
            "timestamp": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Confluence analysis failed: {str(e)}")
    finally:
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass


@app.post("/api/strategy/backtest")
async def backtest_strategy(request: StrategyAnalysisRequest):
    """
    Advanced backtest with:
    - Walk-forward out-of-sample folds
    - Regime-separated scores (trend/range/volatile)
    """
    exchange = None
    try:
        available_ids = {str(item.get("id")) for item in strategy_manager.get_all_strategies()}
        if request.strategy_id not in available_ids:
            raise HTTPException(status_code=400, detail=f"Unknown strategy_id: {request.strategy_id}")
        exchange = await get_public_exchange()
        ohlcv = await _fetch_futures_ohlcv_fast(
            exchange,
            request.symbol,
            request.timeframe,
            500,
        )
        if not ohlcv:
            raise HTTPException(status_code=400, detail="No OHLCV data received")

        ohlcv_data = _ohlcv_from_rows(ohlcv)
        timestamps = [int(_safe_float(row[0], 0.0)) for row in ohlcv]
        initial_balance = 10000.0
        regime_labels = _derive_market_regimes(ohlcv_data, request.timeframe)

        full_run = _run_backtest_window(
            ohlcv_data=ohlcv_data,
            timestamps=timestamps,
            strategy_id=request.strategy_id,
            timeframe=request.timeframe,
            start_index=max(80, min(120, len(ohlcv_data.get("close", [])) // 4)),
            end_index=len(ohlcv_data.get("close", [])),
            initial_balance=initial_balance,
            regime_labels=regime_labels,
        )
        trades = list(full_run.get("trades", []))
        final_balance = _safe_float(full_run.get("final_balance"), initial_balance)
        equity_curve = list(full_run.get("equity_curve", [])) or [initial_balance, final_balance]
        summary = _summarize_backtest_trades(trades, initial_balance, final_balance, equity_curve)
        regime_scores = _regime_scores_from_trades(trades)
        walk_forward = _walk_forward_backtest(
            ohlcv_data=ohlcv_data,
            timestamps=timestamps,
            strategy_id=request.strategy_id,
            timeframe=request.timeframe,
            initial_balance=initial_balance,
            folds=4,
        )

        best_regime = max(
            regime_scores.values(),
            key=lambda row: _safe_float(row.get("score"), 0.0),
        ) if regime_scores else {}
        worst_regime = min(
            regime_scores.values(),
            key=lambda row: _safe_float(row.get("score"), 0.0),
        ) if regime_scores else {}

        results_payload = {
            "total_trades": summary["total_trades"],
            "winning_trades": summary["winning_trades"],
            "losing_trades": summary["losing_trades"],
            "win_rate": summary["win_rate"],
            "total_pnl": summary["total_pnl"],
            "final_balance": summary["final_balance"],
            "roi": summary["roi"],
            "avg_win": summary["avg_win"],
            "avg_loss": summary["avg_loss"],
            "profit_factor": summary["profit_factor"],
            "sharpe_ratio": summary["sharpe_ratio"],
            "max_drawdown": summary["max_drawdown"],
            "score": summary["score"],
            "best_regime": best_regime.get("regime", ""),
            "worst_regime": worst_regime.get("regime", ""),
        }

        return {
            "success": True,
            "symbol": request.symbol,
            "strategy_id": request.strategy_id,
            "timeframe": request.timeframe,
            "results": results_payload,
            "regime_scores": regime_scores,
            "walk_forward": walk_forward,
            "trades": trades[-20:],
            "backtest_version": "walk-forward-v2",
            "timestamp": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest failed: {str(e)}")
    finally:
        if exchange is not None:
            try:
                await exchange.close()
            except Exception:
                pass


@app.get("/api/execution/quality")
async def execution_quality(limit: int = 200, symbol: Optional[str] = None, reference_timeframe: str = "1m"):
    """
    Estimate execution quality from realized trades:
    - adverse slippage (vs reference candle close)
    - spread impact estimate (from current book spread)
    - fee impact and effective total cost
    """
    safe_limit = max(20, min(int(limit), 500))
    normalized_symbol = _normalize_to_futures_symbol(symbol or "")
    reference_tf = _normalize_timeframe(reference_timeframe)

    trade_payload = await get_binance_trade_history(limit=safe_limit, symbol=normalized_symbol or None)
    trades = trade_payload.get("trades", []) if isinstance(trade_payload, dict) else []
    if not trades:
        return {
            "success": True,
            "count": 0,
            "quality_score": 0.0,
            "grade": "N/A",
            "message": "No trade history available for execution quality analysis.",
            "metrics": {},
            "symbols": [],
        }

    unique_symbols = sorted({
        _normalize_to_futures_symbol(row.get("symbol")) or str(row.get("symbol") or "")
        for row in trades
        if row.get("symbol")
    })

    exchange = await get_public_exchange()
    try:
        candle_cache: Dict[str, List[List[float]]] = {}
        spread_cache: Dict[str, float] = {}
        for trade_symbol in unique_symbols:
            if not trade_symbol:
                continue
            candle_cache[trade_symbol] = await _fetch_futures_ohlcv_fast(exchange, trade_symbol, reference_tf, 320)
            book_ticker = await _fetch_futures_book_ticker_fast(exchange, trade_symbol)
            spread_cache[trade_symbol] = max(0.0, _safe_float(book_ticker.get("spread_bps"), 0.0))
    finally:
        try:
            await exchange.close()
        except Exception:
            pass

    adverse_slippage_values: List[float] = []
    fee_bps_values: List[float] = []
    spread_impact_values: List[float] = []
    effective_cost_values: List[float] = []
    symbol_buckets: Dict[str, Dict[str, Any]] = {}

    for row in trades:
        trade_symbol = _normalize_to_futures_symbol(row.get("symbol")) or str(row.get("symbol") or "")
        if not trade_symbol:
            continue
        side_value = str(row.get("side") or "").upper()
        side_sign = 1.0 if side_value.startswith("BUY") else (-1.0 if side_value.startswith("SELL") else 0.0)
        price = _safe_float(row.get("price"), 0.0)
        cost = abs(_safe_float(row.get("cost"), 0.0))
        amount = abs(_safe_float(row.get("amount"), 0.0))
        fee = abs(_safe_float(row.get("fee"), 0.0))
        if cost <= 0 and price > 0 and amount > 0:
            cost = price * amount
        timestamp_ms = int(_safe_float(row.get("timestamp"), 0.0))
        reference_price = _reference_price_from_rows(candle_cache.get(trade_symbol, []), timestamp_ms)
        if reference_price <= 0 or price <= 0:
            continue

        fee_bps = (fee / cost * 10000.0) if cost > 0 else 0.0
        signed_slippage_bps = (((price - reference_price) / reference_price) * 10000.0) * side_sign if side_sign != 0 else 0.0
        adverse_slippage_bps = max(0.0, signed_slippage_bps)
        spread_impact_bps = _safe_float(spread_cache.get(trade_symbol), 0.0) / 2.0
        effective_cost_bps = adverse_slippage_bps + spread_impact_bps + fee_bps

        adverse_slippage_values.append(adverse_slippage_bps)
        fee_bps_values.append(fee_bps)
        spread_impact_values.append(spread_impact_bps)
        effective_cost_values.append(effective_cost_bps)

        bucket = symbol_buckets.setdefault(
            trade_symbol,
            {
                "symbol": trade_symbol,
                "trades": 0,
                "adverse_slippage_bps": [],
                "effective_cost_bps": [],
            },
        )
        bucket["trades"] += 1
        bucket["adverse_slippage_bps"].append(adverse_slippage_bps)
        bucket["effective_cost_bps"].append(effective_cost_bps)

    analyzed_count = len(adverse_slippage_values)
    if analyzed_count == 0:
        return {
            "success": True,
            "count": 0,
            "quality_score": 0.0,
            "grade": "N/A",
            "message": "Trade rows were found but execution metrics could not be derived.",
            "metrics": {},
            "symbols": [],
        }

    avg_adverse_slippage = _safe_mean(adverse_slippage_values)
    p90_slippage = _percentile(adverse_slippage_values, 90.0)
    avg_fee_bps = _safe_mean(fee_bps_values)
    avg_spread_impact = _safe_mean(spread_impact_values)
    avg_effective_cost = _safe_mean(effective_cost_values)
    median_effective_cost = _safe_median(effective_cost_values)
    p90_effective_cost = _percentile(effective_cost_values, 90.0)
    quality_score = _execution_quality_score(avg_effective_cost, p90_slippage)

    if quality_score >= 80:
        grade = "A"
    elif quality_score >= 65:
        grade = "B"
    elif quality_score >= 50:
        grade = "C"
    else:
        grade = "D"

    symbol_summary: List[Dict[str, Any]] = []
    for bucket in symbol_buckets.values():
        symbol_summary.append({
            "symbol": bucket["symbol"],
            "trades": bucket["trades"],
            "avg_adverse_slippage_bps": round(_safe_mean(bucket["adverse_slippage_bps"]), 3),
            "p90_adverse_slippage_bps": round(_percentile(bucket["adverse_slippage_bps"], 90.0), 3),
            "avg_effective_cost_bps": round(_safe_mean(bucket["effective_cost_bps"]), 3),
        })
    symbol_summary.sort(key=lambda row: row.get("avg_effective_cost_bps", 0.0), reverse=True)

    recommendations: List[str] = []
    if p90_slippage > 12.0:
        recommendations.append("Use limit/iceberg entries on volatile bursts; p90 slippage is elevated.")
    if avg_effective_cost > 10.0:
        recommendations.append("Effective execution cost is high. Reduce taker frequency and widen entry patience.")
    if avg_spread_impact > 4.0:
        recommendations.append("Spread impact is material. Favor higher-liquidity symbols/sessions.")
    if not recommendations:
        recommendations.append("Execution quality is stable. Keep current order discipline.")

    return {
        "success": True,
        "count": analyzed_count,
        "symbol_filter": normalized_symbol or None,
        "reference_timeframe": reference_tf,
        "quality_score": quality_score,
        "grade": grade,
        "metrics": {
            "avg_adverse_slippage_bps": round(avg_adverse_slippage, 3),
            "median_adverse_slippage_bps": round(_safe_median(adverse_slippage_values), 3),
            "p90_adverse_slippage_bps": round(p90_slippage, 3),
            "avg_fee_bps": round(avg_fee_bps, 3),
            "avg_spread_impact_bps": round(avg_spread_impact, 3),
            "avg_effective_cost_bps": round(avg_effective_cost, 3),
            "median_effective_cost_bps": round(median_effective_cost, 3),
            "p90_effective_cost_bps": round(p90_effective_cost, 3),
            "max_adverse_slippage_bps": round(max(adverse_slippage_values), 3),
        },
        "symbols": symbol_summary[:12],
        "recommendations": recommendations,
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    import socket
    
    def can_bind(host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
            return True
        except OSError:
            return False

    def find_free_port(host: str, start_port: int = 8000, max_attempts: int = 1000) -> int:
        """Find a free local port starting from start_port."""
        for port in range(start_port, start_port + max_attempts):
            if can_bind(host, port):
                return port
        raise RuntimeError(
            f"Could not find a free port in range {start_port}-{start_port + max_attempts}"
        )

    host = os.getenv("MODAI_HOST", "127.0.0.1")
    try:
        requested_port = int(os.getenv("MODAI_PORT", "5500"))
    except ValueError:
        requested_port = 5500

    if can_bind(host, requested_port):
        port = requested_port
    else:
        port = find_free_port(host, requested_port + 1)
        print(
            f"Requested port {requested_port} is busy, using fallback port {port}",
            flush=True,
        )

    # Marker used by Electron to detect ready backend URL deterministically.
    print(f"BACKEND_READY=http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port)
