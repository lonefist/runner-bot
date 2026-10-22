"""
RUNNER BOT V5.0 — Solana early-cap scanner
Target market cap: $5,000–$50,000 (hard ceiling)
Uses DexScreener public API and Telegram Bot API.

Important:
- "Flow" is a buy/sell-imbalance proxy calculated from 5m volume and transaction counts.
  It is NOT actual net wallet inflow.
- DexScreener's marketCap is preferred; FDV is used only if marketCap is absent and
  the alert explicitly marks it as an FDV proxy.
- This is a screening/alert tool, not financial advice or a guarantee of token safety.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

BOT_VERSION = "V5.0-HARD-CAP-50K"

# ---------- CONFIGURATION ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "15"))
DISCOVERY_INTERVAL_SECONDS = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "60"))
HEATING_ALERTS_ENABLED = os.getenv("HEATING_ALERTS_ENABLED", "true").lower() in ("1", "true", "yes", "on")
RUNNER_DEBUG = os.getenv("RUNNER_DEBUG", "true").lower() in ("1", "true", "yes", "on")

MIN_MCAP_USD = 5_000
MAX_MCAP_USD = 50_000  # HARD MAX — no exception may bypass this
MIN_LIQUIDITY_USD = 6_000
MAX_TOKEN_AGE_HOURS = 48

MIN_CORE_FLOW_USD = 1_500
MIN_FLOW_MCAP_PCT = 8.0
PREFERRED_VOLUME_FLOW_RATIO = 1.25
PREFERRED_BUY_SELL_RATIO = 1.5

# Existing early Pump.fun exception, but it still must pass the hard $5k–$50k cap.
PUMPFUN_EARLY_AGE_HOURS = 1.5
PUMPFUN_EARLY_MIN_VOL5 = 8_000
PUMPFUN_EARLY_MIN_FLOW = 3_000
PUMPFUN_EARLY_MIN_FLOW_MCAP_PCT = 12.0

STRONG_FLOW_PCT = 15.0
STRONG_FLOW_USD = 15_000
ULTRA_FLOW_PCT = 25.0
ULTRA_FLOW_USD = 25_000

NORMAL_PERSISTENCE_OBSERVATIONS = 3
STRONG_PERSISTENCE_OBSERVATIONS = 2
ULTRA_PERSISTENCE_OBSERVATIONS = 1
PENDING_EXPIRY_SECONDS = 300
TOKEN_COOLDOWN_SECONDS = 600
GLOBAL_ALERT_COOLDOWN_SECONDS = 60

MAX_DISCOVERY_TOKENS = 500
DISCOVERY_BATCH_SIZE = 25
MAX_HISTORY_PER_TOKEN = 20
MAX_TRACKED_TOKENS = 1500
PREFILTER_MIN_VOL5 = 500
PREFILTER_MIN_ABS_FLOW = 500

STATE_FILE = Path(os.getenv("STATE_FILE", "runner_state_v50.json"))
DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""

SEARCH_TERMS = ["SOL", "WSOL", "pump", "meme", "cat", "dog", "ai", "inu", "coin", "frog", "agent"]
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": f"RunnerBot/{BOT_VERSION}"})

logging.basicConfig(
    level=logging.DEBUG if RUNNER_DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("runner-bot")


def now_ts() -> float:
    return time.time()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_get(url: str, timeout: int = 12) -> Optional[Any]:
    try:
        response = SESSION.get(url, timeout=timeout)
        if response.status_code == 429:
            log.warning("DexScreener rate limit hit for %s", url)
            return None
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Request failed: %s (%s)", url, exc)
        return None


def load_state() -> Dict[str, Any]:
    default = {
        "version": BOT_VERSION,
        "known_tokens": {},
        "pending": {},
        "token_cooldowns": {},
        "last_global_alert": 0,
        "last_discovery": 0,
        "telegram_offset": 0,
        "seen_profiles": [],
    }
    if not STATE_FILE.exists():
        return default
    try:
        with STATE_FILE.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        for key, value in default.items():
            loaded.setdefault(key, value)
        # Avoid carrying malformed state from a different layout.
        if not isinstance(loaded.get("known_tokens"), dict):
            loaded["known_tokens"] = {}
        if not isinstance(loaded.get("pending"), dict):
            loaded["pending"] = {}
        if not isinstance(loaded.get("token_cooldowns"), dict):
            loaded["token_cooldowns"] = {}
        return loaded
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read state file; starting fresh: %s", exc)
        return default


def save_state(state: Dict[str, Any]) -> None:
    try:
        temp_path = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
        temp_path.replace(STATE_FILE)
    except OSError as exc:
        log.error("Could not save state: %s", exc)


def telegram_call(method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not TELEGRAM_BOT_TOKEN:
        return None
    try:
        response = SESSION.post(f"{TELEGRAM_BASE}/{method}", json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            log.warning("Telegram API returned not-ok: %s", result)
            return None
        return result
    except (requests.RequestException, ValueError) as exc:
        log.warning("Telegram request failed (%s): %s", method, exc)
        return None


def send_message(text: str, chat_id: Optional[str] = None) -> bool:
    destination = str(chat_id or TELEGRAM_CHAT_ID).strip()
    if not destination:
        log.info("No TELEGRAM_CHAT_ID configured; message not sent: %s", text[:100])
        return False
    result = telegram_call("sendMessage", {
        "chat_id": destination,
        "text": text,
        "disable_web_page_preview": True,
    })
    return bool(result)


def get_telegram_updates(state: Dict[str, Any]) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    offset = int(state.get("telegram_offset", 0))
    result = telegram_call("getUpdates", {
        "offset": offset,
        "timeout": 1,
        "allowed_updates": ["message"],
    })
    if not result:
        return
    for update in result.get("result", []):
        state["telegram_offset"] = max(int(state.get("telegram_offset", 0)), int(update.get("update_id", 0)) + 1)
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip().lower()
        if not chat_id or not text.startswith("/"):
            continue
        if text.startswith("/start") or text.startswith("/help"):
            send_message(
                "Runner Bot V5.0 is running. Hard market-cap range: $5K–$50K.\\n"
                "Commands: /status, /start, /help\\n"
                "Set TELEGRAM_CHAT_ID in Replit Secrets for alerts to be delivered to a fixed chat.",
                chat_id,
            )
        elif text.startswith("/status"):
            send_message(
                f"Runner Bot {BOT_VERSION}\\n"
                f"Target MC: ${MIN_MCAP_USD:,}–${MAX_MCAP_USD:,} (hard limits)\\n"
                f"Liquidity minimum: ${MIN_LIQUIDITY_USD:,}\\n"
                f"Core flow minimum: ${MIN_CORE_FLOW_USD:,}\\n"
                f"Scan interval: {SCAN_INTERVAL_SECONDS}s\\n"
                f"Alerts enabled: {HEATING_ALERTS_ENABLED}",
                chat_id,
            )


def add_token_address(addresses: set, chain: str, address: Any) -> None:
    if chain == "solana" and isinstance(address, str) and address:
        addresses.add(address)


def discover_token_addresses(state: Dict[str, Any]) -> List[str]:
    addresses = set()

    # Latest token profiles and boosts are useful discovery sources, but are not treated
    # as proof of token quality.
    for endpoint in (
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ):
        data = safe_get(f"{DEX_BASE}{endpoint}")
        if isinstance(data, list):
            for item in data:
                add_token_address(addresses, item.get("chainId"), item.get("tokenAddress"))

    # Search terms are discovery only. Results are filtered by the same strict checks later.
    for term in SEARCH_TERMS:
        data = safe_get(f"{DEX_BASE}/latest/dex/search?q={requests.utils.quote(term)}", timeout=10)
        if isinstance(data, dict):
            for pair in data.get("pairs") or []:
                if pair.get("chainId") == "solana":
                    base = pair.get("baseToken") or {}
                    add_token_address(addresses, "solana", base.get("address"))

    # Existing tracked tokens are revisited even if not found by discovery this cycle.
    for address in list((state.get("known_tokens") or {}).keys()):
        addresses.add(address)

    result = list(addresses)
    if len(result) > MAX_DISCOVERY_TOKENS:
        result = result[:MAX_DISCOVERY_TOKENS]
    log.info("Discovery collected %d Solana token addresses", len(result))
    return result


def get_token_pairs(addresses: List[str]) -> List[Dict[str, Any]]:
    pairs: List[Dict[str, Any]] = []
    # DexScreener token endpoint accepts comma-separated token addresses. Keep batches small.
    for start in range(0, len(addresses), DISCOVERY_BATCH_SIZE):
        batch = addresses[start:start + DISCOVERY_BATCH_SIZE]
        if not batch:
            continue
        joined = ",".join(batch)
        data = safe_get(f"{DEX_BASE}/tokens/v1/solana/{joined}", timeout=15)
        if isinstance(data, list):
            pairs.extend(
                pair for pair in data
                if isinstance(pair, dict) and pair.get("chainId") == "solana"
            )
        # Small pause helps reduce request bursts.
        time.sleep(0.15)
    return pairs


def pair_mcap(pair: Dict[str, Any]) -> Tuple[float, str]:
    market_cap = as_float(pair.get("marketCap"), 0)
    if market_cap > 0:
        return market_cap, "MC"
    fdv = as_float(pair.get("fdv"), 0)
    if fdv > 0:
        return fdv, "FDV proxy"
    return 0.0, "unknown"


def age_hours(pair: Dict[str, Any]) -> Optional[float]:
    created_ms = pair.get("pairCreatedAt")
    if not created_ms:
        return None
    created = as_float(created_ms, 0) / 1000.0
    if created <= 0:
        return None
    return max(0.0, (now_ts() - created) / 3600.0)


def normalize_pair(pair: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = pair.get("baseToken") or {}
    address = base.get("address")
    if not address:
        return None

    mcap, mcap_source = pair_mcap(pair)
    if mcap <= 0:
        return None

    liquidity = as_float((pair.get("liquidity") or {}).get("usd"), 0)
    volume5 = as_float((pair.get("volume") or {}).get("m5"), 0)
    txns5 = (pair.get("txns") or {}).get("m5") or {}
    buys = as_float(txns5.get("buys"), 0)
    sells = as_float(txns5.get("sells"), 0)
    total_tx = buys + sells

    # Proxy only: volume weighted by transaction buy/sell imbalance.
    flow = volume5 * ((buys - sells) / total_tx) if total_tx > 0 else 0.0
    flow_mcap_pct = (flow / mcap * 100.0) if mcap > 0 else 0.0
    buy_sell_ratio = buys / sells if sells > 0 else (float("inf") if buys > 0 else 0.0)
    volume_flow_ratio = volume5 / abs(flow) if abs(flow) > 0 else 0.0
    age = age_hours(pair)

    return {
        "address": address,
        "name": base.get("name") or "Unknown",
        "symbol": base.get("symbol") or "???",
        "pair_address": pair.get("pairAddress") or "",
        "dex_id": pair.get("dexId") or "unknown",
        "url": pair.get("url") or f"https://dexscreener.com/solana/{address}",
        "mcap": mcap,
        "mcap_source": mcap_source,
        "liquidity": liquidity,
        "volume5": volume5,
        "buys": int(buys),
        "sells": int(sells),
        "total_tx": int(total_tx),
        "flow": flow,
        "flow_mcap_pct": flow_mcap_pct,
        "buy_sell_ratio": buy_sell_ratio,
        "volume_flow_ratio": volume_flow_ratio,
        "age_hours": age,
        "pair_created_at": pair.get("pairCreatedAt"),
    }


def choose_best_pairs(pairs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Choose the highest-liquidity Solana pair per base token address."""
    best: Dict[str, Dict[str, Any]] = {}
    for raw in pairs:
        item = normalize_pair(raw)
        if not item:
            continue
        address = item["address"]
        if address not in best or item["liquidity"] > best[address]["liquidity"]:
            best[address] = item
    return best


