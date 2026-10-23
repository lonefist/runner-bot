import os
import json
import time
import logging
import threading
from datetime import datetime, timezone

import requests


# ============================================================
# RUNNER BOT V6.0
# EARLY RUNNER DETECTION AND MULTIPLE TRACKING
# ============================================================

BOT_VERSION = "V6.0-RUNNER-TRACKER"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if TELEGRAM_BOT_TOKEN else ""
)

MIN_MCAP_USD = 20_000
MAX_MCAP_USD = 250_000

MIN_LIQUIDITY_USD = 6_000
MAX_PAIR_AGE_HOURS = 48

SCAN_INTERVAL_SECONDS = 15
DISCOVERY_INTERVAL_SECONDS = 60

MIN_5M_VOLUME_USD = 500
MIN_5M_TRANSACTIONS = 5

MIN_BUY_SELL_RATIO = 1.25
MIN_FLOW_PROXY_USD = 1_500
MIN_FLOW_MC_PCT = 0.8

ALERT_SCORE = 65
HIGH_PRIORITY_SCORE = 80

PERSISTENCE_OBSERVATIONS = 2
TOKEN_COOLDOWN_SECONDS = 600

STATE_FILE = "runner_state_v60.json"

MAX_TRACKED_TOKENS = 1500
MAX_DISCOVERY_PAIRS = 300

MILESTONES = [
    2, 3, 5, 10, 20, 50, 100, 200, 500
]

