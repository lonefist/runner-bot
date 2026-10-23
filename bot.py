import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ============================================================
# RUNNER BOT V6.0 — REPORT-BASED MARKET EVOLUTION ENGINE
# Built around the 11 successful runner reports supplied.
# No lore, X/Twitter, AI research, or unrelated filters.
# ============================================================

BOT_VERSION = "V6.0-REPORT-BASED"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"
CHAIN = "solana"

# ------------------------------------------------------------
# CONFIGURATION: AGREED MARKET RANGES
# ------------------------------------------------------------

MIN_MC = 40_000
MAX_MC = 150_000

MIN_LIQUIDITY = 16_500
MAX_LIQUIDITY = 40_000

# Observed ranges from the 11 successful reports.
# These describe the sample; they are not hard exclusion rules.
REPORT_BS_MIN = 1.22
REPORT_BS_MAX = 1.54

REPORT_VOLUME_MC_MIN = 46.06
REPORT_VOLUME_MC_MAX = 318.64

REPORT_TX_MIN = 406
REPORT_TX_MAX = 4723

REPORT_PRICE_MIN = 6.08
REPORT_PRICE_MAX = 117.0

REPORT_SCORE_MIN = 81
REPORT_SCORE_MAX = 93

SCAN_INTERVAL = max(
    5, int(os.getenv("SCAN_INTERVAL_SECONDS", "15"))
)

DISCOVERY_INTERVAL = max(
    30, int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "60"))
)

HTTP_TIMEOUT = 15
MAX_HISTORY = 12
ALERT_COOLDOWN_SECONDS = 30 * 60

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
HEATING_ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "true").lower() == "true"
)

STATE_FILE = "runner_state.json"

# Alert quality settings:
# A token must be inside the agreed MC/liquidity range and
# have at least 3 observations. Other report metrics contribute
# to the score rather than becoming arbitrary hard gates.
MIN_OBSERVATIONS = 3
MIN_REPORT_SCORE = 65

STATE: Dict[str, Any] = {
    "subscribers": [],
    "tokens": {},
    "last_update_id": 0,
}


# ============================================================
# HELPERS
# ============================================================

def now_ts() -> int:
    return int(time.time())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def fmt_money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.0f}"


def fmt_ratio(value: float) -> str:
    return "∞" if value >= 999 else f"{value:.2f}"


def load_state() -> None:
    global STATE

    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        if isinstance(loaded, dict):
            STATE.update(loaded)

        STATE.setdefault("subscribers", [])
        STATE.setdefault("tokens", {})
        STATE.setdefault("last_update_id", 0)

    except Exception as exc:
        print(f"[STATE] Load error: {exc}")


def save_state() -> None:
    try:
        temp_file = STATE_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(STATE, f, indent=2)

        os.replace(temp_file, STATE_FILE)

    except Exception as exc:
        print(f"[STATE] Save error: {exc}")


# ============================================================
# HTTP
# ============================================================

def http_get_json(url: str) -> Optional[Any]:
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RunnerBot/6.0",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            request, timeout=HTTP_TIMEOUT
        ) as response:
            return json.loads(
                response.read().decode("utf-8", errors="replace")
            )

    except urllib.error.HTTPError as exc:
        print(f"[HTTP] {exc.code}: {url}")

    except Exception as exc:
        print(f"[HTTP] Request failed: {exc}")

    return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(
    method: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:

    if not TELEGRAM_TOKEN:
        return None

    url = f"{TELEGRAM_BASE}/bot{TELEGRAM_TOKEN}/{method}"

    try:
        data = urllib.parse.urlencode(params or {}).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"User-Agent": "RunnerBot/6.0"},
        )

        with urllib.request.urlopen(
            request, timeout=HTTP_TIMEOUT
        ) as response:
            return json.loads(
                response.read().decode("utf-8", errors="replace")
            )

    except Exception as exc:
        print(f"[TELEGRAM] {method}: {exc}")
        return None


def telegram_send(chat_id: int, message: str) -> bool:
    result = telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        },
    )
    return bool(result and result.get("ok"))


