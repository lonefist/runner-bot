import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V4.2
# DEX SCREENER FIRST
# ============================================================

BOT_VERSION = "V4.2-BATCH"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

SOLANA = "solana"

STATE_FILE = "runner_state_v42.json"


# ============================================================
# CORE FILTERS
# ============================================================

MIN_MC = 20_000
MAX_MC = 300_000

MIN_LIQUIDITY = 10_000

MAX_PAIR_AGE_HOURS = 24


# ============================================================
# HISTORY
# ============================================================

OBSERVE_MIN_MC = 10_000
OBSERVE_MAX_MC = 350_000

MIN_OBSERVATIONS = 6

MAX_HISTORY_PER_TOKEN = 300
MAX_STORED_TOKENS = 1_500


# ============================================================
# RUNNER LOGIC
# ============================================================

REFERENCE_VOLUME_5M = 5_000

ACTIVITY_LOOKBACK_SECONDS = 60
ACTIVITY_EXPANSION_PCT = 20.0

MAX_CONSOLIDATION_RANGE_PCT = 18.0


# ============================================================
# SCANNING
# ============================================================

SCAN_INTERVAL_SECONDS = float(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)

DISCOVERY_INTERVAL_SECONDS = 60

MAX_DISCOVERY_TOKENS = 500

# DexScreener token endpoint supports multiple addresses.
# Keep batches conservative.
TOKEN_BATCH_SIZE = 25


# ============================================================
# HTTP
# ============================================================

HTTP_TIMEOUT = 15
HTTP_RETRIES = 4
HTTP_BACKOFF_SECONDS = 1.5


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

HEATING_ALERTS_ENABLED = (
    os.getenv(
        "HEATING_ALERTS_ENABLED",
        "true"
    ).lower()
    == "true"
)


# ============================================================
# STATE
# ============================================================

state: Dict[str, Any] = {
    "subscribers": [],
    "tokens": {},
    "alerts": {},
    "telegram_offset": None,
}

force_scan_requested = False


# ============================================================
# HELPERS
# ============================================================

def now_ts() -> float:
    return time.time()


def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def safe_float(
    value: Any,
    default: float = 0.0
) -> float:

    try:
        if value is None:
            return default

        return float(value)

    except Exception:
        return default


def safe_int(
    value: Any,
    default: int = 0
) -> int:

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


# ============================================================
# STATE
# ============================================================

def load_state() -> None:

    global state

    if not os.path.exists(STATE_FILE):
        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            loaded = json.load(f)

        if isinstance(loaded, dict):
            state.update(loaded)

    except Exception as e:

        print(
            f"[STATE] Load error: {e}"
        )

    if not isinstance(
        state.get("subscribers"),
        list
    ):
        state["subscribers"] = []

    if not isinstance(
        state.get("tokens"),
        dict
    ):
        state["tokens"] = {}

    if not isinstance(
        state.get("alerts"),
        dict
    ):
        state["alerts"] = {}


def save_state() -> None:

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                indent=2
            )

    except Exception as e:

        print(
            f"[STATE] Save error: {e}"
        )


# ============================================================
# HTTP
# ============================================================

def http_json(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:

    for attempt in range(
        HTTP_RETRIES
    ):

        try:

            data = None

            headers = {
                "User-Agent":
                    "Mozilla/5.0 "
                    "RunnerBot/4.2",
                "Accept":
                    "application/json",
            }

            if payload is not None:

                data = json.dumps(
                    payload
                ).encode("utf-8")

                headers[
                    "Content-Type"
                ] = "application/json"

            request = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method=method,
            )

            with urllib.request.urlopen(
                request,
                timeout=HTTP_TIMEOUT
            ) as response:

                raw = response.read().decode(
                    "utf-8"
                )

                return json.loads(raw)

        except urllib.error.HTTPError as e:

            print(
                f"[HTTP] {e.code} "
                f"{url}"
            )

            if e.code in (
                404,
                400,
                429,
            ):

                if attempt < HTTP_RETRIES - 1:

                    time.sleep(
                        HTTP_BACKOFF_SECONDS
                        * (attempt + 1)
                    )

                    continue

            return None

        except Exception as e:

            if attempt == HTTP_RETRIES - 1:

                print(
                    f"[HTTP ERROR] "
                    f"{url} | {e}"
                )

                return None

            time.sleep(
                HTTP_BACKOFF_SECONDS
                * (attempt + 1)
            )

    return None


