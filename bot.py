"""
RUNNER BOT V5.1
Solana early-cap scanner.

Hard market-cap range: $5,000-$50,000.
Flow is a buy/sell-imbalance proxy, NOT verified net wallet inflow.
"""

import os
import json
import time
import logging
from pathlib import Path

import requests


# ============================================================
# CONFIGURATION
# ============================================================

BOT_VERSION = "V5.1-HARD-CAP-50K"

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID", ""
).strip()

SCAN_INTERVAL_SECONDS = 15
DISCOVERY_INTERVAL_SECONDS = 60

HEATING_ALERTS_ENABLED = True
RUNNER_DEBUG = True

# HARD MARKET-CAP LIMITS
MIN_MCAP_USD = 5_000
MAX_MCAP_USD = 50_000

# LIQUIDITY
MIN_LIQUIDITY_USD = 6_000

# TOKEN AGE
MAX_PAIR_AGE_HOURS = 48

# CORE FLOW FILTERS
MIN_CORE_FLOW_USD = 1_500
MIN_FLOW_MCAP_PCT = 8.0
MIN_VOLUME_FLOW_RATIO = 1.25
MIN_BUY_SELL_RATIO = 1.5

# EARLY PUMP.FUN EXCEPTION
# This exception CANNOT bypass the hard market-cap limits.
PUMPFUN_EARLY_AGE_HOURS = 1.5
PUMPFUN_EARLY_MIN_VOL5 = 8_000
PUMPFUN_EARLY_MIN_FLOW = 3_000
PUMPFUN_EARLY_MIN_FLOW_MCAP_PCT = 12.0

# SIGNAL STRENGTH
STRONG_FLOW_PCT = 15.0
STRONG_FLOW_USD = 15_000

ULTRA_FLOW_PCT = 25.0
ULTRA_FLOW_USD = 25_000

# PERSISTENCE
NORMAL_PERSISTENCE = 3
STRONG_PERSISTENCE = 2
ULTRA_PERSISTENCE = 1

# COOLDOWNS
PENDING_EXPIRY_SECONDS = 300
TOKEN_COOLDOWN_SECONDS = 600
GLOBAL_ALERT_COOLDOWN_SECONDS = 60

# DISCOVERY
MAX_DISCOVERY_TOKENS = 500
DISCOVERY_BATCH_SIZE = 25
MAX_TRACKED_TOKENS = 1500
MAX_HISTORY_PER_TOKEN = 20

PREFILTER_MIN_VOL5 = 500
PREFILTER_MIN_ABS_FLOW = 500

# STATE
STATE_FILE = Path("runner_state_v51.json")

DEX_BASE = "https://api.dexscreener.com"

SEARCH_TERMS = [
    "SOL",
    "WSOL",
    "pump",
    "meme",
    "cat",
    "dog",
    "ai",
    "inu",
    "coin",
    "frog",
    "agent",
]

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": f"RunnerBot/{BOT_VERSION}"
})

logging.basicConfig(
    level=logging.DEBUG if RUNNER_DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger("runner-bot")


# ============================================================
# GENERAL HELPERS
# ============================================================

def now():
    return time.time()


def number(value, default=0.0):
    try:
        if value is None or value == "":
            return default

        return float(value)

    except (TypeError, ValueError):
        return default


def get_json(url, timeout=12):
    try:
        response = SESSION.get(
            url,
            timeout=timeout
        )

        response.raise_for_status()

        return response.json()

    except (requests.RequestException, ValueError) as exc:
        log.warning(
            "Request failed: %s | %s",
            url,
            exc
        )

        return None


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():

    default_state = {
        "known_tokens": {},
        "pending": {},
        "token_cooldowns": {},
        "last_global_alert": 0,
        "last_discovery": 0,
        "telegram_offset": 0,
    }

    if not STATE_FILE.exists():
        return default_state

    try:

        with STATE_FILE.open(
            "r",
            encoding="utf-8"
        ) as file:

            loaded = json.load(file)

        if isinstance(loaded, dict):

            for key in default_state:
                if key in loaded:
                    default_state[key] = loaded[key]

        for key in [
            "known_tokens",
            "pending",
            "token_cooldowns",
        ]:

            if not isinstance(default_state[key], dict):
                default_state[key] = {}

        return default_state

    except (OSError, json.JSONDecodeError) as exc:

        log.warning(
            "Could not load state: %s",
            exc
        )

        return default_state


def save_state(state):

    try:

        temporary = STATE_FILE.with_suffix(".tmp")

        with temporary.open(
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                state,
                file,
                indent=2
            )

        temporary.replace(STATE_FILE)

    except OSError as exc:

        log.error(
            "Could not save state: %s",
            exc
        )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(method, payload):

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/{method}"
    )

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=15
        )

        response.raise_for_status()

        result = response.json()

        if result.get("ok"):
            return result

        log.warning(
            "Telegram API error: %s",
            result
        )

        return None

    except (requests.RequestException, ValueError) as exc:

        log.warning(
            "Telegram request failed: %s",
            exc
        )

        return None