def is_pumpfun_pair(item: Dict[str, Any]) -> bool:
    dex = str(item.get("dex_id", "")).lower()
    url = str(item.get("url", "")).lower()
    return "pump" in dex or "pump.fun" in url


def strength_level(item: Dict[str, Any]) -> str:
    flow_pct = item["flow_mcap_pct"]
    flow_usd = item["flow"]
    if flow_pct >= ULTRA_FLOW_PCT or flow_usd >= ULTRA_FLOW_USD:
        return "ULTRA"
    if flow_pct >= STRONG_FLOW_PCT or flow_usd >= STRONG_FLOW_USD:
        return "STRONG"
    return "NORMAL"


def required_observations(item: Dict[str, Any]) -> int:
    level = strength_level(item)
    if level == "ULTRA":
        return ULTRA_PERSISTENCE_OBSERVATIONS
    if level == "STRONG":
        return STRONG_PERSISTENCE_OBSERVATIONS
    return NORMAL_PERSISTENCE_OBSERVATIONS


def evaluate_candidate(item: Dict[str, Any]) -> Tuple[bool, str]:
    """All candidates pass hard cap limits before any special-case logic."""
    mcap = item["mcap"]
    if mcap < MIN_MCAP_USD:
        return False, "MC below $5K"
    if mcap > MAX_MCAP_USD:
        return False, "MC above hard $50K ceiling"
    if item["liquidity"] < MIN_LIQUIDITY_USD:
        return False, "liquidity below minimum"
    if item["age_hours"] is None:
        return False, "pair age unavailable"
    if item["age_hours"] > MAX_TOKEN_AGE_HOURS:
        return False, "pair older than 48h"
    if item["total_tx"] < 5:
        return False, "fewer than 5 transactions in 5m"
    if item["volume5"] < PREFILTER_MIN_VOL5 and abs(item["flow"]) < PREFILTER_MIN_ABS_FLOW:
        return False, "failed volume/flow prefilter"

    early_pumpfun = (
        is_pumpfun_pair(item)
        and item["age_hours"] <= PUMPFUN_EARLY_AGE_HOURS
        and item["volume5"] >= PUMPFUN_EARLY_MIN_VOL5
        and item["flow"] >= PUMPFUN_EARLY_MIN_FLOW
        and item["flow_mcap_pct"] >= PUMPFUN_EARLY_MIN_FLOW_MCAP_PCT
    )
    if early_pumpfun:
        return True, "PUMPFUN EARLY"

    if item["flow"] < MIN_CORE_FLOW_USD:
        return False, "core flow below $1.5K"
    if item["flow_mcap_pct"] < MIN_FLOW_MCAP_PCT:
        return False, "flow/MC below 8%"
    if item["buy_sell_ratio"] < PREFERRED_BUY_SELL_RATIO:
        return False, "buy/sell ratio below 1.5"
    if item["volume_flow_ratio"] < PREFERRED_VOLUME_FLOW_RATIO:
        return False, "volume/flow ratio below 1.25"
    return True, "CORE FLOW"