def send_to_subscribers(message: str) -> None:
    if not HEATING_ALERTS_ENABLED:
        return

    for chat_id in list(STATE.get("subscribers", [])):
        telegram_send(chat_id, message)


def telegram_poll() -> None:
    if not TELEGRAM_TOKEN:
        return

    offset = safe_int(STATE.get("last_update_id"))

    result = telegram_api(
        "getUpdates",
        {
            "offset": offset + 1,
            "timeout": 1,
            "allowed_updates": '["message"]',
        },
    )

    if not result or not result.get("ok"):
        return

    for update in result.get("result", []):
        update_id = safe_int(update.get("update_id"))
        STATE["last_update_id"] = update_id

        message = update.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        command = message.get("text", "").strip()

        if not chat_id:
            continue

        if command == "/start":
            subscribers = STATE.setdefault("subscribers", [])

            if chat_id not in subscribers:
                subscribers.append(chat_id)

            telegram_send(
                chat_id,
                "🚀 RUNNER BOT V6.0 ONLINE\n\n"
                "Report-based Market Evolution Engine active.\n"
                "Market cap: $40K–$150K\n"
                "Liquidity: $16.5K–$40K\n"
                "Use /status to check the scanner.",
            )

        elif command == "/status":
            telegram_send(
                chat_id,
                "RUNNER BOT V6.0 STATUS\n\n"
                f"Tracked tokens: {len(STATE.get('tokens', {}))}\n"
                f"Subscribers: {len(STATE.get('subscribers', []))}\n"
                f"Scan interval: {SCAN_INTERVAL}s\n"
                f"Discovery interval: {DISCOVERY_INTERVAL}s\n"
                f"Alerts enabled: {HEATING_ALERTS_ENABLED}",
            )

        elif command == "/clear":
            STATE["tokens"] = {}
            save_state()
            telegram_send(chat_id, "Token history cleared.")

    save_state()


# ============================================================
# DISCOVERY
# ============================================================

DISCOVERY_ENDPOINTS = [
    "/token-profiles/latest/v1",
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
    "/community-takeovers/latest/v1",
]


def discover_token_addresses() -> List[str]:
    addresses = set()

    for endpoint in DISCOVERY_ENDPOINTS:
        data = http_get_json(DEX_BASE + endpoint)

        if not isinstance(data, list):
            continue

        for item in data:
            if not isinstance(item, dict):
                continue

            if item.get("chainId") != CHAIN:
                continue

            address = item.get("tokenAddress")

            if address:
                addresses.add(address)

    return list(addresses)


def get_token_pairs(addresses: List[str]) -> List[Dict[str, Any]]:
    all_pairs = []

    for start in range(0, len(addresses), 30):
        batch = addresses[start:start + 30]
        encoded = ",".join(batch)

        url = (
            f"{DEX_BASE}/latest/dex/tokens/"
            f"{urllib.parse.quote(encoded)}"
        )

        data = http_get_json(url)

        if not isinstance(data, dict):
            continue

        for pair in data.get("pairs", []):
            if (
                isinstance(pair, dict)
                and pair.get("chainId") == CHAIN
            ):
                all_pairs.append(pair)

    return all_pairs


def select_best_pair(
    pairs: List[Dict[str, Any]],
    address: str,
) -> Optional[Dict[str, Any]]:

    matching = [
        pair for pair in pairs
        if pair.get("baseToken", {}).get("address") == address
    ]

    if not matching:
        return None

    return max(
        matching,
        key=lambda pair: safe_float(
            pair.get("liquidity", {}).get("usd")
        ),
    )


# ============================================================
# SNAPSHOTS
# ============================================================