SEARCH_TERMS = [
    "SOL", "WSOL", "pump", "meme",
    "cat", "dog", "ai", "inu",
    "coin", "frog", "agent"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("runner-bot")

session = requests.Session()
session.headers.update({
    "User-Agent": "RunnerBot/6.0"
})

stop_event = threading.Event()
telegram_offset = 0

state_lock = threading.Lock()

state = {
    "tokens": {},
    "pending": {},
    "last_alert": {},
    "last_discovery": 0,
    "started_at": None
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_ts():
    return time.time()


def utc_string(timestamp=None):
    if timestamp is None:
        timestamp = now_ts()

    return datetime.fromtimestamp(
        timestamp, timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        result = float(value)

        if result != result:
            return default

        return result

    except (ValueError, TypeError):
        return default


def money(value):
    if value is None:
        return "N/A"

    return f"${value:,.0f}"


def percentage(value):
    if value is None:
        return "N/A"

    return f"{value:+.2f}%"


def ratio(buys, sells):
    if sells <= 0:
        return float(buys) if buys > 0 else 0.0

    return buys / sells


def get_json(url, params=None, timeout=12):
    try:
        response = session.get(
            url,
            params=params,
            timeout=timeout
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:
        log.warning("GET failed: %s | %s", url, exc)
        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials are missing.")
        return False

    url = f"{TELEGRAM_BASE}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }

    try:
        response = session.post(
            url,
            json=payload,
            timeout=15
        )

        if response.status_code != 200:
            log.error(
                "Telegram send failed: %s %s",
                response.status_code,
                response.text[:300]
            )
            return False

        result = response.json()

        if not result.get("ok"):
            log.error("Telegram API rejected message: %s", result)
            return False

        log.info("Telegram alert delivered.")
        return True

    except Exception as exc:
        log.error("Telegram error: %s", exc)
        return False


def telegram_get_updates():
    global telegram_offset

    if not TELEGRAM_BOT_TOKEN:
        return []

    url = f"{TELEGRAM_BASE}/getUpdates"

    try:
        response = session.get(
            url,
            params={
                "offset": telegram_offset,
                "timeout": 5,
                "allowed_updates": json.dumps(["message"])
            },
            timeout=10
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            return []

        updates = result.get("result", [])

        for update in updates:
            telegram_offset = max(
                telegram_offset,
                update["update_id"] + 1
            )

        return updates

    except Exception as exc:
        log.warning("Telegram polling error: %s", exc)
        return []


def handle_telegram_commands():
    for update in telegram_get_updates():
        message = update.get("message", {})
        text = message.get("text", "").strip()

        chat_id = str(
            message.get("chat", {}).get("id", "")
        )

        if not text:
            continue

        if text.startswith("/start"):
            telegram_send(
                "Runner Bot V6.0 is online.\n\n"
                "Commands:\n"
                "/status - Scanner status\n"
                "/help - Show commands\n\n"
                f"Market-cap range: "
                f"{money(MIN_MCAP_USD)}–{money(MAX_MCAP_USD)}"
            )

        elif text.startswith("/help"):
            telegram_send(
                "Runner Bot V6.0\n\n"
                "/start - Start menu\n"
                "/status - Scanner status\n"
                "/help - Help\n\n"
                "Alerts use market-cap, liquidity, "
                "momentum, volume and transaction data."
            )

        elif text.startswith("/status"):
            with state_lock:
                token_count = len(state["tokens"])
                pending_count = len(state["pending"])

            telegram_send(
                "RUNNER BOT STATUS\n\n"
                f"Version: {BOT_VERSION}\n"
                "Scanner: Running\n"
                f"Tracked tokens: {token_count}\n"
                f"Pending candidates: {pending_count}\n"
                f"Market cap: {money(MIN_MCAP_USD)}"
                f"–{money(MAX_MCAP_USD)}\n"
                f"Minimum liquidity: "
                f"{money(MIN_LIQUIDITY_USD)}\n"
                f"Started: {state.get('started_at')}"
            )


# ============================================================
# PERSISTENT STATE
# ============================================================

def load_state():
    global state

    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as file:
                loaded = json.load(file)

            for key in state:
                if key in loaded:
                    state[key] = loaded[key]

            log.info("State restored from %s", STATE_FILE)

    except Exception as exc:
        log.error("Could not load state: %s", exc)


def save_state():
    try:
        with state_lock:
            snapshot = json.dumps(
                state,
                separators=(",", ":")
            )

        temporary_file = STATE_FILE + ".tmp"

        with open(temporary_file, "w", encoding="utf-8") as file:
            file.write(snapshot)

        os.replace(temporary_file, STATE_FILE)

    except Exception as exc:
        log.error("State save failed: %s", exc)


# ============================================================
# DEXSCREENER DISCOVERY
# ============================================================

def discover_pairs():
    discovered = {}

    endpoints = [
        f"{DEX_BASE}/token-profiles/latest/v1",
        f"{DEX_BASE}/token-boosts/latest/v1",
        f"{DEX_BASE}/token-boosts/top/v1"
    ]

    for url in endpoints:
        data = get_json(url)

        if not isinstance(data, list):
            continue

        for item in data:
            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if address:
                discovered[address] = True

    for term in SEARCH_TERMS:
        data = get_json(
            f"{DEX_BASE}/latest/dex/search",
            params={"q": term}
        )

        if not isinstance(data, dict):
            continue

        for pair in data.get("pairs") or []:
            if pair.get("chainId") != "solana":
                continue

            token = pair.get("baseToken") or {}
            address = token.get("address")

            if address:
                discovered[address] = True

            quote = pair.get("quoteToken") or {}

            if quote.get("address"):
                discovered[quote["address"]] = True

    addresses = list(discovered.keys())

    log.info(
        "Discovery found %d distinct token addresses.",
        len(addresses)
    )

    return addresses[:MAX_DISCOVERY_PAIRS]


def fetch_token_pairs(address):
    data = get_json(
        f"{DEX_BASE}/token-pairs/v1/solana/{address}"
    )

    if not isinstance(data, list):
        return []

    return [
        pair for pair in data
        if pair.get("chainId") == "solana"
    ]


def choose_best_pair(pairs):
    if not pairs:
        return None

    # Prefer the pair with the greatest reported liquidity.
    # This does not guarantee that the pair has the best execution.
    return max(
        pairs,
        key=lambda pair: safe_float(
            (pair.get("liquidity") or {}).get("usd")
        )
    )


# ============================================================
# TOKEN METRICS
# ============================================================

def extract_metrics(pair):
    base = pair.get("baseToken") or {}
    txns = pair.get("txns") or {}
    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    liquidity = pair.get("liquidity") or {}

    tx5 = txns.get("m5") or {}
    tx1h = txns.get("h1") or {}

    buys5 = int(safe_float(tx5.get("buys")))
    sells5 = int(safe_float(tx5.get("sells")))

    buys1h = int(safe_float(tx1h.get("buys")))
    sells1h = int(safe_float(tx1h.get("sells")))

    vol5 = safe_float(volume.get("m5"))
    vol1h = safe_float(volume.get("h1"))

    change5 = safe_float(changes.get("m5"))
    change1h = safe_float(changes.get("h1"))
    change6h = safe_float(changes.get("h6"))

    mcap = safe_float(
        pair.get("marketCap"),
        safe_float(pair.get("fdv"))
    )

    fdv = safe_float(pair.get("fdv"))
    liq = safe_float(liquidity.get("usd"))

    pair_created = safe_float(pair.get("pairCreatedAt"))

    if pair_created > 0:
        # DEX Screener normally reports milliseconds.
        if pair_created > 10_000_000_000:
            pair_created /= 1000

        age_hours = max(
            0,
            (now_ts() - pair_created) / 3600
        )
    else:
        age_hours = None

    total5 = buys5 + sells5

    # This is a transaction-imbalance proxy, NOT actual net inflow.
    flow_proxy = (
        vol5 * (buys5 - sells5) / total5
        if total5 > 0 else 0.0
    )

    flow_mc_pct = (
        abs(flow_proxy) / mcap * 100
        if mcap > 0 else 0.0
    )

    buy_sell_ratio_5m = ratio(buys5, sells5)
    buy_sell_ratio_1h = ratio(buys1h, sells1h)

    url = pair.get("url") or (
        f"https://dexscreener.com/solana/"
        f"{pair.get('pairAddress', '')}"
    )

    return {
        "address": base.get("address", ""),
        "name": base.get("name", "Unknown"),
        "symbol": base.get("symbol", "UNKNOWN"),
        "pair_address": pair.get("pairAddress", ""),
        "url": url,

        "mcap": mcap,
        "fdv": fdv,
        "price": safe_float(pair.get("priceUsd")),
        "liquidity": liq,

        "age_hours": age_hours,

        "vol5": vol5,
        "vol1h": vol1h,

        "change5": change5,
        "change1h": change1h,
        "change6h": change6h,

        "buys5": buys5,
        "sells5": sells5,
        "buys1h": buys1h,
        "sells1h": sells1h,

        "buy_sell_ratio_5m": buy_sell_ratio_5m,
        "buy_sell_ratio_1h": buy_sell_ratio_1h,

        "flow_proxy": flow_proxy,
        "flow_mc_pct": flow_mc_pct,

        "timestamp": now_ts()
    }


# ============================================================
# RUNNER SCORE
# ============================================================

def calculate_score(m):
    score = 0.0

    # Buying pressure: 0–25 points.
    ratio5 = m["buy_sell_ratio_5m"]

    if ratio5 >= 3:
        score += 25
    elif ratio5 >= 2:
        score += 21
    elif ratio5 >= 1.5:
        score += 17
    elif ratio5 >= 1.25:
        score += 12
    elif ratio5 >= 1:
        score += 5

    # Volume acceleration: 0–20 points.
    vol5 = m["vol5"]
    mcap = m["mcap"]

    vol_mc_pct = (
        vol5 / mcap * 100 if mcap > 0 else 0
    )

    if vol_mc_pct >= 15:
        score += 20
    elif vol_mc_pct >= 8:
        score += 16
    elif vol_mc_pct >= 4:
        score += 12
    elif vol_mc_pct >= 2:
        score += 7
    elif vol_mc_pct >= 1:
        score += 3

    # Short-term momentum: 0–20 points.
    change5 = m["change5"]

    if 5 <= change5 <= 25:
        score += 20
    elif 0 < change5 < 5:
        score += 12
    elif 25 < change5 <= 50:
        score += 15
    elif change5 > 50:
        score += 8
    elif change5 < 0:
        score += 2

    # Liquidity quality: 0–15 points.
    liquidity = m["liquidity"]

    if liquidity >= 25_000:
        score += 15
    elif liquidity >= 15_000:
        score += 13
    elif liquidity >= 10_000:
        score += 10
    elif liquidity >= 6_000:
        score += 7

    # Early-stage opportunity: 0–20 points.
    age = m["age_hours"]

    if age is not None:
        if age <= 1:
            score += 20
        elif age <= 3:
            score += 17
        elif age <= 12:
            score += 12
        elif age <= 24:
            score += 8
        elif age <= 48:
            score += 4

    return round(min(100, max(0, score)), 1)


# ============================================================
# ELIGIBILITY AND PERSISTENCE
# ============================================================

def rejection_reason(m):
    if not m["address"]:
        return "missing token address"

    if m["mcap"] < MIN_MCAP_USD:
        return "market cap below minimum"

    if m["mcap"] > MAX_MCAP_USD:
        return "market cap above maximum"

    if m["liquidity"] < MIN_LIQUIDITY_USD:
        return "liquidity below minimum"

    if m["age_hours"] is not None:
        if m["age_hours"] > MAX_PAIR_AGE_HOURS:
            return "pair older than maximum age"

    if m["vol5"] < MIN_5M_VOLUME_USD:
        return "5-minute volume too low"

    if m["buys5"] + m["sells5"] < MIN_5M_TRANSACTIONS:
        return "not enough 5-minute transactions"

    if m["flow_proxy"] < MIN_FLOW_PROXY_USD:
        return "buying-pressure proxy too low"

    if m["flow_mc_pct"] < MIN_FLOW_MC_PCT:
        return "flow-to-market-cap ratio too low"

    if m["buy_sell_ratio_5m"] < MIN_BUY_SELL_RATIO:
        return "buy/sell ratio too low"

    return None


def is_on_cooldown(address):
    last = safe_float(
        state["last_alert"].get(address)
    )

    return now_ts() - last < TOKEN_COOLDOWN_SECONDS


def persistence_confirmed(address, score):
    with state_lock:
        pending = state["pending"]

        entry = pending.get(address)

        if not entry:
            pending[address] = {
                "count": 1,
                "first_seen": now_ts(),
                "last_seen": now_ts(),
                "best_score": score
            }

            return False

        if now_ts() - entry["first_seen"] > 300:
            pending[address] = {
                "count": 1,
                "first_seen": now_ts(),
                "last_seen": now_ts(),
                "best_score": score
            }

            return False

        entry["count"] += 1
        entry["last_seen"] = now_ts()
        entry["best_score"] = max(
            entry["best_score"], score
        )

        return entry["count"] >= PERSISTENCE_OBSERVATIONS


# ============================================================
# INITIAL ALERT
# ============================================================

def build_alert(m, score):
    age = (
        f"{m['age_hours']:.1f}h"
        if m["age_hours"] is not None
        else "Unknown"
    )

    ratio5 = m["buy_sell_ratio_5m"]
    ratio1h = m["buy_sell_ratio_1h"]

    priority = (
        "🔥 HIGH PRIORITY"
        if score >= HIGH_PRIORITY_SCORE
        else "🟡 RUNNER CANDIDATE"
    )

    return (
        f"🚨 NEW {m['symbol']} | Score {score}/100\n\n"
        f"🪙 Token: {m['name']} (${m['symbol']})\n"
        f"⏱️ Age: {age}\n"
        f"💵 Price: ${m['price']:.10g}\n"
        f"📊 MC: {money(m['mcap'])}\n"
        f"💎 FDV: {money(m['fdv'])}\n"
        f"💧 Liquidity: {money(m['liquidity'])}\n\n"
        f"📈 5m: {percentage(m['change5'])}\n"
        f"📈 1h: {percentage(m['change1h'])}\n"
        f"📈 6h: {percentage(m['change6h'])}\n\n"
        f"📊 5m Volume: {money(m['vol5'])}\n"
        f"📊 1h Volume: {money(m['vol1h'])}\n\n"
        f"🟢 Buys (5m): {m['buys5']}\n"
        f"🔴 Sells (5m): {m['sells5']}\n"
        f"⚖️ B/S (5m): {ratio5:.2f}\n\n"
        f"🟢 Buys (1h): {m['buys1h']}\n"
        f"🔴 Sells (1h): {m['sells1h']}\n"
        f"⚖️ B/S (1h): {ratio1h:.2f}\n\n"
        f"📐 Flow proxy: {money(m['flow_proxy'])}\n"
        f"⚠️ Proxy is not actual net inflow.\n\n"
        f"{priority}\n"
        f"🛡️ Holder concentration: Not checked\n"
        f"🛡️ Sell simulation: Not checked\n\n"
        f"CA:\n{m['address']}\n\n"
        f"🔗 {m['url']}\n\n"
        f"🕒 Alerted: {utc_string()}\n"
        f"🤖 {BOT_VERSION}"
    )


# ============================================================
# FOLLOW-UP MULTIPLE TRACKING
# ============================================================

def build_milestone_alert(m, entry, multiple):
    initial_mcap = entry["initial_mcap"]

    current_mcap = m["mcap"]

    percentage_gain = (multiple - 1) * 100

    return (
        f"🚀 RUNNER MILESTONE: {multiple}X\n\n"
        f"🪙 ${m['symbol']}\n"
        f"Initial MC: {money(initial_mcap)}\n"
        f"Current MC: {money(current_mcap)}\n"
        f"Move from initial alert: +{percentage_gain:,.0f}%\n\n"
        f"Initial price: ${entry['initial_price']:.10g}\n"
        f"Current price: ${m['price']:.10g}\n"
        f"Score now: {calculate_score(m)}/100\n"
        f"Liquidity now: {money(m['liquidity'])}\n"
        f"Buys/sells (5m): {m['buys5']}/{m['sells5']}\n\n"
        f"CA:\n{m['address']}\n\n"
        f"🔗 {m['url']}\n\n"
        f"Note: Multiple is based on market-cap "
        f"comparison; market cap and liquidity can change."
    )


def track_existing_token(m):
    address = m["address"]

    with state_lock:
        entry = state["tokens"].get(address)

    if not entry:
        return

    initial_mcap = safe_float(entry.get("initial_mcap"))

    if initial_mcap <= 0:
        return

    multiple = m["mcap"] / initial_mcap

    if multiple > safe_float(entry.get("peak_multiple"), 1):
        with state_lock:
            entry["peak_multiple"] = multiple
            entry["peak_mcap"] = m["mcap"]

    sent = set(entry.get("milestones_sent", []))

    for milestone in MILESTONES:
        if multiple >= milestone and milestone not in sent:
            message = build_milestone_alert(
                m, entry, milestone
            )

            if telegram_send(message):
                with state_lock:
                    entry["milestones_sent"] = list(
                        set(entry.get("milestones_sent", []))
                        | {milestone}
                    )

                save_state()

    with state_lock:
        entry["last_mcap"] = m["mcap"]
        entry["last_price"] = m["price"]
        entry["last_seen"] = now_ts()


# ============================================================
# PROCESS CANDIDATES
# ============================================================

def process_token(address):
    pairs = fetch_token_pairs(address)

    pair = choose_best_pair(pairs)

    if not pair:
        return

    m = extract_metrics(pair)

    if not m["address"]:
        return

    # Keep monitoring tokens that have already alerted,
    # even if they later leave the entry market-cap range.
    with state_lock:
        already_tracked = m["address"] in state["tokens"]

    if already_tracked:
        track_existing_token(m)
        return

    reason = rejection_reason(m)

    if reason:
        log.info(
            "REJECT %s | MC=%s | LIQ=%s | %s",
            m["symbol"],
            money(m["mcap"]),
            money(m["liquidity"]),
            reason
        )

        return

    score = calculate_score(m)

    if score < ALERT_SCORE:
        log.info(
            "REJECT %s | score %.1f below %.1f",
            m["symbol"],
            score,
            ALERT_SCORE
        )

        return

    if is_on_cooldown(m["address"]):
        return

    if not persistence_confirmed(m["address"], score):
        log.info(
            "PENDING %s | score=%.1f",
            m["symbol"],
            score
        )

        return

    message = build_alert(m, score)

    if telegram_send(message):
        with state_lock:
            state["tokens"][m["address"]] = {
                "address": m["address"],
                "symbol": m["symbol"],
                "initial_mcap": m["mcap"],
                "initial_price": m["price"],
                "initial_score": score,
                "alert_time": now_ts(),
                "last_seen": now_ts(),
                "last_mcap": m["mcap"],
                "last_price": m["price"],
                "peak_mcap": m["mcap"],
                "peak_multiple": 1.0,
                "milestones_sent": []
            }

            state["last_alert"][m["address"]] = now_ts()

        save_state()

        log.info(
            "ALERT SENT: %s | score %.1f | MC %s",
            m["symbol"],
            score,
            money(m["mcap"])
        )


# ============================================================
# MAIN SCANNER
# ============================================================

def trim_state():
    with state_lock:
        tokens = state["tokens"]

        if len(tokens) > MAX_TRACKED_TOKENS:
            ordered = sorted(
                tokens.items(),
                key=lambda item: safe_float(
                    item[1].get("last_seen")
                )
            )

            remove_count = len(tokens) - MAX_TRACKED_TOKENS

            for address, _ in ordered[:remove_count]:
                tokens.pop(address, None)

        cutoff = now_ts() - 600

        pending = state["pending"]

        for address in list(pending.keys()):
            if safe_float(
                pending[address].get("first_seen")
            ) < cutoff:
                pending.pop(address, None)

        last_alert = state["last_alert"]

        for address in list(last_alert.keys()):
            if safe_float(last_alert[address]) < now_ts() - 86400:
                last_alert.pop(address, None)


def scanner_loop():
    addresses = []
    last_discovery = 0

    while not stop_event.is_set():
        started = now_ts()

        try:
            if (
                not addresses
                or now_ts() - last_discovery
                >= DISCOVERY_INTERVAL_SECONDS
            ):
                new_addresses = discover_pairs()

                if new_addresses:
                    addresses = list(
                        dict.fromkeys(addresses + new_addresses)
                    )

                addresses = addresses[-MAX_DISCOVERY_PAIRS:]

                last_discovery = now_ts()

                with state_lock:
                    state["last_discovery"] = last_discovery

            log.info(
                "SCAN | token addresses=%d",
                len(addresses)
            )

            for address in addresses:
                if stop_event.is_set():
                    break

                try:
                    process_token(address)

                except Exception:
                    log.exception(
                        "Token processing failed: %s",
                        address
                    )

                time.sleep(0.05)

            trim_state()
            save_state()

        except Exception:
            log.exception("Scanner loop failed")

        elapsed = now_ts() - started

        sleep_time = max(
            1,
            SCAN_INTERVAL_SECONDS - elapsed
        )

        stop_event.wait(sleep_time)


def telegram_command_loop():
    while not stop_event.is_set():
        try:
            handle_telegram_commands()

        except Exception:
            log.exception("Telegram command loop failed")

        stop_event.wait(2)


# ============================================================
# STARTUP
# ============================================================

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "ERROR: TELEGRAM_BOT_TOKEN is missing. "
            "Add it to GitHub repository secrets."
        )

    if not TELEGRAM_CHAT_ID:
        raise SystemExit(
            "ERROR: TELEGRAM_CHAT_ID is missing. "
            "Add it to GitHub repository secrets."
        )

    load_state()

    state["started_at"] = utc_string()

    log.info("Starting %s", BOT_VERSION)

    log.info(
        "Market-cap range: %s to %s",
        money(MIN_MCAP_USD),
        money(MAX_MCAP_USD)
    )

    log.info(
        "Minimum liquidity: %s",
        money(MIN_LIQUIDITY_USD)
    )

    log.info("Scan interval: %ss", SCAN_INTERVAL_SECONDS)

    if not telegram_send(
        f"🤖 {BOT_VERSION} started.\n\n"
        f"Market cap: {money(MIN_MCAP_USD)}"
        f"–{money(MAX_MCAP_USD)}\n"
        f"Minimum liquidity: {money(MIN_LIQUIDITY_USD)}\n"
        f"Scan interval: {SCAN_INTERVAL_SECONDS}s\n\n"
        f"Started: {utc_string()}"
    ):
        raise SystemExit(
            "ERROR: Could not send Telegram startup message. "
            "Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        )

    command_thread = threading.Thread(
        target=telegram_command_loop,
        daemon=True
    )

    command_thread.start()

    try:
        scanner_loop()

    except KeyboardInterrupt:
        log.info("Shutdown requested.")

    finally:
        stop_event.set()
        save_state()
        log.info("Runner Bot stopped.")


if __name__ == "__main__":
    main()
