import os
import json
import time
import logging
import threading
from datetime import datetime, timezone

import requests


# ============================================================
# RUNNER BOT V6.2
# STRICT FILTERS + AUTOMATIC TELEGRAM CHAT REGISTRATION
# ============================================================

BOT_VERSION = "V6.2-TELEGRAM-AUTO-REGISTER"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SCAN_INTERVAL_SECONDS = 15
DISCOVERY_REFRESH_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 12
TOKEN_BATCH_SIZE = 30
MAX_TRACKED_TOKENS = 1500
MAX_HISTORY_PER_TOKEN = 100
ALERT_COOLDOWN_SECONDS = 3600

STATE_FILE = os.getenv(
    "RUNNER_STATE_FILE",
    "runner_state_v61.json",
)

REGISTERED_CHAT_FILE = os.getenv(
    "REGISTERED_CHAT_FILE",
    "runner_chat.json",
)

DEX_BASE_URL = "https://api.dexscreener.com"

TELEGRAM_BASE_URL = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if TELEGRAM_BOT_TOKEN
    else ""
)

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

# Strict qualification thresholds from your 11 reports.
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
# LOGGING AND SESSION
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("runner_bot")

session = requests.Session()

session.headers.update({
    "User-Agent": f"RunnerBot/{BOT_VERSION}",
    "Accept": "application/json",
})

state_lock = threading.RLock()

state = {
    "tokens": {},
    "alerts_sent": {},
    "telegram_offset": 0,
    "last_discovery": 0,
    "total_alerts": 0,
    "started_at": datetime.now(timezone.utc).isoformat(),
}

last_discovery_pairs = {}
bot_running = True


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_float(value, default=0.0):
    try:
        result = float(value)

        if result != result or result in (
            float("inf"),
            float("-inf"),
        ):
            return default

        return result

    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def money(value):
    value = safe_float(value)

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.2f}"


def pct(value):
    return f"{safe_float(value):.2f}%"


def ratio_text(value):
    if value == float("inf"):
        return "Infinite"

    return f"{safe_float(value):.2f}"


def get_json(url, params=None):
    try:
        response = session.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT_SECONDS,
        )

        if response.status_code != 200:
            logger.warning(
                "HTTP %s from %s",
                response.status_code,
                url,
            )
            return None

        return response.json()

    except (requests.RequestException, ValueError) as exc:
        logger.warning("Request failed: %s", exc)
        return None


# ============================================================
# STATE PERSISTENCE
# ============================================================

def save_json_file(path, data):
    temporary = path + ".tmp"

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)

    os.replace(temporary, path)


def load_state():
    if not os.path.exists(STATE_FILE):
        logger.info("No saved state found. Starting fresh.")
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            saved = json.load(file)

        if not isinstance(saved, dict):
            return

        state["tokens"] = saved.get("tokens", {})
        state["alerts_sent"] = saved.get("alerts_sent", {})
        state["telegram_offset"] = safe_int(
            saved.get("telegram_offset", 0)
        )
        state["last_discovery"] = safe_float(
            saved.get("last_discovery", 0)
        )
        state["total_alerts"] = safe_int(
            saved.get("total_alerts", 0)
        )
        state["started_at"] = saved.get(
            "started_at",
            utc_now(),
        )

        logger.info(
            "Loaded %s tracked tokens.",
            len(state["tokens"]),
        )

    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Could not load state: %s", exc)


def save_state():
    try:
        save_json_file(STATE_FILE, state)
    except (OSError, TypeError, ValueError) as exc:
        logger.error("Could not save state: %s", exc)


# ============================================================
# TELEGRAM CHAT REGISTRATION
# ============================================================

