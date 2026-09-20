import json
import os
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V4
# DEX SCREENER FIRST
#
# Architecture:
#
# DEX Screener discovery
#        ↓
# token addresses
#        ↓
# DEX Screener token-pairs
#        ↓
# strongest Solana pool
#        ↓
# $20K-$300K / liquidity / age
#        ↓
# structure + activity + buy pressure
#        ↓
# runner score
#        ↓
# IGNITION
#        ↓
# Telegram + outcome tracking
# ============================================================

BOT_VERSION = "V4.0"


# ============================================================
# API
# ============================================================

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

SOLANA = "solana"


# ============================================================
# FILES
# ============================================================

STATE_FILE = "runner_state_v40.json"


# ============================================================
# RUNNER RANGE
# ============================================================

MIN_MC = 20_000
MAX_MC = 300_000

MIN_LIQUIDITY = 10_000


# ============================================================
# OBSERVATION RANGE
# ============================================================

OBSERVE_MIN_MC = 10_000
OBSERVE_MAX_MC = 350_000


# ============================================================
# AGE
# ============================================================

# We allow up to 24 hours into the observation universe.
#
# Age is NOT the only signal.
# It contributes to the score.
#
# 0-3h  = strongest early window
# 3-6h  = still very early
# 6-24h = older but still trackable

MAX_PAIR_AGE_HOURS = 24


# ============================================================
# VOLUME
# ============================================================

# IMPORTANT:
#
# 5m volume is NOT a hard gate.
#
# It is a scoring feature.
#
REFERENCE_VOLUME_5M = 5_000


# ============================================================
# CONSOLIDATION
# ============================================================

MIN_CONSOLIDATION_VOLUME_5M = 500
MIN_CONSOLIDATION_TX_5M = 5

MIN_CONSOLIDATION_ACTIVE_OBS = 3

MAX_CONSOLIDATION_RANGE_PCT = 18.0


# ============================================================
# ACTIVITY
# ============================================================

ACTIVITY_LOOKBACK_SECONDS = 60

ACTIVITY_EXPANSION_PCT = 20.0


# ============================================================
# HISTORY
# ============================================================

MAX_STORED_TOKENS = 1_500
MAX_HISTORY_PER_TOKEN = 300

MIN_OBSERVATIONS = 6


# ============================================================
# DISCOVERY
# ============================================================

# DEX Screener's documented latest profile/boost endpoints
# are rate-limited to 60 requests/minute.
#
# We therefore refresh discovery once per minute.

DISCOVERY_INTERVAL_SECONDS = 60

# Number of tokens taken from each discovery source.
MAX_DISCOVERY_TOKENS = 500


# ============================================================
# TOKEN POOL LOOKUPS
# ============================================================

# token-pairs is 300 rpm according to DEX Screener docs.
#
# We deliberately stay far below the limit.

MAX_TOKEN_LOOKUPS_PER_CYCLE = 120

TOKEN_LOOKUP_SLEEP = 0.10


# ============================================================
# SCANNING
# ============================================================

SCAN_INTERVAL_SECONDS = float(
    os.getenv(
        "SCAN_INTERVAL_SECONDS",
        "15",
    )
)


# ============================================================
# HTTP
# ============================================================

HTTP_TIMEOUT = 12
HTTP_RETRIES = 3
HTTP_BACKOFF_SECONDS = 1.5


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
)

HEATING_ALERTS_ENABLED = (
    os.getenv(
        "HEATING_ALERTS_ENABLED",
        "true",
    ).lower()
    == "true"
)


# ============================================================
# GLOBAL DISCOVERY CACHE
# ============================================================

discovery_cache: List[str] = []
discovery_cache_ts = 0.0


# ============================================================
# STATE
# ============================================================

state: Dict[str, Any] = {
    "histories": {},
    "ignitions": {},
    "subscribers": [],
}


telegram_offset = 0


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_ts() -> float:
    return time.time()


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:

    try:

        if value is None:
            return default

        return float(value)

    except (
        TypeError,
        ValueError,
    ):

        return default


def safe_int(
    value: Any,
    default: int = 0,
) -> int:

    try:

        if value is None:
            return default

        return int(value)

    except (
        TypeError,
        ValueError,
    ):

        return default


def pct_change(
    old: float,
    new: float,
) -> float:

    if old <= 0:
        return 0.0

    return (
        (new - old)
        / old
        * 100.0
    )


def format_money(
    value: float,
) -> str:

    if value >= 1_000_000:

        return (
            f"${value / 1_000_000:.2f}M"
        )

    if value >= 1_000:

        return (
            f"${value / 1_000:.2f}K"
        )

    return f"${value:.0f}"


def format_pct(
    value: float,
) -> str:

    if value >= 0:

        return f"+{value:.1f}%"

    return f"{value:.1f}%"


def pair_age_hours(
    pair_created_at: int,
) -> float:

    if pair_created_at <= 0:
        return 9999.0

    # DexScreener pairCreatedAt is milliseconds.
    created_seconds = (
        pair_created_at / 1000.0
    )

    age_seconds = (
        now_ts()
        - created_seconds
    )

    if age_seconds < 0:
        return 0.0

    return age_seconds / 3600.0


# ============================================================
# HTTP
# ============================================================

def http_json(
    url: str,
    headers: Optional[
        Dict[str, str]
    ] = None,
) -> Any:

    headers = headers or {}

    for attempt in range(
        HTTP_RETRIES
    ):

        try:

            request = (
                urllib.request.Request(
                    url,
                    headers=headers,
                    method="GET",
                )
            )

            with urllib.request.urlopen(
                request,
                timeout=HTTP_TIMEOUT,
            ) as response:

                raw = (
                    response
                    .read()
                    .decode("utf-8")
                )

            return json.loads(raw)

        except Exception as exc:

            if (
                attempt
                == HTTP_RETRIES - 1
            ):

                print(
                    "[HTTP ERROR]",
                    url,
                    "->",
                    exc,
                )

                return None

            time.sleep(
                HTTP_BACKOFF_SECONDS
                * (attempt + 1)
            )

    return None


# ============================================================
# STATE
# ============================================================