def add_observation(state: Dict[str, Any], item: Dict[str, Any]) -> Tuple[int, int]:
    address = item["address"]
    pending = state["pending"]
    current = now_ts()
    record = pending.get(address)
    if not record or current - as_float(record.get("first_seen"), current) > PENDING_EXPIRY_SECONDS:
        record = {"first_seen": current, "observations": [], "last_seen": current}
    observations = record.get("observations", [])
    observations.append({
        "time": current,
        "mcap": item["mcap"],
        "flow": item["flow"],
        "flow_mcap_pct": item["flow_mcap_pct"],
        "volume5": item["volume5"],
        "buys": item["buys"],
        "sells": item["sells"],
    })
    record["observations"] = observations[-MAX_HISTORY_PER_TOKEN:]
    record["last_seen"] = current
    pending[address] = record
    return len(record["observations"]), required_observations(item)


def format_money(value: float) -> str:
    if value == float("inf"):
        return "∞"
    return f"${value:,.0f}"


def format_alert(item: Dict[str, Any], reason: str, observations: int, required: int) -> str:
    age_text = f"{item['age_hours']:.1f}h" if item["age_hours"] is not None else "unknown"
    ratio_text = "∞" if item["buy_sell_ratio"] == float("inf") else f"{item['buy_sell_ratio']:.2f}"
    return (
        f"🔥 RUNNER CANDIDATE — {item['symbol']} ({item['name']})\\n"
        f"Engine: {BOT_VERSION}\\n"
        f"Reason: {reason} | Strength: {strength_level(item)}\\n\\n"
        f"MC: {format_money(item['mcap'])} ({item['mcap_source']})\\n"
        f"Liquidity: {format_money(item['liquidity'])}\\n"
        f"5m Volume: {format_money(item['volume5'])}\\n"
        f"5m Buys/Sells: {item['buys']}/{item['sells']} (ratio {ratio_text})\\n"
        f"Flow proxy: {format_money(item['flow'])} ({item['flow_mcap_pct']:.1f}% of MC)\\n"
        f"Volume/|flow|: {item['volume_flow_ratio']:.2f}\\n"
        f"Pair age: {age_text}\\n"
        f"Persistence: {observations}/{required}\\n\\n"
        f"CA: {item['address']}\\n"
        f"DEX: {item['url']}\\n\\n"
        f"⚠️ Flow is a buy/sell-imbalance proxy, NOT verified net inflow. "
        f"Check holder concentration, mint/freeze authority, liquidity locks, and social catalyst before trading."
    )