def load_registered_chat():
    global TELEGRAM_CHAT_ID

    # An explicitly configured secret takes priority.
    if TELEGRAM_CHAT_ID:
        logger.info("Using configured Telegram chat ID.")
        return

    if not os.path.exists(REGISTERED_CHAT_FILE):
        logger.info(
            "No registered Telegram chat yet. "
            "Send /start to the bot."
        )
        return

    try:
        with open(
            REGISTERED_CHAT_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            saved = json.load(file)

        chat_id = str(saved.get("chat_id", "")).strip()

        if chat_id:
            TELEGRAM_CHAT_ID = chat_id
            logger.info("Loaded registered Telegram chat.")

    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Could not load registered chat: %s", exc)


def register_telegram_chat(chat_id):
    global TELEGRAM_CHAT_ID

    chat_id = str(chat_id).strip()

    if not chat_id:
        return False

    # Do not silently replace a chat ID set in GitHub Secrets.
    if TELEGRAM_CHAT_ID and TELEGRAM_CHAT_ID != chat_id:
        logger.warning(
            "Registration rejected because another chat is configured."
        )
        return False

    try:
        save_json_file(
            REGISTERED_CHAT_FILE,
            {"chat_id": chat_id},
        )

        TELEGRAM_CHAT_ID = chat_id

        logger.info("Telegram chat registered successfully.")
        return True

    except (OSError, TypeError, ValueError) as exc:
        logger.error("Could not save Telegram chat: %s", exc)
        return False


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_api(method, payload=None, timeout=20):
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing.")
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
            logger.warning("Telegram API error: %s", result)
            return None

        return result.get("result")

    except (requests.RequestException, ValueError) as exc:
        logger.warning("Telegram request failed: %s", exc)
        return None


def send_message(message, chat_id=None):
    target = str(chat_id or TELEGRAM_CHAT_ID).strip()

    if not target:
        logger.warning("No Telegram chat ID available.")
        return False

    result = telegram_api(
        "sendMessage",
        {
            "chat_id": target,
            "text": str(message)[:4000],
            "disable_web_page_preview": True,
        },
    )

    return result is not None


# ============================================================
# DEX SCREENER DISCOVERY
# ============================================================

def discover_addresses():
    addresses = set()

    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]

    for endpoint in endpoints:
        data = get_json(DEX_BASE_URL + endpoint)

        if not isinstance(data, list):
            continue

        for item in data:
            if not isinstance(item, dict):
                continue

            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if address:
                addresses.add(address)

    for term in DISCOVERY_SEARCH_TERMS:
        data = get_json(
            f"{DEX_BASE_URL}/latest/dex/search",
            params={"q": term},
        )

        if not isinstance(data, dict):
            continue

        for pair in data.get("pairs", []):
            if not isinstance(pair, dict):
                continue

            if pair.get("chainId") != "solana":
                continue

            base = pair.get("baseToken") or {}
            address = base.get("address")

            if address:
                addresses.add(address)

    logger.info(
        "Discovery collected %s unique Solana addresses.",
        len(addresses),
    )

    return list(addresses)


def fetch_pairs(addresses):
    best_pairs = {}

    for start in range(0, len(addresses), TOKEN_BATCH_SIZE):
        batch = addresses[start:start + TOKEN_BATCH_SIZE]

        if not batch:
            continue

        data = get_json(
            f"{DEX_BASE_URL}/latest/dex/tokens/"
            + ",".join(batch)
        )

        if not isinstance(data, dict):
            continue

        for pair in data.get("pairs", []):
            if not isinstance(pair, dict):
                continue

            if pair.get("chainId") != "solana":
                continue

            base = pair.get("baseToken") or {}
            address = base.get("address")

            if not address:
                continue

            liquidity = safe_float(
                (pair.get("liquidity") or {}).get("usd")
            )

            previous = best_pairs.get(address)

            previous_liquidity = safe_float(
                ((previous or {}).get("liquidity") or {}).get("usd")
            )

            if previous is None or liquidity > previous_liquidity:
                best_pairs[address] = pair

    return best_pairs


# ============================================================
# METRIC EXTRACTION
# ============================================================

def extract_metrics(pair):
    base = pair.get("baseToken") or {}
    liquidity_data = pair.get("liquidity") or {}
    volume_data = pair.get("volume") or {}
    price_data = pair.get("priceChange") or {}
    txns_data = pair.get("txns") or {}
    m5 = txns_data.get("m5") or {}

    mc_value = pair.get("marketCap")

    if mc_value is None:
        mc_value = pair.get("fdv")

    market_cap = safe_float(mc_value)
    liquidity = safe_float(liquidity_data.get("usd"))
    volume_5m = safe_float(volume_data.get("m5"))
    price_change = safe_float(price_data.get("m5"))

    buys = safe_int(m5.get("buys"))
    sells = safe_int(m5.get("sells"))
    transactions = buys + sells

    if sells > 0:
        buy_sell_ratio = buys / sells
    elif buys > 0:
        buy_sell_ratio = float("inf")
    else:
        buy_sell_ratio = 0.0

    volume_to_mc = (
        volume_5m / market_cap * 100
        if market_cap > 0
        else 0.0
    )

    return {
        "address": base.get("address", ""),
        "name": base.get("name", "Unknown"),
        "symbol": base.get("symbol", "UNKNOWN"),
        "market_cap": market_cap,
        "liquidity": liquidity,
        "volume_5m": volume_5m,
        "price_change_5m": price_change,
        "buys_5m": buys,
        "sells_5m": sells,
        "transactions_5m": transactions,
        "buy_sell_ratio": buy_sell_ratio,
        "volume_to_mc_percent": volume_to_mc,
        "pair_address": pair.get("pairAddress", ""),
        "dex_id": pair.get("dexId", "Unknown"),
        "url": pair.get("url", ""),
        "observed_at": utc_now(),
    }


