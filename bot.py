import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V4.0 CLEAN
# DEX SCREENER FIRST
# ============================================================

BOT_VERSION = "V4.0-CLEAN"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

SOLANA = "solana"

STATE_FILE = "runner_state_v40.json"


# ============================================================
# SIGNAL FILTERS
# ============================================================

MIN_MC = 20_000
MAX_MC = 300_000

MIN_LIQUIDITY = 10_000

# We observe a wider range so tokens can build history
OBSERVE_MIN_MC = 10_000
OBSERVE_MAX_MC = 350_000

MAX_PAIR_AGE_HOURS = 24

# Activity reference levels
REFERENCE_VOLUME_5M = 5_000

MIN_CONSOLIDATION_VOLUME_5M = 500
MIN_CONSOLIDATION_TX_5M = 5
MIN_CONSOLIDATION_ACTIVE_OBS = 3
MAX_CONSOLIDATION_RANGE_PCT = 18.0

ACTIVITY_LOOKBACK_SECONDS = 60
ACTIVITY_EXPANSION_PCT = 20.0

# History
MAX_STORED_TOKENS = 1_500
MAX_HISTORY_PER_TOKEN = 300
MIN_OBSERVATIONS = 6

# Discovery
DISCOVERY_INTERVAL_SECONDS = 60
MAX_DISCOVERY_TOKENS = 500

# API
MAX_TOKEN_LOOKUPS_PER_CYCLE = 120
TOKEN_LOOKUP_SLEEP = 0.10

# Scan
SCAN_INTERVAL_SECONDS = float(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)

# HTTP
HTTP_TIMEOUT = 12
HTTP_RETRIES = 3
HTTP_BACKOFF_SECONDS = 1.5

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

HEATING_ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "true").lower() == "true"
)


# ============================================================
# GLOBAL STATE
# ============================================================

discovery_cache: List[str] = []
discovery_cache_ts = 0.0

state: Dict[str, Any] = {
    "histories": {},
    "ignitions": {},
    "subscribers": [],
}

telegram_offset = 0

force_scan_requested = False


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts() -> float:
    return time.time()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return default

        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return default

        return int(float(value))
    except (ValueError, TypeError):
        return default


def pct_change(old: float, new: float) -> float:
    if old <= 0:
        return 0.0

    return ((new - old) / old) * 100.0


def format_money(value: float) -> str:
    value = safe_float(value)

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def format_pct(value: float) -> str:
    value = safe_float(value)

    if value >= 0:
        return f"+{value:.1f}%"

    return f"{value:.1f}%"


def pair_age_hours(pair_created_at_ms: Any) -> float:
    created_ms = safe_float(pair_created_at_ms)

    if created_ms <= 0:
        return 9999.0

    created_seconds = created_ms / 1000.0

    return max(
        0.0,
        (now_ts() - created_seconds) / 3600.0,
    )


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


# ============================================================
# HTTP
# ============================================================

def http_json(
    url: str,
    retries: int = HTTP_RETRIES,
) -> Optional[Any]:

    headers = {
        "User-Agent": "RunnerBot/4.0",
        "Accept": "application/json",
    }

    last_error = None

    for attempt in range(retries):

        try:
            request = urllib.request.Request(
                url,
                headers=headers,
                method="GET",
            )

            with urllib.request.urlopen(
                request,
                timeout=HTTP_TIMEOUT,
            ) as response:

                raw = response.read()

                if not raw:
                    return None

                return json.loads(raw.decode("utf-8"))

        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            ConnectionError,
        ) as exc:

            last_error = exc

            if attempt < retries - 1:
                time.sleep(
                    HTTP_BACKOFF_SECONDS * (attempt + 1)
                )

    print(
        f"[HTTP ERROR] {url} -> {last_error}"
    )

    return None


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
            encoding="utf-8",
        ) as file:

            loaded = json.load(file)

        if not isinstance(loaded, dict):
            return

        state["histories"] = loaded.get(
            "histories",
            {},
        )

        state["ignitions"] = loaded.get(
            "ignitions",
            {},
        )

        state["subscribers"] = loaded.get(
            "subscribers",
            [],
        )

        if not isinstance(
            state["histories"],
            dict,
        ):
            state["histories"] = {}

        if not isinstance(
            state["ignitions"],
            dict,
        ):
            state["ignitions"] = {}

        if not isinstance(
            state["subscribers"],
            list,
        ):
            state["subscribers"] = []

        trim_state()

        print(
            "[STATE] Loaded existing state"
        )

    except Exception as exc:

        print(
            f"[STATE ERROR] {exc}"
        )


def save_state() -> None:

    try:

        trim_state()

        temporary_file = (
            STATE_FILE + ".tmp"
        )

        with open(
            temporary_file,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                state,
                file,
                indent=2,
            )

        os.replace(
            temporary_file,
            STATE_FILE,
        )

    except Exception as exc:

        print(
            f"[STATE SAVE ERROR] {exc}"
        )


