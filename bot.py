import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V4.1
# DEX SCREENER FIRST
# ============================================================

BOT_VERSION = "V4.1-DIAGNOSTIC"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

SOLANA = "solana"

STATE_FILE = "runner_state_v41.json"


# ============================================================
# CORE FILTERS
# ============================================================

MIN_MC = 20_000
MAX_MC = 300_000

MIN_LIQUIDITY = 10_000

MAX_PAIR_AGE_HOURS = 24


# ============================================================
# OBSERVATION / HISTORY
# ============================================================

OBSERVE_MIN_MC = 10_000
OBSERVE_MAX_MC = 350_000

MIN_OBSERVATIONS = 6

MAX_HISTORY_PER_TOKEN = 300
MAX_STORED_TOKENS = 1_500


# ============================================================
# RUNNER CONDITIONS
# ============================================================

REFERENCE_VOLUME_5M = 5_000

MIN_CONSOLIDATION_VOLUME_5M = 500
MIN_CONSOLIDATION_TX_5M = 5
MIN_CONSOLIDATION_ACTIVE_OBS = 3

MAX_CONSOLIDATION_RANGE_PCT = 18.0

ACTIVITY_LOOKBACK_SECONDS = 60
ACTIVITY_EXPANSION_PCT = 20.0


# ============================================================
# SCANNING
# ============================================================

SCAN_INTERVAL_SECONDS = float(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)

DISCOVERY_INTERVAL_SECONDS = 60

MAX_DISCOVERY_TOKENS = 500
MAX_TOKEN_LOOKUPS_PER_CYCLE = 120

TOKEN_LOOKUP_SLEEP = 0.10


# ============================================================
# HTTP
# ============================================================

HTTP_TIMEOUT = 12
HTTP_RETRIES = 3
HTTP_BACKOFF_SECONDS = 1.5


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

HEATING_ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "true").lower()
    == "true"
)


# ============================================================
# STATE
# ============================================================

state: Dict[str, Any] = {
    "subscribers": [],
    "tokens": {},
    "alerts": {},
    "last_discovery": 0,
}


force_scan_requested = False


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_ts() -> float:
    return time.time()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def format_money(value: Any) -> str:
    value = safe_float(value)

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:,.0f}"


def format_price(value: Any) -> str:
    value = safe_float(value)

    if value == 0:
        return "$0"

    if value < 0.000001:
        return f"${value:.10f}"

    if value < 0.001:
        return f"${value:.8f}"

    if value < 1:
        return f"${value:.6f}"

    return f"${value:.4f}"


# ============================================================
# STATE FILE
# ============================================================

def load_state() -> None:
    global state

    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        if isinstance(loaded, dict):
            state.update(loaded)

        if not isinstance(state.get("subscribers"), list):
            state["subscribers"] = []

        if not isinstance(state.get("tokens"), dict):
            state["tokens"] = {}

        if not isinstance(state.get("alerts"), dict):
            state["alerts"] = {}

    except Exception as e:
        print(f"[STATE] Could not load state: {e}")


def save_state() -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[STATE] Save error: {e}")


# ============================================================
# HTTP
# ============================================================

def http_json(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:

    for attempt in range(HTTP_RETRIES):

        try:
            data = None

            headers = {
                "User-Agent": "RunnerBot/4.1",
                "Accept": "application/json",
            }

            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"

            request = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method=method,
            )

            with urllib.request.urlopen(
                request,
                timeout=HTTP_TIMEOUT,
            ) as response:

                raw = response.read().decode("utf-8")

                return json.loads(raw)

        except Exception as e:

            if attempt == HTTP_RETRIES - 1:
                print(
                    f"[HTTP ERROR] "
                    f"{url} | {e}"
                )
                return None

            time.sleep(
                HTTP_BACKOFF_SECONDS * (attempt + 1)
            )

    return None


# ============================================================
# DEX SCREENER DISCOVERY
# ============================================================