def load_state():

    global state

    if not os.path.exists(
        STATE_FILE
    ):

        print(
            "No previous V4 state found."
        )

        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            loaded = json.load(
                file
            )

        if isinstance(
            loaded,
            dict,
        ):

            state.update(
                loaded
            )

        if not isinstance(
            state.get("histories"),
            dict,
        ):

            state["histories"] = {}

        if not isinstance(
            state.get("ignitions"),
            dict,
        ):

            state["ignitions"] = {}

        if not isinstance(
            state.get("subscribers"),
            list,
        ):

            state["subscribers"] = []

        print(
            "Persistent state loaded:",
            len(
                state["histories"]
            ),
            "tokens |",
            len(
                state["ignitions"]
            ),
            "ignitions",
        )

    except Exception as exc:

        print(
            "State load failed:",
            exc,
        )


def save_state():

    try:

        histories = state.get(
            "histories",
            {},
        )

        if len(histories) > (
            MAX_STORED_TOKENS
        ):

            ranked = sorted(
                histories.items(),
                key=lambda item: (
                    item[1][-1]["ts"]
                    if item[1]
                    else 0
                ),
                reverse=True,
            )

            state[
                "histories"
            ] = dict(
                ranked[
                    :MAX_STORED_TOKENS
                ]
            )

        for (
            address,
            history,
        ) in state[
            "histories"
        ].items():

            if len(history) > (
                MAX_HISTORY_PER_TOKEN
            ):

                state[
                    "histories"
                ][address] = history[
                    -MAX_HISTORY_PER_TOKEN:
                ]

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                state,
                file,
                indent=2,
            )

    except Exception as exc:

        print(
            "State save failed:",
            exc,
        )


# ============================================================
# DEX SCREENER DISCOVERY
# ============================================================

def extract_token_addresses(
    data: Any,
) -> List[str]:

    addresses = []

    if not isinstance(
        data,
        list,
    ):

        return addresses

    for item in data:

        if not isinstance(
            item,
            dict,
        ):

            continue

        chain = item.get(
            "chainId"
        )

        address = item.get(
            "tokenAddress"
        )

        if (
            chain == SOLANA
            and address
        ):

            addresses.append(
                address
            )

    return addresses


def dex_discovery_request(
    path: str,
) -> Any:

    url = (
        f"{DEX_BASE}"
        f"{path}"
    )

    return http_json(
        url,
        {
            "Accept":
                "application/json",
            "User-Agent":
                "RunnerBot/4.0",
        },
    )


def discover_token_addresses(
    force: bool = False,
) -> List[str]:

    global discovery_cache
    global discovery_cache_ts

    current = now_ts()

    if (
        not force
        and discovery_cache
        and (
            current
            - discovery_cache_ts
            < DISCOVERY_INTERVAL_SECONDS
        )
    ):

        return discovery_cache

    found = set()

    print(
        "\n"
        "=========================================="
    )

    print(
        "DEX SCREENER DISCOVERY"
    )

    print(
        "=========================================="
    )

    # --------------------------------------------------------
    # 1. Latest token profiles
    #
    # These are DEX Screener's latest token-profile
    # records. They give us token addresses without
    # hardcoding names such as PEPE/CAT/DOG.
    # --------------------------------------------------------

    profiles = dex_discovery_request(
        "/token-profiles/latest/v1"
    )

    profile_addresses = (
        extract_token_addresses(
            profiles
        )
    )

    before = len(found)

    found.update(
        profile_addresses
    )

    print(
        "Latest profiles:",
        len(profile_addresses),
        "| new:",
        len(found) - before,
    )

    # --------------------------------------------------------
    # 2. Latest boosts
    #
    # Boosts are NOT treated as a quality signal.
    # They are simply another DEX Screener discovery
    # source.
    # --------------------------------------------------------

    boosts = dex_discovery_request(
        "/token-boosts/latest/v1"
    )

    boost_addresses = (
        extract_token_addresses(
            boosts
        )
    )

    before = len(found)

    found.update(
        boost_addresses
    )

    print(
        "Latest boosts:",
        len(boost_addresses),
        "| new:",
        len(found) - before,
    )

    # --------------------------------------------------------
    # 3. Top boosts
    #
    # Again, discovery only.
    # We do NOT give a token extra runner score merely
    # because it is boosted.
    # --------------------------------------------------------

    top_boosts = dex_discovery_request(
        "/token-boosts/top/v1"
    )

    top_boost_addresses = (
        extract_token_addresses(
            top_boosts
        )
    )

    before = len(found)

    found.update(
        top_boost_addresses
    )

    print(
        "Top boosts:",
        len(top_boost_addresses),
        "| new:",
        len(found) - before,
    )

    # --------------------------------------------------------
    # Preserve previously discovered tokens.
    #
    # This is important:
    #
    # Once the bot discovers a token, it continues tracking
    # it even if it disappears from a DEX Screener discovery
    # feed on the next refresh.
    # --------------------------------------------------------

    existing_histories = state.get(
        "histories",
        {}
    )

    for address in (
        existing_histories.keys()
    ):

        found.add(
            address
        )

    result = list(
        found
    )

    result = result[
        :MAX_DISCOVERY_TOKENS
    ]

    discovery_cache = result
    discovery_cache_ts = current

    print(
        "Total DEX-discovered/retained tokens:",
        len(result),
    )

    return result


# ============================================================
# DEX SCREENER TOKEN PAIRS
# ============================================================

def extract_pairs(
    data: Any,
) -> List[Dict[str, Any]]:

    if isinstance(
        data,
        list,
    ):

        return data

    if not isinstance(
        data,
        dict,
    ):

        return []

    pairs = data.get(
        "pairs"
    )

    if isinstance(
        pairs,
        list,
    ):

        return pairs

    return []


def get_token_pairs(
    token_address: str,
) -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}"
        f"/token-pairs/v1/"
        f"{SOLANA}/"
        f"{token_address}"
    )

    data = http_json(
        url,
        {
            "Accept":
                "application/json",
            "User-Agent":
                "RunnerBot/4.0",
        },
    )

    pairs = extract_pairs(
        data
    )

    return [
        pair
        for pair in pairs
        if pair.get(
            "chainId"
        ) == SOLANA
    ]