# ============================================================
# DEX SCREENER DISCOVERY
# ============================================================

def discover_tokens() -> List[str]:

    addresses = set()

    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]

    for endpoint in endpoints:

        data = http_json(
            DEX_BASE + endpoint
        )

        if not isinstance(
            data,
            list
        ):
            continue

        for item in data:

            if not isinstance(
                item,
                dict
            ):
                continue

            chain = str(
                item.get(
                    "chainId",
                    ""
                )
            ).lower()

            if chain != SOLANA:
                continue

            address = (
                item.get(
                    "tokenAddress"
                )
                or item.get(
                    "address"
                )
            )

            if address:
                addresses.add(
                    str(address)
                )

    tokens = list(addresses)

    print(
        f"[DISCOVERY] "
        f"{len(tokens)} Solana tokens available"
    )

    return tokens[
        :MAX_DISCOVERY_TOKENS
    ]


# ============================================================
# BATCH TOKEN LOOKUP
#
# THIS REPLACES THE BROKEN:
# /token-pairs/solana/{token}
#
# WITH:
# /latest/dex/tokens/{token1,token2,...}
# ============================================================

def get_token_pairs_batch(
    token_addresses: List[str]
) -> List[Dict[str, Any]]:

    all_pairs = []

    if not token_addresses:
        return []

    for start in range(
        0,
        len(token_addresses),
        TOKEN_BATCH_SIZE
    ):

        batch = token_addresses[
            start:
            start + TOKEN_BATCH_SIZE
        ]

        address_string = ",".join(
            batch
        )

        url = (
            f"{DEX_BASE}"
            f"/latest/dex/tokens/"
            f"{address_string}"
        )

        data = http_json(url)

        if not isinstance(
            data,
            dict
        ):
            continue

        pairs = data.get(
            "pairs",
            []
        )

        if not isinstance(
            pairs,
            list
        ):
            continue

        for pair in pairs:

            if not isinstance(
                pair,
                dict
            ):
                continue

            if str(
                pair.get(
                    "chainId",
                    ""
                )
            ).lower() != SOLANA:

                continue

            all_pairs.append(
                pair
            )

        # Small pause between batches.
        time.sleep(0.25)

    return all_pairs


# ============================================================
# BEST PAIR PER TOKEN
# ============================================================