def send_message(message, chat_id=None):

    destination = str(
        chat_id or TELEGRAM_CHAT_ID
    ).strip()

    if not destination:

        log.warning(
            "TELEGRAM_CHAT_ID is missing. "
            "Message was not sent."
        )

        return False

    result = telegram_api(
        "sendMessage",
        {
            "chat_id": destination,
            "text": message,
            "disable_web_page_preview": True,
        }
    )

    return result is not None


def process_telegram_commands(state):

    if not TELEGRAM_BOT_TOKEN:
        return

    result = telegram_api(
        "getUpdates",
        {
            "offset": int(
                state.get("telegram_offset", 0)
            ),
            "timeout": 1,
            "allowed_updates": ["message"],
        }
    )

    if not result:
        return

    for update in result.get("result", []):

        state["telegram_offset"] = max(
            int(state.get("telegram_offset", 0)),
            int(update.get("update_id", 0)) + 1
        )

        message = update.get("message") or {}

        chat = message.get("chat") or {}

        chat_id = str(chat.get("id", ""))

        command = (
            message.get("text") or ""
        ).strip().lower()

        if not chat_id:
            continue

        if command.startswith(("/start", "/help")):

            send_message(
                f"Runner Bot {BOT_VERSION} is running.\n"
                f"Market-cap range: "
                f"${MIN_MCAP_USD:,}-${MAX_MCAP_USD:,}\n"
                "Commands: /start, /help, /status",
                chat_id
            )

        elif command.startswith("/status"):

            send_message(
                f"Runner Bot {BOT_VERSION}\n"
                f"Minimum MC: ${MIN_MCAP_USD:,}\n"
                f"Maximum MC: ${MAX_MCAP_USD:,}\n"
                f"Minimum liquidity: "
                f"${MIN_LIQUIDITY_USD:,}\n"
                f"Minimum core flow proxy: "
                f"${MIN_CORE_FLOW_USD:,}\n"
                f"Scan interval: "
                f"{SCAN_INTERVAL_SECONDS} seconds\n"
                f"Alerts enabled: "
                f"{HEATING_ALERTS_ENABLED}",
                chat_id
            )


# ============================================================
# TOKEN DISCOVERY
# ============================================================

def discover_addresses():

    addresses = set()

    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]

    for endpoint in endpoints:

        data = get_json(
            f"{DEX_BASE}{endpoint}"
        )

        if not isinstance(data, list):
            continue

        for item in data:

            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if address:
                addresses.add(address)

    # Search additional Solana pairs.
    for term in SEARCH_TERMS:

        url = (
            f"{DEX_BASE}/latest/dex/search"
            f"?q={requests.utils.quote(term)}"
        )

        data = get_json(
            url,
            timeout=10
        )

        if not isinstance(data, dict):
            continue

        for pair in data.get("pairs") or []:

            if pair.get("chainId") != "solana":
                continue

            base = pair.get("baseToken") or {}

            address = base.get("address")

            if address:
                addresses.add(address)

    discovered = list(addresses)

    if len(discovered) > MAX_DISCOVERY_TOKENS:
        discovered = discovered[:MAX_DISCOVERY_TOKENS]

    log.info(
        "Discovery found %s token addresses",
        len(discovered)
    )

    return discovered


def fetch_pairs(addresses):

    pairs = []

    for start in range(
        0,
        len(addresses),
        DISCOVERY_BATCH_SIZE
    ):

        batch = addresses[
            start:start + DISCOVERY_BATCH_SIZE
        ]

        if not batch:
            continue

        url = (
            f"{DEX_BASE}/tokens/v1/solana/"
            f"{','.join(batch)}"
        )

        data = get_json(
            url,
            timeout=15
        )

        if isinstance(data, list):

            for pair in data:

                if (
                    isinstance(pair, dict)
                    and pair.get("chainId") == "solana"
                ):

                    pairs.append(pair)

        time.sleep(0.15)

    return pairs


# ============================================================
# PAIR NORMALIZATION
# ============================================================