def choose_best_pair(
    pairs: List[Dict[str, Any]],
) -> Optional[
    Dict[str, Any]
]:

    best = None
    best_liquidity = 0.0

    for pair in pairs:

        liquidity = safe_float(
            (
                pair.get(
                    "liquidity"
                )
                or {}
            ).get(
                "usd"
            )
        )

        if (
            best is None
            or liquidity
            > best_liquidity
        ):

            best = pair
            best_liquidity = liquidity

    return best


def discover_pairs(
    force: bool = False,
) -> List[Dict[str, Any]]:

    addresses = (
        discover_token_addresses(
            force=force
        )
    )

    if not addresses:

        return []

    pairs_by_token = {}

    lookups = min(
        len(addresses),
        MAX_TOKEN_LOOKUPS_PER_CYCLE,
    )

    print(
        f"Fetching DEX Screener pools "
        f"for {lookups} tokens..."
    )

    for index in range(
        lookups
    ):

        address = addresses[
            index
        ]

        pairs = get_token_pairs(
            address
        )

        if pairs:

            best = choose_best_pair(
                pairs
            )

            if best:

                base = (
                    best.get(
                        "baseToken"
                    )
                    or {}
                )

                token_address = (
                    base.get(
                        "address"
                    )
                    or address
                )

                pairs_by_token[
                    token_address
                ] = best

        time.sleep(
            TOKEN_LOOKUP_SLEEP
        )

    result = list(
        pairs_by_token.values()
    )

    print(
        f"DEX Screener returned "
        f"{len(result)} unique Solana pools."
    )

    return result


# ============================================================
# SNAPSHOT
# ============================================================

