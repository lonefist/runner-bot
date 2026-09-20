import os
import json
import time
import logging
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from typing import Optional

# ============================================================
# RUNNER ENGINE v3.5
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s runner_bot: %(message)s"
)
logger = logging.getLogger("runner_bot")

# -----------------------------
# CONFIG
# -----------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

DEX_BASE = "https://api.dexscreener.com"

SCAN_INTERVAL = float(os.getenv("DEX_SCAN_INTERVAL_SECONDS", "15"))

# Observation band:
# We WATCH tokens here even before they enter the runner range.
OBSERVE_MIN_MC = 10_000
OBSERVE_MAX_MC = 250_000

# Main runner range
MIN_MC = 20_000
MAX_MC = 200_000

MIN_LIQUIDITY = 10_000

# This is NO LONGER a hard gate.
# It is only used as a feature.
REFERENCE_VOLUME_5M = 5_000

MAX_PRICE_CHANGE_5M = 100

MAX_STORED_TOKENS = 750
MAX_HISTORY_PER_TOKEN = 180

MIN_OBSERVATIONS = 6

STATE_FILE = "runner_state.json"

HEATING_ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "false").lower() == "true"
)

# Prevent repeated Telegram alerts
ALERT_COOLDOWN_SECONDS = 3600

# -----------------------------
# TELEGRAM STATE
# -----------------------------

subscribers = set()
last_alert_time = {}

telegram_offset = 0

# -----------------------------
# DATA CLASSES
# -----------------------------


@dataclass
class TokenSnapshot:
    timestamp: float
    address: str
    symbol: str

    market_cap: float
    liquidity: float

    volume_5m: float
    volume_1h: float

    price: float

    price_change_5m: float
    price_change_1h: float

    buys_5m: int
    sells_5m: int

    pair_created_at: Optional[int]


# -----------------------------
# PERSISTENT HISTORY
# -----------------------------

histories = {}


def load_state():
    global histories

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        histories = data.get("histories", {})

        logger.info(
            "Persistent state loaded: %s tokens",
            len(histories)
        )

    except FileNotFoundError:
        histories = {}
        logger.info("No persistent state found")

    except Exception as e:
        logger.warning("Could not load state: %s", e)
        histories = {}


def save_state():
    try:
        # Limit total tokens
        if len(histories) > MAX_STORED_TOKENS:
            items = list(histories.items())

            items.sort(
                key=lambda x: (
                    x[1][-1]["timestamp"]
                    if x[1]
                    else 0
                ),
                reverse=True
            )

            histories.clear()

            for address, history in items[:MAX_STORED_TOKENS]:
                histories[address] = history

        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"histories": histories},
                f,
                separators=(",", ":")
            )

        logger.info(
            "Persistent state saved: %s tokens",
            len(histories)
        )

    except Exception as e:
        logger.warning("Could not save state: %s", e)


# -----------------------------
# HTTP
# -----------------------------


def http_get_json(url, timeout=10):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "runner-bot/3.5"
            }
        )

        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")

        return json.loads(raw)

    except Exception as e:
        logger.warning("HTTP error: %s", e)
        return None


# -----------------------------
# DEX DISCOVERY
# -----------------------------


def discover_pairs():
    """
    Build a broad Solana discovery pool.

    We deliberately use multiple discovery routes because
    one endpoint alone can produce a very small universe.
    """

    pairs = {}

    profile_urls = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]

    for endpoint in profile_urls:
        data = http_get_json(DEX_BASE + endpoint)

        if not isinstance(data, list):
            continue

        for item in data:
            chain_id = str(item.get("chainId", "")).lower()

            if chain_id != "solana":
                continue

            address = (
                item.get("tokenAddress")
                or item.get("address")
            )

            if address:
                pairs[address] = address

    search_terms = [
        "SOL",
        "USDC",
        "USDT",
        "WSOL",
        "pump",
        "meme",
        "cat",
        "dog",
        "ai",
        "inu",
        "pepe",
        "coin",
        "doge",
        "frog",
        "trump",
        "elon",
        "baby",
    ]

    for term in search_terms:

        encoded = urllib.parse.quote(term)

        url = (
            f"{DEX_BASE}/latest/dex/search"
            f"?q={encoded}"
        )

        data = http_get_json(url)

        if not isinstance(data, dict):
            continue

        for pair in data.get("pairs", []):

            if str(pair.get("chainId", "")).lower() != "solana":
                continue

            token = pair.get("baseToken", {})
            address = token.get("address")

            if address:
                pairs[address] = pair

    # Resolve addresses that came from profiles/boosts
    # and keep already-resolved search pairs.
    resolved = []

    for address, value in pairs.items():

        if isinstance(value, dict):
            resolved.append(value)
            continue

        # Search by token address.
        encoded = urllib.parse.quote(address)

        url = (
            f"{DEX_BASE}/latest/dex/tokens/"
            f"{encoded}"
        )

        data = http_get_json(url)

        if not isinstance(data, dict):
            continue

        for pair in (data.get("pairs") or []):
            if str(pair.get("chainId", "")).lower() == "solana":
                resolved.append(pair)

    return resolved