def trim_state() -> None:

    histories = state.get(
        "histories",
        {},
    )

    if not isinstance(histories, dict):
        histories = {}

    # Remove oldest token histories if necessary
    if len(histories) > MAX_STORED_TOKENS:

        items = sorted(
            histories.items(),
            key=lambda item: (
                item[1][-1]["ts"]
                if item[1]
                else 0
            ),
            reverse=True,
        )

        histories = dict(
            items[:MAX_STORED_TOKENS]
        )

    # Trim each token history
    for token_address in list(
        histories.keys()
    ):

        history = histories[token_address]

        if not isinstance(history, list):
            histories[token_address] = []
            continue

        histories[token_address] = (
            history[-MAX_HISTORY_PER_TOKEN:]
        )

    state["histories"] = histories

    # Subscribers
    subscribers = state.get(
        "subscribers",
        [],
    )

    clean_subscribers = []

    for chat_id in subscribers:

        try:
            chat_id = int(chat_id)

            if chat_id not in clean_subscribers:
                clean_subscribers.append(chat_id)

        except Exception:
            pass

    state["subscribers"] = clean_subscribers


# ============================================================
# DEX SCREENER DISCOVERY
# ============================================================

def dex_discovery_request(
    path: str,
) -> Optional[Any]:

    url = DEX_BASE + path

    return http_json(url)


def extract_token_addresses(
    data: Any,
) -> List[str]:

    addresses = []

    if not isinstance(data, list):
        return addresses

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

        if not address:
            continue

        address = str(address).strip()

        if len(address) < 20:
            continue

        if address not in addresses:
            addresses.append(address)

    return addresses


def discover_token_addresses(
    force: bool = False,
) -> List[str]:

    global discovery_cache
    global discovery_cache_ts

    current = now_ts()

    if (
        not force
        and discovery_cache
        and current - discovery_cache_ts
        < DISCOVERY_INTERVAL_SECONDS
    ):
        return discovery_cache

    discovered: List[str] = []

    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]

    for endpoint in endpoints:

        data = dex_discovery_request(
            endpoint
        )

        addresses = extract_token_addresses(
            data
        )

        for address in addresses:

            if address not in discovered:

                discovered.append(address)

            if len(discovered) >= MAX_DISCOVERY_TOKENS:
                break

        if len(discovered) >= MAX_DISCOVERY_TOKENS:
            break

    # Keep tokens already being tracked.
    for address in state["histories"].keys():

        if address not in discovered:
            discovered.append(address)

        if len(discovered) >= MAX_DISCOVERY_TOKENS:
            break

    discovery_cache = discovered[
        :MAX_DISCOVERY_TOKENS
    ]

    discovery_cache_ts = current

    print(
        f"[DISCOVERY] {len(discovery_cache)} "
        f"Solana tokens available"
    )

    return discovery_cache


# ============================================================
# DEX SCREENER TOKEN PAIRS
# ============================================================

def get_token_pairs(
    token_address: str,
) -> List[Dict[str, Any]]:

    encoded = urllib.parse.quote(
        token_address,
        safe="",
    )

    url = (
        f"{DEX_BASE}/token-pairs/"
        f"v1/{SOLANA}/{encoded}"
    )

    data = http_json(url)

    if not isinstance(data, list):
        return []

    pairs = []

    for pair in data:

        if not isinstance(pair, dict):
            continue

        if str(
            pair.get("chainId", "")
        ).lower() != SOLANA:
            continue

        pairs.append(pair)

    return pairs


