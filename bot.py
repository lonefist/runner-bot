import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V4.5
# BALANCED FLOW-PRESSURE FILTER
# ============================================================

BOT_VERSION = "V4.5-BALANCED-FLOW"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

STATE_FILE = "runner_state_v42.json"


# ============================================================
# ENVIRONMENT
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "true").lower()
    in ("1", "true", "yes", "on")
)

SCAN_INTERVAL_SECONDS = int(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)


# ============================================================
# V4.5 CORE FILTERS
# ============================================================

# Market cap
MIN_MC = 6_000

NORMAL_MAX_MC = 150_000
EXTENDED_MAX_MC = 250_000

# Extended MC requires stronger Flow/MC
EXTENDED_MC_MIN_FLOW_PCT = 11.0


# Liquidity
MIN_LIQUIDITY = 6_000

# Liquidity fallback for extremely early tokens
EARLY_LIQUIDITY_FALLBACK_AGE_HOURS = 1.5
EARLY_LIQUIDITY_FALLBACK_VOLUME = 8_000
EARLY_LIQUIDITY_FALLBACK_FLOW = 3_000
EARLY_LIQUIDITY_FALLBACK_FLOW_MC_PCT = 12.0


# Age
MAX_AGE_HOURS = 48.0


# 5m activity
MIN_VOLUME_5M = 1_500
MIN_FLOW_PROXY_USD = 1_500


# Flow / market cap
MIN_FLOW_MC_PCT = 8.0
STRONG_FLOW_MC_PCT = 15.0
ULTRA_FLOW_MC_PCT = 25.0


# Volume / flow
#
# This is a SOFT preference.
# Strong Flow/MC or absolute flow can override it.
PREFERRED_VOLUME_FLOW_RATIO = 1.25


# Buy/sell ratio is also soft.
MIN_BUY_SELL_RATIO = 1.50


# ============================================================
# CONFIRMATION
# ============================================================

NORMAL_OBSERVATIONS = 3
STRONG_OBSERVATIONS = 2
ULTRA_OBSERVATIONS = 1

PENDING_EXPIRY_SECONDS = 5 * 60

MAX_IGNITION_DRAWDOWN_PCT = -5.0

CONFIRMATION_MC_GROWTH_PCT = 5.0


# ============================================================
# ALERT COOLDOWNS
# ============================================================

ALERT_COOLDOWN_SECONDS = 10 * 60

GLOBAL_ALERT_COOLDOWN_SECONDS = 60


# ============================================================
# FLOW PROXY
# ============================================================

FLOW_LOOKBACK_SECONDS = 60
FLOW_ACCELERATION_PCT = 20.0


# ============================================================
# ACTIVITY
# ============================================================

ACTIVITY_LOOKBACK_SECONDS = 60
ACTIVITY_EXPANSION_PCT = 20.0

MIN_TX_5M = 8


# ============================================================
# STRUCTURE
# ============================================================

MAX_CONSOLIDATION_RANGE_PCT = 18.0


# ============================================================
# HISTORY / DISCOVERY
# ============================================================

MIN_OBSERVATIONS = 6

MAX_STORED_TOKENS = 1500
MAX_HISTORY_PER_TOKEN = 300

DISCOVERY_INTERVAL_SECONDS = 60
MAX_DISCOVERY_TOKENS = 500

DEX_BATCH_SIZE = 25


# ============================================================
# DEBUGGING
# ============================================================

DEBUG_MODE = (
    os.getenv("RUNNER_DEBUG", "true").lower()
    in ("1", "true", "yes", "on")
)

DEBUG_TOP_CANDIDATES = 10

PRE_FILTER_MIN_VOLUME_5M = 500
PRE_FILTER_MIN_ABS_FLOW_USD = 500


# ============================================================
# OUTCOME WINDOWS
# ============================================================

OUTCOME_WINDOWS = [
    5 * 60,
    15 * 60,
    30 * 60,
    60 * 60,
]


# ============================================================
# HTTP
# ============================================================

def http_get_json(
    url: str,
    timeout: int = 20,
) -> Optional[Dict[str, Any]]:

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RunnerBot/4.5",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=timeout,
        ) as response:

            raw = response.read().decode("utf-8")

            if not raw:
                return None

            return json.loads(raw)

    except Exception as exc:
        print(f"[HTTP ERROR] {exc}")
        return None


# ============================================================
# TIME HELPERS
# ============================================================

def now_ts() -> int:
    return int(time.time())


def iso_now() -> str:
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


def pct_change(old: float, new: float) -> float:

    if old <= 0:
        return 0.0

    return ((new - old) / old) * 100.0


# ============================================================
# STATE
# ============================================================

def default_state() -> Dict[str, Any]:

    return {
        "version": BOT_VERSION,

        "subscribers": [],

        "alerts_enabled": ALERTS_ENABLED,

        "tokens": {},

        "pending": {},

        "alerts": {},

        "last_global_alert": 0,

        "last_discovery": 0,

        "offset": 0,

        "created_at": iso_now(),

        "updated_at": iso_now(),
    }


def load_state() -> Dict[str, Any]:

    if not os.path.exists(STATE_FILE):
        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            state = json.load(f)

        base = default_state()

        for key, value in base.items():

            if key not in state:
                state[key] = value

        state["version"] = BOT_VERSION

        return state

    except Exception as exc:

        print(f"[STATE] Failed to load state: {exc}")

        return default_state()


STATE = load_state()


def save_state() -> None:

    STATE["version"] = BOT_VERSION
    STATE["updated_at"] = iso_now()

    try:

        tmp_file = STATE_FILE + ".tmp"

        with open(
            tmp_file,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                STATE,
                f,
                indent=2,
                ensure_ascii=False,
            )

        os.replace(
            tmp_file,
            STATE_FILE,
        )

    except Exception as exc:

        print(f"[STATE] Save error: {exc}")


# ============================================================
# TOKEN HELPERS
# ============================================================

def token_key(address: str) -> str:

    return address.lower().strip()


def get_token_state(address: str) -> Dict[str, Any]:

    key = token_key(address)

    if key not in STATE["tokens"]:

        STATE["tokens"][key] = {
            "address": address,
            "symbol": "UNKNOWN",
            "name": "UNKNOWN",
            "history": [],
            "first_seen": now_ts(),
            "last_seen": now_ts(),
        }

    return STATE["tokens"][key]


# ============================================================
# DISCOVERY
# ============================================================

def discover_tokens() -> List[str]:

    """
    Discover currently active Solana tokens from DEX Screener.

    This intentionally uses the search endpoint rather than
    claiming that every token on Solana can be discovered.
    """

    urls = [
        f"{DEX_BASE}/token-profiles/latest/v1",
        f"{DEX_BASE}/token-boosts/latest/v1",
    ]

    addresses: List[str] = []

    for url in urls:

        data = http_get_json(url)

        if not data:
            continue

        if isinstance(data, list):

            items = data

        elif isinstance(data, dict):

            items = data.get("tokens", [])

        else:

            items = []

        for item in items:

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

            if not address:
                continue

            if address not in addresses:
                addresses.append(address)

            if len(addresses) >= MAX_DISCOVERY_TOKENS:
                break

        if len(addresses) >= MAX_DISCOVERY_TOKENS:
            break

    print(
        f"[DISCOVERY] Found {len(addresses)} Solana tokens"
    )

    return addresses