def choose_best_pairs(
    pairs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:

    best_by_token = {}

    for pair in pairs:

        base = (
            pair.get(
                "baseToken"
            )
            or {}
        )

        token_address = (
            base.get("address")
        )

        if not token_address:
            continue

        liquidity = (
            pair.get(
                "liquidity"
            )
            or {}
        )

        liquidity_usd = safe_float(
            liquidity.get("usd")
        )

        current = best_by_token.get(
            token_address
        )

        if current is None:

            best_by_token[
                token_address
            ] = pair

        else:

            current_liquidity = (
                current.get(
                    "liquidity"
                )
                or {}
            )

            current_value = safe_float(
                current_liquidity.get(
                    "usd"
                )
            )

            if (
                liquidity_usd
                > current_value
            ):

                best_by_token[
                    token_address
                ] = pair

    return list(
        best_by_token.values()
    )


# ============================================================
# SNAPSHOT
# ============================================================

def pair_to_snapshot(
    pair: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    base = (
        pair.get(
            "baseToken"
        )
        or {}
    )

    token_address = (
        base.get(
            "address"
        )
    )

    if not token_address:
        return None

    symbol = (
        base.get(
            "symbol"
        )
        or "UNKNOWN"
    )

    name = (
        base.get(
            "name"
        )
        or symbol
    )

    liquidity = (
        pair.get(
            "liquidity"
        )
        or {}
    )

    volume = (
        pair.get(
            "volume"
        )
        or {}
    )

    txns = (
        pair.get(
            "txns"
        )
        or {}
    )

    m5 = (
        txns.get(
            "m5"
        )
        or {}
    )

    h1 = (
        txns.get(
            "h1"
        )
        or {}
    )

    changes = (
        pair.get(
            "priceChange"
        )
        or {}
    )

    market_cap = safe_float(
        pair.get(
            "marketCap"
        )
    )

    if market_cap <= 0:

        market_cap = safe_float(
            pair.get(
                "fdv"
            )
        )

    liquidity_usd = safe_float(
        liquidity.get(
            "usd"
        )
    )

    volume_5m = safe_float(
        volume.get(
            "m5"
        )
    )

    volume_1h = safe_float(
        volume.get(
            "h1"
        )
    )

    buys_5m = safe_int(
        m5.get(
            "buys"
        )
    )

    sells_5m = safe_int(
        m5.get(
            "sells"
        )
    )

    buys_1h = safe_int(
        h1.get(
            "buys"
        )
    )

    sells_1h = safe_int(
        h1.get(
            "sells"
        )
    )

    total_5m = (
        buys_5m
        + sells_5m
    )

    if total_5m > 0:

        buy_percent = (
            buys_5m
            / total_5m
        ) * 100

    else:

        buy_percent = 0

    if sells_5m > 0:

        buy_sell_ratio = (
            buys_5m
            / sells_5m
        )

    elif buys_5m > 0:

        buy_sell_ratio = float(
            buys_5m
        )

    else:

        buy_sell_ratio = 0

    pair_created = safe_float(
        pair.get(
            "pairCreatedAt"
        )
    )

    if pair_created > 0:

        created_seconds = (
            pair_created / 1000
        )

        age_hours = max(
            0,
            (
                time.time()
                - created_seconds
            ) / 3600
        )

    else:

        age_hours = 9999

    return {
        "timestamp":
            now_ts(),

        "timestamp_iso":
            now_iso(),

        "token_address":
            str(token_address),

        "symbol":
            str(symbol),

        "name":
            str(name),

        "market_cap":
            market_cap,

        "fdv":
            safe_float(
                pair.get(
                    "fdv"
                )
            ),

        "liquidity":
            liquidity_usd,

        "volume_5m":
            volume_5m,

        "volume_1h":
            volume_1h,

        "buys_5m":
            buys_5m,

        "sells_5m":
            sells_5m,

        "buys_1h":
            buys_1h,

        "sells_1h":
            sells_1h,

        "buy_percent_5m":
            buy_percent,

        "buy_sell_ratio_5m":
            buy_sell_ratio,

        "price":
            safe_float(
                pair.get(
                    "priceUsd"
                )
            ),

        "price_change_5m":
            safe_float(
                changes.get(
                    "m5"
                )
            ),

        "price_change_1h":
            safe_float(
                changes.get(
                    "h1"
                )
            ),

        "age_hours":
            age_hours,

        "pair_address":
            pair.get(
                "pairAddress",
                ""
            ),

        "dex_id":
            pair.get(
                "dexId",
                ""
            ),

        "pair_url":
            pair.get(
                "url",
                ""
            ),
    }


# ============================================================
# HISTORY
# ============================================================

def get_history(
    token_address: str
) -> List[Dict[str, Any]]:

    token = state[
        "tokens"
    ].get(
        token_address
    )

    if not token:
        return []

    return token.get(
        "history",
        []
    )


def record_snapshot(
    snapshot: Dict[str, Any]
) -> None:

    address = snapshot[
        "token_address"
    ]

    token = state[
        "tokens"
    ].setdefault(
        address,
        {
            "symbol":
                snapshot.get(
                    "symbol",
                    "UNKNOWN"
                ),
            "name":
                snapshot.get(
                    "name",
                    ""
                ),
            "history":
                [],
        }
    )

    token[
        "symbol"
    ] = snapshot.get(
        "symbol",
        token.get(
            "symbol",
            "UNKNOWN"
        )
    )

    history = token[
        "history"
    ]

    history.append(
        snapshot
    )

    if len(history) > MAX_HISTORY_PER_TOKEN:

        token[
            "history"
        ] = history[
            -MAX_HISTORY_PER_TOKEN:
        ]


# ============================================================
# STRUCTURE
# ============================================================

def calculate_structure(
    history: List[Dict[str, Any]]
) -> Dict[str, Any]:

    if len(history) < 3:

        return {
            "breakout":
                False,
            "consolidation":
                False,
            "range_pct":
                0,
        }

    recent = history[
        -6:
    ]

    prices = [
        safe_float(
            x.get("price")
        )
        for x in recent
        if safe_float(
            x.get("price")
        ) > 0
    ]

    if len(prices) < 3:

        return {
            "breakout":
                False,
            "consolidation":
                False,
            "range_pct":
                0,
        }

    low = min(prices)
    high = max(prices)

    range_pct = (
        (
            high - low
        )
        / low
    ) * 100

    current = prices[-1]

    previous = prices[:-1]

    previous_high = max(
        previous
    )

    breakout = (
        current
        > previous_high
    )

    consolidation = (
        range_pct
        <= MAX_CONSOLIDATION_RANGE_PCT
    )

    return {
        "breakout":
            breakout,

        "consolidation":
            consolidation,

        "range_pct":
            range_pct,
    }


# ============================================================
# ACTIVITY
# ============================================================

def activity_expanding(
    history: List[Dict[str, Any]]
) -> bool:

    if len(history) < 2:
        return False

    current = history[-1]

    current_volume = safe_float(
        current.get(
            "volume_5m"
        )
    )

    current_tx = (
        safe_int(
            current.get(
                "buys_5m"
            )
        )
        +
        safe_int(
            current.get(
                "sells_5m"
            )
        )
    )

    target_time = (
        current.get(
            "timestamp",
            0
        )
        - ACTIVITY_LOOKBACK_SECONDS
    )

    previous = min(
        history[:-1],
        key=lambda x:
            abs(
                safe_float(
                    x.get(
                        "timestamp"
                    )
                )
                - target_time
            )
    )

    previous_volume = safe_float(
        previous.get(
            "volume_5m"
        )
    )

    previous_tx = (
        safe_int(
            previous.get(
                "buys_5m"
            )
        )
        +
        safe_int(
            previous.get(
                "sells_5m"
            )
        )
    )

    volume_growth = 0

    tx_growth = 0

    if previous_volume > 0:

        volume_growth = (
            (
                current_volume
                - previous_volume
            )
            / previous_volume
        ) * 100

    if previous_tx > 0:

        tx_growth = (
            (
                current_tx
                - previous_tx
            )
            / previous_tx
        ) * 100

    return (
        volume_growth
        >= ACTIVITY_EXPANSION_PCT
        or
        tx_growth
        >= ACTIVITY_EXPANSION_PCT
    )


# ============================================================
# SCORE
# ============================================================

def score_token(
    snapshot: Dict[str, Any],
    breakout: bool,
    activity: bool,
) -> int:

    score = 0

    mc = safe_float(
        snapshot.get(
            "market_cap"
        )
    )

    liquidity = safe_float(
        snapshot.get(
            "liquidity"
        )
    )

    volume = safe_float(
        snapshot.get(
            "volume_5m"
        )
    )

    buy_percent = safe_float(
        snapshot.get(
            "buy_percent_5m"
        )
    )

    tx = (
        safe_int(
            snapshot.get(
                "buys_5m"
            )
        )
        +
        safe_int(
            snapshot.get(
                "sells_5m"
            )
        )
    )

    age = safe_float(
        snapshot.get(
            "age_hours"
        )
    )

    price_change = safe_float(
        snapshot.get(
            "price_change_5m"
        )
    )

    # MC
    if 20_000 <= mc <= 150_000:
        score += 15
    elif mc <= 300_000:
        score += 10

    # Liquidity
    if liquidity >= 30_000:
        score += 15
    elif liquidity >= 20_000:
        score += 12
    elif liquidity >= 10_000:
        score += 8

    # Volume
    if volume >= 20_000:
        score += 15
    elif volume >= 10_000:
        score += 12
    elif volume >= 5_000:
        score += 8
    elif volume >= 2_500:
        score += 4

    # Buys
    if buy_percent >= 65:
        score += 15
    elif buy_percent >= 55:
        score += 11
    elif buy_percent >= 45:
        score += 7
    elif buy_percent >= 35:
        score += 3

    # Transactions
    if tx >= 100:
        score += 10
    elif tx >= 50:
        score += 8
    elif tx >= 25:
        score += 5
    elif tx >= 10:
        score += 2

    # Age
    if age <= 6:
        score += 10
    elif age <= 12:
        score += 7
    elif age <= 24:
        score += 4

    # Structure
    if breakout:
        score += 10

    # Activity
    if activity:
        score += 10

    # Extreme vertical move penalty
    if price_change > 40:
        score -= 10
    elif price_change > 25:
        score -= 5

    return max(
        0,
        min(
            100,
            score
        )
    )


# ============================================================
# ANALYSIS
# ============================================================

def analyze(
    snapshot: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    address = snapshot[
        "token_address"
    ]

    history = get_history(
        address
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

    activity = activity_expanding(
        history
    )

    score = score_token(
        snapshot,
        breakout,
        activity
    )

    if (
        score >= 70
        and breakout
        and activity
    ):

        setup = "IGNITION"

    elif (
        breakout
        and activity
    ):

        setup = "STRUCTURE BREAK"

    elif activity:

        setup = "EXPANSION"

    elif consolidation:

        setup = "CONSOLIDATION"

    else:

        setup = "OBSERVING"

    return {
        "score":
            score,

        "setup":
            setup,

        "breakout":
            breakout,

        "activity":
            activity,

        "consolidation":
            consolidation,

        "range_pct":
            structure[
                "range_pct"
            ],

        "observations":
            len(history),
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram(
    method: str,
    payload: Dict[str, Any]
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
        payload=payload
    )


def send_message(
    chat_id: Any,
    text: str
) -> None:

    telegram(
        "sendMessage",
        {
            "chat_id":
                chat_id,

            "text":
                text,

            "disable_web_page_preview":
                True,
        }
    )


def broadcast(
    text: str
) -> None:

    if not HEATING_ALERTS_ENABLED:
        return

    for chat_id in state[
        "subscribers"
    ]:

        try:

            send_message(
                chat_id,
                text
            )

        except Exception as e:

            print(
                f"[TELEGRAM] "
                f"{e}"
            )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def process_telegram() -> None:

    global force_scan_requested

    if not TELEGRAM_BOT_TOKEN:
        return

    payload = {}

    offset = state.get(
        "telegram_offset"
    )

    if offset is not None:

        payload[
            "offset"
        ] = offset

    data = telegram(
        "getUpdates",
        payload
    )

    if not isinstance(
        data,
        dict
    ):
        return

    updates = data.get(
        "result",
        []
    )

    for update in updates:

        update_id = update.get(
            "update_id"
        )

        if update_id is not None:

            state[
                "telegram_offset"
            ] = (
                update_id + 1
            )

        message = (
            update.get(
                "message"
            )
            or {}
        )

        chat = (
            message.get(
                "chat"
            )
            or {}
        )

        chat_id = chat.get(
            "id"
        )

        command = (
            message.get(
                "text"
            )
            or ""
        ).strip()

        if chat_id is None:
            continue

        if command.startswith(
            "/start"
        ):

            if chat_id not in state[
                "subscribers"
            ]:

                state[
                    "subscribers"
                ].append(
                    chat_id
                )

            send_message(
                chat_id,
                (
                    "🚀 Runner Bot is online.\n\n"
                    "DEX Screener-first\n"
                    "Solana scanner active\n"
                    "15-second scanning\n\n"
                    "/status\n"
                    "/alerts\n"
                    "/scan\n"
                    "/stop"
                )
            )

        elif command.startswith(
            "/stop"
        ):

            if chat_id in state[
                "subscribers"
            ]:

                state[
                    "subscribers"
                ].remove(
                    chat_id
                )

            send_message(
                chat_id,
                "🛑 Alerts stopped."
            )

        elif command.startswith(
            "/scan"
        ):

            force_scan_requested = True

            send_message(
                chat_id,
                "🔎 Manual scan requested."
            )

        elif command.startswith(
            "/alerts"
        ):

            status = (
                "ON"
                if HEATING_ALERTS_ENABLED
                else "OFF"
            )

            send_message(
                chat_id,
                f"🔔 Alerts: {status}"
            )

        elif command.startswith(
            "/status"
        ):

            send_message(
                chat_id,
                (
                    "🚀 RUNNER BOT V4.2\n\n"
                    f"Tracked tokens: "
                    f"{len(state['tokens'])}\n"
                    f"Subscribers: "
                    f"{len(state['subscribers'])}\n"
                    f"Scan: "
                    f"{SCAN_INTERVAL_SECONDS:.0f}s\n"
                    f"MC: "
                    f"${MIN_MC:,} - "
                    f"${MAX_MC:,}\n"
                    f"Liquidity: "
                    f"${MIN_LIQUIDITY:,}+"
                )
            )

    save_state()


# ============================================================
# ALERT
# ============================================================

def ignition_message(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any]
) -> str:

    symbol = snapshot.get(
        "symbol",
        "UNKNOWN"
    )

    mc = safe_float(
        snapshot.get(
            "market_cap"
        )
    )

    liquidity = safe_float(
        snapshot.get(
            "liquidity"
        )
    )

    volume = safe_float(
        snapshot.get(
            "volume_5m"
        )
    )

    buys = safe_int(
        snapshot.get(
            "buys_5m"
        )
    )

    sells = safe_int(
        snapshot.get(
            "sells_5m"
        )
    )

    buy_pct = safe_float(
        snapshot.get(
            "buy_percent_5m"
        )
    )

    ratio = safe_float(
        snapshot.get(
            "buy_sell_ratio_5m"
        )
    )

    change = safe_float(
        snapshot.get(
            "price_change_5m"
        )
    )

    age = safe_float(
        snapshot.get(
            "age_hours"
        )
    )

    score = analysis.get(
        "score",
        0
    )

    url = snapshot.get(
        "pair_url",
        ""
    )

    return (
        "🔥 IGNITION DETECTED\n\n"
        f"🪙 {symbol}\n\n"
        f"📊 Score: {score}/100\n"
        f"💰 MC: {format_money(mc)}\n"
        f"💧 Liquidity: "
        f"{format_money(liquidity)}\n"
        f"📈 5m Volume: "
        f"{format_money(volume)}\n"
        f"🕐 Age: {age:.1f}h\n\n"
        f"🟢 Buys: {buys}\n"
        f"🔴 Sells: {sells}\n"
        f"📊 Buy %: {buy_pct:.1f}%\n"
        f"⚖️ Ratio: {ratio:.2f}\n"
        f"📈 5m Change: {change:+.1f}%\n\n"
        f"🚀 Breakout: YES\n"
        f"⚡ Activity expansion: YES\n"
        f"👀 Observations: "
        f"{analysis.get('observations', 0)}\n\n"
        f"{url}"
    )


# ============================================================
# ALERT REGISTRATION
# ============================================================

def register_alert(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any]
) -> None:

    address = snapshot[
        "token_address"
    ]

    if address in state[
        "alerts"
    ]:
        return

    mc = safe_float(
        snapshot.get(
            "market_cap"
        )
    )

    state[
        "alerts"
    ][address] = {
        "symbol":
            snapshot.get(
                "symbol",
                "UNKNOWN"
            ),

        "started_at":
            now_ts(),

        "start_mc":
            mc,

        "peak_mc":
            mc,

        "min_mc":
            mc,

        "score":
            analysis.get(
                "score",
                0
            ),

        "checks": {
            "5m":
                None,
            "15m":
                None,
            "30m":
                None,
        }
    }


# ============================================================
# SCAN
# ============================================================

def scan_once(
    token_addresses: List[str]
) -> None:

    print("=" * 60)

    print(
        f"[SCAN] "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # --------------------------------------------------------
    # ONE BATCHED DEX SCREENER LOOKUP
    # --------------------------------------------------------

    pairs = get_token_pairs_batch(
        token_addresses
    )

    print(
        f"[PAIRS] "
        f"{len(pairs)} Solana pairs received"
    )

    # --------------------------------------------------------
    # STRONGEST LIQUIDITY PAIR PER TOKEN
    # --------------------------------------------------------

    best_pairs = choose_best_pairs(
        pairs
    )

    print(
        f"[PAIRS] "
        f"{len(best_pairs)} strongest pools selected"
    )

    # --------------------------------------------------------
    # ANALYZE
    # --------------------------------------------------------

    for pair in best_pairs:

        snapshot = pair_to_snapshot(
            pair
        )

        if not snapshot:
            continue

        address = snapshot[
            "token_address"
        ]

        symbol = snapshot.get(
            "symbol",
            "UNKNOWN"
        )

        mc = safe_float(
            snapshot.get(
                "market_cap"
            )
        )

        liquidity = safe_float(
            snapshot.get(
                "liquidity"
            )
        )

        age = safe_float(
            snapshot.get(
                "age_hours"
            )
        )

        volume = safe_float(
            snapshot.get(
                "volume_5m"
            )
        )

        # ALWAYS record history
        record_snapshot(
            snapshot
        )

        history = get_history(
            address
        )

        print(
            f"[CANDIDATE] "
            f"{symbol} | "
            f"MC={format_money(mc)} | "
            f"Liq={format_money(liquidity)} | "
            f"Age={age:.1f}h | "
            f"5mVol={format_money(volume)} | "
            f"Obs={len(history)}/{MIN_OBSERVATIONS}"
        )

        # ----------------------------------------------------
        # CORE FILTER
        # ----------------------------------------------------

        if mc < MIN_MC:

            print(
                f"[FILTER] "
                f"{symbol}: MC below "
                f"${MIN_MC:,}"
            )

            continue

        if mc > MAX_MC:

            print(
                f"[FILTER] "
                f"{symbol}: MC above "
                f"${MAX_MC:,}"
            )

            continue

        if liquidity < MIN_LIQUIDITY:

            print(
                f"[FILTER] "
                f"{symbol}: liquidity below "
                f"${MIN_LIQUIDITY:,}"
            )

            continue

        if age > MAX_PAIR_AGE_HOURS:

            print(
                f"[FILTER] "
                f"{symbol}: age above "
                f"{MAX_PAIR_AGE_HOURS}h"
            )

            continue

        # ----------------------------------------------------
        # WAIT FOR HISTORY
        # ----------------------------------------------------

        if len(history) < MIN_OBSERVATIONS:

            print(
                f"[WAIT] "
                f"{symbol}: "
                f"{len(history)}/"
                f"{MIN_OBSERVATIONS} observations"
            )

            continue

        # ----------------------------------------------------
        # ANALYSIS
        # ----------------------------------------------------

        analysis = analyze(
            snapshot
        )

        if analysis is None:
            continue

        setup = analysis[
            "setup"
        ]

        score = analysis[
            "score"
        ]

        print(
            f"[TRACK] "
            f"{symbol} | "
            f"Score={score}/100 | "
            f"State={setup} | "
            f"MC={format_money(mc)} | "
            f"Liq={format_money(liquidity)} | "
            f"5mVol={format_money(volume)}"
        )

        # ----------------------------------------------------
        # IGNITION
        # ----------------------------------------------------

        if setup == "IGNITION":

            if address not in state[
                "alerts"
            ]:

                print(
                    f"[IGNITION] "
                    f"{symbol} | "
                    f"Score={score}"
                )

                register_alert(
                    snapshot,
                    analysis
                )

                broadcast(
                    ignition_message(
                        snapshot,
                        analysis
                    )
                )

    save_state()


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    global force_scan_requested

    load_state()

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

    if TELEGRAM_BOT_TOKEN:

        print(
            "[TELEGRAM] "
            "Bot token detected."
        )

    else:

        print(
            "[TELEGRAM] "
            "WARNING: token missing."
        )

    token_addresses = []

    last_discovery = 0

    while True:

        try:

            process_telegram()

            current = time.time()

            if (
                not token_addresses
                or
                current
                - last_discovery
                >= DISCOVERY_INTERVAL_SECONDS
                or
                force_scan_requested
            ):

                token_addresses = (
                    discover_tokens()
                )

                last_discovery = current

                force_scan_requested = False

            if token_addresses:

                scan_once(
                    token_addresses
                )

            else:

                print(
                    "[SCAN] "
                    "No Solana tokens found."
                )

            time.sleep(
                SCAN_INTERVAL_SECONDS
            )

        except KeyboardInterrupt:

            print(
                "[STOP] "
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
