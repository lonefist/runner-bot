import os
import time
import json
import logging
import threading
from datetime import datetime, timezone

import requests

# ============================================================
# RUNNER BOT V6.1 — TELEGRAM CHAT AUTO-DISCOVERY
# ============================================================

BOT_VERSION = "V6.1-RUNNER-TRACKER"

STATE_FILE = "runner_state_v60.json"

SCAN_INTERVAL_SECONDS = 15
DISCOVERY_INTERVAL_SECONDS = 60

MIN_MARKET_CAP = 20_000
MAX_MARKET_CAP = 250_000
MIN_LIQUIDITY = 6_000
MAX_TOKEN_AGE_HOURS = 48

MIN_VOLUME_5M = 500
MIN_TRANSACTIONS_5M = 5
MIN_BUY_SELL_RATIO = 1.25
MIN_FLOW_PROXY = 1_500
MIN_FLOW_MC_PCT = 0.8

MIN_SCORE = 65
HIGH_PRIORITY_SCORE = 80

PERSISTENCE_REQUIRED = 2
TOKEN_COOLDOWN_SECONDS = 600

MAX_DISCOVERY_PAIRS = 300
MAX_TRACKED_TOKENS = 1500

DISCOVERY_SEARCH_TERMS = [
    "SOL", "WSOL", "pump", "meme", "cat", "dog",
    "ai", "inu", "coin", "frog", "agent"
]

DEX_API = "https://api.dexscreener.com"
SESSION = requests.Session()

# ============================================================
# TELEGRAM SETTINGS
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

TELEGRAM_BASE = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if TELEGRAM_BOT_TOKEN
    else ""
)

# The bot's Telegram polling offset.
telegram_offset = 0

# Automatically discovered chat ID is held in memory.
discovered_chat_id = TELEGRAM_CHAT_ID

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("runner-bot")


# ============================================================
# HTTP HELPERS
# ============================================================

def http_get(url, params=None, timeout=15):
    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=timeout,
            headers={"User-Agent": f"RunnerBot/{BOT_VERSION}"},
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        log.warning("GET failed: %s | %s", url, exc)
        return None


def http_post(url, payload, timeout=15):
    try:
        response = SESSION.post(
            url,
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            log.warning("Telegram API returned an unsuccessful response: %s", data)
            return None

        return data
    except Exception as exc:
        log.error("POST failed: %s", exc)
        return None


# ============================================================
# TELEGRAM CHAT ID DISCOVERY
# ============================================================

def discover_chat_id():
    """
    Uses a recent private Telegram message to discover the chat ID.

    If TELEGRAM_CHAT_ID is already configured, that value is used.
    Otherwise, send /start to your bot before launching the workflow.
    """

    global discovered_chat_id

    if discovered_chat_id:
        return discovered_chat_id

    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is missing from GitHub Secrets.")
        return None

    log.info("TELEGRAM_CHAT_ID is missing. Trying to discover it...")

    # Do not call getUpdates here: the main Telegram polling loop owns
    # getUpdates so that the two requests cannot consume each other's updates.
    #
    # Instead, use a chat ID from a message already seen by the main poller.
    if discovered_chat_id:
        return discovered_chat_id

    log.error(
        "Chat ID is not known yet. Send /start to your Telegram bot, "
        "then restart the workflow. For the most reliable setup, add "
        "TELEGRAM_CHAT_ID to GitHub Secrets."
    )
    return None


def telegram_send(message):
    if not TELEGRAM_BOT_TOKEN:
        log.error("Cannot send Telegram message: TELEGRAM_BOT_TOKEN is missing.")
        return False

    chat_id = discover_chat_id()

    if not chat_id:
        log.error("Cannot send Telegram message because no chat ID is available.")
        return False

    result = http_post(
        f"{TELEGRAM_BASE}/sendMessage",
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        },
    )

    return result is not None