# ============================================================
# TRACKING
# ============================================================

def update_history(metrics):
    address = metrics["address"]

    if not address:
        return 0

    token = state["tokens"].get(address)

    if not isinstance(token, dict):
        token = {
            "first_seen": utc_now(),
            "observations": 0,
            "history": [],
        }

    token["observations"] = safe_int(
        token.get("observations", 0)
    ) + 1

    token["last_seen"] = utc_now()
    token["last_metrics"] = metrics

    ratio = metrics["buy_sell_ratio"]

    if ratio == float("inf"):
        ratio = 999999

    history = token.get("history", [])

    history.append({
        "timestamp": time.time(),
        "market_cap": metrics["market_cap"],
        "liquidity": metrics["liquidity"],
        "volume_5m": metrics["volume_5m"],
        "volume_to_mc_percent": metrics["volume_to_mc_percent"],
        "price_change_5m": metrics["price_change_5m"],
        "buy_sell_ratio": ratio,
        "transactions_5m": metrics["transactions_5m"],
    })

    token["history"] = history[-MAX_HISTORY_PER_TOKEN:]
    state["tokens"][address] = token

    if len(state["tokens"]) > MAX_TRACKED_TOKENS:
        ordered = sorted(
            state["tokens"].items(),
            key=lambda item: item[1].get("last_seen", ""),
        )

        excess = len(state["tokens"]) - MAX_TRACKED_TOKENS

        for old_address, _ in ordered[:excess]:
            state["tokens"].pop(old_address, None)

    return token["observations"]


# ============================================================
# STRICT QUALIFICATION
# ============================================================

def evaluate(metrics, observations):
    failures = []

    if not MIN_MARKET_CAP <= metrics["market_cap"] <= MAX_MARKET_CAP:
        failures.append("market cap")

    if not MIN_LIQUIDITY <= metrics["liquidity"] <= MAX_LIQUIDITY:
        failures.append("liquidity")

    if metrics["buy_sell_ratio"] < MIN_BUY_SELL_RATIO:
        failures.append("buy/sell ratio")

    if metrics["volume_to_mc_percent"] < MIN_VOLUME_TO_MC_PERCENT:
        failures.append("volume/MC")

    if metrics["transactions_5m"] < MIN_5M_TRANSACTIONS:
        failures.append("transactions")

    if metrics["price_change_5m"] < MIN_5M_PRICE_CHANGE_PERCENT:
        failures.append("price change")

    if observations < MIN_OBSERVATIONS:
        failures.append("observations")

    return len(failures) == 0, failures


def ranking_score(metrics):
    score = 0

    ratio = metrics["buy_sell_ratio"]
    volume = metrics["volume_to_mc_percent"]
    txns = metrics["transactions_5m"]
    change = metrics["price_change_5m"]

    if ratio >= 1.50:
        score += 20
    elif ratio >= 1.35:
        score += 15
    else:
        score += 10

    if volume >= 100:
        score += 25
    elif volume >= 80:
        score += 20
    elif volume >= 60:
        score += 15
    else:
        score += 10

    if txns >= 1500:
        score += 20
    elif txns >= 1000:
        score += 15
    elif txns >= 600:
        score += 12
    else:
        score += 8

    if change >= 50:
        score += 20
    elif change >= 25:
        score += 15
    elif change >= 10:
        score += 10
    else:
        score += 5

    if metrics["liquidity"] >= 20_000:
        score += 5

    if 50_000 <= metrics["market_cap"] <= 100_000:
        score += 5

    return min(score, 100)


# ============================================================
# ALERTS
# ============================================================