# ============================================================
# PAIR FETCHING
# ============================================================

def get_token_pairs_batch(
    addresses: List[str],
) -> List[Dict[str, Any]]:

    all_pairs: List[Dict[str, Any]] = []

    for start in range(
        0,
        len(addresses),
        DEX_BATCH_SIZE,
    ):

        batch = addresses[
            start:start + DEX_BATCH_SIZE
        ]

        joined = ",".join(batch)

        encoded = urllib.parse.quote(
            joined,
            safe=",",
        )

        url = (
            f"{DEX_BASE}/latest/dex/tokens/"
            f"{encoded}"
        )

        data = http_get_json(url)

        if not data:
            continue

        pairs = data.get(
            "pairs",
            [],
        )

        if not isinstance(
            pairs,
            list,
        ):
            continue

        for pair in pairs:

            if not isinstance(pair, dict):
                continue

            if str(
                pair.get("chainId", "")
            ).lower() != "solana":
                continue

            all_pairs.append(pair)

    return all_pairs


# ============================================================
# PAIR SELECTION
# ============================================================

def pair_liquidity(pair: Dict[str, Any]) -> float:

    liquidity = pair.get(
        "liquidity"
    )

    if not isinstance(
        liquidity,
        dict,
    ):
        return 0.0

    return safe_float(
        liquidity.get("usd"),
        0.0,
    )


def pair_volume_5m(pair: Dict[str, Any]) -> float:

    volume = pair.get(
        "volume"
    )

    if not isinstance(
        volume,
        dict,
    ):
        return 0.0

    return safe_float(
        volume.get("m5"),
        0.0,
    )


def pair_liquidity_valid(
    pair: Dict[str, Any],
) -> bool:

    liquidity = pair.get(
        "liquidity"
    )

    if not isinstance(
        liquidity,
        dict,
    ):
        return False

    value = liquidity.get("usd")

    if value is None:
        return False

    return safe_float(
        value,
        0.0,
    ) > 0