def telegram_get_updates():
    global telegram_offset, discovered_chat_id

    if not TELEGRAM_BOT_TOKEN:
        return []

    params = {
        "timeout": 5,
        "allowed_updates": json.dumps(["message"]),
    }

    if telegram_offset:
        params["offset"] = telegram_offset

    data = http_get(
        f"{TELEGRAM_BASE}/getUpdates",
        params=params,
        timeout=10,
    )

    if not data or not data.get("ok"):
        return []

    updates = data.get("result", [])

    for update in updates:
        telegram_offset = max(
            telegram_offset,
            update.get("update_id", 0) + 1,
        )

        message = update.get("message", {})
        chat = message.get("chat", {})
        text = message.get("text", "").strip()

        # Automatically discover a private chat when the user sends a message.
        if chat.get("type") == "private" and not discovered_chat_id:
            discovered_chat_id = str(chat.get("id"))
            log.info("Discovered Telegram private chat ID.")

            telegram_send(
                f"✅ Runner Bot {BOT_VERSION} connected.\n"
                "Your Telegram chat has been detected."
            )

        if text == "/start" and chat.get("type") == "private":
            if not discovered_chat_id:
                discovered_chat_id = str(chat.get("id"))

            telegram_send(
                f"🏃 Runner Bot {BOT_VERSION}\n"
                "Bot is online.\n\n"
                f"Market cap range: ${MIN_MARKET_CAP:,.0f}–"
                f"${MAX_MARKET_CAP:,.0f}\n"
                f"Minimum liquidity: ${MIN_LIQUIDITY:,.0f}\n"
                f"Minimum alert score: {MIN_SCORE}/100"
            )

        elif text == "/status" and (
            not discovered_chat_id
            or str(chat.get("id")) == str(discovered_chat_id)
        ):
            telegram_send(
                f"🏃 Runner Bot {BOT_VERSION}\n"
                "Status: Running\n"
                f"Market cap: ${MIN_MARKET_CAP:,.0f}–"
                f"${MAX_MARKET_CAP:,.0f}\n"
                f"Minimum liquidity: ${MIN_LIQUIDITY:,.0f}\n"
                f"Alert score: {MIN_SCORE}/100"
            )

    return updates


def telegram_command_loop():
    while True:
        try:
            telegram_get_updates()
        except Exception:
            log.exception("Telegram command polling error")

        time.sleep(1)


# ============================================================
# PERSISTENT STATE
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "tokens": {},
            "alerts": {},
            "milestones": {},
            "last_discovery": 0,
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

        state.setdefault("tokens", {})
        state.setdefault("alerts", {})
        state.setdefault("milestones", {})
        state.setdefault("last_discovery", 0)

        return state

    except Exception as exc:
        log.warning("Could not load state: %s", exc)

        return {
            "tokens": {},
            "alerts": {},
            "milestones": {},
            "last_discovery": 0,
        }


def save_state(state):
    try:
        temporary_file = STATE_FILE + ".tmp"

        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(state, file)

        os.replace(temporary_file, STATE_FILE)

    except Exception as exc:
        log.error("Could not save state: %s", exc)


# ============================================================
# DISCOVERY
# ============================================================

def discover_pairs():
    pairs = {}

    endpoints = [
        f"{DEX_API}/token-profiles/latest/v1",
        f"{DEX_API}/token-boosts/latest/v1",
        f"{DEX_API}/token-boosts/top/v1",
    ]

    for endpoint in endpoints:
        data = http_get(endpoint)

        if isinstance(data, list):
            for item in data:
                if item.get("chainId") != "solana":
                    continue

                address = item.get("tokenAddress")

                if address:
                    pairs[address] = item

    for term in DISCOVERY_SEARCH_TERMS:
        data = http_get(
            f"{DEX_API}/latest/dex/search",
            params={"q": term},
        )

        if not isinstance(data, dict):
            continue

        for pair in data.get("pairs", []):
            if pair.get("chainId") != "solana":
                continue

            address = (pair.get("baseToken") or {}).get("address")

            if address:
                pairs[address] = pair

    log.info("Discovery found %s unique token addresses.", len(pairs))

    return list(pairs.keys())[:MAX_DISCOVERY_PAIRS]


# ============================================================
# TOKEN DATA
# ============================================================

def fetch_token_pairs(address):
    data = http_get(f"{DEX_API}/token-pairs/v1/solana/{address}")

    if not isinstance(data, list):
        return []

    return [
        pair for pair in data
        if pair.get("chainId") == "solana"
    ]


