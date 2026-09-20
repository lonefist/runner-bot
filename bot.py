import json
import os
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V3.9
# BROAD SOLANA MARKET DISCOVERY
# ============================================================

BOT_VERSION = "v3.9"

# ------------------------------------------------------------
# API
# ------------------------------------------------------------

DEX_BASE = "https://api.dexscreener.com"
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
TELEGRAM_BASE = "https://api.telegram.org"

GECKO_NETWORK = "solana"

# ------------------------------------------------------------
# FILES
# ------------------------------------------------------------

STATE_FILE = "runner_state_v39.json"

# ------------------------------------------------------------
# RUNNER RANGE
# ------------------------------------------------------------

MIN_MC = 20_000
MAX_MC = 200_000

MIN_LIQUIDITY = 10_000

# 5m volume is a FEATURE, not a hard gate.
REFERENCE_VOLUME_5M = 5_000

# ------------------------------------------------------------
# OBSERVATION RANGE
# ------------------------------------------------------------

OBSERVE_MIN_MC = 10_000
OBSERVE_MAX_MC = 250_000

# ------------------------------------------------------------
# HISTORY
# ------------------------------------------------------------

MAX_STORED_TOKENS = 750
MAX_HISTORY_PER_TOKEN = 240

MIN_OBSERVATIONS = 6

# ------------------------------------------------------------
# CONSOLIDATION
# ------------------------------------------------------------

MIN_CONSOLIDATION_VOLUME_5M = 500
MIN_CONSOLIDATION_TX = 5
MIN_CONSOLIDATION_ACTIVE_OBS = 3

# ------------------------------------------------------------
# ACTIVITY ACCELERATION
# ------------------------------------------------------------

ACTIVITY_LOOKBACK_1M = 4
ACTIVITY_LOOKBACK_2M = 8

ACTIVITY_EXPANSION_PCT = 20.0

# ------------------------------------------------------------
# DISCOVERY
# ------------------------------------------------------------

DISCOVERY_INTERVAL_SECONDS = 60

# Number of Gecko pages used for each source.
# 3 sources x 2 pages = 6 Gecko requests per discovery cycle.
GECKO_PAGES = 2

# Number of tokens sent to DEX Screener per request.
DEX_BATCH_SIZE = 30

# Maximum number of discovered tokens processed.
MAX_DISCOVERY_TOKENS = 300

# ------------------------------------------------------------
# SCANNING
# ------------------------------------------------------------

SCAN_INTERVAL_SECONDS = float(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

HTTP_TIMEOUT = 12
HTTP_RETRIES = 3
HTTP_BACKOFF_SECONDS = 2

# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

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

# ------------------------------------------------------------
# GLOBAL CACHES
# ------------------------------------------------------------

discovery_cache: List[Dict[str, Any]] = []
discovery_cache_ts = 0.0

# ------------------------------------------------------------
# STATE
# ------------------------------------------------------------

state: Dict[str, Any] = {
    "histories": {},
    "ignitions": {},
    "subscribers": [],
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_ts() -> float:
    return time.time()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default

        return float(value)

    except (
        TypeError,
        ValueError,
    ):
        return default


def safe_int(value: Any, default: int = 0) -> int:
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


def clamp(
    value: float,
    minimum: float,
    maximum: float,
) -> float:
    return max(
        minimum,
        min(
            maximum,
            value,
        ),
    )


def format_money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.2f}K"

    return f"${value:.2f}"


def format_pct(value: float) -> str:
    if value >= 0:
        return f"+{value:.1f}%"

    return f"{value:.1f}%"


# ============================================================
# HTTP
# ============================================================

def http_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
) -> Any:

    headers = headers or {}

    for attempt in range(
        HTTP_RETRIES
    ):
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

                raw = response.read().decode(
                    "utf-8"
                )

            return json.loads(raw)

        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            Exception,
        ) as exc:

            if attempt == HTTP_RETRIES - 1:

                print(
                    f"[HTTP ERROR] "
                    f"{url} -> {exc}"
                )

                return None

            time.sleep(
                HTTP_BACKOFF_SECONDS
                * (attempt + 1)
            )

    return None


def gecko_json(
    path: str,
) -> Any:

    url = (
        f"{GECKO_BASE}"
        f"{path}"
    )

    headers = {
        "Accept":
            "application/json;version=20230203",
        "User-Agent":
            "RunnerBot/3.9",
    }

    return http_json(
        url,
        headers,
    )