def pair_to_snapshot(
    pair: Dict[str, Any],
) -> Optional[
    Dict[str, Any]
]:

    base = (
        pair.get(
            "baseToken"
        )
        or {}
    )

    address = base.get(
        "address"
    )

    if not address:

        return None

    symbol = (
        base.get(
            "symbol"
        )
        or "?"
    )

    name = (
        base.get(
            "name"
        )
        or symbol
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

    liquidity = safe_float(
        (
            pair.get(
                "liquidity"
            )
            or {}
        ).get(
            "usd"
        )
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

    price_change = (
        pair.get(
            "priceChange"
        )
        or {}
    )

    m5 = txns.get(
        "m5"
    ) or {}

    h1 = txns.get(
        "h1"
    ) or {}

    pair_created_at = safe_int(
        pair.get(
            "pairCreatedAt"
        )
    )

    snapshot = {
        "ts":
            now_ts(),

        "address":
            address,

        "symbol":
            symbol,

        "name":
            name,

        "market_cap":
            market_cap,

        "liquidity_usd":
            liquidity,

        "volume_5m":
            safe_float(
                volume.get(
                    "m5"
                )
            ),

        "volume_1h":
            safe_float(
                volume.get(
                    "h1"
                )
            ),

        "buys_5m":
            safe_int(
                m5.get(
                    "buys"
                )
            ),

        "sells_5m":
            safe_int(
                m5.get(
                    "sells"
                )
            ),

        "buys_1h":
            safe_int(
                h1.get(
                    "buys"
                )
            ),

        "sells_1h":
            safe_int(
                h1.get(
                    "sells"
                )
            ),

        "tx_5m":
            (
                safe_int(
                    m5.get(
                        "buys"
                    )
                )
                +
                safe_int(
                    m5.get(
                        "sells"
                    )
                )
            ),

        "tx_1h":
            (
                safe_int(
                    h1.get(
                        "buys"
                    )
                )
                +
                safe_int(
                    h1.get(
                        "sells"
                    )
                )
            ),

        "price_usd":
            safe_float(
                pair.get(
                    "priceUsd"
                )
            ),

        "price_change_5m":
            safe_float(
                price_change.get(
                    "m5"
                )
            ),

        "price_change_1h":
            safe_float(
                price_change.get(
                    "h1"
                )
            ),

        "pair_created_at":
            pair_created_at,

        "age_hours":
            pair_age_hours(
                pair_created_at
            ),

        "pair_address":
            pair.get(
                "pairAddress"
            ),

        "dex_id":
            pair.get(
                "dexId"
            ),

        "url":
            pair.get(
                "url"
            ),

        "labels":
            pair.get(
                "labels"
            )
            or [],

        "boost_active":
            safe_float(
                (
                    pair.get(
                        "boosts"
                    )
                    or {}
                ).get(
                    "active"
                )
            ),
    }

    return snapshot


# ============================================================
# HISTORY
# ============================================================

def recent_history(
    address: str,
) -> List[Dict[str, Any]]:

    return state[
        "histories"
    ].get(
        address,
        [],
    )


def record_snapshot(
    snapshot: Dict[str, Any],
):

    address = snapshot[
        "address"
    ]

    mc = snapshot[
        "market_cap"
    ]

    if not (
        OBSERVE_MIN_MC
        <= mc
        <= OBSERVE_MAX_MC
    ):

        return

    history = state[
        "histories"
    ].setdefault(
        address,
        [],
    )

    history.append(
        snapshot
    )

    if len(history) > (
        MAX_HISTORY_PER_TOKEN
    ):

        del history[
            :-MAX_HISTORY_PER_TOKEN
        ]


# ============================================================
# FIND SNAPSHOT NEAR TIME
# ============================================================

def snapshot_about_seconds_ago(
    history: List[Dict[str, Any]],
    seconds: float,
) -> Optional[
    Dict[str, Any]
]:

    if not history:
        return None

    target = (
        now_ts()
        - seconds
    )

    best = None
    best_distance = float(
        "inf"
    )

    for item in history:

        ts = safe_float(
            item.get(
                "ts"
            )
        )

        distance = abs(
            ts - target
        )

        if distance < best_distance:

            best_distance = distance
            best = item

    return best


# ============================================================
# ACTIVITY MODEL
# ============================================================

def calculate_activity(
    history: List[Dict[str, Any]],
) -> Dict[str, float]:

    result = {
        "vol_now":
            0.0,

        "vol_1m":
            0.0,

        "tx_now":
            0,

        "tx_1m":
            0,

        "volume_acceleration":
            0.0,

        "tx_acceleration":
            0.0,

        "activity_acceleration":
            0.0,
    }

    if not history:

        return result

    latest = history[-1]

    result[
        "vol_now"
    ] = safe_float(
        latest.get(
            "volume_5m"
        )
    )

    result[
        "tx_now"
    ] = safe_int(
        latest.get(
            "tx_5m"
        )
    )

    previous = (
        snapshot_about_seconds_ago(
            history,
            ACTIVITY_LOOKBACK_SECONDS,
        )
    )

    if not previous:

        return result

    previous_volume = safe_float(
        previous.get(
            "volume_5m"
        )
    )

    previous_tx = safe_int(
        previous.get(
            "tx_5m"
        )
    )

    result[
        "vol_1m"
    ] = previous_volume

    result[
        "tx_1m"
    ] = previous_tx

    if previous_volume > 0:

        result[
            "volume_acceleration"
        ] = pct_change(
            previous_volume,
            result[
                "vol_now"
            ],
        )

    if previous_tx > 0:

        result[
            "tx_acceleration"
        ] = pct_change(
            previous_tx,
            result[
                "tx_now"
            ],
        )

    result[
        "activity_acceleration"
    ] = (
        result[
            "volume_acceleration"
        ]
        + result[
            "tx_acceleration"
        ]
    ) / 2.0

    return result


# ============================================================
# ANALYSIS
# ============================================================

def analyze(
    snapshot: Dict[str, Any],
) -> Optional[
    Dict[str, Any]
]:

    address = snapshot[
        "address"
    ]

    history = recent_history(
        address
    )

    if len(history) < (
        MIN_OBSERVATIONS
    ):

        return None

    recent = history[
        -MIN_OBSERVATIONS:
    ]

    mcs = [
        safe_float(
            item.get(
                "market_cap"
            )
        )
        for item in recent
        if safe_float(
            item.get(
                "market_cap"
            )
        ) > 0
    ]

    if len(mcs) < 2:

        return None

    oldest_mc = mcs[0]
    latest_mc = mcs[-1]

    mc_move = pct_change(
        oldest_mc,
        latest_mc,
    )

    previous_mcs = mcs[
        :-1
    ]

    previous_high = max(
        previous_mcs
    )

    current_mc = safe_float(
        snapshot.get(
            "market_cap"
        )
    )

    breakout = (
        current_mc
        > previous_high * 1.01
    )

    highest_mc = max(
        mcs
    )

    lowest_mc = min(
        mcs
    )

    range_pct = 0.0

    if lowest_mc > 0:

        range_pct = (
            (
                highest_mc
                - lowest_mc
            )
            / lowest_mc
            * 100.0
        )

    # --------------------------------------------------------
    # Instant MC movement
    # --------------------------------------------------------

    previous = recent[-2]

    instant_mc_move = pct_change(
        safe_float(
            previous.get(
                "market_cap"
            )
        ),
        current_mc,
    )

    # --------------------------------------------------------
    # Activity
    # --------------------------------------------------------

    activity = calculate_activity(
        history
    )

    activity_expansion = (
        activity[
            "volume_acceleration"
        ]
        >= ACTIVITY_EXPANSION_PCT
        or
        activity[
            "tx_acceleration"
        ]
        >= ACTIVITY_EXPANSION_PCT
    )

    # --------------------------------------------------------
    # Buy pressure
    # --------------------------------------------------------

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

    total_tx = (
        buys
        + sells
    )

    if sells > 0:

        buy_ratio = (
            buys / sells
        )

    elif buys > 0:

        buy_ratio = float(
            buys
        )

    else:

        buy_ratio = 0.0

    buy_percentage = (
        buys / total_tx * 100.0
        if total_tx > 0
        else 0.0
    )

    # --------------------------------------------------------
    # Consolidation
    # --------------------------------------------------------

    active_consolidation = 0

    for item in recent:

        volume = safe_float(
            item.get(
                "volume_5m"
            )
        )

        tx = safe_int(
            item.get(
                "tx_5m"
            )
        )

        if (
            volume
            >= MIN_CONSOLIDATION_VOLUME_5M
            and tx
            >= MIN_CONSOLIDATION_TX_5M
        ):

            active_consolidation += 1

    consolidation = (
        active_consolidation
        >= MIN_CONSOLIDATION_ACTIVE_OBS
        and range_pct
        <= MAX_CONSOLIDATION_RANGE_PCT
    )

    # --------------------------------------------------------
    # Liquidity stability
    # --------------------------------------------------------

    liquidities = [
        safe_float(
            item.get(
                "liquidity_usd"
            )
        )
        for item in recent
        if safe_float(
            item.get(
                "liquidity_usd"
            )
        ) > 0
    ]

    liquidity_move = 0.0

    if len(liquidities) >= 2:

        liquidity_move = pct_change(
            liquidities[0],
            liquidities[-1],
        )

    liquidity_stable = (
        liquidity_move >= -15.0
    )

    # --------------------------------------------------------
    # AGE SCORE
    # --------------------------------------------------------

    age = safe_float(
        snapshot.get(
            "age_hours"
        )
    )

    age_points = 0

    if age <= 3:

        age_points = 15

    elif age <= 6:

        age_points = 12

    elif age <= 12:

        age_points = 9

    elif age <= 24:

        age_points = 6

    # --------------------------------------------------------
    # MC ASYMMETRY SCORE
    # --------------------------------------------------------

    mc_points = 0

    if (
        20_000
        <= current_mc
        <= 75_000
    ):

        mc_points = 15

    elif (
        current_mc
        <= 150_000
    ):

        mc_points = 11

    elif (
        current_mc
        <= 225_000
    ):

        mc_points = 8

    else:

        mc_points = 5

    # --------------------------------------------------------
    # LIQUIDITY SCORE
    # --------------------------------------------------------

    liquidity = safe_float(
        snapshot.get(
            "liquidity_usd"
        )
    )

    liquidity_points = 0

    if liquidity >= 50_000:

        liquidity_points = 10

    elif liquidity >= 25_000:

        liquidity_points = 9

    elif liquidity >= 15_000:

        liquidity_points = 8

    elif liquidity >= 10_000:

        liquidity_points = 6

    # --------------------------------------------------------
    # VOLUME SCORE
    # --------------------------------------------------------

    volume_5m = safe_float(
        snapshot.get(
            "volume_5m"
        )
    )

    volume_points = 0

    if volume_5m >= 20_000:

        volume_points = 10

    elif volume_5m >= 10_000:

        volume_points = 8

    elif volume_5m >= 5_000:

        volume_points = 6

    elif volume_5m >= 2_000:

        volume_points = 4

    elif volume_5m >= 500:

        volume_points = 2

    # --------------------------------------------------------
    # BUY PRESSURE SCORE
    # --------------------------------------------------------

    buy_points = 0

    if buy_percentage >= 70:

        buy_points = 10

    elif buy_percentage >= 60:

        buy_points = 8

    elif buy_percentage >= 55:

        buy_points = 6

    elif buy_percentage >= 50:

        buy_points = 3

    # --------------------------------------------------------
    # TRANSACTION PARTICIPATION SCORE
    # --------------------------------------------------------

    tx_points = 0

    if total_tx >= 200:

        tx_points = 10

    elif total_tx >= 100:

        tx_points = 8

    elif total_tx >= 50:

        tx_points = 6

    elif total_tx >= 25:

        tx_points = 4

    elif total_tx >= 10:

        tx_points = 2

    # --------------------------------------------------------
    # STRUCTURE SCORE
    # --------------------------------------------------------

    structure_points = 0

    if consolidation:

        structure_points += 7

    if breakout:

        structure_points += 8

    if (
        current_mc
        > oldest_mc
    ):

        structure_points = min(
            15,
            structure_points + 2,
        )

    # --------------------------------------------------------
    # ACTIVITY SCORE
    # --------------------------------------------------------

    activity_points = 0

    if (
        activity[
            "activity_acceleration"
        ] >= 20
    ):

        activity_points = 8

    if (
        activity[
            "activity_acceleration"
        ] >= 40
    ):

        activity_points = 12

    if (
        activity[
            "activity_acceleration"
        ] >= 75
    ):

        activity_points = 15

    # --------------------------------------------------------
    # EXTREME VERTICAL MOVE PENALTY
    #
    # We don't want to chase a candle that has already
    # gone vertical.
    # --------------------------------------------------------

    vertical_penalty = 0

    price_change_5m = safe_float(
        snapshot.get(
            "price_change_5m"
        )
    )

    if price_change_5m > 100:

        vertical_penalty = 15

    elif price_change_5m > 75:

        vertical_penalty = 10

    elif price_change_5m > 50:

        vertical_penalty = 5

    # --------------------------------------------------------
    # TOTAL SCORE
    #
    # Maximum base:
    #
    # Age             15
    # MC              15
    # Liquidity       10
    # Volume          10
    # Buy pressure    10
    # Transactions    10
    # Structure       15
    # Activity        15
    #
    # TOTAL            100
    # --------------------------------------------------------

    raw_score = (
        age_points
        + mc_points
        + liquidity_points
        + volume_points
        + buy_points
        + tx_points
        + structure_points
        + activity_points
    )

    score = max(
        0,
        raw_score
        - vertical_penalty,
    )

    # --------------------------------------------------------
    # Reasons
    # --------------------------------------------------------

    reasons = []

    if age <= 3:

        reasons.append(
            "very early pair"
        )

    elif age <= 6:

        reasons.append(
            "early pair"
        )

    if current_mc <= 75_000:

        reasons.append(
            "low MC"
        )

    if liquidity >= 15_000:

        reasons.append(
            "healthy liquidity"
        )

    if volume_5m >= REFERENCE_VOLUME_5M:

        reasons.append(
            "5m volume >= $5K"
        )

    if buy_percentage >= 60:

        reasons.append(
            "buy participation"
        )

    if total_tx >= 50:

        reasons.append(
            "active transactions"
        )

    if consolidation:

        reasons.append(
            "consolidation"
        )

    if breakout:

        reasons.append(
            "structure breakout"
        )

    if activity_expansion:

        reasons.append(
            "activity expansion"
        )

    if liquidity_stable:

        reasons.append(
            "liquidity stable"
        )

    if vertical_penalty > 0:

        reasons.append(
            "vertical-move penalty"
        )

    # --------------------------------------------------------
    # State
    # --------------------------------------------------------

    # Important:
    #
    # Score alone does NOT create ignition.
    #
    # We require actual structural/activity confirmation.
    # --------------------------------------------------------

    if (
        score >= 70
        and breakout
        and activity_expansion
        and buy_percentage >= 50
    ):

        setup_state = (
            "IGNITION"
        )

    elif (
        breakout
        and activity_expansion
    ):

        setup_state = (
            "STRUCTURE BREAK"
        )

    elif activity_expansion:

        setup_state = (
            "EXPANSION"
        )

    elif consolidation:

        setup_state = (
            "CONSOLIDATION"
        )

    else:

        setup_state = (
            "OBSERVING"
        )

    return {
        "state":
            setup_state,

        "score":
            score,

        "raw_score":
            raw_score,

        "reasons":
            reasons,

        "history_count":
            len(history),

        "age_hours":
            age,

        "mc_move":
            mc_move,

        "instant_mc_move":
            instant_mc_move,

        "range_pct":
            range_pct,

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

        "vol_now":
            activity[
                "vol_now"
            ],

        "vol_1m":
            activity[
                "vol_1m"
            ],

        "tx_now":
            activity[
                "tx_now"
            ],

        "tx_1m":
            activity[
                "tx_1m"
            ],

        "buy_ratio":
            buy_ratio,

        "buy_percentage":
            buy_percentage,

        "total_tx":
            total_tx,

        "breakout":
            breakout,

        "liquidity_move":
            liquidity_move,

        "liquidity_stable":
            liquidity_stable,

        "consolidation":
            consolidation,

        "vertical_penalty":
            vertical_penalty,
    }


# ============================================================
# IGNITION TRACKING
# ============================================================

def create_ignition(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> bool:

    address = snapshot[
        "address"
    ]

    existing = state[
        "ignitions"
    ].get(
        address
    )

    if existing:

        return False

    event = {
        "address":
            address,

        "symbol":
            snapshot[
                "symbol"
            ],

        "name":
            snapshot[
                "name"
            ],

        "pair_address":
            snapshot.get(
                "pair_address"
            ),

        "url":
            snapshot.get(
                "url"
            ),

        "start_ts":
            now_ts(),

        "start_mc":
            snapshot[
                "market_cap"
            ],

        "peak_mc":
            snapshot[
                "market_cap"
            ],

        "lowest_mc":
            snapshot[
                "market_cap"
            ],

        "max_drawdown":
            0.0,

        "score":
            analysis[
                "score"
            ],

        "reasons":
            analysis[
                "reasons"
            ],

        "outcomes":
            {},

        "final_label":
            None,

        "last_mc":
            snapshot[
                "market_cap"
            ],

        "continuing":
            False,

        "failed":
            False,
    }

    state[
        "ignitions"
    ][address] = event

    save_state()

    return True


def update_ignition(
    snapshot: Dict[str, Any],
):

    address = snapshot[
        "address"
    ]

    event = state[
        "ignitions"
    ].get(
        address
    )

    if not event:

        return None

    start_mc = safe_float(
        event.get(
            "start_mc"
        )
    )

    current_mc = safe_float(
        snapshot.get(
            "market_cap"
        )
    )

    if current_mc <= 0:

        return None

    event[
        "peak_mc"
    ] = max(
        safe_float(
            event.get(
                "peak_mc"
            )
        ),
        current_mc,
    )

    event[
        "lowest_mc"
    ] = min(
        safe_float(
            event.get(
                "lowest_mc"
            )
        ),
        current_mc,
    )

    if start_mc <= 0:

        return None

    current_return = pct_change(
        start_mc,
        current_mc,
    )

    drawdown = pct_change(
        event[
            "peak_mc"
        ],
        current_mc,
    )

    event[
        "max_drawdown"
    ] = min(
        safe_float(
            event.get(
                "max_drawdown"
            )
        ),
        drawdown,
    )

    event[
        "last_mc"
    ] = current_mc

    elapsed = (
        now_ts()
        - safe_float(
            event.get(
                "start_ts"
            )
        )
    )

    # --------------------------------------------------------
    # 5 MIN
    # --------------------------------------------------------

    if (
        elapsed >= 300
        and "5m"
        not in event[
            "outcomes"
        ]
    ):

        label = (
            "CONTINUING"
            if current_return >= 10
            else
            "FAILED"
            if current_return <= -15
            else
            "UNCLEAR"
        )

        event[
            "outcomes"
        ]["5m"] = {
            "return_pct":
                current_return,
            "mc":
                current_mc,
            "label":
                label,
        }

    # --------------------------------------------------------
    # 15 MIN
    # --------------------------------------------------------

    if (
        elapsed >= 900
        and "15m"
        not in event[
            "outcomes"
        ]
    ):

        label = (
            "CONTINUING"
            if current_return >= 10
            else
            "FAILED"
            if current_return <= -15
            else
            "UNCLEAR"
        )

        event[
            "outcomes"
        ]["15m"] = {
            "return_pct":
                current_return,
            "mc":
                current_mc,
            "label":
                label,
        }

    # --------------------------------------------------------
    # 30 MIN FINAL
    # --------------------------------------------------------

    if (
        elapsed >= 1800
        and "30m"
        not in event[
            "outcomes"
        ]
    ):

        if current_return >= 30:

            final_label = (
                "RUNNER"
            )

        elif current_return <= -15:

            final_label = (
                "FAILED"
            )

        else:

            final_label = (
                "UNCLEAR"
            )

        event[
            "outcomes"
        ]["30m"] = {
            "return_pct":
                current_return,
            "mc":
                current_mc,
            "label":
                final_label,
        }

        event[
            "final_label"
        ] = final_label

    save_state()

    return event


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(
    method: str,
    payload: Optional[
        Dict[str, Any]
    ] = None,
) -> Any:

    if not TELEGRAM_BOT_TOKEN:

        return None

    url = (
        f"{TELEGRAM_BASE}"
        f"/bot{TELEGRAM_BOT_TOKEN}"
        f"/{method}"
    )

    encoded = json.dumps(
        payload or {}
    ).encode(
        "utf-8"
    )

    request = (
        urllib.request.Request(
            url,
            data=encoded,
            headers={
                "Content-Type":
                    "application/json",
            },
            method="POST",
        )
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=HTTP_TIMEOUT,
        ) as response:

            raw = (
                response
                .read()
                .decode("utf-8")
            )

        return json.loads(
            raw
        )

    except Exception as exc:

        print(
            "Telegram error:",
            exc,
        )

        return None


def telegram_send(
    chat_id: Any,
    text: str,
):

    if not (
        TELEGRAM_BOT_TOKEN
        and HEATING_ALERTS_ENABLED
    ):

        return

    telegram_api(
        "sendMessage",
        {
            "chat_id":
                chat_id,

            "text":
                text,

            "disable_web_page_preview":
                True,
        },
    )


def broadcast(
    text: str,
):

    if not (
        TELEGRAM_BOT_TOKEN
        and HEATING_ALERTS_ENABLED
    ):

        return

    for chat_id in list(
        state.get(
            "subscribers",
            [],
        )
    ):

        telegram_send(
            chat_id,
            text,
        )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def process_telegram_updates():

    global telegram_offset

    if not TELEGRAM_BOT_TOKEN:

        return

    result = telegram_api(
        "getUpdates",
        {
            "offset":
                telegram_offset,

            "timeout":
                0,

            "allowed_updates":
                [
                    "message"
                ],
        },
    )

    if not result:

        return

    if not result.get(
        "ok"
    ):

        return

    for update in result.get(
        "result",
        [],
    ):

        telegram_offset = (
            update.get(
                "update_id",
                telegram_offset,
            )
            + 1
        )

        message = update.get(
            "message"
        )

        if not message:

            continue

        chat = message.get(
            "chat"
        )

        if not chat:

            continue

        chat_id = chat.get(
            "id"
        )

        text = (
            message.get(
                "text"
            )
            or ""
        ).strip()

        if not text:

            continue

        command = (
            text.split(
                " ",
                1,
            )[0]
            .lower()
        )

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        if command == "/start":

            if chat_id not in (
                state[
                    "subscribers"
                ]
            ):

                state[
                    "subscribers"
                ].append(
                    chat_id
                )

                save_state()

            telegram_send(
                chat_id,
                (
                    "🚀 Runner Bot V4\n\n"
                    "You are subscribed.\n\n"
                    "DEX Screener first.\n"
                    "Solana runner research engine.\n\n"
                    "Runner range:\n"
                    "$20K-$300K MC\n"
                    "Liquidity >= $10K\n"
                    "Pair age <= 24h\n\n"
                    "Scanning every "
                    f"{int(SCAN_INTERVAL_SECONDS)}s."
                ),
            )

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        elif command == "/stop":

            if chat_id in (
                state[
                    "subscribers"
                ]
            ):

                state[
                    "subscribers"
                ].remove(
                    chat_id
                )

                save_state()

            telegram_send(
                chat_id,
                "🛑 Runner alerts stopped.",
            )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        elif command == "/status":

            active = sum(
                1
                for event
                in state[
                    "ignitions"
                ].values()
                if not event.get(
                    "final_label"
                )
            )

            telegram_send(
                chat_id,
                (
                    "📊 RUNNER BOT V4\n\n"
                    f"Tracked tokens: "
                    f"{len(state['histories'])}\n"
                    f"Ignitions: "
                    f"{len(state['ignitions'])}\n"
                    f"Active events: "
                    f"{active}\n"
                    f"Subscribers: "
                    f"{len(state['subscribers'])}\n"
                    f"Alerts: "
                    f"{'ON' if HEATING_ALERTS_ENABLED else 'OFF'}"
                ),
            )

        # ----------------------------------------------------
        # ALERTS
        # ----------------------------------------------------

        elif command == "/alerts":

            telegram_send(
                chat_id,
                (
                    "Runner alerts: "
                    f"{'ON' if HEATING_ALERTS_ENABLED else 'OFF'}"
                ),
            )

        # ----------------------------------------------------
        # SCAN
        # ----------------------------------------------------

        elif command == "/scan":

            telegram_send(
                chat_id,
                (
                    "🔎 Manual scan queued.\n"
                    "The scanner will run on the next cycle."
                ),
            )


# ============================================================
# ALERT
# ============================================================

def build_ignition_alert(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> str:

    symbol = snapshot[
        "symbol"
    ]

    mc = snapshot[
        "market_cap"
    ]

    liquidity = snapshot[
        "liquidity_usd"
    ]

    volume = snapshot[
        "volume_5m"
    ]

    buys = snapshot[
        "buys_5m"
    ]

    sells = snapshot[
        "sells_5m"
    ]

    age = analysis[
        "age_hours"
    ]

    score = analysis[
        "score"
    ]

    buy_percentage = analysis[
        "buy_percentage"
    ]

    activity = analysis[
        "activity_acceleration"
    ]

    price_change = snapshot[
        "price_change_5m"
    ]

    reasons = ", ".join(
        analysis[
            "reasons"
        ]
    )

    url = (
        snapshot.get(
            "url"
        )
        or ""
    )

    return (
        "🔥 RUNNER IGNITION\n\n"

        f"🪙 {symbol}\n"

        f"💰 MC: "
        f"{format_money(mc)}\n"

        f"💧 Liquidity: "
        f"{format_money(liquidity)}\n"

        f"⏱ Pair age: "
        f"{age:.1f}h\n\n"

        f"📊 5m Volume: "
        f"{format_money(volume)}\n"

        f"🟢 Buys: {buys}\n"

        f"🔴 Sells: {sells}\n"

        f"📈 Buy participation: "
        f"{buy_percentage:.1f}%\n\n"

        f"⚡ Activity acceleration: "
        f"{format_pct(activity)}\n"

        f"📈 5m price: "
        f"{format_pct(price_change)}\n\n"

        f"🎯 Runner score: "
        f"{score}/100\n"

        f"💥 Breakout: "
        f"{'YES' if analysis['breakout'] else 'NO'}\n"

        f"📐 Structure range: "
        f"{analysis['range_pct']:.1f}%\n\n"

        f"Reasons: {reasons}\n\n"

        "⚠️ Research signal — not a guarantee.\n"

        + (
            f"\n🔗 {url}"
            if url
            else ""
        )
    )


def build_outcome_alert(
    event: Dict[str, Any],
    timeframe: str,
) -> str:

    outcome = (
        event[
            "outcomes"
        ].get(
            timeframe
        )
        or {}
    )

    label = outcome.get(
        "label",
        "UNCLEAR",
    )

    return_pct = safe_float(
        outcome.get(
            "return_pct"
        )
    )

    mc = safe_float(
        outcome.get(
            "mc"
        )
    )

    return (
        "📊 RUNNER RESEARCH UPDATE\n\n"

        f"🪙 {event['symbol']}\n"

        f"⏱ {timeframe}\n"

        f"🏷 {label}\n"

        f"MC: {format_money(mc)}\n"

        f"Move: "
        f"{format_pct(return_pct)}\n"
    )


# ============================================================
# MAIN SCAN
# ============================================================

def scan_once():

    pairs = discover_pairs()

    if not pairs:

        print(
            "No DEX Screener pairs discovered."
        )

        return

    candidates = 0
    analyzed = 0
    ignitions = 0

    for pair in pairs:

        snapshot = (
            pair_to_snapshot(
                pair
            )
        )

        if not snapshot:

            continue

        # ----------------------------------------------------
        # Observe before filtering.
        #
        # This allows the bot to build history around tokens
        # that enter the observation band.
        # ----------------------------------------------------

        record_snapshot(
            snapshot
        )

        mc = snapshot[
            "market_cap"
        ]

        liquidity = snapshot[
            "liquidity_usd"
        ]

        age = snapshot[
            "age_hours"
        ]

        # ----------------------------------------------------
        # HARD FILTERS
        # ----------------------------------------------------

        if not (
            MIN_MC
            <= mc
            <= MAX_MC
        ):

            continue

        if liquidity < (
            MIN_LIQUIDITY
        ):

            continue

        if age > (
            MAX_PAIR_AGE_HOURS
        ):

            continue

        candidates += 1

        analysis = analyze(
            snapshot
        )

        if not analysis:

            continue

        analyzed += 1

        symbol = snapshot[
            "symbol"
        ]

        print(
            f"[TRACK] "
            f"{symbol:<12} "
            f"Age={age:>5.1f}h "
            f"MC={format_money(mc):>9} "
            f"Liq={format_money(liquidity):>9} "
            f"5mVol={format_money(snapshot['volume_5m']):>9} "
            f"Buy%={analysis['buy_percentage']:>5.1f} "
            f"Act={analysis['activity_acceleration']:>6.1f}% "
            f"MC={analysis['mc_move']:>6.1f}% "
            f"State={analysis['state']:<17} "
            f"Score={analysis['score']:>3}/100"
        )

        # ----------------------------------------------------
        # IGNITION
        # ----------------------------------------------------

        if (
            analysis[
                "state"
            ]
            == "IGNITION"
        ):

            created = (
                create_ignition(
                    snapshot,
                    analysis,
                )
            )

            if created:

                ignitions += 1

                print(
                    "🔥 IGNITION:",
                    symbol,
                    format_money(
                        mc
                    ),
                    "score=",
                    analysis[
                        "score"
                    ],
                )

                broadcast(
                    build_ignition_alert(
                        snapshot,
                        analysis,
                    )
                )

        # ----------------------------------------------------
        # UPDATE IGNITION
        # ----------------------------------------------------

        event = update_ignition(
            snapshot
        )

        if event:

            outcomes = event.get(
                "outcomes",
                {},
            )

            for timeframe in (
                "5m",
                "15m",
                "30m",
            ):

                outcome = (
                    outcomes.get(
                        timeframe
                    )
                )

                if not outcome:

                    continue

                notification_key = (
                    f"notified_{timeframe}"
                )

                if event.get(
                    notification_key
                ):

                    continue

                event[
                    notification_key
                ] = True

                broadcast(
                    build_outcome_alert(
                        event,
                        timeframe,
                    )
                )

    print(
        "\n"
        f"SCAN COMPLETE | "
        f"DEX pairs={len(pairs)} | "
        f"candidates={candidates} | "
        f"analyzed={analyzed} | "
        f"new ignitions={ignitions}"
    )

    save_state()


# ============================================================
# CONFIG
# ============================================================

def print_config():

    print(
        "\n"
        "========================================================\n"
        f"              RUNNER BOT {BOT_VERSION}\n"
        "========================================================\n"

        "DATA SOURCE:\n"
        "  • DEX Screener only\n"

        "DISCOVERY:\n"
        "  • Latest DEX Screener token profiles\n"
        "  • Latest DEX Screener boosts\n"
        "  • Top DEX Screener boosts\n"
        "  • Previously discovered tokens retained\n"

        "MARKET:\n"
        "  • Solana only\n"

        f"RUNNER RANGE:\n"
        f"  • MC: ${MIN_MC/1000:.0f}K"
        f"-${MAX_MC/1000:.0f}K\n"

        f"  • Liquidity >= "
        f"${MIN_LIQUIDITY/1000:.0f}K\n"

        f"  • Pair age <= "
        f"{MAX_PAIR_AGE_HOURS:.0f}h\n"

        "  • 5m volume = FEATURE, NOT HARD GATE\n"
        "  • Buy/sell = SCORE, NOT HARD GATE\n"

        "SIGNALS:\n"
        "  • Early age\n"
        "  • Low MC\n"
        "  • Liquidity\n"
        "  • 5m volume\n"
        "  • Buy participation\n"
        "  • Transaction participation\n"
        "  • Consolidation\n"
        "  • Breakout\n"
        "  • Activity acceleration\n"
        "  • Liquidity stability\n"
        "  • Vertical-pump penalty\n"

        "IGNITION:\n"
        "  • Score >= 70/100\n"
        "  • Breakout\n"
        "  • Activity expansion\n"
        "  • Buy participation >= 50%\n"

        "TRACKING:\n"
        "  • +5m\n"
        "  • +15m\n"
        "  • +30m\n"
        "  • Peak MC\n"
        "  • Max drawdown\n"

        f"SCAN INTERVAL:\n"
        f"  • {SCAN_INTERVAL_SECONDS}s\n"

        "========================================================\n"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print_config()

    load_state()

    if not TELEGRAM_BOT_TOKEN:

        print(
            "WARNING: "
            "TELEGRAM_BOT_TOKEN is not set."
        )

    if not HEATING_ALERTS_ENABLED:

        print(
            "WARNING: "
            "HEATING_ALERTS_ENABLED=false"
        )

    print(
        f"Runner Engine {BOT_VERSION} "
        "is online."
    )

    # --------------------------------------------------------
    # Initial DEX Screener discovery
    # --------------------------------------------------------

    try:

        discover_pairs(
            force=True
        )

    except Exception as exc:

        print(
            "Initial discovery error:",
            exc,
        )

    next_scan = 0.0

    while True:

        loop_start = now_ts()

        try:

            process_telegram_updates()

            current = now_ts()

            if current >= next_scan:

                scan_once()

                next_scan = (
                    now_ts()
                    + SCAN_INTERVAL_SECONDS
                )

            process_telegram_updates()

        except KeyboardInterrupt:

            print(
                "\nRunner Bot stopped."
            )

            save_state()

            break

        except Exception as exc:

            print(
                "Main loop error:",
                exc,
            )

        elapsed = (
            now_ts()
            - loop_start
        )

        sleep_for = max(
            1.0,
            min(
                5.0,
                SCAN_INTERVAL_SECONDS
                - elapsed,
            ),
        )

        time.sleep(
            sleep_for
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