def choose_best_pair(pairs):
    if not pairs:
        return None

    return max(
        pairs,
        key=lambda pair: float(
            (pair.get("liquidity") or {}).get("usd") or 0
        ),
    )


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pair_metrics(pair):
    base = pair.get("baseToken") or {}
    liquidity = safe_float((pair.get("liquidity") or {}).get("usd"))
    market_cap = safe_float(pair.get("marketCap"))
    fdv = safe_float(pair.get("fdv"))

    if market_cap <= 0:
        market_cap = fdv

    volume = pair.get("volume") or {}
    volume_5m = safe_float(volume.get("m5"))
    volume_1h = safe_float(volume.get("h1"))
    volume_6h = safe_float(volume.get("h6"))

    txns = pair.get("txns") or {}
    tx5 = txns.get("m5") or {}
    tx1h = txns.get("h1") or {}

    buys_5m = safe_float(tx5.get("buys"))
    sells_5m = safe_float(tx5.get("sells"))
    buys_1h = safe_float(tx1h.get("buys"))
    sells_1h = safe_float(tx1h.get("sells"))

    total_5m = buys_5m + sells_5m
    total_1h = buys_1h + sells_1h

    bs_ratio = buys_5m / max(sells_5m, 1)
    bs_ratio_1h = buys_1h / max(sells_1h, 1)

    # Approximate buy/sell activity imbalance, not actual net inflow.
    flow_proxy = (
        volume_5m * (buys_5m - sells_5m) / total_5m
        if total_5m > 0
        else 0
    )

    flow_mc_pct = (
        abs(flow_proxy) / market_cap * 100
        if market_cap > 0
        else 0
    )

    price_change = pair.get("priceChange") or {}

    created_at = safe_float(pair.get("pairCreatedAt")) / 1000
    age_hours = (
        max(0, (time.time() - created_at) / 3600)
        if created_at
        else 9999
    )

    return {
        "address": base.get("address", ""),
        "name": base.get("name", "Unknown"),
        "symbol": base.get("symbol", "???"),
        "pair_address": pair.get("pairAddress", ""),
        "url": pair.get("url", ""),
        "market_cap": market_cap,
        "fdv": fdv,
        "liquidity": liquidity,
        "age_hours": age_hours,
        "volume_5m": volume_5m,
        "volume_1h": volume_1h,
        "volume_6h": volume_6h,
        "buys_5m": int(buys_5m),
        "sells_5m": int(sells_5m),
        "buys_1h": int(buys_1h),
        "sells_1h": int(sells_1h),
        "total_5m": int(total_5m),
        "total_1h": int(total_1h),
        "bs_ratio": bs_ratio,
        "bs_ratio_1h": bs_ratio_1h,
        "flow_proxy": flow_proxy,
        "flow_mc_pct": flow_mc_pct,
        "change_5m": safe_float(price_change.get("m5")),
        "change_1h": safe_float(price_change.get("h1")),
        "change_6h": safe_float(price_change.get("h6")),
    }


# ============================================================
# SCORING
# ============================================================

def score_token(m):
    score = 0
    reasons = []

    if m["buys_5m"] > m["sells_5m"]:
        score += 10
        reasons.append("buyers leading")

    if m["bs_ratio"] >= 1.5:
        score += 15
        reasons.append("strong 5m buy/sell ratio")
    elif m["bs_ratio"] >= MIN_BUY_SELL_RATIO:
        score += 10
        reasons.append("positive buy/sell ratio")

    if m["volume_5m"] >= 5000:
        score += 10
        reasons.append("strong 5m volume")
    elif m["volume_5m"] >= MIN_VOLUME_5M:
        score += 5
        reasons.append("active 5m volume")

    if m["market_cap"] > 0:
        vol_mc = m["volume_5m"] / m["market_cap"] * 100

        if vol_mc >= 10:
            score += 10
            reasons.append("high volume/MC")
        elif vol_mc >= 5:
            score += 5
            reasons.append("healthy volume/MC")

    if m["flow_proxy"] >= MIN_FLOW_PROXY:
        score += 15
        reasons.append("positive flow-pressure proxy")

    if m["flow_mc_pct"] >= MIN_FLOW_MC_PCT:
        score += 5
        reasons.append("flow proxy material relative to MC")

    if m["change_5m"] > 0:
        score += 5
        reasons.append("positive 5m momentum")

    if 0 < m["change_1h"] <= 100:
        score += 10
        reasons.append("positive 1h momentum")

    if m["change_5m"] > 80:
        score -= 15
        reasons.append("extreme short-term move risk")

    if m["change_1h"] > 200:
        score -= 10
        reasons.append("large 1h move risk")

    if m["liquidity"] >= 20_000:
        score += 15
        reasons.append("strong liquidity")
    elif m["liquidity"] >= 10_000:
        score += 10
        reasons.append("healthy liquidity")
    elif m["liquidity"] >= MIN_LIQUIDITY:
        score += 5
        reasons.append("minimum liquidity met")

    if m["age_hours"] <= 6:
        score += 10
        reasons.append("young pair")
    elif m["age_hours"] <= 24:
        score += 5
        reasons.append("recent pair")

    return max(0, min(100, score)), reasons