def prune_state(state: Dict[str, Any]) -> None:
    current = now_ts()
    state["pending"] = {
        address: data for address, data in state["pending"].items()
        if current - as_float(data.get("last_seen"), 0) <= PENDING_EXPIRY_SECONDS
    }
    state["token_cooldowns"] = {
        address: expiry for address, expiry in state["token_cooldowns"].items()
        if as_float(expiry, 0) > current
    }
    known = state.get("known_tokens", {})
    if len(known) > MAX_TRACKED_TOKENS:
        ordered = sorted(
            known.items(),
            key=lambda kv: as_float(kv[1].get("last_seen"), 0) if isinstance(kv[1], dict) else 0,
            reverse=True,
        )
        state["known_tokens"] = dict(ordered[:MAX_TRACKED_TOKENS])


def run_scan(state: Dict[str, Any]) -> None:
    current = now_ts()
    if current - as_float(state.get("last_discovery"), 0) >= DISCOVERY_INTERVAL_SECONDS:
        addresses = discover_token_addresses(state)
        state["last_discovery"] = current
    else:
        addresses = list((state.get("known_tokens") or {}).keys())

    if not addresses:
        log.info("No token addresses discovered this cycle.")
        return

    pairs = get_token_pairs(addresses)
    candidates = choose_best_pairs(pairs)
    log.info("Received %d pairs; normalized %d tokens", len(pairs), len(candidates))

    for address, item in candidates.items():
        state["known_tokens"].setdefault(address, {})
        state["known_tokens"][address]["last_seen"] = current
        state["known_tokens"][address]["symbol"] = item["symbol"]

        # Hard-cap guard is intentionally first and repeated before alerting.
        if item["mcap"] < MIN_MCAP_USD or item["mcap"] > MAX_MCAP_USD:
            state["pending"].pop(address, None)
            if RUNNER_DEBUG:
                log.debug("Reject %s: outside hard MC range (%s)", item["symbol"], format_money(item["mcap"]))
            continue

        eligible, reason = evaluate_candidate(item)
        if not eligible:
            state["pending"].pop(address, None)
            if RUNNER_DEBUG:
                log.debug("Reject %s: %s", item["symbol"], reason)
            continue

        observations, required = add_observation(state, item)
        if observations < required:
            if RUNNER_DEBUG:
                log.debug("Pending %s: %d/%d observations", item["symbol"], observations, required)
            continue

        # Final market-cap check immediately before alerting, preventing any bypass.
        if not (MIN_MCAP_USD <= item["mcap"] <= MAX_MCAP_USD):
            state["pending"].pop(address, None)
            continue

        if not HEATING_ALERTS_ENABLED:
            log.info("Qualified but alerts disabled: %s (%s)", item["symbol"], reason)
            continue

        if as_float(state["token_cooldowns"].get(address), 0) > current:
            continue
        if current - as_float(state.get("last_global_alert"), 0) < GLOBAL_ALERT_COOLDOWN_SECONDS:
            continue

        alert = format_alert(item, reason, observations, required)
        if send_message(alert):
            state["token_cooldowns"][address] = current + TOKEN_COOLDOWN_SECONDS
            state["last_global_alert"] = current
            state["pending"].pop(address, None)
            log.info("Alert sent for %s (%s)", item["symbol"], address)
        else:
            log.warning("Qualified candidate but alert was not delivered: %s", item["symbol"])

    prune_state(state)
    save_state(state)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "Missing TELEGRAM_BOT_TOKEN. Add it in Replit Secrets before running this bot."
        )
    log.info("Starting %s; hard MC range $%s–$%s", BOT_VERSION, f"{MIN_MCAP_USD:,}", f"{MAX_MCAP_USD:,}")
    send_message(
        f"✅ Runner Bot {BOT_VERSION} started.\\n"
        f"Hard MC range: ${MIN_MCAP_USD:,}–${MAX_MCAP_USD:,}\\n"
        f"Liquidity minimum: ${MIN_LIQUIDITY_USD:,}. Scan interval: {SCAN_INTERVAL_SECONDS}s."
    )
    state = load_state()

    while True:
        cycle_start = now_ts()
        try:
            get_telegram_updates(state)
            run_scan(state)
            save_state(state)
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            save_state(state)
            break
        except Exception:
            log.exception("Unexpected scan error; bot will continue after the next interval.")
        elapsed = now_ts() - cycle_start
        time.sleep(max(1, SCAN_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    main()