# ============================================================
# STATE
# ============================================================

def load_state():

    global state

    if not os.path.exists(
        STATE_FILE
    ):

        print(
            "No previous state found."
        )

        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            loaded = json.load(file)

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
            "Persistent state loaded: "
            f"{len(state['histories'])} tokens | "
            f"{len(state['ignitions'])} ignition events"
        )

    except Exception as exc:

        print(
            f"State load failed: {exc}"
        )


def save_state():

    try:

        histories = state.get(
            "histories",
            {},
        )

        # Keep only the newest tokens.
        if len(histories) > MAX_STORED_TOKENS:

            ranked = sorted(
                histories.items(),
                key=lambda item: (
                    item[1][-1]["ts"]
                    if item[1]
                    else 0
                ),
                reverse=True,
            )

            state["histories"] = dict(
                ranked[
                    :MAX_STORED_TOKENS
                ]
            )

        # Trim individual histories.
        for address, history in (
            state["histories"].items()
        ):

            if len(history) > MAX_HISTORY_PER_TOKEN:

                state["histories"][
                    address
                ] = history[
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
            f"State save failed: {exc}"
        )


# ============================================================
# GECKO TERMINAL DISCOVERY
# ============================================================

def extract_gecko_token_addresses(
    data: Any,
) -> List[str]:

    addresses = []

    if not isinstance(
        data,
        dict,
    ):
        return addresses

    items = data.get(
        "data",
        [],
    )

    if not isinstance(
        items,
        list,
    ):
        return addresses

    for item in items:

        if not isinstance(
            item,
            dict,
        ):
            continue

        relationships = (
            item.get(
                "relationships"
            )
            or {}
        )

        base_token = (
            relationships.get(
                "base_token"
            )
            or {}
        )

        token_data = (
            base_token.get(
                "data"
            )
            or {}
        )

        token_id = token_data.get(
            "id"
        )

        if not token_id:
            continue

        if token_id.startswith(
            "solana_"
        ):

            address = token_id[
                len("solana_"):
            ]

            if address:
                addresses.append(
                    address
                )

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
        and (
            current
            - discovery_cache_ts
            < DISCOVERY_INTERVAL_SECONDS
        )
    ):

        return [
            item["address"]
            for item in discovery_cache
        ]

    found = set()

    sources = [
        (
            "TOP",
            (
                f"/networks/"
                f"{GECKO_NETWORK}"
                f"/pools"
            ),
        ),
        (
            "NEW",
            (
                f"/networks/"
                f"{GECKO_NETWORK}"
                f"/new_pools"
            ),
        ),
        (
            "TRENDING",
            (
                f"/networks/"
                f"{GECKO_NETWORK}"
                f"/trending_pools"
            ),
        ),
    ]

    print(
        "\n--- MARKET DISCOVERY ---"
    )

    for source_name, base_path in sources:

        for page in range(
            1,
            GECKO_PAGES + 1,
        ):

            separator = (
                "&"
                if "?" in base_path
                else "?"
            )

            endpoint = (
                f"{base_path}"
                f"{separator}"
                f"page={page}"
            )

            data = gecko_json(
                endpoint
            )

            addresses = (
                extract_gecko_token_addresses(
                    data
                )
            )

            before = len(found)

            found.update(
                addresses
            )

            added = (
                len(found)
                - before
            )

            print(
                f"Gecko {source_name} "
                f"page {page}: "
                f"{len(addresses)} tokens "
                f"| +{added} new"
            )

    result = list(found)

    # Do not let discovery explode.
    result = result[
        :MAX_DISCOVERY_TOKENS
    ]

    discovery_cache = [
        {
            "address": address
        }
        for address in result
    ]

    discovery_cache_ts = current

    print(
        f"Unique Solana tokens discovered: "
        f"{len(result)}"
    )

    return result


# ============================================================
# DEX SCREENER
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