def choose_best_pairs(
    pairs: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    grouped: Dict[
        str,
        List[Dict[str, Any]]
    ] = {}

    for pair in pairs:

        base = pair.get("baseToken") or {}

        address = base.get(
            "address"
        )

        if not address:
            continue

        key = token_key(address)

        grouped.setdefault(
            key,
            [],
        ).append(pair)

    selected: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for address, token_pairs in grouped.items():

        valid_liquidity_pairs = [
            pair
            for pair in token_pairs
            if pair_liquidity_valid(pair)
        ]

        if valid_liquidity_pairs:

            selected[address] = max(
                valid_liquidity_pairs,
                key=lambda p: (
                    pair_liquidity(p),
                    pair_volume_5m(p),
                ),
            )

        else:

            # No valid liquidity information exists.
            #
            # Select the highest-volume pair only for diagnostics.
            # The candidate filter will NOT treat this as valid
            # liquidity unless the early-token fallback passes.

            selected[address] = max(
                token_pairs,
                key=lambda p: (
                    pair_volume_5m(p),
                ),
            )

    return selected


# ============================================================
# PAIR -> SNAPSHOT
# ============================================================

def pair_to_snapshot(
    pair: Dict[str, Any],
) -> Dict[str, Any]:

    base = pair.get(
        "baseToken"
    ) or {}

    address = base.get(
        "address",
        "",
    )

    symbol = base.get(
        "symbol",
        "UNKNOWN",
    )

    name = base.get(
        "name",
        symbol,
    )

    txns = pair.get(
        "txns"
    ) or {}

    m5_txns = txns.get(
        "m5"
    ) or {}

    buys = safe_int(
        m5_txns.get("buys"),
        0,
    )

    sells = safe_int(
        m5_txns.get("sells"),
        0,
    )

    total_tx = buys + sells

    volume = pair.get(
        "volume"
    ) or {}

    volume_5m = safe_float(
        volume.get("m5"),
        0.0,
    )

    price_change = pair.get(
        "priceChange"
    ) or {}

    price_change_5m = safe_float(
        price_change.get("m5"),
        0.0,
    )

    liquidity_obj = pair.get(
        "liquidity"
    )

    liquidity_data_valid = (
        isinstance(
            liquidity_obj,
            dict,
        )
        and liquidity_obj.get("usd") is not None
        and safe_float(
            liquidity_obj.get("usd"),
            0.0,
        ) > 0
    )

    liquidity = (
        safe_float(
            liquidity_obj.get("usd"),
            0.0,
        )
        if isinstance(
            liquidity_obj,
            dict,
        )
        else 0.0
    )

    market_cap = safe_float(
        pair.get("marketCap"),
        0.0,
    )

    if market_cap <= 0:
        market_cap = safe_float(
            pair.get("fdv"),
            0.0,
        )

    # --------------------------------------------------------
    # FLOW PROXY
    #
    # IMPORTANT:
    #
    # This is NOT real dollar net flow.
    #
    # Flow Proxy =
    # 5m Volume × ((Buys - Sells) / Total Transactions)
    #
    # Trade-level dollar flow requires another data provider.
    # --------------------------------------------------------

    if total_tx > 0:

        buy_sell_imbalance = (
            buys - sells
        ) / total_tx

        flow_proxy = (
            volume_5m
            * buy_sell_imbalance
        )

    else:

        flow_proxy = 0.0

    flow_pressure_pct = (
        (
            flow_proxy
            / market_cap
        )
        * 100.0
        if market_cap > 0
        else 0.0
    )

    abs_flow = abs(
        flow_proxy
    )

    volume_flow_ratio = (
        volume_5m / abs_flow
        if abs_flow > 0
        else 0.0
    )

    buy_sell_ratio = (
        buys / sells
        if sells > 0
        else (
            float(buys)
            if buys > 0
            else 0.0
        )
    )

    pair_created_at = safe_float(
        pair.get(
            "pairCreatedAt"
        ),
        0.0,
    )

    if pair_created_at > 0:

        # DEX Screener timestamps are milliseconds.
        created_seconds = (
            pair_created_at / 1000.0
            if pair_created_at > 10_000_000_000
            else pair_created_at
        )

        age_hours = max(
            0.0,
            (
                now_ts()
                - created_seconds
            )
            / 3600.0,
        )

    else:

        age_hours = None

    return {
        "address": address,
        "symbol": symbol,
        "name": name,

        "market_cap": market_cap,

        "liquidity": liquidity,
        "liquidity_data_valid": (
            liquidity_data_valid
        ),

        "volume_5m": volume_5m,

        "buys_5m": buys,
        "sells_5m": sells,
        "tx_5m": total_tx,

        "buy_sell_ratio": buy_sell_ratio,

        "flow_proxy_5m": flow_proxy,
        "flow_pressure_pct": flow_pressure_pct,

        "volume_flow_ratio": (
            volume_flow_ratio
        ),

        "price_change_5m": (
            price_change_5m
        ),

        "age_hours": age_hours,

        "pair_address": pair.get(
            "pairAddress",
            "",
        ),

        "dex_id": pair.get(
            "dexId",
            "unknown",
        ),

        "quote_symbol": (
            (
                pair.get("quoteToken")
                or {}
            ).get(
                "symbol",
                "",
            )
        ),

        "url": pair.get(
            "url",
            "",
        ),

        "timestamp": now_ts(),
    }


# ============================================================
# HISTORY
# ============================================================

def record_snapshot(
    snapshot: Dict[str, Any],
) -> None:

    address = snapshot["address"]

    token = get_token_state(
        address
    )

    token["symbol"] = snapshot[
        "symbol"
    ]

    token["name"] = snapshot[
        "name"
    ]

    token["last_seen"] = now_ts()

    history = token.setdefault(
        "history",
        [],
    )

    history.append(
        snapshot
    )

    if len(history) > MAX_HISTORY_PER_TOKEN:

        del history[
            :-MAX_HISTORY_PER_TOKEN
        ]

    # Limit token count
    if len(
        STATE["tokens"]
    ) > MAX_STORED_TOKENS:

        oldest = sorted(
            STATE["tokens"].items(),
            key=lambda item: item[1].get(
                "last_seen",
                0,
            ),
        )

        remove_count = (
            len(
                STATE["tokens"]
            )
            - MAX_STORED_TOKENS
        )

        for key, _ in oldest[
            :remove_count
        ]:

            STATE["tokens"].pop(
                key,
                None,
            )


# ============================================================
# PRE-FILTER
# ============================================================

def passes_pre_filter(
    snapshot: Dict[str, Any],
) -> bool:

    volume = safe_float(
        snapshot.get(
            "volume_5m"
        ),
        0.0,
    )

    flow = abs(
        safe_float(
            snapshot.get(
                "flow_proxy_5m"
            ),
            0.0,
        )
    )

    if (
        volume < PRE_FILTER_MIN_VOLUME_5M
        and flow < PRE_FILTER_MIN_ABS_FLOW_USD
    ):

        return False

    return True


# ============================================================
# STRUCTURE
# ============================================================

def find_previous_snapshot(
    address: str,
    seconds_back: int,
) -> Optional[Dict[str, Any]]:

    token = STATE["tokens"].get(
        token_key(address)
    )

    if not token:
        return None

    history = token.get(
        "history",
        [],
    )

    if not history:
        return None

    target = now_ts() - seconds_back

    best = None
    best_distance = None

    for snap in history:

        ts = safe_int(
            snap.get(
                "timestamp"
            ),
            0,
        )

        distance = abs(
            ts - target
        )

        if (
            best_distance is None
            or distance < best_distance
        ):

            best = snap
            best_distance = distance

    return best


def calculate_structure(
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:

    history = (
        STATE["tokens"]
        .get(
            token_key(
                snapshot["address"]
            ),
            {},
        )
        .get(
            "history",
            [],
        )
    )

    if len(history) < 3:

        return {
            "breakout": False,
            "range_pct": 0.0,
        }

    recent = history[
        -6:
    ]

    mcs = [
        safe_float(
            x.get(
                "market_cap"
            ),
            0.0,
        )
        for x in recent
        if safe_float(
            x.get(
                "market_cap"
            ),
            0.0,
        ) > 0
    ]

    if len(mcs) < 3:

        return {
            "breakout": False,
            "range_pct": 0.0,
        }

    previous = mcs[
        :-1
    ]

    current = mcs[
        -1
    ]

    high = max(
        previous
    )

    low = min(
        previous
    )

    range_pct = (
        (
            (high - low)
            / low
        )
        * 100.0
        if low > 0
        else 0.0
    )

    breakout = (
        current > high
        and range_pct
        <= MAX_CONSOLIDATION_RANGE_PCT
    )

    return {
        "breakout": breakout,
        "range_pct": range_pct,
    }


# ============================================================
# ACTIVITY
# ============================================================

def activity_expanding(
    snapshot: Dict[str, Any],
) -> bool:

    previous = find_previous_snapshot(
        snapshot["address"],
        ACTIVITY_LOOKBACK_SECONDS,
    )

    if not previous:
        return False

    current_volume = safe_float(
        snapshot.get(
            "volume_5m"
        ),
        0.0,
    )

    old_volume = safe_float(
        previous.get(
            "volume_5m"
        ),
        0.0,
    )

    if old_volume <= 0:
        return False

    growth = pct_change(
        old_volume,
        current_volume,
    )

    return (
        growth >= ACTIVITY_EXPANSION_PCT
    )


def flow_accelerating(
    snapshot: Dict[str, Any],
) -> bool:

    previous = find_previous_snapshot(
        snapshot["address"],
        FLOW_LOOKBACK_SECONDS,
    )

    if not previous:
        return False

    current_flow = safe_float(
        snapshot.get(
            "flow_proxy_5m"
        ),
        0.0,
    )

    old_flow = safe_float(
        previous.get(
            "flow_proxy_5m"
        ),
        0.0,
    )

    if old_flow <= 0:
        return current_flow > 0

    growth = pct_change(
        old_flow,
        current_flow,
    )

    return (
        growth >= FLOW_ACCELERATION_PCT
    )


# ============================================================
# SIGNAL STRENGTH
# ============================================================

def get_signal_strength(
    snapshot: Dict[str, Any],
) -> Tuple[str, int]:

    flow_pct = safe_float(
        snapshot.get(
            "flow_pressure_pct"
        ),
        0.0,
    )

    flow = safe_float(
        snapshot.get(
            "flow_proxy_5m"
        ),
        0.0,
    )

    if flow_pct >= ULTRA_FLOW_MC_PCT:

        return (
            "ULTRA",
            ULTRA_OBSERVATIONS,
        )

    if (
        flow_pct >= STRONG_FLOW_MC_PCT
        or flow >= 15_000
    ):

        return (
            "STRONG",
            STRONG_OBSERVATIONS,
        )

    return (
        "NORMAL",
        NORMAL_OBSERVATIONS,
    )


# ============================================================
# SAFETY CHECKS
# ============================================================

def safety_checks(
    snapshot: Dict[str, Any],
) -> Tuple[bool, str]:

    mc = safe_float(
        snapshot.get(
            "market_cap"
        ),
        0.0,
    )

    flow_pct = safe_float(
        snapshot.get(
            "flow_pressure_pct"
        ),
        0.0,
    )

    liquidity = safe_float(
        snapshot.get(
            "liquidity"
        ),
        0.0,
    )

    liquidity_valid = bool(
        snapshot.get(
            "liquidity_data_valid",
            False,
        )
    )

    age_hours = snapshot.get(
        "age_hours"
    )

    # --------------------------------------------------------
    # MC
    # --------------------------------------------------------

    if mc < MIN_MC:

        return (
            False,
            "MC_BELOW_6K",
        )

    if mc > EXTENDED_MAX_MC:

        return (
            False,
            "MC_OVER_250K",
        )

    if (
        mc > NORMAL_MAX_MC
        and flow_pct < EXTENDED_MC_MIN_FLOW_PCT
    ):

        return (
            False,
            "EXTENDED_MC_FLOW_BELOW_11PCT",
        )

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    if age_hours is None:

        return (
            False,
            "AGE_UNAVAILABLE",
        )

    if age_hours > MAX_AGE_HOURS:

        return (
            False,
            "AGE_OVER_48H",
        )

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if liquidity_valid and liquidity >= MIN_LIQUIDITY:

        return (
            True,
            "PASS",
        )

    # --------------------------------------------------------
    # LIQUIDITY FALLBACK
    # --------------------------------------------------------

    volume = safe_float(
        snapshot.get(
            "volume_5m"
        ),
        0.0,
    )

    flow = safe_float(
        snapshot.get(
            "flow_proxy_5m"
        ),
        0.0,
    )

    if (
        age_hours
        <= EARLY_LIQUIDITY_FALLBACK_AGE_HOURS
        and volume
        >= EARLY_LIQUIDITY_FALLBACK_VOLUME
        and flow
        >= EARLY_LIQUIDITY_FALLBACK_FLOW
        and flow_pct
        >= EARLY_LIQUIDITY_FALLBACK_FLOW_MC_PCT
    ):

        return (
            True,
            "LIQUIDITY_UNVERIFIED_EARLY_STAGE",
        )

    return (
        False,
        "LIQUIDITY_DATA_UNAVAILABLE",
    )


# ============================================================
# FILTER
# ============================================================

def evaluate_candidate(
    snapshot: Dict[str, Any],
) -> Tuple[
    bool,
    str,
    Dict[str, Any],
]:

    # --------------------------------------------------------
    # 1. BASIC DATA VALIDITY
    # --------------------------------------------------------

    mc = safe_float(
        snapshot.get(
            "market_cap"
        ),
        0.0,
    )

    volume = safe_float(
        snapshot.get(
            "volume_5m"
        ),
        0.0,
    )

    flow = safe_float(
        snapshot.get(
            "flow_proxy_5m"
        ),
        0.0,
    )

    flow_pct = safe_float(
        snapshot.get(
            "flow_pressure_pct"
        ),
        0.0,
    )

    age_hours = snapshot.get(
        "age_hours"
    )

    if mc <= 0:

        return (
            False,
            "INVALID_MC",
            {},
        )

    if volume <= 0 and flow <= 0:

        return (
            False,
            "NO_VOLUME_OR_FLOW",
            {},
        )

    if age_hours is None:

        return (
            False,
            "AGE_UNAVAILABLE",
            {},
        )

    # --------------------------------------------------------
    # 2. HARD AGE
    # --------------------------------------------------------

    if age_hours > MAX_AGE_HOURS:

        return (
            False,
            "AGE_OVER_48H",
            {},
        )

    # --------------------------------------------------------
    # 3. SAFETY
    # --------------------------------------------------------

    safe, safety_reason = safety_checks(
        snapshot
    )

    if not safe:

        return (
            False,
            safety_reason,
            {},
        )

    liquidity_valid = bool(
        snapshot.get(
            "liquidity_data_valid",
            False,
        )
    )

    liquidity = safe_float(
        snapshot.get(
            "liquidity"
        ),
        0.0,
    )

    liquidity_fallback = (
        not liquidity_valid
        or liquidity < MIN_LIQUIDITY
    )

    # --------------------------------------------------------
    # 4. MARKET CAP MODE
    # --------------------------------------------------------

    extended_mc = (
        mc > NORMAL_MAX_MC
    )

    # --------------------------------------------------------
    # 5. ABSOLUTE FLOW
    # --------------------------------------------------------

    if flow < MIN_FLOW_PROXY_USD:

        return (
            False,
            "FLOW_BELOW_1500",
            {},
        )

    # --------------------------------------------------------
    # 6. FLOW / MC
    # --------------------------------------------------------

    if flow_pct < MIN_FLOW_MC_PCT:

        return (
            False,
            "FLOW_MC_BELOW_8PCT",
            {},
        )

    # --------------------------------------------------------
    # 7. VOLUME
    # --------------------------------------------------------

    if volume < MIN_VOLUME_5M:

        return (
            False,
            "VOLUME_BELOW_1500",
            {},
        )

    # --------------------------------------------------------
    # 8. VOLUME / FLOW
    #
    # Soft preference.
    #
    # Strong signals override it.
    # --------------------------------------------------------

    abs_flow = abs(
        flow
    )

    ratio = (
        volume / abs_flow
        if abs_flow > 0
        else 0.0
    )

    strong_flow = (
        flow_pct >= STRONG_FLOW_MC_PCT
        or flow >= 15_000
    )

    ratio_below_preference = (
        ratio < PREFERRED_VOLUME_FLOW_RATIO
    )

    if (
        ratio_below_preference
        and not strong_flow
    ):

        return (
            False,
            "VOLUME_FLOW_RATIO_BELOW_1_25",
            {},
        )

    # --------------------------------------------------------
    # 9. BUY / SELL
    #
    # Soft only.
    # --------------------------------------------------------

    buy_sell_ratio = safe_float(
        snapshot.get(
            "buy_sell_ratio"
        ),
        0.0,
    )

    buy_sell_weak = (
        buy_sell_ratio
        < MIN_BUY_SELL_RATIO
    )

    # --------------------------------------------------------
    # 10. SIGNAL STRENGTH
    # --------------------------------------------------------

    signal_strength, required_observations = (
        get_signal_strength(
            snapshot
        )
    )

    ultra_strong = (
        signal_strength
        == "ULTRA"
    )

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    return (
        True,
        "PASS",
        {
            "extended_mc": extended_mc,

            "liquidity_fallback": (
                liquidity_fallback
            ),

            "strong_flow": strong_flow,

            "ultra_strong": ultra_strong,

            "signal_strength": (
                signal_strength
            ),

            "required_observations": (
                required_observations
            ),

            "volume_flow_ratio": ratio,

            "buy_sell_weak": buy_sell_weak,

            "safety_reason": safety_reason,
        },
    )


# ============================================================
# NEAR MISS
# ============================================================

def near_miss_rank(
    snapshot: Dict[str, Any],
) -> float:

    flow_pct = max(
        0.0,
        safe_float(
            snapshot.get(
                "flow_pressure_pct"
            ),
            0.0,
        ),
    )

    flow = max(
        0.0,
        safe_float(
            snapshot.get(
                "flow_proxy_5m"
            ),
            0.0,
        ),
    )

    liquidity = max(
        0.0,
        safe_float(
            snapshot.get(
                "liquidity"
            ),
            0.0,
        ),
    )

    volume = max(
        0.0,
        safe_float(
            snapshot.get(
                "volume_5m"
            ),
            0.0,
        ),
    )

    ratio = max(
        0.0,
        safe_float(
            snapshot.get(
                "volume_flow_ratio"
            ),
            0.0,
        ),
    )

    return (
        flow_pct * 3.0
        + min(flow / 1000.0, 50.0)
        + min(liquidity / 1000.0, 20.0)
        + min(volume / 1000.0, 50.0)
        + min(ratio * 2.0, 10.0)
    )


def should_log_near_miss(
    snapshot: Dict[str, Any],
) -> bool:

    flow_pct = safe_float(
        snapshot.get(
            "flow_pressure_pct"
        ),
        0.0,
    )

    flow = abs(
        safe_float(
            snapshot.get(
                "flow_proxy_5m"
            ),
            0.0,
        )
    )

    volume = safe_float(
        snapshot.get(
            "volume_5m"
        ),
        0.0,
    )

    return (
        flow_pct >= 6.0
        or flow >= 1_000
        or volume >= MIN_VOLUME_5M
    )


def print_near_misses(
    near_misses: List[
        Tuple[
            float,
            str,
            Dict[str, Any],
        ]
    ],
) -> None:

    if not DEBUG_MODE:
        return

    if not near_misses:
        return

    near_misses.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    print(
        f"\n[NEAR MISSES] Top "
        f"{min(DEBUG_TOP_CANDIDATES, len(near_misses))}"
    )

    for index, (
        rank,
        reason,
        snapshot,
    ) in enumerate(
        near_misses[
            :DEBUG_TOP_CANDIDATES
        ],
        start=1,
    ):

        symbol = snapshot.get(
            "symbol",
            "UNKNOWN",
        )

        mc = safe_float(
            snapshot.get(
                "market_cap"
            ),
            0.0,
        )

        flow = safe_float(
            snapshot.get(
                "flow_proxy_5m"
            ),
            0.0,
        )

        flow_pct = safe_float(
            snapshot.get(
                "flow_pressure_pct"
            ),
            0.0,
        )

        volume = safe_float(
            snapshot.get(
                "volume_5m"
            ),
            0.0,
        )

        liquidity = safe_float(
            snapshot.get(
                "liquidity"
            ),
            0.0,
        )

        ratio = safe_float(
            snapshot.get(
                "volume_flow_ratio"
            ),
            0.0,
        )

        liq_valid = bool(
            snapshot.get(
                "liquidity_data_valid",
                False,
            )
        )

        liq_text = (
            f"${liquidity:,.0f}"
            if liq_valid
            else "N/A"
        )

        print(
            f"{index}. {symbol} | "
            f"Reason={reason} | "
            f"MC=${mc:,.0f} | "
            f"Flow/MC={flow_pct:.1f}% | "
            f"Flow=${flow:,.0f} | "
            f"Vol=${volume:,.0f} | "
            f"Liq={liq_text} | "
            f"V/F={ratio:.2f}x"
        )


# ============================================================
# SCORING
# ============================================================

def score_token(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> int:

    score = 0

    flow_pct = safe_float(
        snapshot.get(
            "flow_pressure_pct"
        ),
        0.0,
    )

    flow = safe_float(
        snapshot.get(
            "flow_proxy_5m"
        ),
        0.0,
    )

    mc = safe_float(
        snapshot.get(
            "market_cap"
        ),
        0.0,
    )

    liquidity = safe_float(
        snapshot.get(
            "liquidity"
        ),
        0.0,
    )

    ratio = safe_float(
        snapshot.get(
            "volume_flow_ratio"
        ),
        0.0,
    )

    buy_sell = safe_float(
        snapshot.get(
            "buy_sell_ratio"
        ),
        0.0,
    )

    tx = safe_int(
        snapshot.get(
            "tx_5m"
        ),
        0,
    )

    age = snapshot.get(
        "age_hours"
    )

    # --------------------------------------------------------
    # FLOW / MC
    # --------------------------------------------------------

    if flow_pct >= 25:
        score += 30

    elif flow_pct >= 15:
        score += 24

    elif flow_pct >= 11:
        score += 20

    elif flow_pct >= 8:
        score += 16

    elif flow_pct >= 5:
        score += 7

    # --------------------------------------------------------
    # ABSOLUTE FLOW
    # --------------------------------------------------------

    if flow >= 25_000:
        score += 15

    elif flow >= 15_000:
        score += 13

    elif flow >= 10_000:
        score += 11

    elif flow >= 5_000:
        score += 9

    elif flow >= 3_000:
        score += 7

    elif flow >= 1_500:
        score += 5

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    if (
        MIN_MC
        <= mc
        <= NORMAL_MAX_MC
    ):

        score += 8

    elif mc <= EXTENDED_MAX_MC:

        score += 3

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if liquidity >= 15_000:
        score += 10

    elif liquidity >= 10_000:
        score += 8

    elif liquidity >= 6_000:
        score += 6

    elif not snapshot.get(
        "liquidity_data_valid",
        False,
    ):

        # Early-stage fallback gets a small
        # penalty rather than being treated
        # as normal liquidity.
        score += 2

    # --------------------------------------------------------
    # VOLUME / FLOW
    # --------------------------------------------------------

    if ratio >= 3.0:
        score += 8

    elif ratio >= 2.0:
        score += 7

    elif ratio >= 1.25:
        score += 6

    elif ratio >= 1.0:
        score += 4

    else:
        score += 2

    # --------------------------------------------------------
    # BUY / SELL
    # --------------------------------------------------------

    if buy_sell >= 5:
        score += 8

    elif buy_sell >= 3:
        score += 6

    elif buy_sell >= 1.5:
        score += 4

    else:
        score += 1

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    if tx >= 30:
        score += 5

    elif tx >= 15:
        score += 3

    elif tx >= MIN_TX_5M:
        score += 1

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    if age is not None:

        if age <= 12:
            score += 5

        elif age <= 24:
            score += 4

        elif age <= 36:
            score += 3

        elif age <= 48:
            score += 2

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    if analysis.get(
        "breakout",
        False,
    ):

        score += 10

    # --------------------------------------------------------
    # ACTIVITY
    # --------------------------------------------------------

    if analysis.get(
        "activity_expanding",
        False,
    ):

        score += 8

    if analysis.get(
        "flow_accelerating",
        False,
    ):

        score += 8

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    price_change = safe_float(
        snapshot.get(
            "price_change_5m"
        ),
        0.0,
    )

    if price_change > 10:
        score += 5

    elif price_change > 0:
        score += 3

    return min(
        score,
        100,
    )


# ============================================================
# ANALYSIS
# ============================================================

def analyze(
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:

    structure = calculate_structure(
        snapshot
    )

    activity = activity_expanding(
        snapshot
    )

    flow_acceleration = flow_accelerating(
        snapshot
    )

    passed, reason, metadata = (
        evaluate_candidate(
            snapshot
        )
    )

    analysis = {
        "passed": passed,
        "reason": reason,

        "breakout": structure.get(
            "breakout",
            False,
        ),

        "range_pct": structure.get(
            "range_pct",
            0.0,
        ),

        "activity_expanding": activity,

        "flow_accelerating": (
            flow_acceleration
        ),
    }

    if metadata:
        analysis.update(
            metadata
        )

    analysis["score"] = score_token(
        snapshot,
        analysis,
    )

    return analysis


# ============================================================
# IGNITION VALIDATION
# ============================================================

def valid_current_ignition(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> bool:

    if not analysis.get(
        "passed",
        False,
    ):
        return False

    price_change = safe_float(
        snapshot.get(
            "price_change_5m"
        ),
        0.0,
    )

    if (
        price_change
        < MAX_IGNITION_DRAWDOWN_PCT
    ):

        return False

    # Strong candidates can qualify from
    # flow pressure alone, while normal
    # candidates benefit from structure/activity.
    signal_strength = analysis.get(
        "signal_strength",
        "NORMAL",
    )

    breakout = analysis.get(
        "breakout",
        False,
    )

    activity = analysis.get(
        "activity_expanding",
        False,
    )

    flow_accel = analysis.get(
        "flow_accelerating",
        False,
    )

    if signal_strength == "ULTRA":

        return True

    if signal_strength == "STRONG":

        return (
            breakout
            or activity
            or flow_accel
            or safe_float(
                snapshot.get(
                    "flow_pressure_pct"
                ),
                0.0,
            ) >= 20.0
        )

    return (
        breakout
        or activity
        or flow_accel
    )


# ============================================================
# PENDING CONFIRMATION
# ============================================================

def create_pending_ignition(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> None:

    address = token_key(
        snapshot["address"]
    )

    STATE["pending"][
        address
    ] = {
        "address": snapshot[
            "address"
        ],

        "symbol": snapshot[
            "symbol"
        ],

        "first_seen": now_ts(),

        "last_seen": now_ts(),

        "confirmations": 1,

        "required": analysis.get(
            "required_observations",
            NORMAL_OBSERVATIONS,
        ),

        "signal_strength": analysis.get(
            "signal_strength",
            "NORMAL",
        ),

        "first_mc": safe_float(
            snapshot.get(
                "market_cap"
            ),
            0.0,
        ),

        "first_flow": safe_float(
            snapshot.get(
                "flow_proxy_5m"
            ),
            0.0,
        ),

        "first_flow_pct": safe_float(
            snapshot.get(
                "flow_pressure_pct"
            ),
            0.0,
        ),

        "first_price_change": safe_float(
            snapshot.get(
                "price_change_5m"
            ),
            0.0,
        ),

        "snapshot": snapshot,
    }


def token_on_cooldown(
    address: str,
) -> bool:

    item = STATE["alerts"].get(
        token_key(address)
    )

    if not item:
        return False

    last_alert = safe_int(
        item.get(
            "timestamp"
        ),
        0,
    )

    return (
        now_ts()
        - last_alert
        < ALERT_COOLDOWN_SECONDS
    )


def global_on_cooldown() -> bool:

    last = safe_int(
        STATE.get(
            "last_global_alert",
            0,
        ),
        0,
    )

    return (
        now_ts()
        - last
        < GLOBAL_ALERT_COOLDOWN_SECONDS
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(
    method: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

    try:

        data = None

        if payload is not None:

            data = urllib.parse.urlencode(
                payload
            ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "RunnerBot/4.5",
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=20,
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

            return json.loads(raw)

    except Exception as exc:

        print(
            f"[TELEGRAM ERROR] {exc}"
        )

        return None


def telegram_send(
    chat_id: int,
    text: str,
) -> None:

    telegram_api(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
            "disable_web_page_preview": "true",
        },
    )


def broadcast(
    text: str,
) -> None:

    if not ALERTS_ENABLED:
        return

    for chat_id in list(
        STATE.get(
            "subscribers",
            [],
        )
    ):

        try:

            telegram_send(
                int(chat_id),
                text,
            )

        except Exception as exc:

            print(
                f"[TELEGRAM] Broadcast error: {exc}"
            )


# ============================================================
# ALERT FORMAT
# ============================================================

def format_alert(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> str:

    symbol = snapshot.get(
        "symbol",
        "UNKNOWN",
    )

    address = snapshot.get(
        "address",
        "",
    )

    mc = safe_float(
        snapshot.get(
            "market_cap"
        ),
        0.0,
    )

    liquidity = safe_float(
        snapshot.get(
            "liquidity"
        ),
        0.0,
    )

    volume = safe_float(
        snapshot.get(
            "volume_5m"
        ),
        0.0,
    )

    flow = safe_float(
        snapshot.get(
            "flow_proxy_5m"
        ),
        0.0,
    )

    flow_pct = safe_float(
        snapshot.get(
            "flow_pressure_pct"
        ),
        0.0,
    )

    ratio = safe_float(
        snapshot.get(
            "volume_flow_ratio"
        ),
        0.0,
    )

    buy_sell = safe_float(
        snapshot.get(
            "buy_sell_ratio"
        ),
        0.0,
    )

    age = snapshot.get(
        "age_hours"
    )

    tx = safe_int(
        snapshot.get(
            "tx_5m"
        ),
        0,
    )

    dex = snapshot.get(
        "dex_id",
        "unknown",
    )

    liquidity_valid = bool(
        snapshot.get(
            "liquidity_data_valid",
            False,
        )
    )

    if liquidity_valid:

        liq_text = (
            f"${liquidity:,.0f}"
        )

    else:

        liq_text = (
            "N/A — unverified"
        )

    signal_strength = analysis.get(
        "signal_strength",
        "NORMAL",
    )

    required = analysis.get(
        "required_observations",
        NORMAL_OBSERVATIONS,
    )

    extended = analysis.get(
        "extended_mc",
        False,
    )

    fallback = analysis.get(
        "liquidity_fallback",
        False,
    )

    mc_mode = (
        "EXTENDED"
        if extended
        else "NORMAL"
    )

    liquidity_note = (
        "\n⚠️ Liquidity unverified — early-stage fallback"
        if fallback
        else ""
    )

    ratio_note = ""

    if ratio < PREFERRED_VOLUME_FLOW_RATIO:

        ratio_note = (
            "\n⚡ Strong-flow ratio override"
        )

    url = snapshot.get(
        "url",
        "",
    )

    lines = [
        f"🔥 RUNNER SIGNAL {BOT_VERSION}",
        "",
        f"🪙 {symbol}",
        f"MC: ${mc:,.0f} ({mc_mode})",
        f"Liq: {liq_text}",
        f"5m Vol: ${volume:,.0f}",
        f"5m Flow Proxy: +${flow:,.0f}",
        f"Flow/MC: {flow_pct:.1f}%",
        f"Vol/Flow: {ratio:.2f}x",
        f"Buy/Sell: {buy_sell:.2f}x",
        f"5m TX: {tx}",
        (
            f"Age: {age:.1f}h"
            if age is not None
            else "Age: N/A"
        ),
        f"DEX: {dex}",
        "",
        f"Signal: {signal_strength}",
        f"Confirmation: {required} observation(s)",
        f"Score: {analysis.get('score', 0)}/100",
        liquidity_note,
        ratio_note,
        "",
        "Flow Proxy:",
        "5m Volume × ((Buys - Sells) / Total TX)",
        "Not real dollar net flow.",
    ]

    if url:

        lines.extend(
            [
                "",
                url,
            ]
        )

    lines.extend(
        [
            "",
            f"CA: {address}",
        ]
    )

    return "\n".join(
        x
        for x in lines
        if x is not None
    )


# ============================================================
# ALERT REGISTRATION
# ============================================================

def register_alert(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> None:

    address = token_key(
        snapshot["address"]
    )

    STATE["alerts"][
        address
    ] = {
        "timestamp": now_ts(),
        "symbol": snapshot[
            "symbol"
        ],
        "address": snapshot[
            "address"
        ],
        "entry_mc": safe_float(
            snapshot.get(
                "market_cap"
            ),
            0.0,
        ),
        "entry_price_change_5m": safe_float(
            snapshot.get(
                "price_change_5m"
            ),
            0.0,
        ),
        "score": analysis.get(
            "score",
            0,
        ),
        "signal_strength": analysis.get(
            "signal_strength",
            "NORMAL",
        ),
    }

    STATE["last_global_alert"] = now_ts()


# ============================================================
# OUTCOME TRACKING
# ============================================================

def update_outcomes() -> None:

    alerts = STATE.get(
        "alerts",
        {},
    )

    if not alerts:
        return

    current_time = now_ts()

    for address, alert in alerts.items():

        entry_mc = safe_float(
            alert.get(
                "entry_mc"
            ),
            0.0,
        )

        if entry_mc <= 0:
            continue

        token = STATE["tokens"].get(
            address
        )

        if not token:
            continue

        history = token.get(
            "history",
            [],
        )

        alert_time = safe_int(
            alert.get(
                "timestamp"
            ),
            0,
        )

        for window in OUTCOME_WINDOWS:

            key = f"outcome_{window}"

            if key in alert:
                continue

            if (
                current_time
                - alert_time
                < window
            ):
                continue

            closest = None

            for snapshot in history:

                ts = safe_int(
                    snapshot.get(
                        "timestamp"
                    ),
                    0,
                )

                if ts < alert_time:
                    continue

                if (
                    ts
                    <= alert_time + window
                ):

                    closest = snapshot

            if closest is None:
                continue

            mc = safe_float(
                closest.get(
                    "market_cap"
                ),
                0.0,
            )

            change = pct_change(
                entry_mc,
                mc,
            )

            alert[key] = {
                "timestamp": (
                    closest.get(
                        "timestamp"
                    )
                ),
                "mc": mc,
                "change_pct": change,
            }


# ============================================================
# CONFIRMATION ENGINE
# ============================================================

def process_confirmation(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> bool:

    address = token_key(
        snapshot["address"]
    )

    if token_on_cooldown(
        address
    ):

        return False

    current_valid = valid_current_ignition(
        snapshot,
        analysis,
    )

    pending = STATE[
        "pending"
    ].get(address)

    # --------------------------------------------------------
    # ULTRA
    # --------------------------------------------------------

    if (
        current_valid
        and analysis.get(
            "signal_strength"
        ) == "ULTRA"
    ):

        if global_on_cooldown():

            return False

        message = format_alert(
            snapshot,
            analysis,
        )

        broadcast(
            message
        )

        register_alert(
            snapshot,
            analysis,
        )

        STATE["pending"].pop(
            address,
            None,
        )

        print(
            f"[ALERT] ULTRA {snapshot['symbol']}"
        )

        return True

    # --------------------------------------------------------
    # NO PENDING
    # --------------------------------------------------------

    if pending is None:

        if current_valid:

            create_pending_ignition(
                snapshot,
                analysis,
            )

            print(
                f"[PENDING] "
                f"{snapshot['symbol']} "
                f"1/"
                f"{analysis.get('required_observations', 3)} "
                f"{analysis.get('signal_strength', 'NORMAL')}"
            )

        return False

    # --------------------------------------------------------
    # EXPIRY
    # --------------------------------------------------------

    if (
        now_ts()
        - safe_int(
            pending.get(
                "first_seen"
            ),
            now_ts(),
        )
        > PENDING_EXPIRY_SECONDS
    ):

        STATE["pending"].pop(
            address,
            None,
        )

        if current_valid:

            create_pending_ignition(
                snapshot,
                analysis,
            )

        return False

    # --------------------------------------------------------
    # CURRENT VALID
    # --------------------------------------------------------

    if current_valid:

        pending["confirmations"] = (
            safe_int(
                pending.get(
                    "confirmations"
                ),
                1,
            )
            + 1
        )

        pending["last_seen"] = now_ts()

        # Use the strongest required observation count
        # associated with the current signal.
        required = min(
            safe_int(
                pending.get(
                    "required"
                ),
                NORMAL_OBSERVATIONS,
            ),
            safe_int(
                analysis.get(
                    "required_observations"
                ),
                NORMAL_OBSERVATIONS,
            ),
        )

        pending["required"] = required

        confirmations = safe_int(
            pending.get(
                "confirmations"
            ),
            1,
        )

        print(
            f"[CONFIRM] "
            f"{snapshot['symbol']} "
            f"{confirmations}/{required} "
            f"{analysis.get('signal_strength', 'NORMAL')}"
        )

        if (
            confirmations
            >= required
        ):

            if global_on_cooldown():

                return False

            message = format_alert(
                snapshot,
                analysis,
            )

            broadcast(
                message
            )

            register_alert(
                snapshot,
                analysis,
            )

            STATE["pending"].pop(
                address,
                None,
            )

            print(
                f"[ALERT] "
                f"{snapshot['symbol']} "
                f"{confirmations}/{required}"
            )

            return True

        return False

    # --------------------------------------------------------
    # INVALID CURRENT OBSERVATION
    # --------------------------------------------------------

    print(
        f"[PENDING INVALID] "
        f"{snapshot['symbol']} "
        f"Reason={analysis.get('reason', 'UNKNOWN')}"
    )

    return False


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def status_text() -> str:

    subscriber_count = len(
        STATE.get(
            "subscribers",
            [],
        )
    )

    token_count = len(
        STATE.get(
            "tokens",
            {},
        )
    )

    pending_count = len(
        STATE.get(
            "pending",
            {},
        )
    )

    return (
        f"🤖 Runner Bot {BOT_VERSION}\n\n"
        f"Scan interval: {SCAN_INTERVAL_SECONDS}s\n\n"

        f"MC:\n"
        f"• Minimum: $6K\n"
        f"• Normal max: $150K\n"
        f"• Extended max: $250K\n"
        f"• Extended requires Flow/MC ≥11%\n\n"

        f"Liquidity:\n"
        f"• Normal minimum: $6K\n"
        f"• Early fallback: ≤1.5h + "
        f"$8K volume + $3K flow + 12% Flow/MC\n\n"

        f"Flow:\n"
        f"• Minimum: $1.5K\n"
        f"• Flow/MC minimum: 8%\n"
        f"• Strong: 15% or $15K flow\n"
        f"• Ultra: 25% Flow/MC\n\n"

        f"Volume/Flow:\n"
        f"• Preferred: ≥1.25x\n"
        f"• Soft preference only\n\n"

        f"Confirmation:\n"
        f"• Normal: 3 scans\n"
        f"• Strong: 2 scans\n"
        f"• Ultra: 1 scan\n\n"

        f"Subscribers: {subscriber_count}\n"
        f"Tracked tokens: {token_count}\n"
        f"Pending: {pending_count}\n"
        f"Alerts enabled: {ALERTS_ENABLED}"
    )


def handle_command(
    chat_id: int,
    text: str,
) -> None:

    command = text.strip().split()[0].lower()

    if command == "/start":

        subscribers = STATE.setdefault(
            "subscribers",
            [],
        )

        if chat_id not in subscribers:

            subscribers.append(
                chat_id
            )

        telegram_send(
            chat_id,
            (
                f"🤖 Runner Bot {BOT_VERSION} "
                f"is online.\n\n"
                f"Alerts are enabled for this chat."
            ),
        )

        save_state()

        return

    if command == "/stop":

        subscribers = STATE.setdefault(
            "subscribers",
            [],
        )

        if chat_id in subscribers:

            subscribers.remove(
                chat_id
            )

        telegram_send(
            chat_id,
            "🛑 Runner alerts stopped for this chat.",
        )

        save_state()

        return

    if command == "/alerts":

        telegram_send(
            chat_id,
            (
                "Alerts are "
                f"{'ON' if ALERTS_ENABLED else 'OFF'}."
            ),
        )

        return

    if command == "/status":

        telegram_send(
            chat_id,
            status_text(),
        )

        return

    if command == "/scan":

        telegram_send(
            chat_id,
            "🔎 Manual scan requested.",
        )

        scan_once()

        return


def telegram_poll() -> None:

    if not TELEGRAM_BOT_TOKEN:
        return

    result = telegram_api(
        "getUpdates",
        {
            "timeout": "1",
            "offset": str(
                STATE.get(
                    "offset",
                    0,
                )
            ),
        },
    )

    if not result:
        return

    if not result.get(
        "ok",
        False,
    ):
        return

    updates = result.get(
        "result",
        [],
    )

    for update in updates:

        update_id = safe_int(
            update.get(
                "update_id"
            ),
            0,
        )

        STATE["offset"] = (
            update_id + 1
        )

        message = update.get(
            "message"
        ) or {}

        chat = message.get(
            "chat"
        ) or {}

        chat_id = chat.get(
            "id"
        )

        text = message.get(
            "text"
        )

        if (
            chat_id is None
            or not text
        ):
            continue

        if text.startswith("/"):

            handle_command(
                int(chat_id),
                text,
            )

    save_state()


# ============================================================
# SCAN
# ============================================================

def scan_once() -> None:

    print(
        f"\n{'=' * 70}"
    )

    print(
        f"[SCAN] {iso_now()} | "
        f"{BOT_VERSION}"
    )

    addresses = discover_tokens()

    if not addresses:

        print(
            "[SCAN] No discovery results."
        )

        return

    pairs = get_token_pairs_batch(
        addresses
    )

    if not pairs:

        print(
            "[SCAN] No pair data."
        )

        return

    selected_pairs = choose_best_pairs(
        pairs
    )

    print(
        f"[PAIRS] "
        f"{len(pairs)} pairs | "
        f"{len(selected_pairs)} tokens selected"
    )

    near_misses: List[
        Tuple[
            float,
            str,
            Dict[str, Any],
        ]
    ] = []

    passed_count = 0
    processed_count = 0

    for address, pair in selected_pairs.items():

        snapshot = pair_to_snapshot(
            pair
        )

        # ----------------------------------------------------
        # PRE-FILTER
        # ----------------------------------------------------

        if not passes_pre_filter(
            snapshot
        ):

            continue

        processed_count += 1

        # ----------------------------------------------------
        # RECORD HISTORY
        # ----------------------------------------------------

        record_snapshot(
            snapshot
        )

        # ----------------------------------------------------
        # LIQUIDITY DEBUG
        # ----------------------------------------------------

        if not snapshot.get(
            "liquidity_data_valid",
            False,
        ):

            if DEBUG_MODE:

                print(
                    f"[LIQ N/A] "
                    f"{snapshot.get('symbol', 'UNKNOWN')} | "
                    f"Pair={snapshot.get('pair_address', '')} | "
                    f"DEX={snapshot.get('dex_id', 'unknown')} | "
                    f"Vol=${snapshot.get('volume_5m', 0):,.0f}"
                )

        # ----------------------------------------------------
        # ANALYSIS
        # ----------------------------------------------------

        analysis = analyze(
            snapshot
        )

        if not analysis.get(
            "passed",
            False,
        ):

            reason = analysis.get(
                "reason",
                "UNKNOWN",
            )

            if should_log_near_miss(
                snapshot
            ):

                near_misses.append(
                    (
                        near_miss_rank(
                            snapshot
                        ),
                        reason,
                        snapshot,
                    )
                )

            continue

        passed_count += 1

        print(
            f"[QUALIFIED] "
            f"{snapshot['symbol']} | "
            f"MC=${snapshot['market_cap']:,.0f} | "
            f"Flow/MC={snapshot['flow_pressure_pct']:.1f}% | "
            f"Flow=${snapshot['flow_proxy_5m']:,.0f} | "
            f"Vol=${snapshot['volume_5m']:,.0f} | "
            f"V/F={snapshot['volume_flow_ratio']:.2f}x | "
            f"Strength={analysis.get('signal_strength')}"
        )

        if (
            analysis.get(
                "liquidity_fallback",
                False,
            )
        ):

            print(
                f"[LIQ FALLBACK PASS] "
                f"{snapshot['symbol']} | "
                f"Age={snapshot['age_hours']:.2f}h | "
                f"Vol=${snapshot['volume_5m']:,.0f} | "
                f"Flow=${snapshot['flow_proxy_5m']:,.0f} | "
                f"Flow/MC={snapshot['flow_pressure_pct']:.1f}%"
            )

        # ----------------------------------------------------
        # CONFIRMATION
        # ----------------------------------------------------

        process_confirmation(
            snapshot,
            analysis,
        )

    # --------------------------------------------------------
    # NEAR MISSES
    # --------------------------------------------------------

    print_near_misses(
        near_misses
    )

    # --------------------------------------------------------
    # OUTCOMES
    # --------------------------------------------------------

    update_outcomes()

    save_state()

    print(
        f"[SCAN SUMMARY] "
        f"Processed={processed_count} | "
        f"Qualified={passed_count} | "
        f"NearMisses={len(near_misses)}"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    print(
        "=" * 70
    )

    print(
        f"RUNNER BOT {BOT_VERSION}"
    )

    print(
        "=" * 70
    )

    print(
        f"Scan interval: "
        f"{SCAN_INTERVAL_SECONDS}s"
    )

    print(
        f"MC: "
        f"${MIN_MC:,} - "
        f"${NORMAL_MAX_MC:,} normal / "
        f"${EXTENDED_MAX_MC:,} extended"
    )

    print(
        f"Liquidity minimum: "
        f"${MIN_LIQUIDITY:,}"
    )

    print(
        f"Flow minimum: "
        f"${MIN_FLOW_PROXY_USD:,}"
    )

    print(
        f"Flow/MC minimum: "
        f"{MIN_FLOW_MC_PCT}%"
    )

    print(
        f"Volume/Flow preference: "
        f"{PREFERRED_VOLUME_FLOW_RATIO}x"
    )

    print(
        f"Confirmation: "
        f"{NORMAL_OBSERVATIONS}/"
        f"{STRONG_OBSERVATIONS}/"
        f"{ULTRA_OBSERVATIONS}"
    )

    print(
        "=" * 70
    )

    last_scan = 0

    while True:

        try:

            telegram_poll()

            current = now_ts()

            if (
                current - last_scan
                >= SCAN_INTERVAL_SECONDS
            ):

                scan_once()

                last_scan = current

        except KeyboardInterrupt:

            print(
                "\n[STOP] Bot stopped."
            )

            save_state()

            break

        except Exception as exc:

            print(
                f"[MAIN ERROR] {exc}"
            )

            save_state()

            time.sleep(5)

        time.sleep(1)


if __name__ == "__main__":
    main()