# -----------------------------
# PAIR SELECTION
# -----------------------------


def numeric(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def select_best_pairs(pairs):

    best = {}

    for pair in pairs:

        if str(pair.get("chainId", "")).lower() != "solana":
            continue

        base = pair.get("baseToken", {})
        address = base.get("address")

        if not address:
            continue

        liquidity = numeric(
            pair.get("liquidity", {}).get("usd")
        )

        existing = best.get(address)

        if existing is None:
            best[address] = pair
        else:
            existing_liq = numeric(
                existing.get("liquidity", {}).get("usd")
            )

            if liquidity > existing_liq:
                best[address] = pair

    return list(best.values())


# -----------------------------
# SNAPSHOT CREATION
# -----------------------------


def snapshot_from_pair(pair):

    base = pair.get("baseToken", {})

    address = base.get("address")

    if not address:
        return None

    symbol = (
        base.get("symbol")
        or "UNKNOWN"
    )

    market_cap = numeric(
        pair.get("marketCap")
    )

    if market_cap <= 0:
        market_cap = numeric(
            pair.get("fdv")
        )

    liquidity = numeric(
        pair.get("liquidity", {}).get("usd")
    )

    volume = pair.get("volume", {})

    volume_5m = numeric(
        volume.get("m5")
    )

    volume_1h = numeric(
        volume.get("h1")
    )

    price = numeric(
        pair.get("priceUsd")
    )

    price_change = pair.get(
        "priceChange", {}
    )

    change_5m = numeric(
        price_change.get("m5")
    )

    change_1h = numeric(
        price_change.get("h1")
    )

    txns = pair.get("txns", {})
    tx5 = txns.get("m5", {})

    buys = int(
        numeric(tx5.get("buys"))
    )

    sells = int(
        numeric(tx5.get("sells"))
    )

    pair_created = pair.get(
        "pairCreatedAt"
    )

    return TokenSnapshot(
        timestamp=time.time(),
        address=address,
        symbol=symbol,
        market_cap=market_cap,
        liquidity=liquidity,
        volume_5m=volume_5m,
        volume_1h=volume_1h,
        price=price,
        price_change_5m=change_5m,
        price_change_1h=change_1h,
        buys_5m=buys,
        sells_5m=sells,
        pair_created_at=pair_created,
    )


# -----------------------------
# HISTORY
# -----------------------------


def record_snapshot(snapshot):

    address = snapshot.address

    history = histories.setdefault(
        address,
        []
    )

    history.append(
        asdict(snapshot)
    )

    if len(history) > MAX_HISTORY_PER_TOKEN:
        del history[
            :-MAX_HISTORY_PER_TOKEN
        ]


# -----------------------------
# FEATURE HELPERS
# -----------------------------


def safe_ratio(a, b):
    if b <= 0:
        return 0.0

    return a / b


def pct_change(current, previous):

    if previous == 0:
        return 0.0

    return (
        (current - previous)
        / abs(previous)
    ) * 100


def get_recent(history, count):

    if len(history) <= count:
        return history

    return history[-count:]


def local_high(history):

    if not history:
        return 0

    return max(
        x["market_cap"]
        for x in history
    )


def local_low(history):

    if not history:
        return 0

    return min(
        x["market_cap"]
        for x in history
    )


# -----------------------------
# RUNNER ANALYSIS
# -----------------------------


def analyze(snapshot):

    address = snapshot.address

    history = histories.get(
        address,
        []
    )

    if len(history) < MIN_OBSERVATIONS:
        return None

    current = history[-1]

    previous = history[-2]

    # ------------------------------------------------
    # BASIC ACTIVITY
    # ------------------------------------------------

    volume_change = pct_change(
        current["volume_5m"],
        previous["volume_5m"]
    )

    transaction_current = (
        current["buys_5m"]
        + current["sells_5m"]
    )

    transaction_previous = (
        previous["buys_5m"]
        + previous["sells_5m"]
    )

    transaction_change = pct_change(
        transaction_current,
        transaction_previous
    )

    mc_change = pct_change(
        current["market_cap"],
        previous["market_cap"]
    )

    liquidity_change = pct_change(
        current["liquidity"],
        previous["liquidity"]
    )

    # ------------------------------------------------
    # SHORT-TERM MOMENTUM
    # ------------------------------------------------

    recent_3 = get_recent(
        history,
        3
    )

    recent_5 = get_recent(
        history,
        5
    )

    first_3 = recent_3[0]

    mc_move_3 = pct_change(
        current["market_cap"],
        first_3["market_cap"]
    )

    vol_move_3 = pct_change(
        current["volume_5m"],
        first_3["volume_5m"]
    )

    tx_now = (
        current["buys_5m"]
        + current["sells_5m"]
    )

    tx_start = (
        recent_3[0]["buys_5m"]
        + recent_3[0]["sells_5m"]
    )

    tx_move_3 = pct_change(
        tx_now,
        tx_start
    )

    # ------------------------------------------------
    # BUYING PRESSURE
    # ------------------------------------------------

    buys = current["buys_5m"]
    sells = current["sells_5m"]

    buy_sell_ratio = safe_ratio(
        buys,
        sells
    )

    buy_share = safe_ratio(
        buys,
        buys + sells
    )

    # ------------------------------------------------
    # RANGE / CONSOLIDATION
    # ------------------------------------------------

    mc_values = [
        x["market_cap"]
        for x in recent_5
        if x["market_cap"] > 0
    ]

    if mc_values:

        high = max(mc_values)
        low = min(mc_values)

        range_pct = (
            (high - low)
            / low
        ) * 100 if low > 0 else 0

    else:
        range_pct = 0

    # ------------------------------------------------
    # BREAKOUT
    # ------------------------------------------------

    older_history = history[:-1]

    if older_history:

        previous_high = max(
            x["market_cap"]
            for x in older_history[-5:]
        )

        breakout = (
            current["market_cap"]
            > previous_high * 1.03
        )

    else:
        breakout = False

    # ------------------------------------------------
    # CONSOLIDATION
    # ------------------------------------------------

    consolidation = (
        len(recent_5) >= 5
        and range_pct <= 18
        and mc_move_3 <= 18
    )

    # ------------------------------------------------
    # ACTIVITY EXPANSION
    # ------------------------------------------------

    activity_expansion = (
        volume_change >= 20
        or vol_move_3 >= 40
        or transaction_change >= 20
        or tx_move_3 >= 40
    )

    # ------------------------------------------------
    # PRICE EXPANSION
    # ------------------------------------------------

    price_expansion = (
        mc_change >= 5
        or mc_move_3 >= 10
    )

    # ------------------------------------------------
    # LIQUIDITY HEALTH
    # ------------------------------------------------

    liquidity_healthy = (
        current["liquidity"] >= MIN_LIQUIDITY
        and liquidity_change >= -15
    )

    # ------------------------------------------------
    # RUNNER SCORE
    # ------------------------------------------------

    score = 0
    reasons = []

    if consolidation:
        score += 2
        reasons.append("base")

    if activity_expansion:
        score += 2
        reasons.append("activity expansion")

    if price_expansion:
        score += 2
        reasons.append("MC expansion")

    if breakout:
        score += 3
        reasons.append("breakout")

    if buy_sell_ratio >= 1.25:
        score += 1
        reasons.append("buy pressure")

    if buy_share >= 0.55:
        score += 1

    if liquidity_healthy:
        score += 1
        reasons.append("liquidity stable")

    # Reference-volume context,
    # NOT a hard rejection.
    if current["volume_5m"] >= REFERENCE_VOLUME_5M:
        score += 1
        reasons.append("5m volume > $5K")

    # ------------------------------------------------
    # NEGATIVE CONDITIONS
    # ------------------------------------------------

    penalties = []

    if liquidity_change <= -20:
        score -= 2
        penalties.append("liquidity falling")

    if mc_change > 40 and volume_change < 0:
        score -= 2
        penalties.append("price/volume divergence")

    if buy_sell_ratio < 0.80:
        score -= 2
        penalties.append("sell pressure")

    # ------------------------------------------------
    # STATE
    # ------------------------------------------------

    if breakout and activity_expansion and score >= 7:
        state = "IGNITION"

    elif breakout:
        state = "STRUCTURE BREAK"

    elif activity_expansion and price_expansion:
        state = "EXPANSION"

    elif consolidation:
        state = "CONSOLIDATION"

    else:
        state = "OBSERVING"

    # ------------------------------------------------
    # LOGGING
    # ------------------------------------------------

    in_runner_range = (
        MIN_MC
        <= current["market_cap"]
        <= MAX_MC
    )

    if in_runner_range:

        logger.info(
            "TRACKING %s | state=%s | obs=%s | "
            "MC=%.2fK | 5mVol=%.2fK | "
            "buys/sells=%s/%s | "
            "volChange=%.1f%% | txChange=%.1f%% | "
            "MCmove=%.1f%% | liqMove=%.1f%% | "
            "range=%.1f%% | breakout=%s | "
            "score=%s | reasons=%s",
            current["symbol"],
            state,
            len(history),
            current["market_cap"] / 1000,
            current["volume_5m"] / 1000,
            buys,
            sells,
            volume_change,
            transaction_change,
            mc_change,
            liquidity_change,
            range_pct,
            breakout,
            score,
            ",".join(reasons)
        )

    return {
        "state": state,
        "score": score,
        "reasons": reasons,
        "penalties": penalties,
        "history_length": len(history),
        "volume_change": volume_change,
        "transaction_change": transaction_change,
        "mc_change": mc_change,
        "liquidity_change": liquidity_change,
        "range_pct": range_pct,
        "breakout": breakout,
    }


# -----------------------------
# TELEGRAM
# -----------------------------


def telegram_request(method, payload):

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

    try:

        data = urllib.parse.urlencode(
            payload
        ).encode()

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "runner-bot/3.5"
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=15
        ) as response:

            return json.loads(
                response.read().decode()
            )

    except Exception as e:
        logger.warning(
            "Telegram error: %s",
            e
        )

        return None


