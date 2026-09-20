import os
import json
import time
import signal
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, asdict
from typing import Optional


# ============================================================
# RUNNER BOT v3.6
# ============================================================

BOT_VERSION = "v3.6"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

DEX_BASE = "https://api.dexscreener.com"

# ------------------------------------------------------------
# Scanner settings
# ------------------------------------------------------------

SCAN_INTERVAL_SECONDS = float(
    os.getenv("DEX_SCAN_INTERVAL_SECONDS", "15")
)

MAX_TOKENS_PER_SCAN = int(
    os.getenv("DEX_MAX_TOKENS_PER_SCAN", "250")
)

# Observation band.
# We watch a wider band than the actual runner range so that
# we can see tokens before they enter the target range.
OBSERVE_MIN_MC = 10_000
OBSERVE_MAX_MC = 250_000

# Actual runner range.
MIN_MC = 20_000
MAX_MC = 200_000

MIN_LIQUIDITY = 10_000

# 5m volume is NOT a hard gate.
# It remains a scoring/context feature.
REFERENCE_VOLUME_5M = 5_000

# ------------------------------------------------------------
# History
# ------------------------------------------------------------

STATE_FILE = "runner_state.json"

MAX_STORED_TOKENS = 750
MAX_HISTORY_PER_TOKEN = 120
MIN_OBSERVATIONS = 6

# ------------------------------------------------------------
# Ignition outcome tracking
# ------------------------------------------------------------

OUTCOME_WINDOWS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
}

# These are deliberately modest because this is research mode.
# We are NOT claiming these are optimal thresholds.
CONTINUATION_MC_CHANGE = 10.0
FAILURE_MC_CHANGE = -15.0

MAX_IGNITION_EVENTS_PER_TOKEN = 20

# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------

HEATING_ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "false").lower()
    == "true"
)

# Subscribers are persisted so separate GitHub Actions runs
# can continue using the same Telegram subscribers.
TELEGRAM_STATE_FILE = "telegram_state.json"


# ============================================================
# DATA STRUCTURES
# ============================================================

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

    pair_created_at: int


@dataclass
class IgnitionEvent:
    ignition_timestamp: float
    address: str
    symbol: str

    ignition_mc: float
    ignition_liquidity: float
    ignition_volume_5m: float

    score: int

    outcome_5m: str = "PENDING"
    outcome_15m: str = "PENDING"
    outcome_30m: str = "PENDING"

    mc_5m: Optional[float] = None
    mc_15m: Optional[float] = None
    mc_30m: Optional[float] = None

    change_5m: Optional[float] = None
    change_15m: Optional[float] = None
    change_30m: Optional[float] = None


# ============================================================
# GLOBAL STATE
# ============================================================

histories = {}
ignition_events = []
telegram_chats = set()

running = True


# ============================================================
# HTTP
# ============================================================

def http_get_json(url, timeout=12):
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RunnerBot/3.6",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            raw = response.read().decode("utf-8")

        return json.loads(raw)

    except Exception as e:
        print(f"HTTP error: {e}")
        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(method, payload=None):
    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/{method}"
    )

    try:
        data = None

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "RunnerBot/3.6",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=15,
        ) as response:
            return json.loads(
                response.read().decode("utf-8")
            )

    except Exception as e:
        print(f"Telegram error: {e}")
        return None


def load_telegram_state():
    global telegram_chats

    try:
        with open(
            TELEGRAM_STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        telegram_chats = set(
            str(x)
            for x in data.get("chat_ids", [])
        )

        print(
            f"Telegram subscribers loaded: "
            f"{len(telegram_chats)}"
        )

    except Exception:
        telegram_chats = set()


def save_telegram_state():
    try:
        with open(
            TELEGRAM_STATE_FILE,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                {
                    "chat_ids": sorted(
                        telegram_chats
                    )
                },
                f,
                indent=2,
            )
    except Exception as e:
        print(f"Telegram state save error: {e}")


def send_telegram_message(text):
    if not HEATING_ALERTS_ENABLED:
        return

    if not telegram_chats:
        return

    for chat_id in list(telegram_chats):
        result = telegram_api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )

        if not result or not result.get("ok"):
            print(
                f"Telegram send failed for {chat_id}"
            )


