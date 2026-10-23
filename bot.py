"""
RUNNER BOT V6.1
STRICT REPORT-MATCHING ENGINE

Purpose:
Discover Solana tokens through DEX Screener, track their market
metrics, and alert Telegram when every mandatory condition passes.

No social-media analysis.
No wallet analysis.
No token-age filter.
No synthetic net-flow calculation.
No score override.

IMPORTANT:
This bot requires a continuously running Python process for
15-second scanning. GitHub Actions scheduled workflows do not
guarantee continuous 15-second execution.
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timezone
from collections import defaultdict

import requests


# ============================================================
# CONFIGURATION
# ============================================================

BOT_VERSION = "V6.1-STRICT-REPORT-MATCHING"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SCAN_INTERVAL_SECONDS = 15

STATE_FILE = os.getenv(
    "RUNNER_STATE_FILE",
    "runner_state_v61.json"
)

DEX_BASE_URL = "https://api.dexscreener.com"

TELEGRAM_BASE_URL = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if TELEGRAM_BOT_TOKEN
    else ""
)

# Maximum number of tokens remembered.
MAX_TRACKED_TOKENS = 1500

# Keep recent observations for each token.
MAX_HISTORY_PER_TOKEN = 100

# Discovery batch size for DEX Screener token lookups.
TOKEN_BATCH_SIZE = 30

# Prevent repeatedly alerting the same token.
ALERT_COOLDOWN_SECONDS = 60 * 60

# Avoid Telegram messages that are too long.
MAX_TELEGRAM_MESSAGE_LENGTH = 4000

# Logging.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("runner_bot")


# ============================================================
# STRICT MARKET FILTERS
# ============================================================
#
# These thresholds come from the observed ranges in the
# 11 successful reports supplied by the user.
#
# A candidate MUST pass EVERY condition below.
# A score cannot override a failed condition.
#
# These are historical matching thresholds, not guarantees
# that another token will become a successful runner.
# ============================================================

MIN_MARKET_CAP = 40_000
MAX_MARKET_CAP = 150_000

MIN_LIQUIDITY = 16_500
MAX_LIQUIDITY = 40_000

MIN_BUY_SELL_RATIO = 1.22

MIN_VOLUME_TO_MC_PERCENT = 46.06

MIN_5M_TRANSACTIONS = 406

MIN_5M_PRICE_CHANGE_PERCENT = 6.08

MIN_OBSERVATIONS = 3


# ============================================================
# DEX SCREENER DISCOVERY
# ============================================================

DISCOVERY_SEARCH_TERMS = [
    "SOL",
    "WSOL",
    "USDC",
    "USDT",
    "pump",
    "meme",
    "cat",
    "dog",
    "ai",
    "inu",
    "coin",
]

DISCOVERY_REFRESH_SECONDS = 60

# DEX Screener API requests use a timeout to prevent hanging.
HTTP_TIMEOUT_SECONDS = 12


# ============================================================
# SESSION AND SHARED STATE
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": f"RunnerBot/{BOT_VERSION}",
    "Accept": "application/json",
})

state_lock = threading.RLock()

state = {
    "version": BOT_VERSION,
    "tokens": {},
    "alerts_sent": {},
    "telegram_offset": 0,
    "last_discovery": 0,
    "last_scan": 0,
    "total_alerts": 0,
    "started_at": datetime.now(timezone.utc).isoformat(),
}

last_discovery_pairs = {}

bot_running = True


# ============================================================
# GENERAL HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        result = float(value)

        if result != result:
            return default

        if result == float("inf") or result == float("-inf"):
            return default

        return result

    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def format_money(value):
    value = safe_float(value)

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.2f}"


def format_percent(value):
    return f"{safe_float(value):.2f}%"


def format_ratio(value):
    if value == float("inf"):
        return "Infinite"

    return f"{safe_float(value):.2f}"


def shorten_address(address, first=6, last=6):
    if not address:
        return "Unknown"

    address = str(address)

    if len(address) <= first + last + 3:
        return address

    return f"{address[:first]}...{address[-last:]}"


def get_json(url, params=None):
    try:
        response = session.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT_SECONDS,
        )

        if response.status_code != 200:
            logger.warning(
                "HTTP %s for %s",
                response.status_code,
                url,
            )
            return None

        return response.json()

    except requests.RequestException as exc:
        logger.warning("Request failed: %s", exc)

    except ValueError:
        logger.warning("Invalid JSON response from %s", url)

    return None


# ============================================================
# PERSISTENT STATE
# ============================================================

def load_state():
    global state

    if not os.path.exists(STATE_FILE):
        logger.info("No saved state found. Starting fresh.")
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            saved = json.load(file)

        if not isinstance(saved, dict):
            logger.warning("Invalid state format. Starting fresh.")
            return

        with state_lock:
            state["tokens"] = saved.get("tokens", {})
            state["alerts_sent"] = saved.get("alerts_sent", {})
            state["telegram_offset"] = safe_int(
                saved.get("telegram_offset", 0)
            )
            state["last_discovery"] = safe_float(
                saved.get("last_discovery", 0)
            )
            state["last_scan"] = safe_float(
                saved.get("last_scan", 0)
            )
            state["total_alerts"] = safe_int(
                saved.get("total_alerts", 0)
            )
            state["started_at"] = saved.get(
                "started_at",
                utc_now(),
            )

        logger.info(
            "Loaded state: %s tracked tokens, %s previous alerts",
            len(state["tokens"]),
            len(state["alerts_sent"]),
        )

    except (OSError, ValueError, TypeError) as exc:
        logger.error("Could not load state: %s", exc)


def save_state():
    try:
        with state_lock:
            snapshot = {
                "version": BOT_VERSION,
                "tokens": state["tokens"],
                "alerts_sent": state["alerts_sent"],
                "telegram_offset": state["telegram_offset"],
                "last_discovery": state["last_discovery"],
                "last_scan": state["last_scan"],
                "total_alerts": state["total_alerts"],
                "started_at": state["started_at"],
            }

        temporary_file = STATE_FILE + ".tmp"

        with open(
            temporary_file,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(snapshot, file, indent=2)

        os.replace(temporary_file, STATE_FILE)

    except (OSError, TypeError, ValueError) as exc:
        logger.error("Could not save state: %s", exc)


# ============================================================
# TELEGRAM MESSAGING
# ============================================================

def telegram_api(method, payload=None, timeout=15):
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not configured.")
        return None

    url = f"{TELEGRAM_BASE_URL}/{method}"

    try:
        response = session.post(
            url,
            json=payload or {},
            timeout=timeout,
        )

        result = response.json()

        if not result.get("ok"):
            logger.warning(
                "Telegram API error: %s",
                result,
            )
            return None

        return result.get("result")

    except requests.RequestException as exc:
        logger.warning("Telegram request failed: %s", exc)

    except ValueError:
        logger.warning("Telegram returned invalid JSON.")

    return None


def send_telegram_message(
    message,
    chat_id=None,
    disable_preview=True,
):
    target_chat = str(chat_id or TELEGRAM_CHAT_ID).strip()

    if not target_chat:
        logger.warning(
            "No Telegram chat ID available. Message not sent."
        )
        return False

    # Telegram limits ordinary message text to 4096 characters.
    message = str(message)[:MAX_TELEGRAM_MESSAGE_LENGTH]

    result = telegram_api(
        "sendMessage",
        {
            "chat_id": target_chat,
            "text": message,
            "disable_web_page_preview": disable_preview,
        },
    )

    return result is not None


# ============================================================
# DEX SCREENER DATA COLLECTION
# ============================================================

def discover_token_addresses():
    """
    Discover Solana token addresses from DEX Screener's latest
    token profiles, boosts and search endpoints.
    """

    addresses = set()

    # Latest token profiles.
    profiles = get_json(
        f"{DEX_BASE_URL}/token-profiles/latest/v1"
    )

    if isinstance(profiles, list):
        for item in profiles:
            if not isinstance(item, dict):
                continue

            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if address:
                addresses.add(address)

    # Latest boosted tokens.
    latest_boosts = get_json(
        f"{DEX_BASE_URL}/token-boosts/latest/v1"
    )

    if isinstance(latest_boosts, list):
        for item in latest_boosts:
            if not isinstance(item, dict):
                continue

            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if address:
                addresses.add(address)

    # Top boosted tokens.
    top_boosts = get_json(
        f"{DEX_BASE_URL}/token-boosts/top/v1"
    )

    if isinstance(top_boosts, list):
        for item in top_boosts:
            if not isinstance(item, dict):
                continue

            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if address:
                addresses.add(address)

    # Search multiple terms to expand discovery.
    for term in DISCOVERY_SEARCH_TERMS:
        result = get_json(
            f"{DEX_BASE_URL}/latest/dex/search",
            params={"q": term},
        )

        if not isinstance(result, dict):
            continue

        pairs = result.get("pairs", [])

        if not isinstance(pairs, list):
            continue

        for pair in pairs:
            if not isinstance(pair, dict):
                continue

            if pair.get("chainId") != "solana":
                continue

            base_token = pair.get("baseToken") or {}
            quote_token = pair.get("quoteToken") or {}

            base_address = base_token.get("address")
            quote_address = quote_token.get("address")

            if base_address:
                addresses.add(base_address)

            # Quote tokens are useful discovery inputs too.
            if quote_address:
                addresses.add(quote_address)

    logger.info(
        "Discovery collected %s unique Solana token addresses.",
        len(addresses),
    )

    return list(addresses)


def fetch_token_pairs(addresses):
    """
    Fetch token pairs in batches and keep the best-liquidity
    Solana pair for each base token.

    The liquidity selection helps avoid tracking a token through
    a very small secondary pool when a larger pool is available.
    """

    best_pairs = {}

    for start in range(0, len(addresses), TOKEN_BATCH_SIZE):
        batch = addresses[
            start:start + TOKEN_BATCH_SIZE
        ]

        if not batch:
            continue

        joined_addresses = ",".join(batch)

        result = get_json(
            f"{DEX_BASE_URL}/latest/dex/tokens/{joined_addresses}"
        )

        if not isinstance(result, dict):
            continue

        pairs = result.get("pairs", [])

        if not isinstance(pairs, list):
            continue

        for pair in pairs:
            if not isinstance(pair, dict):
                continue

            if pair.get("chainId") != "solana":
                continue

            base_token = pair.get("baseToken") or {}
            token_address = base_token.get("address")

            if not token_address:
                continue

            liquidity = safe_float(
                (pair.get("liquidity") or {}).get("usd")
            )

            existing = best_pairs.get(token_address)

            if existing is None:
                best_pairs[token_address] = pair
                continue

            existing_liquidity = safe_float(
                (existing.get("liquidity") or {}).get("usd")
            )

            if liquidity > existing_liquidity:
                best_pairs[token_address] = pair

    return best_pairs


# ============================================================
# MARKET METRIC EXTRACTION
# ============================================================

def extract_metrics(pair):
    """
    Extract real market fields supplied by DEX Screener.

    Volume-to-market-cap is calculated as:
        5-minute volume / market cap * 100

    Buy/sell ratio is calculated as:
        5-minute buys / 5-minute sells

    Neither value is treated as a net-flow measurement.
    """

    base = pair.get("baseToken") or {}
    liquidity_data = pair.get("liquidity") or {}
    volume_data = pair.get("volume") or {}
    price_change_data = pair.get("priceChange") or {}
    txns_data = pair.get("txns") or {}
    txns_5m = txns_data.get("m5") or {}

    # Use marketCap when supplied.
    # Fall back to FDV only if marketCap is missing.
    market_cap_value = pair.get("marketCap")

    if market_cap_value is None:
        market_cap_value = pair.get("fdv")

    market_cap = safe_float(market_cap_value)

    liquidity = safe_float(
        liquidity_data.get("usd")
    )

    volume_5m = safe_float(
        volume_data.get("m5")
    )

    price_change_5m = safe_float(
        price_change_data.get("m5")
    )

    buys_5m = safe_int(
        txns_5m.get("buys")
    )

    sells_5m = safe_int(
        txns_5m.get("sells")
    )

    transactions_5m = buys_5m + sells_5m

    if sells_5m > 0:
        buy_sell_ratio = buys_5m / sells_5m
    elif buys_5m > 0:
        buy_sell_ratio = float("inf")
    else:
        buy_sell_ratio = 0.0

    if market_cap > 0:
        volume_to_mc_percent = (
            volume_5m / market_cap
        ) * 100
    else:
        volume_to_mc_percent = 0.0

    return {
        "address": base.get("address", ""),
        "name": base.get("name", "Unknown"),
        "symbol": base.get("symbol", "UNKNOWN"),
        "market_cap": market_cap,
        "liquidity": liquidity,
        "volume_5m": volume_5m,
        "price_change_5m": price_change_5m,
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
        "transactions_5m": transactions_5m,
        "buy_sell_ratio": buy_sell_ratio,
        "volume_to_mc_percent": volume_to_mc_percent,
        "pair_address": pair.get("pairAddress", ""),
        "dex_id": pair.get("dexId", "Unknown"),
        "url": pair.get("url", ""),
        "price_usd": safe_float(pair.get("priceUsd")),
        "observed_at": utc_now(),
    }


# ============================================================
# OBSERVATION AND HISTORY TRACKING
# ============================================================

def update_token_history(metrics):
    """
    Track each token across scans.

    Observation count increases when a token has a usable
    market-cap and liquidity reading.

    History is saved so it can survive process restarts when
    the environment preserves the state file.
    """

    address = metrics["address"]

    if not address:
        return 0

    now = time.time()

    with state_lock:
        token_data = state["tokens"].get(address)

        if not isinstance(token_data, dict):
            token_data = {
                "first_seen": utc_now(),
                "observations": 0,
                "history": [],
                "last_metrics": {},
            }

        token_data["observations"] = safe_int(
            token_data.get("observations", 0)
        ) + 1

        token_data["last_seen"] = utc_now()
        token_data["last_metrics"] = metrics

        history = token_data.get("history", [])

        history.append({
            "timestamp": now,
            "market_cap": metrics["market_cap"],
            "liquidity": metrics["liquidity"],
            "volume_5m": metrics["volume_5m"],
            "volume_to_mc_percent": (
                metrics["volume_to_mc_percent"]
            ),
            "price_change_5m": metrics["price_change_5m"],
            "buy_sell_ratio": (
                metrics["buy_sell_ratio"]
                if metrics["buy_sell_ratio"] != float("inf")
                else 999999
            ),
            "transactions_5m": metrics["transactions_5m"],
            "buys_5m": metrics["buys_5m"],
            "sells_5m": metrics["sells_5m"],
        })

        token_data["history"] = history[
            -MAX_HISTORY_PER_TOKEN:
        ]

        state["tokens"][address] = token_data

        # Limit stored token count.
        if len(state["tokens"]) > MAX_TRACKED_TOKENS:
            ordered = sorted(
                state["tokens"].items(),
                key=lambda item: item[1].get(
                    "last_seen", ""
                ),
            )

            excess = (
                len(state["tokens"]) - MAX_TRACKED_TOKENS
            )

            for old_address, _ in ordered[:excess]:
                state["tokens"].pop(old_address, None)

        observations = token_data["observations"]

    return observations


# ============================================================
# STRICT QUALIFICATION ENGINE
# ============================================================

def evaluate_candidate(metrics, observations):
    """
    All mandatory gates must pass.

    No score can override a failed market condition.
    """

    failures = []

    market_cap = metrics["market_cap"]
    liquidity = metrics["liquidity"]
    buy_sell_ratio = metrics["buy_sell_ratio"]
    volume_to_mc = metrics["volume_to_mc_percent"]
    transactions = metrics["transactions_5m"]
    price_change = metrics["price_change_5m"]

    # Gate 1: Market cap.
    if not (
        MIN_MARKET_CAP
        <= market_cap
        <= MAX_MARKET_CAP
    ):
        failures.append(
            f"MC outside ${MIN_MARKET_CAP:,}–"
            f"${MAX_MARKET_CAP:,}"
        )

    # Gate 2: Liquidity.
    if not (
        MIN_LIQUIDITY
        <= liquidity
        <= MAX_LIQUIDITY
    ):
        failures.append(
            f"Liquidity outside ${MIN_LIQUIDITY:,}–"
            f"${MAX_LIQUIDITY:,}"
        )

    # Gate 3: Buy/sell ratio.
    if buy_sell_ratio < MIN_BUY_SELL_RATIO:
        failures.append(
            f"B/S below {MIN_BUY_SELL_RATIO:.2f}"
        )

    # Gate 4: Five-minute volume / market cap.
    if volume_to_mc < MIN_VOLUME_TO_MC_PERCENT:
        failures.append(
            f"Vol/MC below {MIN_VOLUME_TO_MC_PERCENT:.2f}%"
        )

    # Gate 5: Five-minute transaction count.
    if transactions < MIN_5M_TRANSACTIONS:
        failures.append(
            f"5m transactions below {MIN_5M_TRANSACTIONS}"
        )

    # Gate 6: Five-minute price change.
    if price_change < MIN_5M_PRICE_CHANGE_PERCENT:
        failures.append(
            f"5m price change below "
            f"+{MIN_5M_PRICE_CHANGE_PERCENT:.2f}%"
        )

    # Gate 7: Minimum number of observations.
    if observations < MIN_OBSERVATIONS:
        failures.append(
            f"Only {observations}/{MIN_OBSERVATIONS} observations"
        )

    return {
        "qualified": len(failures) == 0,
        "failures": failures,
    }


# ============================================================
# OPTIONAL RANKING SCORE
# ============================================================
#
# This score is ONLY used to describe already-qualified tokens.
# It cannot make an unqualified token eligible for an alert.
# ============================================================

def calculate_ranking_score(metrics):
    score = 0

    market_cap = metrics["market_cap"]
    liquidity = metrics["liquidity"]
    buy_sell_ratio = metrics["buy_sell_ratio"]
    volume_to_mc = metrics["volume_to_mc_percent"]
    transactions = metrics["transactions_5m"]
    price_change = metrics["price_change_5m"]

    # Buy/sell strength.
    if buy_sell_ratio >= 1.50:
        score += 20
    elif buy_sell_ratio >= 1.35:
        score += 15
    elif buy_sell_ratio >= 1.22:
        score += 10

    # Relative volume.
    if volume_to_mc >= 100:
        score += 25
    elif volume_to_mc >= 80:
        score += 20
    elif volume_to_mc >= 60:
        score += 15
    else:
        score += 10

    # Transaction activity.
    if transactions >= 1500:
        score += 20
    elif transactions >= 1000:
        score += 15
    elif transactions >= 600:
        score += 12
    else:
        score += 8

    # Price movement.
    if price_change >= 50:
        score += 20
    elif price_change >= 25:
        score += 15
    elif price_change >= 10:
        score += 10
    else:
        score += 5

    # Liquidity relative to the permitted band.
    if liquidity >= 20_000:
        score += 5

    # Market cap remains bounded by the mandatory gates.
    if 50_000 <= market_cap <= 100_000:
        score += 5

    return min(score, 100)


# ============================================================
# RUNNER ALERT
# ============================================================

def build_runner_alert(metrics, observations, score):
    name = metrics["name"]
    symbol = metrics["symbol"]
    address = metrics["address"]

    market_cap = metrics["market_cap"]
    liquidity = metrics["liquidity"]
    volume_5m = metrics["volume_5m"]
    volume_to_mc = metrics["volume_to_mc_percent"]
    price_change = metrics["price_change_5m"]
    buy_sell_ratio = metrics["buy_sell_ratio"]
    transactions = metrics["transactions_5m"]

    pair_url = metrics["url"]

    if not pair_url and metrics["pair_address"]:
        pair_url = (
            "https://dexscreener.com/solana/"
            + metrics["pair_address"]
        )

    message = (
        "🚨 RUNNER CANDIDATE — V6.1\n"
        "\n"
        f"Token: {name} (${symbol})\n"
        f"Market Cap: {format_money(market_cap)}\n"
        f"Liquidity: {format_money(liquidity)}\n"
        f"5m Price Change: {format_percent(price_change)}\n"
        f"5m Volume: {format_money(volume_5m)}\n"
        f"5m Vol/MC: {format_percent(volume_to_mc)}\n"
        f"5m Buys: {metrics['buys_5m']}\n"
        f"5m Sells: {metrics['sells_5m']}\n"
        f"5m Buy/Sell Ratio: {format_ratio(buy_sell_ratio)}\n"
        f"5m Transactions: {transactions}\n"
        f"Observations: {observations}\n"
        f"Ranking Score: {score}/100\n"
        "\n"
        f"CA:\n{address}\n"
        "\n"
        f"DEX: {metrics['dex_id']}\n"
    )

    if pair_url:
        message += f"\nChart: {pair_url}\n"

    message += (
        "\n"
        "Qualification: All V6.1 hard gates passed.\n"
        "\n"
        "⚠️ A qualifying signal is not a guarantee of a runner. "
        "Verify the contract, liquidity, holders and execution "
        "risk before trading."
    )

    return message


def token_is_in_cooldown(address):
    last_alert = safe_float(
        state["alerts_sent"].get(address, 0)
    )

    return (
        time.time() - last_alert
        < ALERT_COOLDOWN_SECONDS
    )


def send_runner_alert(metrics, observations):
    address = metrics["address"]

    if not address:
        return False

    with state_lock:
        if token_is_in_cooldown(address):
            logger.info(
                "Qualified token still in alert cooldown: %s",
                metrics["symbol"],
            )
            return False

    score = calculate_ranking_score(metrics)

    message = build_runner_alert(
        metrics,
        observations,
        score,
    )

    sent = send_telegram_message(message)

    if sent:
        with state_lock:
            state["alerts_sent"][address] = time.time()
            state["total_alerts"] += 1

        save_state()

        logger.info(
            "RUNNER ALERT SENT: %s (%s), score=%s",
            metrics["symbol"],
            address,
            score,
        )

        return True

    logger.warning(
        "Qualified token, but Telegram alert failed: %s",
        metrics["symbol"],
    )

    return False


# ============================================================
# SCANNING ENGINE
# ============================================================

def scan_market():
    global last_discovery_pairs

    now = time.time()

    # Refresh discovery periodically.
    discovery_due = (
        now - safe_float(state.get("last_discovery", 0))
        >= DISCOVERY_REFRESH_SECONDS
        or not last_discovery_pairs
    )

    if discovery_due:
        logger.info("Refreshing DEX Screener discovery.")

        addresses = discover_token_addresses()

        if addresses:
            discovered_pairs = fetch_token_pairs(addresses)

            if discovered_pairs:
                last_discovery_pairs = discovered_pairs

        state["last_discovery"] = now

        logger.info(
            "Discovery currently holds %s token pairs.",
            len(last_discovery_pairs),
        )

    if not last_discovery_pairs:
        logger.warning("No token pairs available to scan.")
        return

    # Refresh metrics for discovered token addresses.
    addresses = list(last_discovery_pairs.keys())

    refreshed_pairs = fetch_token_pairs(addresses)

    if refreshed_pairs:
        last_discovery_pairs.update(refreshed_pairs)

    pair_count = 0
    qualified_count = 0
    alert_count = 0

    for address, pair in list(last_discovery_pairs.items()):
        try:
            if not isinstance(pair, dict):
                continue

            metrics = extract_metrics(pair)

            if not metrics["address"]:
                continue

            # Count observations only when MC and liquidity
            # are available and positive.
            if (
                metrics["market_cap"] <= 0
                or metrics["liquidity"] <= 0
            ):
                continue

            observations = update_token_history(metrics)

            pair_count += 1

            result = evaluate_candidate(
                metrics,
                observations,
            )

            if not result["qualified"]:
                logger.debug(
                    "REJECTED %s (%s): %s",
                    metrics["symbol"],
                    address,
                    "; ".join(result["failures"]),
                )
                continue

            qualified_count += 1

            logger.info(
                "QUALIFIED %s | MC=%s | LIQ=%s | "
                "B/S=%s | Vol/MC=%s | TX=%s | "
                "Change=%s | Obs=%s",
                metrics["symbol"],
                format_money(metrics["market_cap"]),
                format_money(metrics["liquidity"]),
                format_ratio(metrics["buy_sell_ratio"]),
                format_percent(metrics["volume_to_mc_percent"]),
                metrics["transactions_5m"],
                format_percent(metrics["price_change_5m"]),
                observations,
            )

            if send_runner_alert(metrics, observations):
                alert_count += 1

        except Exception:
            logger.exception(
                "Error processing token %s",
                address,
            )

    state["last_scan"] = time.time()

    save_state()

    logger.info(
        "SCAN COMPLETE | Pairs=%s | Qualified=%s | "
        "Alerts sent=%s",
        pair_count,
        qualified_count,
        alert_count,
    )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def build_status_message():
    with state_lock:
        token_count = len(state["tokens"])
        alert_count = safe_int(state["total_alerts"])
        discovered_count = len(last_discovery_pairs)

    return (
        f"🤖 Runner Bot {BOT_VERSION}\n"
        "\n"
        f"Status: Running\n"
        f"Scan interval: {SCAN_INTERVAL_SECONDS}s\n"
        f"Tracked tokens: {token_count}\n"
        f"Currently discovered pairs: {discovered_count}\n"
        f"Total alerts sent: {alert_count}\n"
        "\n"
        "STRICT QUALIFICATION FILTERS\n"
        f"Market cap: ${MIN_MARKET_CAP:,}–${MAX_MARKET_CAP:,}\n"
        f"Liquidity: ${MIN_LIQUIDITY:,}–${MAX_LIQUIDITY:,}\n"
        f"Buy/sell ratio: >= {MIN_BUY_SELL_RATIO:.2f}\n"
        f"5m Vol/MC: >= {MIN_VOLUME_TO_MC_PERCENT:.2f}%\n"
        f"5m transactions: >= {MIN_5M_TRANSACTIONS}\n"
        f"5m price change: >= +{MIN_5M_PRICE_CHANGE_PERCENT:.2f}%\n"
        f"Observations: >= {MIN_OBSERVATIONS}\n"
        "\n"
        "All filters are mandatory. Score cannot override them."
    )


def clear_tracking(chat_id):
    """
    Clear tracked observations and alert cooldown history.

    This resets bot history, not token data on DEX Screener.
    """

    with state_lock:
        state["tokens"] = {}
        state["alerts_sent"] = {}
        state["total_alerts"] = 0

    save_state()

    send_telegram_message(
        "🧹 Runner Bot tracking history cleared.\n"
        "The bot will rebuild observations from new scans.",
        chat_id=chat_id,
    )


def handle_telegram_command(message):
    if not isinstance(message, dict):
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    text_value = str(message.get("text", "")).strip()

    if not text_value.startswith("/"):
        return

    command = text_value.split()[0].split("@")[0].lower()

    if command == "/start":
        response = (
            f"✅ Runner Bot {BOT_VERSION} is online.\n"
            "\n"
            "I scan Solana tokens using DEX Screener and "
            "only alert when all mandatory V6.1 market "
            "conditions pass.\n"
            "\n"
            "Commands:\n"
            "/start — Start information\n"
            "/status — Bot status and filters\n"
            "/clear — Clear tracking history"
        )

        send_telegram_message(
            response,
            chat_id=chat_id,
        )

    elif command == "/status":
        send_telegram_message(
            build_status_message(),
            chat_id=chat_id,
        )

    elif command == "/clear":
        clear_tracking(chat_id)

    else:
        send_telegram_message(
            "Unknown command.\n"
            "Available commands: /start, /status, /clear",
            chat_id=chat_id,
        )


def telegram_polling_loop():
    """
    Long-poll Telegram for commands.

    If TELEGRAM_CHAT_ID is configured, commands are accepted
    only from that configured chat ID.
    """

    if not TELEGRAM_BOT_TOKEN:
        logger.error(
            "Telegram polling disabled: missing bot token."
        )
        return

    logger.info("Telegram command polling started.")

    while bot_running:
        try:
            with state_lock:
                offset = safe_int(
                    state.get("telegram_offset", 0)
                )

            updates = telegram_api(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 20,
                    "allowed_updates": ["message"],
                },
                timeout=25,
            )

            if not isinstance(updates, list):
                time.sleep(3)
                continue

            for update in updates:
                if not isinstance(update, dict):
                    continue

                update_id = safe_int(
                    update.get("update_id", 0)
                )

                with state_lock:
                    state["telegram_offset"] = update_id + 1

                message = update.get("message") or {}
                chat = message.get("chat") or {}
                incoming_chat_id = str(chat.get("id", ""))

                # If a target chat ID is configured, reject
                # commands from other chats.
                if (
                    TELEGRAM_CHAT_ID
                    and incoming_chat_id != TELEGRAM_CHAT_ID
                ):
                    logger.warning(
                        "Ignoring command from an unconfigured chat."
                    )
                    continue

                handle_telegram_command(message)

            save_state()

        except Exception:
            logger.exception("Telegram polling loop error.")
            time.sleep(5)


# ============================================================
# STARTUP
# ============================================================

def validate_configuration():
    if not TELEGRAM_BOT_TOKEN:
        logger.warning(
            "TELEGRAM_BOT_TOKEN is missing. "
            "Market scanning may run, but Telegram messages "
            "cannot be delivered."
        )

    if not TELEGRAM_CHAT_ID:
        logger.warning(
            "TELEGRAM_CHAT_ID is missing. "
            "Set it if you want runner alerts delivered to "
            "a specific Telegram chat."
        )


def main():
    global bot_running

    logger.info("=" * 60)
    logger.info("Starting Runner Bot %s", BOT_VERSION)
    logger.info("Scan interval: %s seconds", SCAN_INTERVAL_SECONDS)
    logger.info("Strict matching enabled.")
    logger.info("=" * 60)

    validate_configuration()
    load_state()

    # Start Telegram command handling separately so market
    # scanning does not block while waiting for Telegram.
    telegram_thread = threading.Thread(
        target=telegram_polling_loop,
        daemon=True,
        name="telegram-polling",
    )

    telegram_thread.start()

    # Send startup message when a destination is configured.
    if TELEGRAM_CHAT_ID and TELEGRAM_BOT_TOKEN:
        send_telegram_message(
            f"🟢 Runner Bot {BOT_VERSION} started.\n"
            f"Scanning every {SCAN_INTERVAL_SECONDS} seconds.\n"
            "All V6.1 hard filters are mandatory."
        )

    try:
        while bot_running:
            cycle_started = time.time()

            try:
                scan_market()

            except Exception:
                logger.exception("Market scan failed.")

            elapsed = time.time() - cycle_started

            sleep_seconds = max(
                1,
                SCAN_INTERVAL_SECONDS - elapsed,
            )

            time.sleep(sleep_seconds)

    except KeyboardInterrupt:
        logger.info("Shutdown requested.")

    finally:
        bot_running = False
        save_state()
        logger.info("Runner Bot stopped.")


if __name__ == "__main__":
    main()