def send_message(chat_id, text):

    return telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    )


def poll_telegram():

    global telegram_offset

    if not TELEGRAM_BOT_TOKEN:
        return

    result = telegram_request(
        "getUpdates",
        {
            "timeout": 1,
            "offset": telegram_offset,
        }
    )

    if not result or not result.get("ok"):
        return

    for update in result.get(
        "result",
        []
    ):

        telegram_offset = (
            update["update_id"] + 1
        )

        message = update.get(
            "message"
        )

        if not message:
            continue

        chat = message.get("chat", {})
        chat_id = chat.get("id")

        text = (
            message.get("text")
            or ""
        ).strip()

        if not chat_id:
            continue

        if text == "/start":

            subscribers.add(
                chat_id
            )

            send_message(
                chat_id,
                "Runner bot is online.\n"
                "Runner Engine v3.5 is active.\n"
                "Historical runner tracking is enabled."
            )

            logger.info(
                "Subscriber added: %s",
                chat_id
            )

        elif text == "/status":

            send_message(
                chat_id,
                f"Runner Engine v3.5\n"
                f"Tracked tokens: {len(histories)}\n"
                f"Alerts enabled: {HEATING_ALERTS_ENABLED}"
            )


# -----------------------------
# ALERT
# -----------------------------


def send_ignition_alert(snapshot, analysis):

    if not HEATING_ALERTS_ENABLED:
        return

    now = time.time()

    last = last_alert_time.get(
        snapshot.address,
        0
    )

    if (
        now - last
        < ALERT_COOLDOWN_SECONDS
    ):
        return

    last_alert_time[
        snapshot.address
    ] = now

    text = (
        "🔥 RUNNER IGNITION DETECTED\n\n"
        f"${snapshot.symbol}\n"
        f"MC: ${snapshot.market_cap:,.0f}\n"
        f"Liquidity: ${snapshot.liquidity:,.0f}\n"
        f"5m Volume: ${snapshot.volume_5m:,.0f}\n"
        f"Buys/Sells: "
        f"{snapshot.buys_5m}/"
        f"{snapshot.sells_5m}\n"
        f"Score: {analysis['score']}\n"
        f"State: {analysis['state']}\n\n"
        f"Signals: "
        f"{', '.join(analysis['reasons'])}\n\n"
        f"CA:\n{snapshot.address}\n\n"
        "DYOR. Experimental signal only."
    )

    for chat_id in list(subscribers):

        send_message(
            chat_id,
            text
        )