def discover_pairs(
    force: bool = False,
) -> List[Dict[str, Any]]:

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

        # Discovery cache contains
        # addresses only.
        addresses = [
            item["address"]
            for item in discovery_cache
        ]

    else:

        addresses = (
            discover_token_addresses(
                force=force
            )
        )

    if not addresses:

        return []

    pairs_by_token = {}

    print(
        f"Fetching detailed data for "
        f"{len(addresses)} tokens..."
    )

    requests_made = 0

    for i in range(
        0,
        len(addresses),
        DEX_BATCH_SIZE,
    ):

        batch = addresses[
            i:
            i + DEX_BATCH_SIZE
        ]

        joined = ",".join(
            batch
        )

        url = (
            f"{DEX_BASE}"
            f"/tokens/v1/"
            f"solana/"
            f"{joined}"
        )

        data = http_json(
            url
        )

        requests_made += 1

        for pair in extract_pairs(
            data
        ):

            if pair.get(
                "chainId"
            ) != "solana":

                continue

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
                continue

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

            existing = (
                pairs_by_token.get(
                    address
                )
            )

            if existing is None:

                pairs_by_token[
                    address
                ] = pair

                continue

            old_liquidity = safe_float(
                (
                    existing.get(
                        "liquidity"
                    )
                    or {}
                ).get(
                    "usd"
                )
            )

            if liquidity > old_liquidity:

                pairs_by_token[
                    address
                ] = pair

    result = list(
        pairs_by_token.values()
    )

    result.sort(
        key=lambda pair: safe_float(
            (
                pair.get(
                    "liquidity"
                )
                or {}
            ).get(
                "usd"
            )
        ),
        reverse=True,
    )

    print(
        f"{len(result)} unique Solana "
        f"pairs received | "
        f"DEX requests={requests_made}"
    )

    return result


# ============================================================
# SNAPSHOT
# ============================================================