def get_market_cap(pair):

    market_cap = number(
        pair.get("marketCap")
    )

    if market_cap > 0:
        return market_cap, "MC"

    fdv = number(
        pair.get("fdv")
    )

    if fdv > 0:
        return fdv, "FDV proxy"

    return 0, "unknown"


def normalize_pair(pair):

    base = pair.get("baseToken") or {}

    address = base.get("address")

    if not address:
        return None

    market_cap, cap_source = get_market_cap(pair)

    if market_cap <= 0:
        return None

    liquidity = number(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume5 = number(
        (pair.get("volume") or {}).get("m5")
    )

    txns = (
        (pair.get("txns") or {}).get("m5") or {}
    )

    buys = int(number(txns.get("buys")))
    sells = int(number(txns.get("sells")))

    total_tx = buys + sells

    # This is an estimated pressure proxy, NOT actual net inflow.
    if total_tx > 0:

        flow = volume5 * (
            (buys - sells) / total_tx
        )

    else:

        flow = 0.0

    if market_cap > 0:

        flow_pct = (
            flow / market_cap
        ) * 100

    else:

        flow_pct = 0.0

    if sells > 0:

        buy_sell_ratio = buys / sells

    elif buys > 0:

        buy_sell_ratio = float("inf")

    else:

        buy_sell_ratio = 0.0

    if abs(flow) > 0:

        volume_flow_ratio = (
            volume5 / abs(flow)
        )

    else:

        volume_flow_ratio = 0.0

    created = number(
        pair.get("pairCreatedAt")
    )

    if created > 0:

        pair_age_hours = max(
            0,
            (now() - created / 1000) / 3600
        )

    else:

        pair_age_hours = None

    return {
        "address": address,
        "name": base.get("name") or "Unknown",
        "symbol": base.get("symbol") or "???",
        "dex_id": pair.get("dexId") or "",
        "url": pair.get("url") or (
            f"https://dexscreener.com/solana/{address}"
        ),
        "mcap": market_cap,
        "mcap_source": cap_source,
        "liquidity": liquidity,
        "volume5": volume5,
        "buys": buys,
        "sells": sells,
        "total_tx": total_tx,
        "flow": flow,
        "flow_pct": flow_pct,
        "buy_sell_ratio": buy_sell_ratio,
        "volume_flow_ratio": volume_flow_ratio,
        "age_hours": pair_age_hours,
    }


def best_pair_per_token(pairs):

    best = {}

    for pair in pairs:

        item = normalize_pair(pair)

        if not item:
            continue

        address = item["address"]

        if (
            address not in best
            or item["liquidity"] > best[address]["liquidity"]
        ):

            best[address] = item

    return best


# ============================================================
# CANDIDATE FILTERS
# ============================================================

def is_pumpfun(item):

    dex = str(
        item.get("dex_id", "")
    ).lower()

    url = str(
        item.get("url", "")
    ).lower()

    return (
        "pump" in dex
        or "pump.fun" in url
    )


def signal_strength(item):

    flow = item["flow"]
    flow_pct = item["flow_pct"]

    if (
        flow_pct >= ULTRA_FLOW_PCT
        or flow >= ULTRA_FLOW_USD
    ):

        return "ULTRA"

    if (
        flow_pct >= STRONG_FLOW_PCT
        or flow >= STRONG_FLOW_USD
    ):

        return "STRONG"

    return "NORMAL"


def required_observations(item):

    level = signal_strength(item)

    if level == "ULTRA":
        return ULTRA_PERSISTENCE

    if level == "STRONG":
        return STRONG_PERSISTENCE

    return NORMAL_PERSISTENCE


def evaluate_candidate(item):

    # HARD CAP CHECK — must always run first.
    if not (
        MIN_MCAP_USD
        <= item["mcap"]
        <= MAX_MCAP_USD
    ):

        return False, "Outside hard MC range"

    if item["liquidity"] < MIN_LIQUIDITY_USD:

        return False, "Liquidity below $6K"

    if item["age_hours"] is None:

        return False, "Pair age unavailable"

    if item["age_hours"] > MAX_PAIR_AGE_HOURS:

        return False, "Pair older than 48 hours"

    if item["total_tx"] < 5:

        return False, "Fewer than 5 transactions in 5m"

    if (
        item["volume5"] < PREFILTER_MIN_VOL5
        and abs(item["flow"]) < PREFILTER_MIN_ABS_FLOW
    ):

        return False, "Volume/flow prefilter failed"

    # Early Pump.fun exception.
    # It still cannot bypass the hard market-cap limits.
    early_pump = (
        is_pumpfun(item)
        and item["age_hours"] <= PUMPFUN_EARLY_AGE_HOURS
        and item["volume5"] >= PUMPFUN_EARLY_MIN_VOL5
        and item["flow"] >= PUMPFUN_EARLY_MIN_FLOW
        and item["flow_pct"] >= PUMPFUN_EARLY_MIN_FLOW_MCAP_PCT
    )

    if early_pump:

        return True, "PUMPFUN EARLY"

    if item["flow"] < MIN_CORE_FLOW_USD:

        return False, "Flow proxy below $1.5K"

    if item["flow_pct"] < MIN_FLOW_MCAP_PCT:

        return False, "Flow/MC below 8%"

    if item["buy_sell_ratio"] < MIN_BUY_SELL_RATIO:

        return False, "Buy/sell ratio below 1.5"

    if item["volume_flow_ratio"] < MIN_VOLUME_FLOW_RATIO:

        return False, "Volume/flow ratio below 1.25"

    return True, "CORE FLOW"


# ============================================================
# PERSISTENCE
# ============================================================

def observe_candidate(state, item):

    address = item["address"]

    current = now()

    record = state["pending"].get(address)

    if (
        not record
        or current - number(
            record.get("first_seen"),
            current
        ) > PENDING_EXPIRY_SECONDS
    ):

        record = {
            "first_seen": current,
            "last_seen": current,
            "observations": [],
        }

    record["observations"].append({
        "time": current,
        "mcap": item["mcap"],
        "flow": item["flow"],
        "flow_pct": item["flow_pct"],
        "volume5": item["volume5"],
    })

    record["observations"] = (
        record["observations"][-MAX_HISTORY_PER_TOKEN:]
    )

    record["last_seen"] = current

    state["pending"][address] = record

    count = len(record["observations"])

    required = required_observations(item)

    return count, required


# ============================================================
# ALERT FORMATTING
# ============================================================

def money(value):

    if value == float("inf"):
        return "∞"

    return f"${value:,.0f}"


def format_alert(item, reason, count, required):

    ratio = item["buy_sell_ratio"]

    if ratio == float("inf"):

        ratio_text = "∞"

    else:

        ratio_text = f"{ratio:.2f}"

    if item["age_hours"] is None:

        age_text = "Unknown"

    else:

        age_text = f"{item['age_hours']:.1f} hours"

    return (
        f"🔥 RUNNER CANDIDATE — "
        f"{item['symbol']} ({item['name']})\n"
        f"Engine: {BOT_VERSION}\n"
        f"Reason: {reason}\n"
        f"Strength: {signal_strength(item)}\n\n"
        f"Market cap: {money(item['mcap'])} "
        f"({item['mcap_source']})\n"
        f"Liquidity: {money(item['liquidity'])}\n"
        f"5m volume: {money(item['volume5'])}\n"
        f"5m buys/sells: {item['buys']}/{item['sells']}\n"
        f"Buy/sell ratio: {ratio_text}\n"
        f"Flow proxy: {money(item['flow'])}\n"
        f"Flow/MC: {item['flow_pct']:.1f}%\n"
        f"Volume/|flow|: "
        f"{item['volume_flow_ratio']:.2f}\n"
        f"Pair age: {age_text}\n"
        f"Persistence: {count}/{required}\n\n"
        f"CA: {item['address']}\n"
        f"DEX: {item['url']}\n\n"
        "⚠️ Flow is a buy/sell-imbalance proxy, "
        "not verified net inflow. Check token authorities, "
        "holder concentration, liquidity, and social catalysts."
    )


# ============================================================
# STATE CLEANUP
# ============================================================

def prune_state(state):

    current = now()

    state["pending"] = {
        address: record
        for address, record in state["pending"].items()
        if current - number(record.get("last_seen"))
        <= PENDING_EXPIRY_SECONDS
    }

    state["token_cooldowns"] = {
        address: expiry
        for address, expiry in state["token_cooldowns"].items()
        if number(expiry) > current
    }

    known = state["known_tokens"]

    if len(known) > MAX_TRACKED_TOKENS:

        ordered = sorted(
            known.items(),
            key=lambda entry: number(
                entry[1].get("last_seen")
            ) if isinstance(entry[1], dict) else 0,
            reverse=True,
        )

        state["known_tokens"] = dict(
            ordered[:MAX_TRACKED_TOKENS]
        )


# ============================================================
# SCANNER
# ============================================================

def run_scan(state):

    current = now()

    # Discover new tokens at the configured interval.
    if (
        current - number(state.get("last_discovery"))
        >= DISCOVERY_INTERVAL_SECONDS
        or not state["known_tokens"]
    ):

        discovered = discover_addresses()

        # Merge discoveries instead of losing them between scans.
        for address in discovered:

            state["known_tokens"].setdefault(
                address,
                {}
            )

        state["last_discovery"] = current

    addresses = list(
        state["known_tokens"].keys()
    )[:MAX_TRACKED_TOKENS]

    if not addresses:

        log.info("No token addresses tracked yet.")

        save_state(state)

        return

    pairs = fetch_pairs(addresses)

    candidates = best_pair_per_token(pairs)

    log.info(
        "Fetched %s pairs; evaluating %s tokens",
        len(pairs),
        len(candidates)
    )

    for address, item in candidates.items():

        state["known_tokens"].setdefault(
            address,
            {}
        )

        state["known_tokens"][address]["last_seen"] = current

        state["known_tokens"][address]["symbol"] = (
            item["symbol"]
        )

        # Hard market-cap guard.
        if (
            item["mcap"] < MIN_MCAP_USD
            or item["mcap"] > MAX_MCAP_USD
        ):

            state["pending"].pop(address, None)

            if RUNNER_DEBUG:

                log.debug(
                    "Reject %s: MC %s outside hard range",
                    item["symbol"],
                    money(item["mcap"])
                )

            continue

        qualified, reason = evaluate_candidate(item)

        if not qualified:

            state["pending"].pop(address, None)

            if RUNNER_DEBUG:

                log.debug(
                    "Reject %s: %s",
                    item["symbol"],
                    reason
                )

            continue

        count, required = observe_candidate(
            state,
            item
        )

        if count < required:

            log.debug(
                "Pending %s: %s/%s observations",
                item["symbol"],
                count,
                required
            )

            continue

        # Final hard cap check before alerting.
        if not (
            MIN_MCAP_USD
            <= item["mcap"]
            <= MAX_MCAP_USD
        ):

            state["pending"].pop(address, None)

            continue

        if not HEATING_ALERTS_ENABLED:

            log.info(
                "Qualified but alerts disabled: %s",
                item["symbol"]
            )

            continue

        if number(
            state["token_cooldowns"].get(address)
        ) > current:

            continue

        if (
            current - number(
                state.get("last_global_alert")
            ) < GLOBAL_ALERT_COOLDOWN_SECONDS
        ):

            continue

        alert = format_alert(
            item,
            reason,
            count,
            required
        )

        if send_message(alert):

            state["token_cooldowns"][address] = (
                current + TOKEN_COOLDOWN_SECONDS
            )

            state["last_global_alert"] = current

            state["pending"].pop(address, None)

            log.info(
                "Alert sent: %s",
                item["symbol"]
            )

        else:

            log.warning(
                "Alert not delivered for %s. "
                "Check Telegram settings.",
                item["symbol"]
            )

    prune_state(state)

    save_state(state)


# ============================================================
# MAIN
# ============================================================

def main():

    if not TELEGRAM_BOT_TOKEN:

        raise SystemExit(
            "ERROR: TELEGRAM_BOT_TOKEN is missing. "
            "Open Replit Secrets and add your Telegram bot token."
        )

    if not TELEGRAM_CHAT_ID:

        log.warning(
            "TELEGRAM_CHAT_ID is missing. "
            "The scanner can run, but fixed-chat alerts "
            "cannot be delivered."
        )

    log.info(
        "Starting %s | MC $%s-$%s | Scan %ss | Discovery %ss",
        BOT_VERSION,
        f"{MIN_MCAP_USD:,}",
        f"{MAX_MCAP_USD:,}",
        SCAN_INTERVAL_SECONDS,
        DISCOVERY_INTERVAL_SECONDS
    )

    send_message(
        f"✅ Runner Bot {BOT_VERSION} started.\n"
        f"Hard market-cap range: "
        f"${MIN_MCAP_USD:,}-${MAX_MCAP_USD:,}\n"
        f"Minimum liquidity: ${MIN_LIQUIDITY_USD:,}\n"
        f"Scan interval: {SCAN_INTERVAL_SECONDS} seconds."
    )

    state = load_state()

    while True:

        started = now()

        try:

            process_telegram_commands(state)

            run_scan(state)

            save_state(state)

        except KeyboardInterrupt:

            log.info("Bot stopped by user.")

            save_state(state)

            break

        except Exception:

            log.exception(
                "Unexpected error. "
                "Bot will retry after the next interval."
            )

        elapsed = now() - started

        time.sleep(
            max(
                1,
                SCAN_INTERVAL_SECONDS - elapsed
            )
        )


if __name__ == "__main__":
    main()