def send_runner_alert(metrics, observations):
    address = metrics["address"]

    last_alert = safe_float(
        state["alerts_sent"].get(address, 0)
    )

    if time.time() - last_alert < ALERT_COOLDOWN_SECONDS:
        return False

    score = ranking_score(metrics)

    chart = metrics["url"]

    if not chart and metrics["pair_address"]:
        chart = (
            "https://dexscreener.com/solana/"
            + metrics["pair_address"]
        )

    message = (
        "🚨 RUNNER CANDIDATE — V6.2\n\n"
        f"Token: {metrics['name']} (${metrics['symbol']})\n"
        f"Market Cap: {money(metrics['market_cap'])}\n"
        f"Liquidity: {money(metrics['liquidity'])}\n"
        f"5m Price Change: {pct(metrics['price_change_5m'])}\n"
        f"5m Volume: {money(metrics['volume_5m'])}\n"
        f"5m Vol/MC: {pct(metrics['volume_to_mc_percent'])}\n"
        f"5m Buys: {metrics['buys_5m']}\n"
        f"5m Sells: {metrics['sells_5m']}\n"
        f"5m Buy/Sell Ratio: {ratio_text(metrics['buy_sell_ratio'])}\n"
        f"5m Transactions: {metrics['transactions_5m']}\n"
        f"Observations: {observations}\n"
        f"Ranking Score: {score}/100\n\n"
        f"CA:\n{address}\n\n"
        f"DEX: {metrics['dex_id']}\n"
    )

    if chart:
        message += f"\nChart: {chart}\n"

    message += (
        "\nAll mandatory V6.1 filters passed.\n"
        "⚠️ This is a screening signal, not a guarantee "
        "of future performance."
    )

    # If there is no registered chat, do not mark the alert
    # as sent. It can be retried after registration.
    if not TELEGRAM_CHAT_ID:
        logger.warning(
            "Qualified token, but no Telegram chat is registered."
        )
        return False

    if not send_message(message):
        logger.warning(
            "Qualified token, but Telegram alert failed: %s",
            metrics["symbol"],
        )
        return False

    state["alerts_sent"][address] = time.time()
    state["total_alerts"] += 1

    save_state()

    logger.info(
        "RUNNER ALERT SENT: %s (%s)",
        metrics["symbol"],
        address,
    )

    return True


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def status_message():
    return (
        f"🤖 Runner Bot {BOT_VERSION}\n\n"
        "Status: Running\n"
        f"Scan interval: {SCAN_INTERVAL_SECONDS} seconds\n"
        f"Tracked tokens: {len(state['tokens'])}\n"
        f"Discovered pairs: {len(last_discovery_pairs)}\n"
        f"Alerts sent: {state['total_alerts']}\n\n"
        "STRICT FILTERS\n"
        f"Market cap: ${MIN_MARKET_CAP:,}–${MAX_MARKET_CAP:,}\n"
        f"Liquidity: ${MIN_LIQUIDITY:,}–${MAX_LIQUIDITY:,}\n"
        f"Buy/sell ratio: >= {MIN_BUY_SELL_RATIO:.2f}\n"
        f"5m Vol/MC: >= {MIN_VOLUME_TO_MC_PERCENT:.2f}%\n"
        f"5m transactions: >= {MIN_5M_TRANSACTIONS}\n"
        f"5m price change: >= +{MIN_5M_PRICE_CHANGE_PERCENT:.2f}%\n"
        f"Observations: >= {MIN_OBSERVATIONS}\n"
    )


def clear_tracking(chat_id):
    state["tokens"] = {}
    state["alerts_sent"] = {}
    state["total_alerts"] = 0

    save_state()

    send_message(
        "🧹 Tracking history cleared.",
        chat_id=chat_id,
    )


def handle_message(message):
    if not isinstance(message, dict):
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    text_value = str(message.get("text", "")).strip()

    if not chat_id or not text_value.startswith("/"):
        return

    command = text_value.split()[0].split("@")[0].lower()

    if command == "/start":
        # Only private chats may register themselves.
        if chat.get("type") != "private":
            send_message(
                "Please message me privately and send /start.",
                chat_id=chat_id,
            )
            return

        registered = register_telegram_chat(chat_id)

        if registered:
            send_message(
                "✅ Runner Bot is online.\n\n"
                "Your private Telegram chat is registered.\n"
                "Runner alerts will be sent here.\n\n"
                "Commands:\n"
                "/start — Register this chat\n"
                "/status — Bot status\n"
                "/clear — Clear tracking history",
                chat_id=chat_id,
            )
        else:
            send_message(
                "⚠️ I could not register this chat. "
                "Check whether another chat ID is configured.",
                chat_id=chat_id,
            )

    elif command == "/status":
        send_message(status_message(), chat_id=chat_id)

    elif command == "/clear":
        if str(chat_id) == str(TELEGRAM_CHAT_ID):
            clear_tracking(chat_id)
        else:
            send_message(
                "Only the registered chat can clear history.",
                chat_id=chat_id,
            )

    else:
        send_message(
            "Commands: /start, /status, /clear",
            chat_id=chat_id,
        )