# ============================================================
# ALERT FILTERS
# ============================================================

def rejection_reason(m):
    if not m["address"]:
        return "missing token address"

    if not MIN_MARKET_CAP <= m["market_cap"] <= MAX_MARKET_CAP:
        return "market cap outside target range"

    if m["liquidity"] < MIN_LIQUIDITY:
        return "liquidity below minimum"

    if m["age_hours"] > MAX_TOKEN_AGE_HOURS:
        return "pair older than allowed age"

    if m["volume_5m"] < MIN_VOLUME_5M:
        return "5m volume too low"

    if m["total_5m"] < MIN_TRANSACTIONS_5M:
        return "not enough 5m transactions"

    if m["bs_ratio"] < MIN_BUY_SELL_RATIO:
        return "buy/sell ratio too low"

    if m["flow_proxy"] < MIN_FLOW_PROXY:
        return "flow-pressure proxy too low"

    if m["flow_mc_pct"] < MIN_FLOW_MC_PCT:
        return "flow proxy too small relative to market cap"

    return None


# ============================================================
# ALERT FORMATTING
# ============================================================

def format_alert(m, score, reasons):
    priority = "🔥 HIGH-PRIORITY RUNNER" if score >= HIGH_PRIORITY_SCORE else "🚨 RUNNER CANDIDATE"

    return (
        f"{priority}\n"
        f"Runner Bot {BOT_VERSION}\n\n"
        f"🪙 {m['name']} (${m['symbol']})\n"
        f"📊 Score: {score}/100\n"
        f"💰 MC: ${m['market_cap']:,.0f}\n"
        f"🏷 FDV: ${m['fdv']:,.0f}\n"
        f"💧 Liquidity: ${m['liquidity']:,.0f}\n"
        f"⏳ Pair age: {m['age_hours']:.1f} hours\n\n"
        f"📈 Price change:\n"
        f"5m: {m['change_5m']:+.2f}% | "
        f"1h: {m['change_1h']:+.2f}% | "
        f"6h: {m['change_6h']:+.2f}%\n\n"
        f"📦 Volume:\n"
        f"5m: ${m['volume_5m']:,.0f} | "
        f"1h: ${m['volume_1h']:,.0f} | "
        f"6h: ${m['volume_6h']:,.0f}\n\n"
        f"🟢 5m buys: {m['buys_5m']} | "
        f"🔴 sells: {m['sells_5m']}\n"
        f"Buy/sell ratio: {m['bs_ratio']:.2f}\n"
        f"1h buys/sells: {m['buys_1h']}/{m['sells_1h']}\n\n"
        f"⚖️ Flow-pressure proxy: ${m['flow_proxy']:,.0f}\n"
        f"Proxy/MC: {m['flow_mc_pct']:.2f}%\n\n"
        f"🧠 Signals: {', '.join(reasons)}\n\n"
        f"CA:\n{m['address']}\n\n"
        f"🔎 {m['url']}\n\n"
        "⚠️ Flow pressure is an estimate from transaction counts and volume, "
        "not verified net inflow. This alert is not a guarantee of a runner."
    )


# ============================================================
# MILESTONE TRACKER
# ============================================================

def check_milestones(state, m):
    address = m["address"]
    current_mc = m["market_cap"]

    record = state["milestones"].get(address)

    if not record:
        return

    initial_mc = safe_float(record.get("initial_mc"))

    if initial_mc <= 0 or current_mc <= 0:
        return

    multiple = current_mc / initial_mc
    milestones = [2, 3, 5, 10, 20, 50, 100, 200, 500, 1000]

    sent = record.setdefault("sent", [])

    for milestone in milestones:
        if multiple >= milestone and milestone not in sent:
            message = (
                f"🚀 RUNNER MILESTONE: {milestone}x\n\n"
                f"{m['name']} (${m['symbol']})\n"
                f"Initial MC: ${initial_mc:,.0f}\n"
                f"Current MC: ${current_mc:,.0f}\n"
                f"Multiple: {multiple:.2f}x\n"
                f"CA: {address}\n"
                f"{m['url']}\n\n"
                "⚠️ Market-cap tracking can be affected by data changes "
                "and does not equal realized profit."
            )

            if telegram_send(message):
                sent.append(milestone)
                log.info("Milestone alert sent: %sx for %s", milestone, address)