def process_telegram_updates():
    """
    Uses long polling.

    /start subscribes the chat.
    /stop removes the chat.
    """

    offset = 0

    while running:

        result = telegram_api(
            "getUpdates",
            {
                "timeout": 1,
                "offset": offset,
            },
        )

        if not result or not result.get("ok"):
            time.sleep(2)
            continue

        updates = result.get("result", [])

        for update in updates:

            offset = (
                int(update.get("update_id", 0))
                + 1
            )

            message = update.get("message") or {}

            chat = message.get("chat") or {}
            chat_id = chat.get("id")

            text = (
                message.get("text")
                or ""
            ).strip().lower()

            if chat_id is None:
                continue

            if text == "/start":

                telegram_chats.add(
                    str(chat_id)
                )

                save_telegram_state()

                telegram_api(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": (
                            "Runner bot is online.\n\n"
                            "You are subscribed to "
                            "runner alerts."
                        ),
                    },
                )

            elif text == "/stop":

                telegram_chats.discard(
                    str(chat_id)
                )

                save_telegram_state()

                telegram_api(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": (
                            "Runner alerts disabled."
                        ),
                    },
                )

        time.sleep(1)


# ============================================================
# DEX DISCOVERY
# ============================================================

SEARCH_TERMS = [
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


def discover_addresses():
    addresses = set()

    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]

    for endpoint in endpoints:

        data = http_get_json(
            DEX_BASE + endpoint
        )

        if not isinstance(data, list):
            continue

        for item in data:

            if not isinstance(item, dict):
                continue

            chain = str(
                item.get("chainId", "")
            ).lower()

            if chain != "solana":
                continue

            address = (
                item.get("tokenAddress")
                or item.get("address")
            )

            if address:
                addresses.add(
                    str(address)
                )

    # Search API
    for term in SEARCH_TERMS:

        encoded = urllib.parse.quote(
            term
        )

        url = (
            f"{DEX_BASE}/latest/dex/search"
            f"?q={encoded}"
        )

        data = http_get_json(url)

        if not isinstance(data, dict):
            continue

        pairs = data.get("pairs") or []

        for pair in pairs:

            if not isinstance(pair, dict):
                continue

            if (
                str(
                    pair.get("chainId", "")
                ).lower()
                != "solana"
            ):
                continue

            base = (
                pair.get("baseToken")
                or {}
            )

            address = base.get(
                "address"
            )

            if address:
                addresses.add(
                    str(address)
                )

    return list(addresses)


def get_token_pairs(address):

    encoded = urllib.parse.quote(
        address,
        safe=""
    )

    url = (
        f"{DEX_BASE}/latest/dex/tokens/"
        f"{encoded}"
    )

    data = http_get_json(url)

    if not isinstance(data, dict):
        return []

    pairs = data.get("pairs") or []

    return [
        pair
        for pair in pairs
        if isinstance(pair, dict)
        and str(
            pair.get("chainId", "")
        ).lower()
        == "solana"
    ]


def discover_pairs():

    addresses = discover_addresses()

    all_pairs = []

    for address in addresses:

        pairs = get_token_pairs(
            address
        )

        all_pairs.extend(pairs)

        if len(all_pairs) >= (
            MAX_TOKENS_PER_SCAN * 3
        ):
            break

    print(
        f"{len(all_pairs)} Solana pairs discovered"
    )

    return all_pairs


# ============================================================
# PAIR SELECTION
# ============================================================

def numeric(value):

    try:
        if value is None:
            return 0.0

        return float(value)

    except Exception:
        return 0.0


def select_best_pairs(pairs):

    best = {}

    for pair in pairs:

        base = (
            pair.get("baseToken")
            or {}
        )

        address = base.get(
            "address"
        )

        if not address:
            continue

        liquidity = numeric(
            (
                pair.get("liquidity")
                or {}
            ).get("usd")
        )

        if (
            address not in best
            or liquidity
            > best[address][1]
        ):
            best[address] = (
                pair,
                liquidity,
            )

    return [
        pair
        for pair, _ in best.values()
    ]


# ============================================================
# SNAPSHOT
# ============================================================