def build_snapshot(pair: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = pair.get("baseToken", {})
    address = base.get("address")

    if not address:
        return None

    market_cap = safe_float(pair.get("marketCap"))

    if market_cap <= 0:
        market_cap = safe_float(pair.get("fdv"))

    volume_5m = safe_float(pair.get("volume", {}).get("m5"))
    liquidity = safe_float(pair.get("liquidity", {}).get("usd"))

    txns = pair.get("txns", {}).get("m5", {})
    buys = safe_int(txns.get("buys"))
    sells = safe_int(txns.get("sells"))
    total_tx = buys + sells

    bs_ratio = (
        buys / sells if sells > 0
        else (999.0 if buys > 0 else 0.0)
    )

    volume_mc = (
        volume_5m / market_cap * 100
        if market_cap > 0 else 0.0
    )

    return {
        "timestamp": now_ts(),
        "time": utc_now(),
        "address": address,
        "symbol": base.get("symbol", "UNKNOWN"),
        "name": base.get("name", "UNKNOWN"),
        "market_cap": market_cap,
        "liquidity": liquidity,
        "volume_5m": volume_5m,
        "volume_mc": volume_mc,
        "buys_5m": buys,
        "sells_5m": sells,
        "bs_ratio": bs_ratio,
        "tx_5m": total_tx,
        "price_change_5m": safe_float(
            pair.get("priceChange", {}).get("m5")
        ),
        "price_usd": safe_float(pair.get("priceUsd")),
        "pair_address": pair.get("pairAddress", ""),
        "dex": pair.get("dexId", ""),
        "url": pair.get("url", ""),
    }


# ============================================================
# HISTORY
# ============================================================

def get_token_state(address: str) -> Dict[str, Any]:
    tokens = STATE.setdefault("tokens", {})

    if address not in tokens:
        tokens[address] = {
            "first_seen": now_ts(),
            "last_seen": now_ts(),
            "history": [],
            "last_alert": 0,
            "alerted_types": [],
        }

    return tokens[address]


def add_observation(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    state = get_token_state(snapshot["address"])
    history = state.setdefault("history", [])

    # Avoid storing duplicate observations within the same second.
    if history and history[-1].get("timestamp") == snapshot["timestamp"]:
        return state

    history.append(snapshot)
    del history[:-MAX_HISTORY]

    state["last_seen"] = now_ts()
    return state


# ============================================================
# REPORT-BASED SCORING
# ============================================================

def range_score(
    value: float,
    low: float,
    high: float,
) -> float:
    """Scores proximity to the successful-report range."""
    if value <= 0 or high <= low:
        return 0.0

    if low <= value <= high:
        return 1.0

    # Values outside the sample still receive partial credit.
    distance = low - value if value < low else value - high
    span = high - low
    return max(0.0, 1.0 - distance / max(span, 1.0))


def calculate_market_score(
    snapshot: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> int:

    mc = snapshot["market_cap"]
    liquidity = snapshot["liquidity"]

    # The four agreed financial bounds are mandatory.
    if not (MIN_MC <= mc <= MAX_MC):
        return 0

    if not (MIN_LIQUIDITY <= liquidity <= MAX_LIQUIDITY):
        return 0

    scores = [
        range_score(
            snapshot["bs_ratio"],
            REPORT_BS_MIN,
            REPORT_BS_MAX,
        ),
        range_score(
            snapshot["volume_mc"],
            REPORT_VOLUME_MC_MIN,
            REPORT_VOLUME_MC_MAX,
        ),
        range_score(
            snapshot["tx_5m"],
            REPORT_TX_MIN,
            REPORT_TX_MAX,
        ),
        range_score(
            snapshot["price_change_5m"],
            REPORT_PRICE_MIN,
            REPORT_PRICE_MAX,
        ),
    ]

    # More recent observations help establish persistence.
    history_score = min(len(history) / MIN_OBSERVATIONS, 1.0)

    # MC and liquidity eligibility are mandatory; other
    # report metrics contribute proportionally to the score.
    weighted = (
        scores[0] * 25
        + scores[1] * 30
        + scores[2] * 20
        + scores[3] * 15
        + history_score * 10
    )

    return int(max(0, min(100, round(weighted))))


def analyze_evolution(
    history: List[Dict[str, Any]],
) -> Dict[str, Any]:

    result = {
        "trend": "NEW",
        "improving_steps": 0,
        "improving_metrics": 0,
    }

    if len(history) < 2:
        return result

    current = history[-1]
    previous = history[-2]

    metric_keys = [
        ("bs_ratio", True),
        ("volume_mc", True),
        ("tx_5m", True),
        ("price_change_5m", True),
        ("liquidity", True),
        ("market_cap", True),
    ]

    improved = 0

    for key, higher_is_better in metric_keys:
        if higher_is_better and current[key] > previous[key]:
            improved += 1

    result["improving_metrics"] = improved

    # Count consecutive observation pairs with at least two
    # metrics improving. Trend is informational, not a hard gate.
    steps = 0

    for index in range(len(history) - 1, 0, -1):
        cur = history[index]
        prev = history[index - 1]

        count = sum(
            1 for key, _ in metric_keys
            if cur[key] > prev[key]
        )

        if count >= 2:
            steps += 1
        else:
            break

    result["improving_steps"] = steps

    if steps >= 2:
        result["trend"] = "ACCELERATING"
    elif improved >= 2:
        result["trend"] = "IMPROVING"
    else:
        result["trend"] = "MIXED"

    return result


# ============================================================
# SIGNAL DECISION
# ============================================================

def qualifies_market(snapshot: Dict[str, Any]) -> bool:
    return (
        MIN_MC <= snapshot["market_cap"] <= MAX_MC
        and MIN_LIQUIDITY
        <= snapshot["liquidity"]
        <= MAX_LIQUIDITY
    )


def should_alert(
    snapshot: Dict[str, Any],
    history: List[Dict[str, Any]],
    score: int,
) -> bool:

    if not qualifies_market(snapshot):
        return False

    if len(history) < MIN_OBSERVATIONS:
        return False

    if score < MIN_REPORT_SCORE:
        return False

    # No separate trend, age, social, wallet, or narrative gate.
    return True


# ============================================================
# ALERTS
# ============================================================

def build_alert(
    snapshot: Dict[str, Any],
    history: List[Dict[str, Any]],
    evolution: Dict[str, Any],
    score: int,
) -> str:

    previous = history[-2] if len(history) >= 2 else None

    prev_bs = previous["bs_ratio"] if previous else 0.0
    prev_volume_mc = previous["volume_mc"] if previous else 0.0

    return (
        "🚀 RUNNER CANDIDATE — V6.0\n\n"
        f"{snapshot['name']} ({snapshot['symbol']})\n\n"
        f"💰 MC: {fmt_money(snapshot['market_cap'])}\n"
        f"💧 Liquidity: {fmt_money(snapshot['liquidity'])}\n"
        f"📈 5m Price: {snapshot['price_change_5m']:+.2f}%\n"
        f"🟢 Buy/Sell: {fmt_ratio(snapshot['bs_ratio'])}\n"
        f"   Previous B/S: {fmt_ratio(prev_bs)}\n"
        f"📊 5m Vol/MC: {snapshot['volume_mc']:.2f}%\n"
        f"   Previous Vol/MC: {prev_volume_mc:.2f}%\n"
        f"🔄 5m Transactions: {snapshot['tx_5m']}\n"
        f"📈 Report-Based Score: {score}/100\n"
        f"🔥 Evolution: {evolution['trend']}\n"
        f"⬆️ Improving steps: {evolution['improving_steps']}\n"
        f"📚 Observations: {len(history)}\n\n"
        f"DEX: {snapshot['dex']}\n\n"
        f"CA:\n{snapshot['address']}\n\n"
        f"Chart:\n{snapshot['url']}\n\n"
        "⚠️ Market signals are not guarantees of future performance."
    )


def process_snapshot(snapshot: Dict[str, Any]) -> None:
    state = add_observation(snapshot)
    history = state["history"]

    evolution = analyze_evolution(history)
    score = calculate_market_score(snapshot, history)

    print(
        f"[TOKEN] {snapshot['symbol']} "
        f"| MC {fmt_money(snapshot['market_cap'])} "
        f"| LIQ {fmt_money(snapshot['liquidity'])} "
        f"| B/S {fmt_ratio(snapshot['bs_ratio'])} "
        f"| VOL/MC {snapshot['volume_mc']:.2f}% "
        f"| TX {snapshot['tx_5m']} "
        f"| PRICE {snapshot['price_change_5m']:+.2f}% "
        f"| SCORE {score} "
        f"| TREND {evolution['trend']} "
        f"| OBS {len(history)}"
    )

    if not should_alert(snapshot, history, score):
        return

    if not HEATING_ALERTS_ENABLED:
        return

    if now_ts() - safe_int(state.get("last_alert")) < ALERT_COOLDOWN_SECONDS:
        return

    message = build_alert(
        snapshot,
        history,
        evolution,
        score,
    )

    send_to_subscribers(message)

    state["last_alert"] = now_ts()
    save_state()

    print(f"[SIGNAL] {snapshot['symbol']} score={score}")


# ============================================================
# DISCOVERY AND TRACKING
# ============================================================

def process_pairs(pairs: List[Dict[str, Any]]) -> None:
    grouped: Dict[str, List[Dict[str, Any]]] = {}

    for pair in pairs:
        address = pair.get("baseToken", {}).get("address")

        if address:
            grouped.setdefault(address, []).append(pair)

    for address, token_pairs in grouped.items():
        pair = select_best_pair(token_pairs, address)

        if not pair:
            continue

        snapshot = build_snapshot(pair)

        if snapshot:
            process_snapshot(snapshot)


def discovery_cycle() -> None:
    print("[DISCOVERY] Searching latest Solana token listings...")

    addresses = discover_token_addresses()
    print(f"[DISCOVERY] Found {len(addresses)} token addresses")

    if not addresses:
        return

    pairs = get_token_pairs(addresses)
    print(f"[DISCOVERY] Retrieved {len(pairs)} pairs")

    process_pairs(pairs)
    save_state()


def tracked_token_addresses() -> List[str]:
    result = []

    for address, state in STATE.get("tokens", {}).items():
        last_seen = safe_int(state.get("last_seen"))

        if last_seen and now_ts() - last_seen <= 6 * 60 * 60:
            result.append(address)

    return result


def tracking_cycle() -> None:
    addresses = tracked_token_addresses()

    if not addresses:
        return

    pairs = get_token_pairs(addresses)
    process_pairs(pairs)
    save_state()


def cleanup_old_tokens() -> None:
    tokens = STATE.get("tokens", {})
    cutoff = now_ts() - 12 * 60 * 60

    for address in list(tokens.keys()):
        state = tokens[address]

        if safe_int(state.get("last_seen")) >= cutoff:
            continue

        # Preserve alert history, and otherwise discard stale entries.
        if not state.get("last_alert"):
            tokens.pop(address, None)


# ============================================================
# STARTUP AND WORKFLOW
# ============================================================

def startup() -> None:
    print("=" * 60)
    print(f"RUNNER BOT {BOT_VERSION}")
    print("=" * 60)
    print(f"Chain: {CHAIN}")
    print(f"Market cap: ${MIN_MC:,}–${MAX_MC:,}")
    print(f"Liquidity: ${MIN_LIQUIDITY:,}–${MAX_LIQUIDITY:,}")
    print(f"Scan interval: {SCAN_INTERVAL}s")
    print(f"Discovery interval: {DISCOVERY_INTERVAL}s")
    print(f"Telegram: {'CONFIGURED' if TELEGRAM_TOKEN else 'NOT CONFIGURED'}")
    print(f"Alerts enabled: {HEATING_ALERTS_ENABLED}")
    print("Narrative/X/AI filters: disabled")
    print("=" * 60)

    if TELEGRAM_TOKEN:
        result = telegram_api("getMe")

        if result and result.get("ok"):
            username = result.get("result", {}).get("username", "unknown")
            print(f"[TELEGRAM] Connected as @{username}")
        else:
            print("[TELEGRAM] Connection test failed")
    else:
        print("[TELEGRAM] Add TELEGRAM_BOT_TOKEN in Replit Secrets")

    print("[STARTUP] Scanner ready")


def main() -> None:
    load_state()
    startup()

    last_discovery = 0
    last_cleanup = 0

    while True:
        try:
            telegram_poll()
            current = now_ts()

            if current - last_discovery >= DISCOVERY_INTERVAL:
                discovery_cycle()
                last_discovery = current
            else:
                tracking_cycle()

            if current - last_cleanup >= 15 * 60:
                cleanup_old_tokens()
                save_state()
                last_cleanup = current

            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            print("[STOP] Saving state...")
            save_state()
            break

        except Exception as exc:
            print(f"[MAIN ERROR] {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