def get_discovery_tokens() -> List[str]:

    addresses = set()

    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]

    for endpoint in endpoints:

        url = DEX_BASE + endpoint

        data = http_json(url)

        if not isinstance(data, list):
            continue

        for item in data:

            if not isinstance(item, dict):
                continue

            chain = str(
                item.get("chainId", "")
            ).lower()

            if chain != SOLANA:
                continue

            address = (
                item.get("tokenAddress")
                or item.get("address")
            )

            if address:
                addresses.add(str(address))

    result = list(addresses)

    return result[:MAX_DISCOVERY_TOKENS]


# ============================================================
# TOKEN PAIRS
# ============================================================

def get_token_pairs(
    token_address: str,
) -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}/token-pairs/"
        f"{SOLANA}/{token_address}"
    )

    data = http_json(url)

    if not isinstance(data, list):
        return []

    pairs = []

    for pair in data:

        if not isinstance(pair, dict):
            continue

        if str(pair.get("chainId", "")).lower() != SOLANA:
            continue

        pairs.append(pair)

    return pairs


def choose_best_pair(
    pairs: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:

    if not pairs:
        return None

    def liquidity_value(pair: Dict[str, Any]) -> float:

        liquidity = pair.get("liquidity") or {}

        return safe_float(
            liquidity.get("usd")
        )

    pairs = sorted(
        pairs,
        key=liquidity_value,
        reverse=True,
    )

    return pairs[0]


# ============================================================
# SNAPSHOT
# ============================================================

def pair_to_snapshot(
    pair: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    base = pair.get("baseToken") or {}

    token_address = (
        base.get("address")
        or pair.get("baseToken", {}).get("address")
    )

    if not token_address:
        return None

    symbol = (
        base.get("symbol")
        or "UNKNOWN"
    )

    name = (
        base.get("name")
        or symbol
    )

    liquidity = (
        pair.get("liquidity") or {}
    )

    volume = (
        pair.get("volume") or {}
    )

    txns = (
        pair.get("txns") or {}
    )

    m5_txns = (
        txns.get("m5") or {}
    )

    h1_txns = (
        txns.get("h1") or {}
    )

    price_change = (
        pair.get("priceChange") or {}
    )

    market_cap = safe_float(
        pair.get("marketCap")
    )

    if market_cap <= 0:
        market_cap = safe_float(
            pair.get("fdv")
        )

    liquidity_usd = safe_float(
        liquidity.get("usd")
    )

    volume_5m = safe_float(
        volume.get("m5")
    )

    volume_1h = safe_float(
        volume.get("h1")
    )

    buys_5m = safe_int(
        m5_txns.get("buys")
    )

    sells_5m = safe_int(
        m5_txns.get("sells")
    )

    buys_1h = safe_int(
        h1_txns.get("buys")
    )

    sells_1h = safe_int(
        h1_txns.get("sells")
    )

    price = safe_float(
        pair.get("priceUsd")
    )

    price_change_5m = safe_float(
        price_change.get("m5")
    )

    price_change_1h = safe_float(
        price_change.get("h1")
    )

    pair_created = safe_float(
        pair.get("pairCreatedAt")
    )

    if pair_created > 0:

        # DexScreener timestamps are milliseconds.
        created_seconds = (
            pair_created / 1000
        )

        age_hours = max(
            0,
            (time.time() - created_seconds)
            / 3600,
        )

    else:
        age_hours = 9999

    buy_total = buys_5m + sells_5m

    if buy_total > 0:
        buy_percent = (
            buys_5m / buy_total
        ) * 100
    else:
        buy_percent = 0

    buy_sell_ratio = (
        buys_5m / sells_5m
        if sells_5m > 0
        else (
            float(buys_5m)
            if buys_5m > 0
            else 0
        )
    )

    return {
        "timestamp": now_ts(),
        "timestamp_iso": now_iso(),

        "token_address": str(
            token_address
        ),

        "symbol": str(symbol),
        "name": str(name),

        "market_cap": market_cap,
        "fdv": safe_float(pair.get("fdv")),

        "liquidity": liquidity_usd,

        "volume_5m": volume_5m,
        "volume_1h": volume_1h,

        "buys_5m": buys_5m,
        "sells_5m": sells_5m,

        "buys_1h": buys_1h,
        "sells_1h": sells_1h,

        "buy_percent_5m": buy_percent,
        "buy_sell_ratio_5m": buy_sell_ratio,

        "price": price,

        "price_change_5m": price_change_5m,
        "price_change_1h": price_change_1h,

        "age_hours": age_hours,

        "pair_address": pair.get(
            "pairAddress",
            "",
        ),

        "dex_id": pair.get(
            "dexId",
            "",
        ),

        "pair_url": pair.get(
            "url",
            "",
        ),
    }


# ============================================================
# HISTORY
# ============================================================

def get_token_history(
    token_address: str
) -> List[Dict[str, Any]]:

    token = state["tokens"].get(
        token_address
    )

    if not token:
        return []

    history = token.get(
        "history",
        []
    )

    if not isinstance(history, list):
        return []

    return history


def record_snapshot(
    snapshot: Dict[str, Any]
) -> None:

    token_address = snapshot[
        "token_address"
    ]

    token_data = state["tokens"].setdefault(
        token_address,
        {
            "symbol": snapshot.get(
                "symbol",
                "UNKNOWN",
            ),
            "name": snapshot.get(
                "name",
                "",
            ),
            "history": [],
        },
    )

    token_data["symbol"] = snapshot.get(
        "symbol",
        token_data.get("symbol", "UNKNOWN"),
    )

    token_data["name"] = snapshot.get(
        "name",
        token_data.get("name", ""),
    )

    history = token_data.setdefault(
        "history",
        [],
    )

    history.append(snapshot)

    if len(history) > MAX_HISTORY_PER_TOKEN:

        token_data["history"] = (
            history[-MAX_HISTORY_PER_TOKEN:]
        )

    # Prevent unlimited token growth.
    if len(state["tokens"]) > MAX_STORED_TOKENS:

        oldest_tokens = sorted(
            state["tokens"].items(),
            key=lambda x: (
                x[1]
                .get("history", [{}])[-1]
                .get("timestamp", 0)
                if x[1].get("history")
                else 0
            ),
        )

        remove_count = (
            len(state["tokens"])
            - MAX_STORED_TOKENS
        )

        for address, _ in oldest_tokens[
            :remove_count
        ]:
            state["tokens"].pop(
                address,
                None,
            )


def get_previous_snapshot(
    history: List[Dict[str, Any]],
    seconds_back: float,
) -> Optional[Dict[str, Any]]:

    if len(history) < 2:
        return None

    target = (
        history[-1]["timestamp"]
        - seconds_back
    )

    best = None
    best_distance = float("inf")

    for item in history[:-1]:

        timestamp = safe_float(
            item.get("timestamp")
        )

        distance = abs(
            timestamp - target
        )

        if distance < best_distance:

            best_distance = distance
            best = item

    return best


# ============================================================
# ANALYSIS
# ============================================================

def calculate_activity_expansion(
    history: List[Dict[str, Any]],
    current: Dict[str, Any],
) -> bool:

    previous = get_previous_snapshot(
        history,
        ACTIVITY_LOOKBACK_SECONDS,
    )

    if not previous:
        return False

    previous_volume = safe_float(
        previous.get("volume_5m")
    )

    previous_tx = (
        safe_int(previous.get("buys_5m"))
        + safe_int(previous.get("sells_5m"))
    )

    current_volume = safe_float(
        current.get("volume_5m")
    )

    current_tx = (
        safe_int(current.get("buys_5m"))
        + safe_int(current.get("sells_5m"))
    )

    if previous_volume > 0:

        volume_change = (
            (
                current_volume
                - previous_volume
            )
            / previous_volume
        ) * 100

    else:

        volume_change = 0

    if previous_tx > 0:

        tx_change = (
            (
                current_tx
                - previous_tx
            )
            / previous_tx
        ) * 100

    else:

        tx_change = 0

    return (
        volume_change >= ACTIVITY_EXPANSION_PCT
        or tx_change >= ACTIVITY_EXPANSION_PCT
    )


def calculate_structure(
    history: List[Dict[str, Any]]
) -> Dict[str, Any]:

    if len(history) < 3:

        return {
            "breakout": False,
            "consolidation": False,
            "range_pct": 0,
        }

    recent = history[-6:]

    prices = [
        safe_float(x.get("price"))
        for x in recent
        if safe_float(x.get("price")) > 0
    ]

    if len(prices) < 3:

        return {
            "breakout": False,
            "consolidation": False,
            "range_pct": 0,
        }

    low = min(prices)
    high = max(prices)

    if low > 0:

        range_pct = (
            (high - low) / low
        ) * 100

    else:

        range_pct = 0

    current_price = prices[-1]

    previous_prices = prices[:-1]

    previous_high = max(
        previous_prices
    )

    breakout = (
        current_price > previous_high
        and current_price > 0
    )

    active_obs = 0

    for item in recent:

        volume = safe_float(
            item.get("volume_5m")
        )

        tx = (
            safe_int(item.get("buys_5m"))
            + safe_int(item.get("sells_5m"))
        )

        if (
            volume >=
            MIN_CONSOLIDATION_VOLUME_5M
            and tx >=
            MIN_CONSOLIDATION_TX_5M
        ):
            active_obs += 1

    consolidation = (
        range_pct <= MAX_CONSOLIDATION_RANGE_PCT
        and active_obs >= MIN_CONSOLIDATION_ACTIVE_OBS
    )

    return {
        "breakout": breakout,
        "consolidation": consolidation,
        "range_pct": range_pct,
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    snapshot: Dict[str, Any],
    history: List[Dict[str, Any]],
    breakout: bool,
    activity_expansion: bool,
) -> int:

    score = 0

    mc = safe_float(
        snapshot.get("market_cap")
    )

    liquidity = safe_float(
        snapshot.get("liquidity")
    )

    volume = safe_float(
        snapshot.get("volume_5m")
    )

    buys = safe_int(
        snapshot.get("buys_5m")
    )

    sells = safe_int(
        snapshot.get("sells_5m")
    )

    buy_percent = safe_float(
        snapshot.get("buy_percent_5m")
    )

    tx = buys + sells

    age = safe_float(
        snapshot.get("age_hours")
    )

    price_change = safe_float(
        snapshot.get("price_change_5m")
    )

    # --------------------------------------------------------
    # Market cap
    # --------------------------------------------------------

    if 20_000 <= mc <= 150_000:
        score += 15

    elif 150_000 < mc <= 300_000:
        score += 10

    # --------------------------------------------------------
    # Liquidity
    # --------------------------------------------------------

    if liquidity >= 30_000:
        score += 15

    elif liquidity >= 20_000:
        score += 12

    elif liquidity >= 10_000:
        score += 8

    # --------------------------------------------------------
    # 5m volume
    # --------------------------------------------------------

    if volume >= 20_000:
        score += 15

    elif volume >= 10_000:
        score += 12

    elif volume >= REFERENCE_VOLUME_5M:
        score += 8

    elif volume >= 2_500:
        score += 4

    # --------------------------------------------------------
    # Buy participation
    #
    # IMPORTANT:
    # This is NOT a hard gate.
    # --------------------------------------------------------

    if buy_percent >= 65:
        score += 15

    elif buy_percent >= 55:
        score += 11

    elif buy_percent >= 45:
        score += 7

    elif buy_percent >= 35:
        score += 3

    # --------------------------------------------------------
    # Transactions
    # --------------------------------------------------------

    if tx >= 100:
        score += 10

    elif tx >= 50:
        score += 8

    elif tx >= 25:
        score += 5

    elif tx >= 10:
        score += 2

    # --------------------------------------------------------
    # Age
    # --------------------------------------------------------

    if age <= 6:
        score += 10

    elif age <= 12:
        score += 7

    elif age <= 24:
        score += 4

    # --------------------------------------------------------
    # Structure
    # --------------------------------------------------------

    if breakout:
        score += 10

    # --------------------------------------------------------
    # Activity
    # --------------------------------------------------------

    if activity_expansion:
        score += 10

    # --------------------------------------------------------
    # Vertical movement penalty
    # --------------------------------------------------------

    if price_change > 40:
        score -= 10

    elif price_change > 25:
        score -= 5

    score = max(
        0,
        min(100, score)
    )

    return score


# ============================================================
# ANALYZE
# ============================================================

def analyze(
    snapshot: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    token_address = snapshot[
        "token_address"
    ]

    history = get_token_history(
        token_address
    )

    if len(history) < MIN_OBSERVATIONS:

        return None

    structure = calculate_structure(
        history
    )

    breakout = structure[
        "breakout"
    ]

    consolidation = structure[
        "consolidation"
    ]

    activity_expansion = (
        calculate_activity_expansion(
            history,
            snapshot,
        )
    )

    score = calculate_score(
        snapshot,
        history,
        breakout,
        activity_expansion,
    )

    if (
        score >= 70
        and breakout
        and activity_expansion
    ):

        setup_state = "IGNITION"

    elif (
        breakout
        and activity_expansion
    ):

        setup_state = "STRUCTURE BREAK"

    elif activity_expansion:

        setup_state = "EXPANSION"

    elif consolidation:

        setup_state = "CONSOLIDATION"

    else:

        setup_state = "OBSERVING"

    return {
        "score": score,
        "setup_state": setup_state,
        "breakout": breakout,
        "consolidation": consolidation,
        "activity_expansion": activity_expansion,
        "range_pct": structure[
            "range_pct"
        ],
        "observations": len(history),
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(
    method: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

    return http_json(
        url,
        method="POST",
        payload=payload or {},
    )


def send_telegram(
    chat_id: Any,
    text: str,
) -> None:

    if not TELEGRAM_BOT_TOKEN:
        return

    telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    )


def broadcast(
    text: str,
) -> None:

    if not HEATING_ALERTS_ENABLED:
        return

    subscribers = state.get(
        "subscribers",
        [],
    )

    for chat_id in subscribers:

        try:
            send_telegram(
                chat_id,
                text,
            )
        except Exception as e:
            print(
                f"[TELEGRAM] "
                f"Broadcast error: {e}"
            )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def process_telegram() -> None:

    global force_scan_requested

    if not TELEGRAM_BOT_TOKEN:
        return

    offset = state.get(
        "telegram_offset"
    )

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/getUpdates"
    )

    if offset is not None:
        url += (
            f"?offset={int(offset)}"
        )

    data = http_json(url)

    if not isinstance(data, dict):
        return

    updates = data.get(
        "result",
        [],
    )

    for update in updates:

        update_id = update.get(
            "update_id"
        )

        if update_id is not None:

            state[
                "telegram_offset"
            ] = update_id + 1

        message = update.get(
            "message"
        ) or {}

        chat = message.get(
            "chat"
        ) or {}

        chat_id = chat.get(
            "id"
        )

        text = (
            message.get("text")
            or ""
        ).strip()

        if chat_id is None:
            continue

        if text.startswith("/start"):

            if chat_id not in state[
                "subscribers"
            ]:

                state[
                    "subscribers"
                ].append(chat_id)

            send_telegram(
                chat_id,
                (
                    "🚀 Runner Bot is online.\n\n"
                    "DEX Screener-first\n"
                    "Solana scanner active\n"
                    "Scan interval: 15s\n\n"
                    "Commands:\n"
                    "/status\n"
                    "/alerts\n"
                    "/scan\n"
                    "/stop"
                ),
            )

        elif text.startswith("/stop"):

            if chat_id in state[
                "subscribers"
            ]:

                state[
                    "subscribers"
                ].remove(chat_id)

            send_telegram(
                chat_id,
                "🛑 Alerts stopped.",
            )

        elif text.startswith("/alerts"):

            status = (
                "ON"
                if HEATING_ALERTS_ENABLED
                else "OFF"
            )

            send_telegram(
                chat_id,
                f"🔔 Runner alerts: {status}",
            )

        elif text.startswith("/status"):

            send_telegram(
                chat_id,
                (
                    "🚀 RUNNER BOT V4.1\n\n"
                    f"Tokens tracked: "
                    f"{len(state['tokens'])}\n"
                    f"Subscribers: "
                    f"{len(state['subscribers'])}\n"
                    f"Scan interval: "
                    f"{SCAN_INTERVAL_SECONDS:.0f}s\n"
                    f"MC: "
                    f"${MIN_MC:,} - ${MAX_MC:,}\n"
                    f"Liquidity: "
                    f"${MIN_LIQUIDITY:,}+\n"
                    f"Max age: "
                    f"{MAX_PAIR_AGE_HOURS}h"
                ),
            )

        elif text.startswith("/scan"):

            force_scan_requested = True

            send_telegram(
                chat_id,
                "🔎 Manual scan requested.",
            )

    save_state()


# ============================================================
# ALERT FORMAT
# ============================================================

def make_ignition_alert(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> str:

    symbol = snapshot.get(
        "symbol",
        "UNKNOWN",
    )

    name = snapshot.get(
        "name",
        symbol,
    )

    mc = safe_float(
        snapshot.get("market_cap")
    )

    liquidity = safe_float(
        snapshot.get("liquidity")
    )

    volume = safe_float(
        snapshot.get("volume_5m")
    )

    buys = safe_int(
        snapshot.get("buys_5m")
    )

    sells = safe_int(
        snapshot.get("sells_5m")
    )

    buy_percent = safe_float(
        snapshot.get("buy_percent_5m")
    )

    ratio = safe_float(
        snapshot.get("buy_sell_ratio_5m")
    )

    price_change = safe_float(
        snapshot.get("price_change_5m")
    )

    age = safe_float(
        snapshot.get("age_hours")
    )

    score = safe_int(
        analysis.get("score")
    )

    pair_url = snapshot.get(
        "pair_url",
        "",
    )

    return (
        "🔥 IGNITION DETECTED\n\n"
        f"🪙 {symbol} — {name}\n"
        f"📊 Score: {score}/100\n\n"
        f"💰 MC: {format_money(mc)}\n"
        f"💧 Liquidity: "
        f"{format_money(liquidity)}\n"
        f"📈 5m Volume: "
        f"{format_money(volume)}\n"
        f"🕐 Age: {age:.1f}h\n\n"
        f"🟢 Buys: {buys}\n"
        f"🔴 Sells: {sells}\n"
        f"📊 Buy %: {buy_percent:.1f}%\n"
        f"⚖️ Buy/Sell: {ratio:.2f}\n"
        f"📈 5m Change: {price_change:+.1f}%\n\n"
        f"🚀 Breakout: YES\n"
        f"⚡ Activity expansion: YES\n"
        f"👀 Observations: "
        f"{analysis.get('observations', 0)}\n\n"
        f"{pair_url}"
    )


# ============================================================
# ALERT TRACKING
# ============================================================

def register_alert(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> None:

    token_address = snapshot[
        "token_address"
    ]

    alerts = state[
        "alerts"
    ]

    existing = alerts.get(
        token_address
    )

    if existing:
        return

    alerts[token_address] = {
        "symbol": snapshot.get(
            "symbol",
            "UNKNOWN",
        ),

        "started_at": now_ts(),

        "start_mc": safe_float(
            snapshot.get(
                "market_cap"
            )
        ),

        "peak_mc": safe_float(
            snapshot.get(
                "market_cap"
            )
        ),

        "min_mc": safe_float(
            snapshot.get(
                "market_cap"
            )
        ),

        "score": analysis.get(
            "score",
            0,
        ),

        "checks": {
            "5m": None,
            "15m": None,
            "30m": None,
        },

        "last_update": now_ts(),
    }


def update_alert_tracking(
    snapshot: Dict[str, Any]
) -> None:

    token_address = snapshot[
        "token_address"
    ]

    alert = state[
        "alerts"
    ].get(
        token_address
    )

    if not alert:
        return

    current_mc = safe_float(
        snapshot.get("market_cap")
    )

    alert["peak_mc"] = max(
        safe_float(
            alert.get("peak_mc")
        ),
        current_mc,
    )

    old_min = safe_float(
        alert.get("min_mc")
    )

    if old_min <= 0:
        alert["min_mc"] = current_mc
    else:
        alert["min_mc"] = min(
            old_min,
            current_mc,
        )

    elapsed = (
        now_ts()
        - safe_float(
            alert.get("started_at")
        )
    ) / 60

    start_mc = safe_float(
        alert.get("start_mc")
    )

    if start_mc <= 0:
        return

    return_pct = (
        (current_mc - start_mc)
        / start_mc
    ) * 100

    if elapsed >= 30:

        if return_pct >= 30:
            result = "RUNNER"

        elif return_pct <= -15:
            result = "FAILED"

        else:
            result = "UNCLEAR"

        alert["checks"]["30m"] = result

    elif elapsed >= 15:

        if return_pct >= 10:
            result = "CONTINUING"

        elif return_pct <= -15:
            result = "FAILED"

        else:
            result = "UNCLEAR"

        alert["checks"]["15m"] = result

    elif elapsed >= 5:

        if return_pct >= 10:
            result = "CONTINUING"

        elif return_pct <= -15:
            result = "FAILED"

        else:
            result = "UNCLEAR"

        alert["checks"]["5m"] = result

    alert["last_update"] = now_ts()


# ============================================================
# DISCOVERY + SCAN
# ============================================================

def discover_tokens() -> List[str]:

    tokens = get_discovery_tokens()

    print(
        f"[DISCOVERY] "
        f"{len(tokens)} Solana tokens available"
    )

    return tokens


def scan_once(
    discovery_tokens: List[str]
) -> None:

    print(
        "=" * 60
    )

    print(
        f"[SCAN] "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    pairs = []

    checked = 0

    for token_address in discovery_tokens:

        if checked >= MAX_TOKEN_LOOKUPS_PER_CYCLE:
            break

        checked += 1

        token_pairs = get_token_pairs(
            token_address
        )

        best = choose_best_pair(
            token_pairs
        )

        if best:
            pairs.append(best)

        time.sleep(
            TOKEN_LOOKUP_SLEEP
        )

    print(
        f"[PAIRS] "
        f"{len(pairs)} strongest Solana pools"
    )

    for pair in pairs:

        snapshot = pair_to_snapshot(
            pair
        )

        if not snapshot:
            continue

        symbol = snapshot.get(
            "symbol",
            "UNKNOWN",
        )

        token_address = snapshot[
            "token_address"
        ]

        mc = safe_float(
            snapshot.get("market_cap")
        )

        liquidity = safe_float(
            snapshot.get("liquidity")
        )

        age = safe_float(
            snapshot.get("age_hours")
        )

        volume_5m = safe_float(
            snapshot.get("volume_5m")
        )

        # ----------------------------------------------------
        # ALWAYS RECORD HISTORY FIRST
        # ----------------------------------------------------

        record_snapshot(
            snapshot
        )

        history = get_token_history(
            token_address
        )

        print(
            f"[CANDIDATE] "
            f"{symbol} | "
            f"MC={format_money(mc)} | "
            f"Liq={format_money(liquidity)} | "
            f"Age={age:.1f}h | "
            f"5mVol={format_money(volume_5m)} | "
            f"Obs={len(history)}/{MIN_OBSERVATIONS}"
        )

        # ----------------------------------------------------
        # OBSERVATION RANGE
        # ----------------------------------------------------

        if (
            mc >= OBSERVE_MIN_MC
            and mc <= OBSERVE_MAX_MC
        ):
            pass
        else:
            print(
                f"[OBSERVE SKIP] {symbol} "
                f"outside observation MC range."
            )

        # ----------------------------------------------------
        # CORE FILTERS
        # ----------------------------------------------------

        if mc < MIN_MC:

            print(
                f"[FILTER] {symbol} rejected: "
                f"MC below ${MIN_MC:,}"
            )

            continue

        if mc > MAX_MC:

            print(
                f"[FILTER] {symbol} rejected: "
                f"MC above ${MAX_MC:,}"
            )

            continue

        if liquidity < MIN_LIQUIDITY:

            print(
                f"[FILTER] {symbol} rejected: "
                f"liquidity below "
                f"${MIN_LIQUIDITY:,}"
            )

            continue

        if age > MAX_PAIR_AGE_HOURS:

            print(
                f"[FILTER] {symbol} rejected: "
                f"age above "
                f"{MAX_PAIR_AGE_HOURS}h"
            )

            continue

        # ----------------------------------------------------
        # HISTORY WAIT
        # ----------------------------------------------------

        if len(history) < MIN_OBSERVATIONS:

            print(
                f"[WAIT] {symbol} | "
                f"observations "
                f"{len(history)}/"
                f"{MIN_OBSERVATIONS}"
            )

            continue

        # ----------------------------------------------------
        # ANALYSIS
        # ----------------------------------------------------

        analysis = analyze(
            snapshot
        )

        if analysis is None:

            print(
                f"[WAIT] {symbol} "
                f"analysis unavailable."
            )

            continue

        score = analysis[
            "score"
        ]

        setup = analysis[
            "setup_state"
        ]

        print(
            f"[TRACK] {symbol} | "
            f"Score={score}/100 | "
            f"State={setup} | "
            f"MC={format_money(mc)} | "
            f"Liq={format_money(liquidity)} | "
            f"5mVol={format_money(volume_5m)}"
        )

        # ----------------------------------------------------
        # IGNITION
        # ----------------------------------------------------

        if setup == "IGNITION":

            if token_address not in state[
                "alerts"
            ]:

                print(
                    f"[IGNITION] "
                    f"{symbol} "
                    f"score={score}"
                )

                register_alert(
                    snapshot,
                    analysis,
                )

                alert_text = (
                    make_ignition_alert(
                        snapshot,
                        analysis,
                    )
                )

                broadcast(
                    alert_text
                )

            else:

                print(
                    f"[IGNITION] {symbol} "
                    f"already alerted."
                )

        # ----------------------------------------------------
        # TRACK EXISTING ALERT
        # ----------------------------------------------------

        update_alert_tracking(
            snapshot
        )

    save_state()


# ============================================================
# MAIN
# ============================================================

def print_banner() -> None:

    print("=" * 60)
    print(
        f"🚀 RUNNER BOT {BOT_VERSION}"
    )
    print("=" * 60)

    print(
        "DEX Screener-first"
    )

    print(
        "Solana only"
    )

    print(
        f"MC: ${MIN_MC:,} - "
        f"${MAX_MC:,}"
    )

    print(
        f"Liquidity: "
        f"${MIN_LIQUIDITY:,}+"
    )

    print(
        f"Pair age: "
        f"<= {MAX_PAIR_AGE_HOURS}h"
    )

    print(
        f"Scan interval: "
        f"{SCAN_INTERVAL_SECONDS:.0f}s"
    )

    print("=" * 60)


def main() -> None:

    global force_scan_requested

    load_state()

    print_banner()

    if TELEGRAM_BOT_TOKEN:

        print(
            "[TELEGRAM] "
            "Bot token detected."
        )

    else:

        print(
            "[TELEGRAM] "
            "WARNING: bot token missing."
        )

    discovery_tokens = []
    last_discovery = 0

    while True:

        try:

            # ------------------------------------------------
            # TELEGRAM
            # ------------------------------------------------

            process_telegram()

            # ------------------------------------------------
            # DISCOVERY
            # ------------------------------------------------

            current_time = time.time()

            should_discover = (
                not discovery_tokens
                or (
                    current_time
                    - last_discovery
                    >= DISCOVERY_INTERVAL_SECONDS
                )
                or force_scan_requested
            )

            if should_discover:

                discovery_tokens = (
                    discover_tokens()
                )

                last_discovery = (
                    current_time
                )

                force_scan_requested = False

            # ------------------------------------------------
            # SCAN
            # ------------------------------------------------

            if discovery_tokens:

                scan_once(
                    discovery_tokens
                )

            else:

                print(
                    "[SCAN] "
                    "No discovery tokens found."
                )

            # ------------------------------------------------
            # WAIT
            # ------------------------------------------------

            time.sleep(
                SCAN_INTERVAL_SECONDS
            )

        except KeyboardInterrupt:

            print(
                "\n[STOP] "
                "Runner Bot stopped."
            )

            save_state()

            break

        except Exception as e:

            print(
                f"[MAIN ERROR] {e}"
            )

            time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