def snapshot_from_pair(pair):

    base = (
        pair.get("baseToken")
        or {}
    )

    address = base.get(
        "address"
    )

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
        (
            pair.get("liquidity")
            or {}
        ).get("usd")
    )

    volume = (
        pair.get("volume")
        or {}
    )

    price_change = (
        pair.get("priceChange")
        or {}
    )

    transactions = (
        pair.get("txns")
        or {}
    )

    tx5 = (
        transactions.get("m5")
        or {}
    )

    pair_created = int(
        numeric(
            pair.get(
                "pairCreatedAt"
            )
        )
    )

    return TokenSnapshot(
        timestamp=time.time(),

        address=str(address),

        symbol=str(symbol),

        market_cap=market_cap,

        liquidity=liquidity,

        volume_5m=numeric(
            volume.get("m5")
        ),

        volume_1h=numeric(
            volume.get("h1")
        ),

        price=numeric(
            pair.get("priceUsd")
        ),

        price_change_5m=numeric(
            price_change.get("m5")
        ),

        price_change_1h=numeric(
            price_change.get("h1")
        ),

        buys_5m=int(
            numeric(
                tx5.get("buys")
            )
        ),

        sells_5m=int(
            numeric(
                tx5.get("sells")
            )
        ),

        pair_created_at=pair_created,
    )


# ============================================================
# HISTORY
# ============================================================

def record_snapshot(snapshot):

    address = snapshot.address

    # Ignore invalid MC.
    if (
        snapshot.market_cap
        < OBSERVE_MIN_MC
        or snapshot.market_cap
        > OBSERVE_MAX_MC
    ):
        return False

    history = histories.setdefault(
        address,
        []
    )

    # --------------------------------------------------------
    # IMPORTANT v3.6:
    # Do not create artificial observations when DEX data
    # contains no actual activity.
    # --------------------------------------------------------

    activity = (
        snapshot.volume_5m > 0
        or snapshot.buys_5m > 0
        or snapshot.sells_5m > 0
    )

    if not activity:

        # If we already have history, do not append
        # another identical zero-activity snapshot.
        return False

    history.append(
        asdict(snapshot)
    )

    if len(history) > MAX_HISTORY_PER_TOKEN:
        del history[
            :len(history)
            - MAX_HISTORY_PER_TOKEN
        ]

    return True


# ============================================================
# HELPERS
# ============================================================

def pct_change(old, new):

    if old <= 0:
        return 0.0

    return (
        (new - old)
        / old
    ) * 100.0


def address_short(address):

    if len(address) <= 12:
        return address

    return (
        address[:6]
        + "..."
        + address[-6:]
    )


def latest_snapshot(address):

    history = histories.get(
        address,
        []
    )

    if not history:
        return None

    return history[-1]


# ============================================================
# RUNNER ANALYSIS
# ============================================================