def telegram_polling_loop():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Telegram polling disabled: missing bot token.")
        return

    logger.info("Telegram command polling started.")

    while bot_running:
        try:
            offset = safe_int(state.get("telegram_offset", 0))

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

                update_id = safe_int(update.get("update_id", 0))
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                incoming_id = str(chat.get("id", ""))

                # If a chat is already registered, reject commands
                # from other chats. The first /start can register
                # a chat when none has been registered.
                if TELEGRAM_CHAT_ID and incoming_id != TELEGRAM_CHAT_ID:
                    logger.warning(
                        "Ignoring command from unregistered chat."
                    )
                    state["telegram_offset"] = update_id + 1
                    continue

                handle_message(message)

                state["telegram_offset"] = update_id + 1
                save_state()

        except Exception:
            logger.exception("Telegram polling error.")
            time.sleep(5)


# ============================================================
# MAIN SCANNING LOOP
# ============================================================

def scan_market():
    global last_discovery_pairs

    now = time.time()

    if (
        not last_discovery_pairs
        or now - state["last_discovery"] >= DISCOVERY_REFRESH_SECONDS
    ):
        logger.info("Refreshing DEX Screener discovery.")

        addresses = discover_addresses()

        if addresses:
            discovered = fetch_pairs(addresses)

            if discovered:
                last_discovery_pairs.update(discovered)

        state["last_discovery"] = now

        logger.info(
            "Discovery currently holds %s token pairs.",
            len(last_discovery_pairs),
        )

    if not last_discovery_pairs:
        logger.warning("No token pairs available to scan.")
        return

    refreshed = fetch_pairs(list(last_discovery_pairs.keys()))

    if refreshed:
        last_discovery_pairs.update(refreshed)

    pairs_count = 0
    qualified_count = 0
    alerts_count = 0

    for address, pair in list(last_discovery_pairs.items()):
        try:
            metrics = extract_metrics(pair)

            if (
                not metrics["address"]
                or metrics["market_cap"] <= 0
                or metrics["liquidity"] <= 0
            ):
                continue

            observations = update_history(metrics)
            pairs_count += 1

            qualified, failures = evaluate(
                metrics,
                observations,
            )

            if not qualified:
                logger.debug(
                    "REJECTED %s | %s",
                    metrics["symbol"],
                    ", ".join(failures),
                )
                continue

            qualified_count += 1

            logger.info(
                "QUALIFIED %s | MC=%s | LIQ=%s | "
                "B/S=%s | Vol/MC=%s | TX=%s | "
                "Change=%s | Obs=%s",
                metrics["symbol"],
                money(metrics["market_cap"]),
                money(metrics["liquidity"]),
                ratio_text(metrics["buy_sell_ratio"]),
                pct(metrics["volume_to_mc_percent"]),
                metrics["transactions_5m"],
                pct(metrics["price_change_5m"]),
                observations,
            )

            if send_runner_alert(metrics, observations):
                alerts_count += 1

        except Exception:
            logger.exception("Error processing token %s", address)

    save_state()

    logger.info(
        "SCAN COMPLETE | Pairs=%s | Qualified=%s | Alerts sent=%s",
        pairs_count,
        qualified_count,
        alerts_count,
    )


def main():
    global bot_running

    logger.info("=" * 60)
    logger.info("Starting Runner Bot %s", BOT_VERSION)
    logger.info("Scan interval: %s seconds", SCAN_INTERVAL_SECONDS)
    logger.info("=" * 60)

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing.")

    load_state()
    load_registered_chat()

    threading.Thread(
        target=telegram_polling_loop,
        daemon=True,
        name="telegram-polling",
    ).start()

    logger.info(
        "Chat configured: %s",
        "yes" if TELEGRAM_CHAT_ID else "no — send /start in Telegram",
    )

    try:
        while bot_running:
            cycle_start = time.time()

            try:
                scan_market()
            except Exception:
                logger.exception("Market scan failed.")

            elapsed = time.time() - cycle_start

            time.sleep(
                max(1, SCAN_INTERVAL_SECONDS - elapsed)
            )

    except KeyboardInterrupt:
        logger.info("Shutdown requested.")

    finally:
        bot_running = False
        save_state()
        logger.info("Runner Bot stopped.")


if __name__ == "__main__":
    main()