# ============================================================
# MAIN SCANNER
# ============================================================

def scanner_loop():
    state = load_state()
    log.info("Scanner started.")

    telegram_send(
        f"✅ Runner Bot {BOT_VERSION} is online.\n\n"
        f"Target MC: ${MIN_MARKET_CAP:,.0f}–${MAX_MARKET_CAP:,.0f}\n"
        f"Minimum liquidity: ${MIN_LIQUIDITY:,.0f}\n"
        f"Alert score: {MIN_SCORE}/100\n"
        f"Scan interval target: {SCAN_INTERVAL_SECONDS}s\n\n"
        "⚠️ No alert guarantees a profitable trade."
    )

    while True:
        started = time.time()

        try:
            now = time.time()

            if now - state.get("last_discovery", 0) >= DISCOVERY_INTERVAL_SECONDS:
                addresses = discover_pairs()
                state["addresses"] = addresses
                state["last_discovery"] = now
                save_state(state)

            addresses = state.get("addresses", [])

            for address in addresses:
                try:
                    pairs = fetch_token_pairs(address)
                    pair = choose_best_pair(pairs)

                    if not pair:
                        continue

                    m = pair_metrics(pair)

                    if not m["address"]:
                        m["address"] = address

                    check_milestones(state, m)

                    reason = rejection_reason(m)

                    if reason:
                        log.debug("%s rejected: %s", m["symbol"], reason)
                        continue

                    score, reasons = score_token(m)

                    if score < MIN_SCORE:
                        continue

                    last_alert = safe_float(
                        state["alerts"].get(address, 0)
                    )

                    if now - last_alert < TOKEN_COOLDOWN_SECONDS:
                        continue

                    previous = state["tokens"].get(address, {})
                    observations = int(previous.get("observations", 0)) + 1

                    state["tokens"][address] = {
                        "observations": observations,
                        "last_mc": m["market_cap"],
                        "last_seen": now,
                    }

                    if observations < PERSISTENCE_REQUIRED:
                        continue

                    if telegram_send(format_alert(m, score, reasons)):
                        state["alerts"][address] = now

                        if address not in state["milestones"]:
                            state["milestones"][address] = {
                                "initial_mc": m["market_cap"],
                                "alert_time": now,
                                "sent": [],
                            }

                        log.info(
                            "Alert sent: %s score=%s MC=$%.0f",
                            m["symbol"],
                            score,
                            m["market_cap"],
                        )

                    time.sleep(0.05)

                except Exception:
                    log.exception("Token scan error for %s", address)

            # Keep state bounded.
            if len(state["tokens"]) > MAX_TRACKED_TOKENS:
                sorted_tokens = sorted(
                    state["tokens"].items(),
                    key=lambda item: item[1].get("last_seen", 0),
                    reverse=True,
                )
                state["tokens"] = dict(sorted_tokens[:MAX_TRACKED_TOKENS])

            save_state(state)

        except Exception:
            log.exception("Scanner loop error")

        elapsed = time.time() - started
        time.sleep(max(1, SCAN_INTERVAL_SECONDS - elapsed))


# ============================================================
# STARTUP
# ============================================================

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is missing. Add it to GitHub repository Secrets."
        )

    log.info("Starting Runner Bot %s", BOT_VERSION)

    # Start Telegram polling first. It can learn the chat ID from /start.
    poller = threading.Thread(
        target=telegram_command_loop,
        daemon=True,
    )
    poller.start()

    # If no chat ID secret is configured, wait briefly for /start.
    if not discovered_chat_id:
        log.warning(
            "No TELEGRAM_CHAT_ID secret. Open your bot and send /start now."
        )

        for _ in range(30):
            if discovered_chat_id:
                break
            time.sleep(1)

    if not discovered_chat_id:
        raise SystemExit(
            "Telegram chat ID not discovered. Add TELEGRAM_CHAT_ID as a "
            "GitHub Secret, or send /start to the bot and restart the workflow."
        )

    scanner_loop()


if __name__ == "__main__":
    main()