def analyze(address):

    history = histories.get(
        address,
        []
    )

    if len(history) < MIN_OBSERVATIONS:
        return None

    recent = history[
        -MIN_OBSERVATIONS:
    ]

    current = recent[-1]

    previous = recent[-2]

    recent_3 = recent[-3:]

    recent_5 = recent[-5:]

    # --------------------------------------------------------
    # Activity
    # --------------------------------------------------------

    previous_volume = previous[
        "volume_5m"
    ]

    current_volume = current[
        "volume_5m"
    ]

    volume_change = pct_change(
        previous_volume,
        current_volume,
    )

    previous_tx = (
        previous["buys_5m"]
        + previous["sells_5m"]
    )

    current_tx = (
        current["buys_5m"]
        + current["sells_5m"]
    )

    transaction_change = pct_change(
        previous_tx,
        current_tx,
    )

    # --------------------------------------------------------
    # MC movement
    # --------------------------------------------------------

    mc_change = pct_change(
        previous["market_cap"],
        current["market_cap"],
    )

    mc_change_3 = pct_change(
        recent_3[0]["market_cap"],
        current["market_cap"],
    )

    liquidity_change = pct_change(
        previous["liquidity"],
        current["liquidity"],
    )

    # --------------------------------------------------------
    # Buy pressure
    # --------------------------------------------------------

    buys = current["buys_5m"]
    sells = current["sells_5m"]

    total_tx = buys + sells

    buy_share = (
        buys / total_tx
        if total_tx > 0
        else 0.0
    )

    buy_sell_ratio = (
        buys / sells
        if sells > 0
        else (
            float("inf")
            if buys > 0
            else 0.0
        )
    )

    # --------------------------------------------------------
    # Recent MC range
    # --------------------------------------------------------

    mc_values = [
        x["market_cap"]
        for x in recent_5
        if x["market_cap"] > 0
    ]

    if mc_values:

        low_mc = min(mc_values)
        high_mc = max(mc_values)

        range_pct = (
            (high_mc - low_mc)
            / low_mc
            * 100
            if low_mc > 0
            else 0
        )

    else:
        range_pct = 0.0

    # --------------------------------------------------------
    # True recent activity check
    # --------------------------------------------------------

    active_observations = 0

    for x in recent_5:

        if (
            x["volume_5m"] > 0
            or x["buys_5m"] > 0
            or x["sells_5m"] > 0
        ):
            active_observations += 1

    # Need actual trading observations.
    if active_observations < 3:
        return {
            "state": "OBSERVING",
            "score": 0,
            "reasons": ["insufficient activity"],
            "breakout": False,
            "activity_expansion": False,
            "price_expansion": False,
            "buy_pressure": False,
            "liquidity_healthy": False,
            "range_pct": range_pct,
            "mc_change": mc_change,
            "volume_change": volume_change,
            "transaction_change": transaction_change,
            "liquidity_change": liquidity_change,
            "buy_sell_ratio": buy_sell_ratio,
            "buy_share": buy_share,
            "current": current,
        }

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    previous_mcs = [
        x["market_cap"]
        for x in recent_5[:-1]
        if x["market_cap"] > 0
    ]

    breakout = False

    if previous_mcs:

        previous_high = max(
            previous_mcs
        )

        breakout = (
            current["market_cap"]
            > previous_high * 1.03
        )

    # --------------------------------------------------------
    # Consolidation
    #
    # IMPORTANT:
    # A token with no actual trading activity cannot qualify.
    # --------------------------------------------------------

    consolidation = (
        active_observations >= 4
        and len(recent_5) >= 5
        and range_pct <= 18
        and abs(mc_change_3) <= 18
    )

    # --------------------------------------------------------
    # Activity expansion
    # --------------------------------------------------------

    activity_expansion = (
        volume_change >= 20
        or transaction_change >= 20
    )

    # --------------------------------------------------------
    # Price expansion
    # --------------------------------------------------------

    price_expansion = (
        mc_change >= 5
        or mc_change_3 >= 10
    )

    # --------------------------------------------------------
    # Buy pressure
    # --------------------------------------------------------

    buy_pressure = (
        buys > sells
        and buy_sell_ratio >= 1.20
    )

    strong_buy_pressure = (
        buys > sells
        and buy_sell_ratio >= 1.50
    )

    # --------------------------------------------------------
    # Liquidity
    # --------------------------------------------------------

    liquidity_healthy = (
        current["liquidity"]
        >= MIN_LIQUIDITY
        and liquidity_change >= -15
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0
    reasons = []

    if consolidation:
        score += 2
        reasons.append("base")

    if activity_expansion:
        score += 2
        reasons.append(
            "activity expansion"
        )

    if price_expansion:
        score += 2
        reasons.append(
            "MC expansion"
        )

    if breakout:
        score += 3
        reasons.append("breakout")

    if buy_pressure:
        score += 1
        reasons.append(
            "buy pressure"
        )

    if strong_buy_pressure:
        score += 1
        reasons.append(
            "strong buy pressure"
        )

    if liquidity_healthy:
        score += 1
        reasons.append(
            "liquidity stable"
        )

    if (
        current["volume_5m"]
        >= REFERENCE_VOLUME_5M
    ):
        score += 1
        reasons.append(
            "5m volume > $5K"
        )

    # --------------------------------------------------------
    # PENALTIES
    # --------------------------------------------------------

    if liquidity_change <= -20:
        score -= 2
        reasons.append(
            "liquidity falling"
        )

    if (
        mc_change > 40
        and volume_change < 0
    ):
        score -= 2
        reasons.append(
            "MC up but activity falling"
        )

    if (
        buy_sell_ratio < 0.8
        and total_tx > 0
    ):
        score -= 2
        reasons.append(
            "sell pressure"
        )

    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    if (
        breakout
        and activity_expansion
        and score >= 7
    ):
        state = "IGNITION"

    elif breakout:
        state = "STRUCTURE BREAK"

    elif (
        activity_expansion
        and price_expansion
    ):
        state = "EXPANSION"

    elif consolidation:
        state = "CONSOLIDATION"

    else:
        state = "OBSERVING"

    return {
        "state": state,
        "score": score,
        "reasons": reasons,
        "breakout": breakout,
        "activity_expansion": activity_expansion,
        "price_expansion": price_expansion,
        "buy_pressure": buy_pressure,
        "liquidity_healthy": liquidity_healthy,
        "range_pct": range_pct,
        "mc_change": mc_change,
        "volume_change": volume_change,
        "transaction_change": transaction_change,
        "liquidity_change": liquidity_change,
        "buy_sell_ratio": buy_sell_ratio,
        "buy_share": buy_share,
        "current": current,
    }


# ============================================================
# IGNITION EVENTS
# ============================================================

def has_recent_ignition(address):

    now = time.time()

    for event in ignition_events:

        if (
            event["address"] == address
            and now
            - event["ignition_timestamp"]
            < 30 * 60
        ):
            return True

    return False


def create_ignition_event(
    address,
    analysis,
):

    if has_recent_ignition(address):
        return

    current = analysis["current"]

    event = IgnitionEvent(
        ignition_timestamp=time.time(),

        address=address,

        symbol=current["symbol"],

        ignition_mc=current[
            "market_cap"
        ],

        ignition_liquidity=current[
            "liquidity"
        ],

        ignition_volume_5m=current[
            "volume_5m"
        ],

        score=analysis["score"],
    )

    ignition_events.append(
        asdict(event)
    )

    # Limit total memory.
    if len(ignition_events) > 500:
        del ignition_events[
            :-500
        ]

    print(
        "IGNITION EVENT CREATED | "
        f"{current['symbol']} | "
        f"CA={address_short(address)} | "
        f"MC=${current['market_cap']:,.0f} | "
        f"score={analysis['score']}"
    )

    send_telegram_message(
        "🔥 RUNNER IGNITION DETECTED\n\n"
        f"Token: {current['symbol']}\n"
        f"CA: {address}\n"
        f"MC: ${current['market_cap']:,.0f}\n"
        f"Liquidity: ${current['liquidity']:,.0f}\n"
        f"5m Volume: ${current['volume_5m']:,.0f}\n"
        f"Buys/Sells: "
        f"{current['buys_5m']}/"
        f"{current['sells_5m']}\n"
        f"Score: {analysis['score']}\n\n"
        "Research signal — not a guarantee."
    )


# ============================================================
# OUTCOME TRACKER
# ============================================================

def evaluate_outcome(
    baseline_mc,
    current_mc,
):

    change = pct_change(
        baseline_mc,
        current_mc,
    )

    if change >= CONTINUATION_MC_CHANGE:
        return (
            "CONTINUING",
            change,
        )

    if change <= FAILURE_MC_CHANGE:
        return (
            "FAILED",
            change,
        )

    return (
        "UNCLEAR",
        change,
    )


def update_ignition_outcomes():

    now = time.time()

    for event in ignition_events:

        address = event["address"]

        elapsed = (
            now
            - event["ignition_timestamp"]
        )

        history = histories.get(
            address,
            []
        )

        if not history:
            continue

        baseline = event[
            "ignition_mc"
        ]

        latest = history[-1]

        current_mc = latest[
            "market_cap"
        ]

        # ----------------------------------------------------
        # +5 MIN
        # ----------------------------------------------------

        if (
            elapsed >= OUTCOME_WINDOWS["5m"]
            and event["outcome_5m"]
            == "PENDING"
        ):

            status, change = (
                evaluate_outcome(
                    baseline,
                    current_mc,
                )
            )

            event["outcome_5m"] = status
            event["mc_5m"] = current_mc
            event["change_5m"] = change

            print(
                "IGNITION +5M | "
                f"{event['symbol']} | "
                f"CA={address_short(address)} | "
                f"MC=${current_mc:,.0f} | "
                f"change={change:+.1f}% | "
                f"{status}"
            )

        # ----------------------------------------------------
        # +15 MIN
        # ----------------------------------------------------

        if (
            elapsed >= OUTCOME_WINDOWS["15m"]
            and event["outcome_15m"]
            == "PENDING"
        ):

            status, change = (
                evaluate_outcome(
                    baseline,
                    current_mc,
                )
            )

            event["outcome_15m"] = status
            event["mc_15m"] = current_mc
            event["change_15m"] = change

            print(
                "IGNITION +15M | "
                f"{event['symbol']} | "
                f"CA={address_short(address)} | "
                f"MC=${current_mc:,.0f} | "
                f"change={change:+.1f}% | "
                f"{status}"
            )

        # ----------------------------------------------------
        # +30 MIN
        # ----------------------------------------------------

        if (
            elapsed >= OUTCOME_WINDOWS["30m"]
            and event["outcome_30m"]
            == "PENDING"
        ):

            status, change = (
                evaluate_outcome(
                    baseline,
                    current_mc,
                )
            )

            event["outcome_30m"] = status
            event["mc_30m"] = current_mc
            event["change_30m"] = change

            print(
                "IGNITION +30M | "
                f"{event['symbol']} | "
                f"CA={address_short(address)} | "
                f"MC=${current_mc:,.0f} | "
                f"change={change:+.1f}% | "
                f"{status}"
            )


# ============================================================
# TRACKING OUTPUT
# ============================================================

def print_tracking(
    address,
    analysis,
):

    current = analysis["current"]

    symbol = current["symbol"]

    print(
        "TRACKING "
        f"{symbol} | "
        f"CA={address_short(address)} | "
        f"state={analysis['state']} | "
        f"obs={len(histories[address])} | "
        f"MC=${current['market_cap']/1000:.2f}K | "
        f"5mVol=${current['volume_5m']/1000:.2f}K | "
        f"buys/sells="
        f"{current['buys_5m']}/"
        f"{current['sells_5m']} | "
        f"volChange="
        f"{analysis['volume_change']:.1f}% | "
        f"txChange="
        f"{analysis['transaction_change']:.1f}% | "
        f"MCmove="
        f"{analysis['mc_change']:.1f}% | "
        f"liqMove="
        f"{analysis['liquidity_change']:.1f}% | "
        f"range="
        f"{analysis['range_pct']:.1f}% | "
        f"breakout="
        f"{analysis['breakout']} | "
        f"score="
        f"{analysis['score']} | "
        f"reasons="
        f"{','.join(analysis['reasons'])}"
    )


# ============================================================
# PERSISTENCE
# ============================================================

def save_state():

    try:

        data = {
            "version": BOT_VERSION,

            "histories": histories,

            "ignition_events": ignition_events,
        }

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                separators=(",", ":"),
            )

        print(
            "Persistent state saved: "
            f"{len(histories)} tokens | "
            f"{len(ignition_events)} ignition events"
        )

    except Exception as e:
        print(
            f"State save error: {e}"
        )