def pair_to_snapshot(
    pair: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

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

    snapshot = {
        "ts": now_ts(),
        "address": address,
        "symbol": symbol,
        "name": name,
        "market_cap": market_cap,
        "liquidity_usd": liquidity,
        "volume_5m": safe_float(
            volume.get(
                "m5"
            )
        ),
        "volume_1h": safe_float(
            volume.get(
                "h1"
            )
        ),
        "buys_5m": safe_int(
            m5.get(
                "buys"
            )
        ),
        "sells_5m": safe_int(
            m5.get(
                "sells"
            )
        ),
        "buys_1h": safe_int(
            h1.get(
                "buys"
            )
        ),
        "sells_1h": safe_int(
            h1.get(
                "sells"
            )
        ),
        "price_usd": safe_float(
            pair.get(
                "priceUsd"
            )
        ),
        "price_change_5m": safe_float(
            price_change.get(
                "m5"
            )
        ),
        "price_change_1h": safe_float(
            price_change.get(
                "h1"
            )
        ),
        "pair_created_at": safe_int(
            pair.get(
                "pairCreatedAt"
            )
        ),
        "pair_address": pair.get(
            "pairAddress"
        ),
        "dex_id": pair.get(
            "dexId"
        ),
        "url": pair.get(
            "url"
        ),
    }

    return snapshot


# ============================================================
# HISTORY
# ============================================================

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

    activity = (
        snapshot[
            "volume_5m"
        ] > 0
        or snapshot[
            "buys_5m"
        ] > 0
        or snapshot[
            "sells_5m"
        ] > 0
    )

    if not activity:

        return

    history = state[
        "histories"
    ].setdefault(
        address,
        []
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
# ANALYSIS
# ============================================================

def recent_history(
    address: str,
) -> List[Dict[str, Any]]:

    return state[
        "histories"
    ].get(
        address,
        []
    )


def calculate_activity(
    history: List[Dict[str, Any]],
) -> Dict[str, float]:

    result = {
        "vol_1m": 0.0,
        "vol_2m": 0.0,
        "tx_1m": 0,
        "tx_2m": 0,
        "accel_1m": 0.0,
        "accel_2m": 0.0,
    }

    if len(history) < 2:
        return result

    latest = history[-1]

    # --------------------------------------------------------
    # Approximate rolling activity using scan snapshots.
    # Each scan is approximately 15 seconds.
    # --------------------------------------------------------

    one_m_start = max(
        0,
        len(history)
        - ACTIVITY_LOOKBACK_1M,
    )

    two_m_start = max(
        0,
        len(history)
        - ACTIVITY_LOOKBACK_2M,
    )

    one_m = history[
        one_m_start:
    ]

    two_m = history[
        two_m_start:
    ]

    result[
        "vol_1m"
    ] = sum(
        safe_float(
            x.get(
                "volume_5m"
            )
        )
        for x in one_m
    )

    result[
        "vol_2m"
    ] = sum(
        safe_float(
            x.get(
                "volume_5m"
            )
        )
        for x in two_m
    )

    result[
        "tx_1m"
    ] = sum(
        safe_int(
            x.get(
                "buys_5m"
            )
        )
        + safe_int(
            x.get(
                "sells_5m"
            )
        )
        for x in one_m
    )

    result[
        "tx_2m"
    ] = sum(
        safe_int(
            x.get(
                "buys_5m"
            )
        )
        + safe_int(
            x.get(
                "sells_5m"
            )
        )
        for x in two_m
    )

    # Compare recent activity against
    # the earlier half of the same history.

    if len(history) >= 8:

        previous = history[
            -8:-4
        ]

        previous_vol = sum(
            safe_float(
                x.get(
                    "volume_5m"
                )
            )
            for x in previous
        )

        current_vol = sum(
            safe_float(
                x.get(
                    "volume_5m"
                )
            )
            for x in history[
                -4:
            ]
        )

        if previous_vol > 0:

            result[
                "accel_1m"
            ] = (
                (
                    current_vol
                    - previous_vol
                )
                / previous_vol
                * 100
            )

    if len(history) >= 16:

        previous = history[
            -16:-8
        ]

        previous_vol = sum(
            safe_float(
                x.get(
                    "volume_5m"
                )
            )
            for x in previous
        )

        current_vol = sum(
            safe_float(
                x.get(
                    "volume_5m"
                )
            )
            for x in history[
                -8:
            ]
        )

        if previous_vol > 0:

            result[
                "accel_2m"
            ] = (
                (
                    current_vol
                    - previous_vol
                )
                / previous_vol
                * 100
            )

    return result


def analyze(
    snapshot: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

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
            x.get(
                "market_cap"
            )
        )
        for x in recent
        if safe_float(
            x.get(
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

    highest_mc = max(
        mcs
    )

    lowest_mc = min(
        mcs
    )

    if lowest_mc > 0:

        range_pct = (
            (
                highest_mc
                - lowest_mc
            )
            / lowest_mc
            * 100
        )

    else:

        range_pct = 0.0

    previous = recent[
        -2
    ]

    previous_mc = safe_float(
        previous.get(
            "market_cap"
        )
    )

    current_mc = safe_float(
        snapshot.get(
            "market_cap"
        )
    )

    instant_mc_move = pct_change(
        previous_mc,
        current_mc,
    )

    activity = calculate_activity(
        history
    )

    current_buys = safe_int(
        snapshot.get(
            "buys_5m"
        )
    )

    current_sells = safe_int(
        snapshot.get(
            "sells_5m"
        )
    )

    total_tx = (
        current_buys
        + current_sells
    )

    if current_sells > 0:

        buy_ratio = (
            current_buys
            / current_sells
        )

    elif current_buys > 0:

        buy_ratio = float(
            current_buys
        )

    else:

        buy_ratio = 0.0

    breakout = (
        current_mc
        > highest_mc * 1.01
    )

    # If the latest value is already the
    # highest point, compare it against
    # previous observations.
    if len(mcs) >= 3:

        previous_high = max(
            mcs[:-1]
        )

        breakout = (
            current_mc
            > previous_high * 1.01
        )

    activity_expansion = (
        activity[
            "accel_1m"
        ]
        >= ACTIVITY_EXPANSION_PCT
        or
        activity[
            "accel_2m"
        ]
        >= ACTIVITY_EXPANSION_PCT
    )

    # --------------------------------------------------------
    # Consolidation
    # --------------------------------------------------------

    active_consolidation = 0

    for item in recent:

        vol = safe_float(
            item.get(
                "volume_5m"
            )
        )

        buys = safe_int(
            item.get(
                "buys_5m"
            )
        )

        sells = safe_int(
            item.get(
                "sells_5m"
            )
        )

        tx = (
            buys
            + sells
        )

        if (
            vol
            >= MIN_CONSOLIDATION_VOLUME_5M
            and tx
            >= MIN_CONSOLIDATION_TX
        ):

            active_consolidation += 1

    consolidation = (
        active_consolidation
        >= MIN_CONSOLIDATION_ACTIVE_OBS
        and range_pct
        <= 18.0
    )

    # --------------------------------------------------------
    # Liquidity stability
    # --------------------------------------------------------

    liquidities = [
        safe_float(
            x.get(
                "liquidity_usd"
            )
        )
        for x in recent
        if safe_float(
            x.get(
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
    # SCORE
    # --------------------------------------------------------

    score = 0
    reasons = []

    # Structure
    if (
        len(recent) >= 5
        and range_pct <= 18.0
        and abs(mc_move) <= 18.0
    ):

        score += 2

        reasons.append(
            "tight structure"
        )

    if activity_expansion:

        score += 2

        reasons.append(
            "activity expansion"
        )

    if mc_move >= 5.0:

        score += 2

        reasons.append(
            "MC expansion"
        )

    if breakout:

        score += 3

        reasons.append(
            "breakout"
        )

    if buy_ratio >= 1.2:

        score += 1

        reasons.append(
            "buy pressure"
        )

    if buy_ratio >= 1.5:

        score += 2

        reasons.append(
            "strong buy pressure"
        )

    if liquidity_stable:

        score += 1

        reasons.append(
            "liquidity stable"
        )

    if safe_float(
        snapshot.get(
            "volume_5m"
        )
    ) >= REFERENCE_VOLUME_5M:

        score += 1

        reasons.append(
            "5m volume >= $5K"
        )

    if (
        current_sells
        > current_buys
        and total_tx > 0
    ):

        reasons.append(
            "sell pressure"
        )

    # --------------------------------------------------------
    # State
    # --------------------------------------------------------

    if (
        breakout
        and activity_expansion
        and score >= 7
    ):

        setup_state = (
            "IGNITION"
        )

    elif breakout:

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
        "state": setup_state,
        "score": score,
        "reasons": reasons,
        "history_count": len(history),
        "mc_move": mc_move,
        "instant_mc_move": instant_mc_move,
        "range_pct": range_pct,
        "activity_expansion": activity_expansion,
        "accel_1m": activity[
            "accel_1m"
        ],
        "accel_2m": activity[
            "accel_2m"
        ],
        "vol_1m": activity[
            "vol_1m"
        ],
        "vol_2m": activity[
            "vol_2m"
        ],
        "tx_1m": activity[
            "tx_1m"
        ],
        "tx_2m": activity[
            "tx_2m"
        ],
        "buy_ratio": buy_ratio,
        "breakout": breakout,
        "liquidity_move": liquidity_move,
        "liquidity_stable": liquidity_stable,
        "consolidation": consolidation,
    }


# ============================================================
# IGNITION TRACKING
# ============================================================

def create_ignition(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
):

    address = snapshot[
        "address"
    ]

    # Don't repeatedly create ignition
    # events for the same token.
    existing = state[
        "ignitions"
    ].get(
        address
    )

    if existing:

        return False

    event = {
        "address": address,
        "symbol": snapshot[
            "symbol"
        ],
        "name": snapshot[
            "name"
        ],
        "pair_address": snapshot.get(
            "pair_address"
        ),
        "url": snapshot.get(
            "url"
        ),
        "start_ts": now_ts(),
        "start_mc": snapshot[
            "market_cap"
        ],
        "peak_mc": snapshot[
            "market_cap"
        ],
        "lowest_mc": snapshot[
            "market_cap"
        ],
        "max_drawdown": 0.0,
        "score": analysis[
            "score"
        ],
        "reasons": analysis[
            "reasons"
        ],
        "outcomes": {},
        "final_label": None,
        "last_mc": snapshot[
            "market_cap"
        ],
        "continuing": False,
        "failed": False,
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

    if start_mc > 0:

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

        # ----------------------------------------------------
        # +5 MIN
        # ----------------------------------------------------

        if (
            elapsed >= 300
            and "5m"
            not in event[
                "outcomes"
            ]
        ):

            label = (
                "CONTINUING"
                if current_return
                >= 10.0
                else
                "FAILED"
                if current_return
                <= -15.0
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

        # ----------------------------------------------------
        # +15 MIN
        # ----------------------------------------------------

        if (
            elapsed >= 900
            and "15m"
            not in event[
                "outcomes"
            ]
        ):

            label = (
                "CONTINUING"
                if current_return
                >= 10.0
                else
                "FAILED"
                if current_return
                <= -15.0
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

        # ----------------------------------------------------
        # +30 MIN FINAL
        # ----------------------------------------------------

        if (
            elapsed >= 1800
            and "30m"
            not in event[
                "outcomes"
            ]
        ):

            if current_return >= 30.0:

                final_label = (
                    "RUNNER"
                )

            elif current_return <= -15.0:

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

    payload = payload or {}

    encoded = json.dumps(
        payload
    ).encode(
        "utf-8"
    )

    request = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "Content-Type":
                "application/json",
        },
        method="POST",
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=HTTP_TIMEOUT,
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

        return json.loads(
            raw
        )

    except Exception as exc:

        print(
            f"Telegram error: {exc}"
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

    subscribers = state.get(
        "subscribers",
        [],
    )

    for chat_id in list(
        subscribers
    ):

        telegram_send(
            chat_id,
            text,
        )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

telegram_offset = 0


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

    updates = result.get(
        "result",
        [],
    )

    for update in updates:

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

        command = text.split(
            " ",
            1
        )[0].lower()

        # ----------------------------------------------------
        # /start
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
                    "🚀 Runner Bot V3.9\n\n"
                    "You are subscribed.\n\n"
                    "Discovery:\n"
                    "• Solana top pools\n"
                    "• Solana new pools\n"
                    "• Solana trending pools\n\n"
                    "Runner range:\n"
                    "$20K-$200K MC\n"
                    "Liquidity >= $10K\n\n"
                    "Scanning every "
                    f"{int(SCAN_INTERVAL_SECONDS)}s."
                ),
            )

        # ----------------------------------------------------
        # /stop
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
                "🛑 Runner alerts stopped for this chat.",
            )

        # ----------------------------------------------------
        # /alerts
        # ----------------------------------------------------

        elif command == "/alerts":

            status = (
                "ON"
                if HEATING_ALERTS_ENABLED
                else "OFF"
            )

            telegram_send(
                chat_id,
                (
                    "Runner alerts: "
                    f"{status}\n"
                    "Global bot alerts are controlled "
                    "by HEATING_ALERTS_ENABLED."
                ),
            )

        # ----------------------------------------------------
        # /status
        # ----------------------------------------------------

        elif command == "/status":

            active_ignitions = sum(
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
                    "📊 RUNNER BOT V3.9\n\n"
                    f"Tokens tracked: "
                    f"{len(state['histories'])}\n"
                    f"Ignitions: "
                    f"{len(state['ignitions'])}\n"
                    f"Active research events: "
                    f"{active_ignitions}\n"
                    f"Subscribers: "
                    f"{len(state['subscribers'])}\n"
                    f"Alerts: "
                    f"{'ON' if HEATING_ALERTS_ENABLED else 'OFF'}"
                ),
            )

        # ----------------------------------------------------
        # /scan
        # ----------------------------------------------------

        elif command == "/scan":

            telegram_send(
                chat_id,
                (
                    "🔎 Manual scan requested.\n"
                    "The scanner will run on the next cycle."
                ),
            )


# ============================================================
# ALERT FORMAT
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

    buy_ratio = analysis[
        "buy_ratio"
    ]

    accel_1m = analysis[
        "accel_1m"
    ]

    accel_2m = analysis[
        "accel_2m"
    ]

    price_change = snapshot[
        "price_change_5m"
    ]

    reasons = ", ".join(
        analysis[
            "reasons"
        ]
    )

    url = snapshot.get(
        "url"
    ) or ""

    return (
        "🔥 RUNNER IGNITION\n\n"

        f"🪙 {symbol}\n"
        f"💰 MC: {format_money(mc)}\n"
        f"💧 Liquidity: "
        f"{format_money(liquidity)}\n"
        f"📊 5m Volume: "
        f"{format_money(volume)}\n\n"

        f"🟢 Buys: {buys}\n"
        f"🔴 Sells: {sells}\n"
        f"⚖️ Buy/Sell: "
        f"{buy_ratio:.2f}\n\n"

        f"⚡ 1m acceleration: "
        f"{format_pct(accel_1m)}\n"
        f"⚡ 2m acceleration: "
        f"{format_pct(accel_2m)}\n"
        f"📈 5m price: "
        f"{format_pct(price_change)}\n\n"

        f"🎯 Score: "
        f"{analysis['score']}\n"
        f"💥 Breakout: "
        f"{'YES' if analysis['breakout'] else 'NO'}\n"
        f"📐 Structure: "
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
        "UNCLEAR"
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
        f"Move: {format_pct(return_pct)}\n"
    )


# ============================================================
# MAIN SCAN
# ============================================================

def scan_once():

    pairs = discover_pairs()

    if not pairs:

        print(
            "No pairs discovered."
        )

        return

    candidates = 0
    ignitions = 0

    for pair in pairs:

        snapshot = (
            pair_to_snapshot(
                pair
            )
        )

        if not snapshot:
            continue

        record_snapshot(
            snapshot
        )

        mc = snapshot[
            "market_cap"
        ]

        liquidity = snapshot[
            "liquidity_usd"
        ]

        # ----------------------------------------------------
        # RUNNER FILTER
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

        candidates += 1

        analysis = analyze(
            snapshot
        )

        if not analysis:
            continue

        symbol = snapshot[
            "symbol"
        ]

        print(
            f"[TRACK] "
            f"{symbol:<12} "
            f"MC={format_money(mc):>10} "
            f"5mVol="
            f"{format_money(snapshot['volume_5m']):>9} "
            f"Buy/Sell="
            f"{snapshot['buys_5m']}/"
            f"{snapshot['sells_5m']} "
            f"1mAcc="
            f"{analysis['accel_1m']:.1f}% "
            f"2mAcc="
            f"{analysis['accel_2m']:.1f}% "
            f"MC="
            f"{analysis['mc_move']:.1f}% "
            f"State="
            f"{analysis['state']} "
            f"Score="
            f"{analysis['score']}"
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

            created = create_ignition(
                snapshot,
                analysis,
            )

            if created:

                ignitions += 1

                print(
                    f"🔥 IGNITION: "
                    f"{symbol} "
                    f"MC={format_money(mc)} "
                    f"Score={analysis['score']}"
                )

                broadcast(
                    build_ignition_alert(
                        snapshot,
                        analysis,
                    )
                )

        # ----------------------------------------------------
        # UPDATE EXISTING IGNITION
        # ----------------------------------------------------

        event = update_ignition(
            snapshot
        )

        if event:

            outcomes = event.get(
                "outcomes",
                {}
            )

            # Notify when each milestone
            # is first reached.
            for timeframe in [
                "5m",
                "15m",
                "30m",
            ]:

                outcome = outcomes.get(
                    timeframe
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
        f"Scan complete | "
        f"discovered={len(pairs)} | "
        f"runner_candidates={candidates} | "
        f"new_ignitions={ignitions}"
    )

    save_state()


# ============================================================
# STARTUP
# ============================================================

def print_config():

    print(
        "\n"
        "==================================================\n"
        f"        RUNNER BOT {BOT_VERSION}\n"
        "==================================================\n"
        f"Runner range: "
        f"${MIN_MC / 1000:.0f}K-"
        f"${MAX_MC / 1000:.0f}K\n"
        f"Minimum liquidity: "
        f"${MIN_LIQUIDITY / 1000:.0f}K\n"
        f"5m volume: FEATURE, NOT HARD GATE\n"
        f"Scan interval: "
        f"{SCAN_INTERVAL_SECONDS}s\n"
        f"Discovery refresh: "
        f"{DISCOVERY_INTERVAL_SECONDS}s\n"
        f"Discovery sources:\n"
        f"  • GeckoTerminal top pools\n"
        f"  • GeckoTerminal new pools\n"
        f"  • GeckoTerminal trending pools\n"
        f"Observation range: "
        f"${OBSERVE_MIN_MC / 1000:.0f}K-"
        f"${OBSERVE_MAX_MC / 1000:.0f}K\n"
        f"Activity model: "
        f"1m + 2m acceleration\n"
        f"Ignition score threshold: 7\n"
        f"Telegram alerts: "
        f"{'ON' if HEATING_ALERTS_ENABLED else 'OFF'}\n"
        "==================================================\n"
    )


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
            "HEATING_ALERTS_ENABLED is false."
        )

    print(
        "Runner Engine "
        f"{BOT_VERSION} is online"
    )

    # --------------------------------------------------------
    # Initial discovery
    # --------------------------------------------------------

    try:

        discover_pairs(
            force=True
        )

    except Exception as exc:

        print(
            f"Initial discovery error: "
            f"{exc}"
        )

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

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
                f"Main loop error: "
                f"{exc}"
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


if __name__ == "__main__":
    main()