# -----------------------------
# MAIN SCAN
# -----------------------------


def run_scan():

    pairs = discover_pairs()

    selected = select_best_pairs(
        pairs
    )

    histories_recorded = 0
    runner_range = 0
    full_filter = 0

    mc_reject = 0
    liquidity_reject = 0
    volume_reject = 0
    missing_data = 0
    ignition = 0

    for pair in selected:

        snapshot = snapshot_from_pair(
            pair
        )

        if snapshot is None:
            missing_data += 1
            continue

        # ------------------------------------------
        # IMPORTANT:
        # HISTORY IS RECORDED BEFORE OLD FILTERS.
        # ------------------------------------------

        if (
            OBSERVE_MIN_MC
            <= snapshot.market_cap
            <= OBSERVE_MAX_MC
        ):

            record_snapshot(
                snapshot
            )

            histories_recorded += 1

            result = analyze(
                snapshot
            )

            if result:

                if (
                    MIN_MC
                    <= snapshot.market_cap
                    <= MAX_MC
                ):

                    runner_range += 1

                    if (
                        snapshot.liquidity
                        >= MIN_LIQUIDITY
                    ):
                        full_filter += 1

                    if result["state"] == "IGNITION":
                        ignition += 1

                        send_ignition_alert(
                            snapshot,
                            result
                        )

        # Statistics only.
        # These DO NOT stop historical tracking.

        if snapshot.market_cap < MIN_MC:
            mc_reject += 1

        elif snapshot.market_cap > MAX_MC:
            mc_reject += 1

        if (
            snapshot.liquidity
            < MIN_LIQUIDITY
        ):
            liquidity_reject += 1

        if (
            snapshot.volume_5m
            < REFERENCE_VOLUME_5M
        ):
            volume_reject += 1

    logger.info(
        "DEX scan complete: %s Solana pairs discovered | "
        "%s unique tokens | "
        "%s histories recorded | "
        "%s in runner range | "
        "%s reached liquidity filter | "
        "MC outside range=%s | "
        "liquidity below minimum=%s | "
        "volume below reference=%s | "
        "missing data=%s | "
        "ignition=%s",
        len(pairs),
        len(selected),
        histories_recorded,
        runner_range,
        full_filter,
        mc_reject,
        liquidity_reject,
        volume_reject,
        missing_data,
        ignition
    )

    save_state()


# -----------------------------
# MAIN LOOP
# -----------------------------


def main():

    logger.info(
        "Runner Engine v3.5 is online"
    )

    logger.info(
        "Runner range: MC=$%.2fK-$%.2fK | "
        "observation band=$%.2fK-$%.2fK | "
        "minimum liquidity=$%.2fK | "
        "5m volume is now a FEATURE, not a hard gate | "
        "scan interval=%.1fs",
        MIN_MC / 1000,
        MAX_MC / 1000,
        OBSERVE_MIN_MC / 1000,
        OBSERVE_MAX_MC / 1000,
        MIN_LIQUIDITY / 1000,
        SCAN_INTERVAL
    )

    logger.info(
        "Telegram alerts enabled: %s",
        HEATING_ALERTS_ENABLED
    )

    load_state()

    while True:

        try:
            poll_telegram()
            run_scan()

        except KeyboardInterrupt:
            logger.info(
                "Bot stopped"
            )
            break

        except Exception as e:
            logger.exception(
                "Main loop error: %s",
                e
            )

        time.sleep(
            SCAN_INTERVAL
        )


if __name__ == "__main__":
    main()