def load_state():

    global histories
    global ignition_events

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        histories = data.get(
            "histories",
            {}
        )

        ignition_events = data.get(
            "ignition_events",
            []
        )

        print(
            "Persistent state loaded: "
            f"{len(histories)} tokens | "
            f"{len(ignition_events)} ignition events"
        )

    except FileNotFoundError:

        histories = {}
        ignition_events = []

        print(
            "No previous runner state found."
        )

    except Exception as e:

        print(
            f"State load error: {e}"
        )

        histories = {}
        ignition_events = []


# ============================================================
# SCAN
# ============================================================

def scan_once():

    pairs = discover_pairs()

    best_pairs = select_best_pairs(
        pairs
    )

    if len(best_pairs) > MAX_TOKENS_PER_SCAN:
        best_pairs = best_pairs[
            :MAX_TOKENS_PER_SCAN
        ]

    unique_tokens = len(best_pairs)

    histories_recorded = 0

    in_runner_range = 0

    reached_liquidity = 0

    mc_reject = 0

    liquidity_reject = 0

    volume_reference = 0

    missing_data = 0

    ignition_count = 0

    for pair in best_pairs:

        try:

            snapshot = snapshot_from_pair(
                pair
            )

            if not snapshot.address:
                missing_data += 1
                continue

            # --------------------------------------------
            # Record history BEFORE runner filters.
            # --------------------------------------------

            recorded = record_snapshot(
                snapshot
            )

            if recorded:
                histories_recorded += 1

            # --------------------------------------------
            # Observation band
            # --------------------------------------------

            if (
                snapshot.market_cap
                < OBSERVE_MIN_MC
                or snapshot.market_cap
                > OBSERVE_MAX_MC
            ):
                mc_reject += 1
                continue

            # --------------------------------------------
            # Runner MC range
            # --------------------------------------------

            if (
                MIN_MC
                <= snapshot.market_cap
                <= MAX_MC
            ):
                in_runner_range += 1

            # --------------------------------------------
            # Liquidity
            # --------------------------------------------

            if (
                snapshot.liquidity
                < MIN_LIQUIDITY
            ):
                liquidity_reject += 1
                continue

            reached_liquidity += 1

            # --------------------------------------------
            # Volume is now ONLY a reference feature.
            # --------------------------------------------

            if (
                snapshot.volume_5m
                >= REFERENCE_VOLUME_5M
            ):
                volume_reference += 1

            # --------------------------------------------
            # Analyze
            # --------------------------------------------

            analysis = analyze(
                snapshot.address
            )

            if analysis is None:
                continue

            # Only print meaningful histories.
            if len(
                histories.get(
                    snapshot.address,
                    []
                )
            ) >= MIN_OBSERVATIONS:

                print_tracking(
                    snapshot.address,
                    analysis,
                )

            # --------------------------------------------
            # Ignition
            # --------------------------------------------

            if (
                analysis["state"]
                == "IGNITION"
            ):

                if not has_recent_ignition(
                    snapshot.address
                ):

                    create_ignition_event(
                        snapshot.address,
                        analysis,
                    )

                    ignition_count += 1

        except Exception as e:

            print(
                "Token processing error: "
                f"{e}"
            )

    # --------------------------------------------------------
    # Update old ignition events.
    # --------------------------------------------------------

    update_ignition_outcomes()

    print(
        "DEX scan complete: "
        f"{len(pairs)} Solana pairs discovered | "
        f"{unique_tokens} unique tokens | "
        f"{histories_recorded} histories recorded | "
        f"{in_runner_range} in runner range | "
        f"{reached_liquidity} reached liquidity filter | "
        f"MC outside range={mc_reject} | "
        f"liquidity below minimum={liquidity_reject} | "
        f"volume >= reference={volume_reference} | "
        f"missing data={missing_data} | "
        f"ignition={ignition_count}"
    )