def choose_best_pair(
    pairs: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:

    if not pairs:
        return None

    def liquidity_value(
        pair: Dict[str, Any],
    ) -> float:

        liquidity = pair.get(
            "liquidity",
            {},
        )

        if not isinstance(
            liquidity,
            dict,
        ):
            return 0.0

        return safe_float(
            liquidity.get("usd")
        )

    pairs = sorted(
        pairs,
        key=liquidity_value,
        reverse=True,
    )

    return pairs[0]


def discover_pairs() -> List[Dict[str, Any]]:

    addresses = discover_token_addresses()

    selected = []

    limit = min(
        len(addresses),
        MAX_TOKEN_LOOKUPS_PER_CYCLE,
    )

    for index in range(limit):

        token_address = addresses[index]

        try:

            pairs = get_token_pairs(
                token_address
            )

            best_pair = choose_best_pair(
                pairs
            )

            if best_pair:

                selected.append(
                    best_pair
                )

        except Exception as exc:

            print(
                f"[PAIR ERROR] "
                f"{token_address}: {exc}"
            )

        time.sleep(
            TOKEN_LOOKUP_SLEEP
        )

    print(
        f"[PAIRS] {len(selected)} strongest "
        f"Solana pools"
    )

    return selected


# ============================================================
# PAIR -> SNAPSHOT
# ============================================================

def pair_to_snapshot(
    pair: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    try:

        base_token = pair.get(
            "baseToken",
            {},
        )

        if not isinstance(
            base_token,
            dict,
        ):
            base_token = {}

        token_address = str(
            base_token.get(
                "address",
                "",
            )
        ).strip()

        if not token_address:
            return None

        symbol = str(
            base_token.get(
                "symbol",
                "?",
            )
        ).strip()

        name = str(
            base_token.get(
                "name",
                symbol,
            )
        ).strip()

        liquidity_data = pair.get(
            "liquidity",
            {},
        )

        volume_data = pair.get(
            "volume",
            {},
        )

        txns_data = pair.get(
            "txns",
            {},
        )

        price_change = pair.get(
            "priceChange",
            {},
        )

        if not isinstance(
            liquidity_data,
            dict,
        ):
            liquidity_data = {}

        if not isinstance(
            volume_data,
            dict,
        ):
            volume_data = {}

        if not isinstance(
            txns_data,
            dict,
        ):
            txns_data = {}

        if not isinstance(
            price_change,
            dict,
        ):
            price_change = {}

        tx_m5 = txns_data.get(
            "m5",
            {},
        )

        tx_h1 = txns_data.get(
            "h1",
            {},
        )

        if not isinstance(
            tx_m5,
            dict,
        ):
            tx_m5 = {}

        if not isinstance(
            tx_h1,
            dict,
        ):
            tx_h1 = {}

        buys_5m = safe_int(
            tx_m5.get("buys")
        )

        sells_5m = safe_int(
            tx_m5.get("sells")
        )

        buys_h1 = safe_int(
            tx_h1.get("buys")
        )

        sells_h1 = safe_int(
            tx_h1.get("sells")
        )

        volume_5m = safe_float(
            volume_data.get("m5")
        )

        volume_h1 = safe_float(
            volume_data.get("h1")
        )

        market_cap = safe_float(
            pair.get("marketCap")
        )

        if market_cap <= 0:
            market_cap = safe_float(
                pair.get("fdv")
            )

        liquidity = safe_float(
            liquidity_data.get("usd")
        )

        created_at = safe_float(
            pair.get("pairCreatedAt")
        )

        age_hours = pair_age_hours(
            created_at
        )

        snapshot = {
            "ts": now_ts(),

            "token_address": token_address,

            "pair_address": str(
                pair.get(
                    "pairAddress",
                    "",
                )
            ),

            "symbol": symbol,

            "name": name,

            "market_cap": market_cap,

            "fdv": safe_float(
                pair.get("fdv")
            ),

            "liquidity": liquidity,

            "volume_5m": volume_5m,

            "volume_1h": volume_h1,

            "buys_5m": buys_5m,

            "sells_5m": sells_5m,

            "buys_1h": buys_h1,

            "sells_1h": sells_h1,

            "tx_5m": buys_5m + sells_5m,

            "tx_1h": buys_h1 + sells_h1,

            "price_usd": safe_float(
                pair.get("priceUsd")
            ),

            "price_change_5m": safe_float(
                price_change.get("m5")
            ),

            "price_change_1h": safe_float(
                price_change.get("h1")
            ),

            "pair_created_at": created_at,

            "age_hours": age_hours,

            "dex_id": str(
                pair.get(
                    "dexId",
                    "",
                )
            ),

            "url": str(
                pair.get(
                    "url",
                    "",
                )
            ),

            "labels": pair.get(
                "labels",
                [],
            ),

            "boosts": pair.get(
                "boosts",
                {},
            ),
        }

        return snapshot

    except Exception as exc:

        print(
            f"[SNAPSHOT ERROR] {exc}"
        )

        return None


# ============================================================
# HISTORY
# ============================================================

def record_snapshot(
    snapshot: Dict[str, Any],
) -> None:

    token_address = snapshot[
        "token_address"
    ]

    mc = safe_float(
        snapshot["market_cap"]
    )

    if (
        mc < OBSERVE_MIN_MC
        or mc > OBSERVE_MAX_MC
    ):
        return

    histories = state[
        "histories"
    ]

    if token_address not in histories:
        histories[token_address] = []

    histories[token_address].append(
        snapshot
    )

    histories[token_address] = (
        histories[token_address][
            -MAX_HISTORY_PER_TOKEN:
        ]
    )


def recent_history(
    token_address: str,
) -> List[Dict[str, Any]]:

    history = state[
        "histories"
    ].get(
        token_address,
        [],
    )

    if not isinstance(
        history,
        list,
    ):
        return []

    return history[
        -MAX_HISTORY_PER_TOKEN:
    ]


def snapshot_about_seconds_ago(
    history: List[Dict[str, Any]],
    seconds: float,
) -> Optional[Dict[str, Any]]:

    if not history:
        return None

    target = now_ts() - seconds

    best = None
    best_distance = float("inf")

    for snapshot in history:

        ts = safe_float(
            snapshot.get("ts")
        )

        distance = abs(
            ts - target
        )

        if distance < best_distance:

            best = snapshot
            best_distance = distance

    return best


# ============================================================
# ACTIVITY MODEL
# ============================================================

def calculate_activity(
    history: List[Dict[str, Any]],
) -> Dict[str, float]:

    if not history:
        return {
            "volume_acceleration": 0.0,
            "tx_acceleration": 0.0,
            "activity_acceleration": 0.0,
        }

    current = history[-1]

    older = snapshot_about_seconds_ago(
        history,
        ACTIVITY_LOOKBACK_SECONDS,
    )

    if older is None:

        return {
            "volume_acceleration": 0.0,
            "tx_acceleration": 0.0,
            "activity_acceleration": 0.0,
        }

    current_volume = safe_float(
        current.get("volume_5m")
    )

    older_volume = safe_float(
        older.get("volume_5m")
    )

    current_tx = safe_float(
        current.get("tx_5m")
    )

    older_tx = safe_float(
        older.get("tx_5m")
    )

    volume_acceleration = pct_change(
        older_volume,
        current_volume,
    )

    tx_acceleration = pct_change(
        older_tx,
        current_tx,
    )

    activity_acceleration = (
        volume_acceleration
        + tx_acceleration
    ) / 2.0

    return {
        "volume_acceleration":
            volume_acceleration,

        "tx_acceleration":
            tx_acceleration,

        "activity_acceleration":
            activity_acceleration,
    }


# ============================================================
# BUY PRESSURE
# ============================================================

def calculate_buy_pressure(
    snapshot: Dict[str, Any],
) -> Dict[str, float]:

    buys = safe_float(
        snapshot.get("buys_5m")
    )

    sells = safe_float(
        snapshot.get("sells_5m")
    )

    total = buys + sells

    if total <= 0:

        return {
            "buy_percentage": 0.0,
            "buy_sell_ratio": 0.0,
        }

    buy_percentage = (
        buys / total
    ) * 100.0

    if sells <= 0:
        ratio = float("inf")
    else:
        ratio = buys / sells

    return {
        "buy_percentage":
            buy_percentage,

        "buy_sell_ratio":
            ratio,
    }


# ============================================================
# CONSOLIDATION
# ============================================================

def is_active_consolidation_snapshot(
    snapshot: Dict[str, Any],
) -> bool:

    volume = safe_float(
        snapshot.get("volume_5m")
    )

    tx = safe_int(
        snapshot.get("tx_5m")
    )

    price_change = abs(
        safe_float(
            snapshot.get(
                "price_change_5m"
            )
        )
    )

    return (
        volume >= MIN_CONSOLIDATION_VOLUME_5M
        and tx >= MIN_CONSOLIDATION_TX_5M
        and price_change <= MAX_CONSOLIDATION_RANGE_PCT
    )


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    current: Dict[str, Any],
    history: List[Dict[str, Any]],
    activity: Dict[str, float],
    buy_pressure: Dict[str, float],
    consolidation: bool,
    breakout: bool,
) -> Tuple[int, List[str]]:

    score = 0
    reasons: List[str] = []

    age = safe_float(
        current.get("age_hours")
    )

    mc = safe_float(
        current.get("market_cap")
    )

    liquidity = safe_float(
        current.get("liquidity")
    )

    volume = safe_float(
        current.get("volume_5m")
    )

    tx = safe_int(
        current.get("tx_5m")
    )

    buy_percentage = safe_float(
        buy_pressure.get(
            "buy_percentage"
        )
    )

    activity_acceleration = safe_float(
        activity.get(
            "activity_acceleration"
        )
    )

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    if age <= 3:
        score += 15
        reasons.append("very early")

    elif age <= 6:
        score += 12
        reasons.append("early")

    elif age <= 12:
        score += 9
        reasons.append("young")

    elif age <= 24:
        score += 6
        reasons.append("under 24h")

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    if mc <= 75_000:
        score += 15
        reasons.append("low MC")

    elif mc <= 150_000:
        score += 11

    elif mc <= 225_000:
        score += 8

    else:
        score += 5

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if liquidity >= 50_000:
        score += 10
        reasons.append("strong liquidity")

    elif liquidity >= 25_000:
        score += 9
        reasons.append("healthy liquidity")

    elif liquidity >= 15_000:
        score += 8
        reasons.append("adequate liquidity")

    elif liquidity >= 10_000:
        score += 6

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume >= 20_000:
        score += 10
        reasons.append("high 5m volume")

    elif volume >= 10_000:
        score += 8
        reasons.append("strong 5m volume")

    elif volume >= 5_000:
        score += 6
        reasons.append("5m volume >= $5K")

    elif volume >= 2_000:
        score += 4

    elif volume >= 500:
        score += 2

    # --------------------------------------------------------
    # BUY PARTICIPATION
    #
    # IMPORTANT:
    # This contributes to the score.
    # It is NOT a hard ignition gate.
    # --------------------------------------------------------

    if buy_percentage >= 70:
        score += 10
        reasons.append("strong buy participation")

    elif buy_percentage >= 60:
        score += 8
        reasons.append("good buy participation")

    elif buy_percentage >= 55:
        score += 6

    elif buy_percentage >= 50:
        score += 3

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    if tx >= 200:
        score += 10
        reasons.append("very active")

    elif tx >= 100:
        score += 8
        reasons.append("high transactions")

    elif tx >= 50:
        score += 6

    elif tx >= 25:
        score += 4

    elif tx >= 10:
        score += 2

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    if consolidation:
        score += 7
        reasons.append("consolidation")

    if breakout:
        score += 8
        reasons.append("MC breakout")

    if history:

        oldest_mc = safe_float(
            history[0].get(
                "market_cap"
            )
        )

        if (
            oldest_mc > 0
            and mc > oldest_mc
        ):
            score += 2

    # --------------------------------------------------------
    # ACTIVITY
    # --------------------------------------------------------

    if activity_acceleration >= 75:
        score += 15
        reasons.append("major activity expansion")

    elif activity_acceleration >= 40:
        score += 12
        reasons.append("strong activity expansion")

    elif activity_acceleration >= 20:
        score += 8
        reasons.append("activity expansion")

    # --------------------------------------------------------
    # VERTICAL MOVE PENALTY
    # --------------------------------------------------------

    price_change_5m = safe_float(
        current.get(
            "price_change_5m"
        )
    )

    penalty = 0

    if price_change_5m > 100:
        penalty = 15
        reasons.append("extremely vertical")

    elif price_change_5m > 75:
        penalty = 10
        reasons.append("very vertical")

    elif price_change_5m > 50:
        penalty = 5
        reasons.append("vertical move")

    score -= penalty

    score = int(
        clamp(
            score,
            0,
            100,
        )
    )

    return score, reasons


# ============================================================
# ANALYSIS
# ============================================================

def analyze(
    snapshot: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    token_address = snapshot[
        "token_address"
    ]

    history = recent_history(
        token_address
    )

    if len(history) < MIN_OBSERVATIONS:
        return None

    current = history[-1]

    recent = history[
        -MIN_OBSERVATIONS:
    ]

    oldest = recent[0]

    current_mc = safe_float(
        current.get("market_cap")
    )

    previous_mc = safe_float(
        recent[-2].get(
            "market_cap"
        )
    )

    oldest_mc = safe_float(
        oldest.get("market_cap")
    )

    previous_high = max(
        safe_float(
            item.get("market_cap")
        )
        for item in recent[:-1]
    )

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    breakout = (
        previous_high > 0
        and current_mc
        > previous_high * 1.01
    )

    instant_mc_move = pct_change(
        previous_mc,
        current_mc,
    )

    mc_move = pct_change(
        oldest_mc,
        current_mc,
    )

    # --------------------------------------------------------
    # RANGE
    # --------------------------------------------------------

    recent_mcs = [
        safe_float(
            item.get("market_cap")
        )
        for item in recent
        if safe_float(
            item.get("market_cap")
        ) > 0
    ]

    if recent_mcs:

        high_mc = max(
            recent_mcs
        )

        low_mc = min(
            recent_mcs
        )

        if low_mc > 0:

            range_pct = (
                (high_mc - low_mc)
                / low_mc
            ) * 100.0

        else:
            range_pct = 0.0

    else:
        range_pct = 0.0

    # --------------------------------------------------------
    # ACTIVITY
    # --------------------------------------------------------

    activity = calculate_activity(
        recent
    )

    activity_expansion = (
        activity[
            "volume_acceleration"
        ] >= ACTIVITY_EXPANSION_PCT
        or
        activity[
            "tx_acceleration"
        ] >= ACTIVITY_EXPANSION_PCT
    )

    # --------------------------------------------------------
    # BUY PRESSURE
    # --------------------------------------------------------

    buy_pressure = calculate_buy_pressure(
        current
    )

    # --------------------------------------------------------
    # CONSOLIDATION
    # --------------------------------------------------------

    active_consolidation_count = sum(
        1
        for item in recent
        if is_active_consolidation_snapshot(
            item
        )
    )

    consolidation = (
        active_consolidation_count
        >= MIN_CONSOLIDATION_ACTIVE_OBS
        and range_pct
        <= MAX_CONSOLIDATION_RANGE_PCT
    )

    # --------------------------------------------------------
    # LIQUIDITY STABILITY
    # --------------------------------------------------------

    old_liquidity = safe_float(
        oldest.get(
            "liquidity"
        )
    )

    current_liquidity = safe_float(
        current.get(
            "liquidity"
        )
    )

    liquidity_move = pct_change(
        old_liquidity,
        current_liquidity,
    )

    liquidity_stable = (
        liquidity_move >= -15
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score, reasons = calculate_score(
        current=current,
        history=recent,
        activity=activity,
        buy_pressure=buy_pressure,
        consolidation=consolidation,
        breakout=breakout,
    )

    if liquidity_stable:
        reasons.append(
            "liquidity stable"
        )

    # --------------------------------------------------------
    # SETUP STATE
    #
    # THIS IS THE IMPORTANT PART.
    #
    # NO buy_percentage >= 50 gate.
    #
    # A token becomes IGNITION when:
    #
    # score >= 70
    # AND breakout
    # AND activity expansion
    # --------------------------------------------------------

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
        "state": setup_state,

        "score": score,

        "reasons": reasons,

        "breakout": breakout,

        "consolidation": consolidation,

        "activity_expansion":
            activity_expansion,

        "liquidity_stable":
            liquidity_stable,

        "mc_move": mc_move,

        "instant_mc_move":
            instant_mc_move,

        "range_pct": range_pct,

        "liquidity_move":
            liquidity_move,

        "buy_percentage":
            buy_pressure[
                "buy_percentage"
            ],

        "buy_sell_ratio":
            buy_pressure[
                "buy_sell_ratio"
            ],

        "volume_acceleration":
            activity[
                "volume_acceleration"
            ],

        "tx_acceleration":
            activity[
                "tx_acceleration"
            ],

        "activity_acceleration":
            activity[
                "activity_acceleration"
            ],
    }


# ============================================================
# IGNITION TRACKING
# ============================================================

def create_ignition(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    token_address = snapshot[
        "token_address"
    ]

    if token_address in state[
        "ignitions"
    ]:
        return None

    mc = safe_float(
        snapshot.get(
            "market_cap"
        )
    )

    event = {
        "token_address":
            token_address,

        "symbol":
            snapshot.get(
                "symbol",
                "?",
            ),

        "name":
            snapshot.get(
                "name",
                "",
            ),

        "pair_address":
            snapshot.get(
                "pair_address",
                "",
            ),

        "url":
            snapshot.get(
                "url",
                "",
            ),

        "start_ts":
            now_ts(),

        "start_mc":
            mc,

        "peak_mc":
            mc,

        "lowest_mc":
            mc,

        "max_drawdown_pct":
            0.0,

        "score":
            analysis.get(
                "score",
                0,
            ),

        "reasons":
            analysis.get(
                "reasons",
                [],
            ),

        "outcomes": {},

        "last_notification":
            {},
    }

    state[
        "ignitions"
    ][token_address] = event

    save_state()

    return event


def update_ignition(
    snapshot: Dict[str, Any],
    send_notifications: bool = True,
) -> None:

    token_address = snapshot[
        "token_address"
    ]

    event = state[
        "ignitions"
    ].get(
        token_address
    )

    if not event:
        return

    current_mc = safe_float(
        snapshot.get(
            "market_cap"
        )
    )

    start_mc = safe_float(
        event.get(
            "start_mc"
        )
    )

    peak_mc = safe_float(
        event.get(
            "peak_mc"
        )
    )

    lowest_mc = safe_float(
        event.get(
            "lowest_mc"
        )
    )

    if current_mc > peak_mc:

        event["peak_mc"] = current_mc
        peak_mc = current_mc

    if (
        lowest_mc <= 0
        or current_mc < lowest_mc
    ):

        event["lowest_mc"] = current_mc
        lowest_mc = current_mc

    if start_mc > 0:

        drawdown = (
            (lowest_mc - start_mc)
            / start_mc
        ) * 100.0

        event[
            "max_drawdown_pct"
        ] = min(
            0.0,
            drawdown,
        )

    elapsed_seconds = (
        now_ts()
        - safe_float(
            event.get(
                "start_ts"
            )
        )
    )

    checkpoints = [
        (300, "5m"),
        (900, "15m"),
        (1800, "30m"),
    ]

    for seconds, label in checkpoints:

        if elapsed_seconds < seconds:
            continue

        if label in event[
            "outcomes"
        ]:
            continue

        if start_mc <= 0:
            continue

        return_pct = (
            (current_mc - start_mc)
            / start_mc
        ) * 100.0

        if label in (
            "5m",
            "15m",
        ):

            if return_pct >= 10:
                result = "CONTINUING"

            elif return_pct <= -15:
                result = "FAILED"

            else:
                result = "UNCLEAR"

        else:

            if return_pct >= 30:
                result = "RUNNER"

            elif return_pct <= -15:
                result = "FAILED"

            else:
                result = "UNCLEAR"

        event[
            "outcomes"
        ][label] = {
            "return_pct":
                return_pct,

            "result":
                result,

            "mc":
                current_mc,

            "ts":
                now_ts(),
        }

        if send_notifications:

            message = build_outcome_alert(
                event,
                label,
                return_pct,
                result,
                current_mc,
            )

            broadcast(message)

    save_state()


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

    payload = payload or {}

    try:

        encoded = urllib.parse.urlencode(
            payload
        ).encode()

        request = urllib.request.Request(
            url,
            data=encoded,
            headers={
                "Content-Type":
                    "application/x-www-form-urlencoded",
                "User-Agent":
                    "RunnerBot/4.0",
            },
            method="POST",
        )

        with urllib.request.urlopen(
            request,
            timeout=HTTP_TIMEOUT,
        ) as response:

            raw = response.read()

            if not raw:
                return None

            return json.loads(
                raw.decode("utf-8")
            )

    except Exception as exc:

        print(
            f"[TELEGRAM ERROR] {exc}"
        )

        return None


def telegram_send(
    chat_id: int,
    text: str,
) -> None:

    if not TELEGRAM_BOT_TOKEN:
        return

    telegram_api(
        "sendMessage",
        {
            "chat_id":
                chat_id,

            "text":
                text,

            "disable_web_page_preview":
                False,
        },
    )


def broadcast(
    text: str,
) -> None:

    if not HEATING_ALERTS_ENABLED:
        return

    subscribers = list(
        state.get(
            "subscribers",
            [],
        )
    )

    for chat_id in subscribers:

        try:
            telegram_send(
                int(chat_id),
                text,
            )

            time.sleep(0.05)

        except Exception as exc:

            print(
                f"[BROADCAST ERROR] {exc}"
            )


def process_telegram_updates() -> None:

    global telegram_offset
    global force_scan_requested

    if not TELEGRAM_BOT_TOKEN:
        return

    result = telegram_api(
        "getUpdates",
        {
            "offset":
                telegram_offset,

            "timeout":
                1,

            "allowed_updates":
                json.dumps(
                    ["message"]
                ),
        },
    )

    if not isinstance(
        result,
        dict,
    ):
        return

    if not result.get("ok"):
        return

    updates = result.get(
        "result",
        [],
    )

    if not isinstance(
        updates,
        list,
    ):
        return

    for update in updates:

        update_id = safe_int(
            update.get(
                "update_id"
            )
        )

        telegram_offset = max(
            telegram_offset,
            update_id + 1,
        )

        message = update.get(
            "message",
            {},
        )

        if not isinstance(
            message,
            dict,
        ):
            continue

        chat = message.get(
            "chat",
            {},
        )

        if not isinstance(
            chat,
            dict,
        ):
            continue

        chat_id = safe_int(
            chat.get(
                "id"
            )
        )

        text = str(
            message.get(
                "text",
                "",
            )
        ).strip()

        if not text:
            continue

        command = text.split()[0].lower()

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        if command == "/start":

            if chat_id not in state[
                "subscribers"
            ]:

                state[
                    "subscribers"
                ].append(
                    chat_id
                )

                save_state()

            telegram_send(
                chat_id,
                (
                    "🚀 Runner Bot V4 is online.\n\n"
                    "DEX Screener-first Solana scanner.\n\n"
                    "Watching:\n"
                    "• $20K–$300K MC\n"
                    "• $10K+ liquidity\n"
                    "• Max 24h pair age\n"
                    "• Structure\n"
                    "• Activity expansion\n"
                    "• Buy participation\n"
                    "• Runner score\n\n"
                    "IGNITION requires:\n"
                    "Score ≥ 70\n"
                    "AND breakout\n"
                    "AND activity expansion."
                ),
            )

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        elif command == "/stop":

            if chat_id in state[
                "subscribers"
            ]:

                state[
                    "subscribers"
                ].remove(
                    chat_id
                )

                save_state()

            telegram_send(
                chat_id,
                "🛑 Runner alerts stopped for this chat.",
            )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        elif command == "/status":

            histories = len(
                state[
                    "histories"
                ]
            )

            ignitions = len(
                state[
                    "ignitions"
                ]
            )

            subscribers = len(
                state[
                    "subscribers"
                ]
            )

            telegram_send(
                chat_id,
                (
                    f"🤖 Runner Bot {BOT_VERSION}\n\n"
                    f"Tracked tokens: {histories}\n"
                    f"Active ignition records: {ignitions}\n"
                    f"Subscribers: {subscribers}\n"
                    f"Scan interval: {SCAN_INTERVAL_SECONDS:.0f}s\n"
                    f"Discovery cache: {len(discovery_cache)}"
                ),
            )

        # ----------------------------------------------------
        # ALERTS
        # ----------------------------------------------------

        elif command == "/alerts":

            enabled = (
                chat_id
                in state["subscribers"]
            )

            telegram_send(
                chat_id,
                (
                    "🔔 Alerts: "
                    + (
                        "ON"
                        if enabled
                        else "OFF"
                    )
                ),
            )

        # ----------------------------------------------------
        # SCAN
        # ----------------------------------------------------

        elif command == "/scan":

            force_scan_requested = True

            telegram_send(
                chat_id,
                "🔎 Scan requested. Running on the next cycle.",
            )


# ============================================================
# TELEGRAM MESSAGE BUILDERS
# ============================================================

def build_ignition_alert(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> str:

    symbol = snapshot.get(
        "symbol",
        "?",
    )

    name = snapshot.get(
        "name",
        symbol,
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

    volume_5m = safe_float(
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

    score = safe_int(
        analysis.get(
            "score"
        )
    )

    buy_pct = safe_float(
        analysis.get(
            "buy_percentage"
        )
    )

    buy_sell_ratio = safe_float(
        analysis.get(
            "buy_sell_ratio"
        )
    )

    activity = safe_float(
        analysis.get(
            "activity_acceleration"
        )
    )

    mc_move = safe_float(
        analysis.get(
            "mc_move"
        )
    )

    range_pct = safe_float(
        analysis.get(
            "range_pct"
        )
    )

    reasons = analysis.get(
        "reasons",
        [],
    )

    if not isinstance(
        reasons,
        list,
    ):
        reasons = []

    reason_text = "\n".join(
        f"• {reason}"
        for reason in reasons[:8]
    )

    url = str(
        snapshot.get(
            "url",
            "",
        )
    )

    return (
        "🔥 RUNNER IGNITION\n\n"

        f"🪙 {name} (${symbol})\n"

        f"💰 MC: {format_money(mc)}\n"

        f"💧 Liquidity: "
        f"{format_money(liquidity)}\n"

        f"⏱ Age: {age:.1f}h\n"

        f"📊 5m Vol: "
        f"{format_money(volume_5m)}\n"

        f"🟢 Buys: {buys}\n"

        f"🔴 Sells: {sells}\n"

        f"📈 Buy participation: "
        f"{buy_pct:.1f}%\n"

        f"⚖️ Buy/Sell: "
        f"{buy_sell_ratio:.2f}x\n"

        f"⚡ Activity expansion: "
        f"{format_pct(activity)}\n"

        f"📈 5m price: "
        f"{format_pct(price_change)}\n"

        f"💥 MC move: "
        f"{format_pct(mc_move)}\n"

        f"📐 Range: "
        f"{range_pct:.1f}%\n"

        f"🏆 Score: {score}/100\n\n"

        "WHY IT TRIGGERED:\n"
        f"{reason_text}\n\n"

        "✅ IGNITION CONDITIONS:\n"
        "• Score ≥ 70\n"
        "• MC breakout\n"
        "• Activity expansion\n\n"

        + (
            f"🔗 {url}"
            if url
            else ""
        )
    )


def build_outcome_alert(
    event: Dict[str, Any],
    label: str,
    return_pct: float,
    result: str,
    current_mc: float,
) -> str:

    symbol = event.get(
        "symbol",
        "?",
    )

    start_mc = safe_float(
        event.get(
            "start_mc"
        )
    )

    peak_mc = safe_float(
        event.get(
            "peak_mc"
        )
    )

    drawdown = safe_float(
        event.get(
            "max_drawdown_pct"
        )
    )

    emoji = {
        "RUNNER": "🚀",
        "CONTINUING": "📈",
        "FAILED": "❌",
        "UNCLEAR": "⚪",
    }.get(
        result,
        "📊",
    )

    return (
        f"{emoji} RUNNER UPDATE\n\n"

        f"🪙 ${symbol}\n"

        f"⏱ Checkpoint: {label}\n"

        f"📌 Result: {result}\n\n"

        f"Start MC: "
        f"{format_money(start_mc)}\n"

        f"Current MC: "
        f"{format_money(current_mc)}\n"

        f"Peak MC: "
        f"{format_money(peak_mc)}\n"

        f"Return: "
        f"{format_pct(return_pct)}\n"

        f"Max drawdown: "
        f"{format_pct(drawdown)}"
    )


# ============================================================
# SCAN
# ============================================================

def scan_once() -> None:

    print(
        "\n"
        + "=" * 60
    )

    print(
        f"[SCAN] {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    pairs = discover_pairs()

    if not pairs:

        print(
            "[SCAN] No pairs discovered"
        )

        return

    seen_tokens = set()

    for pair in pairs:

        snapshot = pair_to_snapshot(
            pair
        )

        if snapshot is None:
            continue

        token_address = snapshot[
            "token_address"
        ]

        # Avoid analyzing duplicate token
        # snapshots from duplicate pools.
        if token_address in seen_tokens:
            continue

        seen_tokens.add(
            token_address
        )

        # ----------------------------------------------------
        # RECORD BEFORE FILTERING
        # ----------------------------------------------------

        record_snapshot(
            snapshot
        )

        # ----------------------------------------------------
        # HARD QUALIFICATION FILTER
        # ----------------------------------------------------

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

        if mc < MIN_MC:
            continue

        if mc > MAX_MC:
            continue

        if liquidity < MIN_LIQUIDITY:
            continue

        if age > MAX_PAIR_AGE_HOURS:
            continue

        # ----------------------------------------------------
        # ANALYZE
        # ----------------------------------------------------

        analysis = analyze(
            snapshot
        )

        if analysis is None:
            continue

        score = safe_int(
            analysis.get(
                "score"
            )
        )

        setup_state = analysis.get(
            "state",
            "OBSERVING",
        )

        buy_pct = safe_float(
            analysis.get(
                "buy_percentage"
            )
        )

        activity = safe_float(
            analysis.get(
                "activity_acceleration"
            )
        )

        mc_move = safe_float(
            analysis.get(
                "mc_move"
            )
        )

        print(
            f"[TRACK] "
            f"{snapshot.get('symbol', '?')} "
            f"Age={age:.1f}h "
            f"MC={format_money(mc)} "
            f"Liq={format_money(liquidity)} "
            f"5mVol={format_money(snapshot.get('volume_5m', 0))} "
            f"Buy%={buy_pct:.1f} "
            f"Act={activity:.1f}% "
            f"MC={format_pct(mc_move)} "
            f"State={setup_state} "
            f"Score={score}/100"
        )

        # ----------------------------------------------------
        # IGNITION
        # ----------------------------------------------------

        if setup_state == "IGNITION":

            event = create_ignition(
                snapshot,
                analysis,
            )

            if event is not None:

                print(
                    f"[IGNITION] "
                    f"{snapshot.get('symbol', '?')} "
                    f"MC={format_money(mc)} "
                    f"Score={score}"
                )

                alert = build_ignition_alert(
                    snapshot,
                    analysis,
                )

                broadcast(
                    alert
                )

        # ----------------------------------------------------
        # UPDATE EXISTING IGNITION
        # ----------------------------------------------------

        if token_address in state[
            "ignitions"
        ]:

            update_ignition(
                snapshot,
                send_notifications=True,
            )

    save_state()


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    global force_scan_requested

    print(
        "\n"
        "============================================================\n"
        f"🚀 RUNNER BOT {BOT_VERSION}\n"
        "============================================================\n"
        "DEX Screener-first\n"
        "Solana only\n"
        f"MC: ${MIN_MC:,} - ${MAX_MC:,}\n"
        f"Liquidity: ${MIN_LIQUIDITY:,}+\n"
        f"Pair age: <= {MAX_PAIR_AGE_HOURS}h\n"
        f"Scan interval: {SCAN_INTERVAL_SECONDS:.0f}s\n"
        "============================================================\n"
    )

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARNING] "
            "TELEGRAM_BOT_TOKEN is not set."
        )

    else:

        print(
            "[TELEGRAM] Bot token detected."
        )

    load_state()

    next_scan = 0.0

    while True:

        try:

            process_telegram_updates()

            current = now_ts()

            if (
                current >= next_scan
                or force_scan_requested
            ):

                force_scan_requested = False

                scan_once()

                next_scan = (
                    now_ts()
                    + SCAN_INTERVAL_SECONDS
                )

            time.sleep(1)

        except KeyboardInterrupt:

            print(
                "\n[STOP] Runner Bot stopped."
            )

            save_state()

            break

        except Exception as exc:

            print(
                f"[MAIN ERROR] {exc}"
            )

            time.sleep(5)


if __name__ == "__main__":
    main()