# ============================================================
# SHUTDOWN
# ============================================================

def handle_shutdown(
    signum=None,
    frame=None,
):

    global running

    if not running:
        return

    print(
        "Shutdown requested..."
    )

    running = False

    save_state()

    save_telegram_state()


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        f"Runner Engine {BOT_VERSION} is online"
    )

    print(
        "Runner range: "
        f"MC=${MIN_MC/1000:.2f}K-"
        f"${MAX_MC/1000:.0f}K | "
        f"minimum liquidity="
        f"${MIN_LIQUIDITY/1000:.0f}K | "
        f"5m volume is a FEATURE, "
        f"not a hard gate | "
        f"scan interval="
        f"{SCAN_INTERVAL_SECONDS}s"
    )

    print(
        "Observation band: "
        f"${OBSERVE_MIN_MC/1000:.0f}K-"
        f"${OBSERVE_MAX_MC/1000:.0f}K"
    )

    print(
        "Ignition outcome tracking: "
        "+5m / +15m / +30m"
    )

    print(
        "Telegram alerts enabled: "
        f"{HEATING_ALERTS_ENABLED}"
    )

    load_state()

    load_telegram_state()

    signal.signal(
        signal.SIGTERM,
        handle_shutdown,
    )

    signal.signal(
        signal.SIGINT,
        handle_shutdown,
    )

    # --------------------------------------------------------
    # Telegram polling runs in a separate thread.
    # --------------------------------------------------------

    import threading

    telegram_thread = threading.Thread(
        target=process_telegram_updates,
        daemon=True,
    )

    telegram_thread.start()

    # --------------------------------------------------------
    # Main scan loop
    # --------------------------------------------------------

    while running:

        started = time.time()

        try:

            scan_once()

        except Exception as e:

            print(
                f"Scan error: {e}"
            )

        # Save after every scan.
        save_state()

        elapsed = (
            time.time()
            - started
        )

        sleep_for = max(
            1,
            SCAN_INTERVAL_SECONDS
            - elapsed,
        )

        if running:

            time.sleep(
                sleep_for
            )

    print(
        "Runner Engine stopped."
    )


if __name__ == "__main__":
    main()
