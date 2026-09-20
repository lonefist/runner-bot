import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V1
# FRESH IDEAL RUNNER ENGINE
#
# IMPORTANT:
# - NO SEARCH QUERIES
# - NO /latest/dex/search?q=...
# - NO TOKEN-NAME DISCOVERY
#
# Discovery comes from DexScreener's latest token/boost
# feeds, then the bot evaluates the actual token data.
# ============================================================

BOT_VERSION = "V1-NO-SEARCH-IDEAL-RUNNER"


# ============================================================
# API
# ============================================================

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"


# ============================================================
# FILES
# ============================================================

STATE_FILE = "runner_v1_state.json"
HISTORY_FILE = "runner_v1_history.json"


# ============================================================
# TIMING
# ============================================================

SCAN_SECONDS = 20

DISCOVERY_SECONDS = 300

VALIDATION_SECONDS = 300

TRACKING_HOURS = 8.0


# ============================================================
# BROAD DATA BOUNDARIES
#
# These are NOT ideal-runner rules.
# They simply prevent obviously unusable records.
# ============================================================

MIN_MC = 10_000
MAX_MC = 1_000_000

MIN_LIQUIDITY = 5_000


# ============================================================
# RUNNER CLASSIFICATION
# ============================================================

WATCH_SCORE = 55

RUNNER_SCORE = 72

IDEAL_SCORE = 85

MIN_OBSERVATIONS = 2


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()


# ============================================================
# STATE
# ============================================================

STATE = {
    "subscribers": [],
    "tracking": {},
    "last_discovery": 0,
    "last_validation": 0,
    "telegram_offset": 0,
}


HISTORY = {
    "records": []
}


# ============================================================
# LOAD FILES
# ============================================================

def load_json(
    path: str,
    default: Any
) -> Any:

    try:

        if not os.path.exists(path):
            return default

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return default


STATE = load_json(
    STATE_FILE,
    STATE
)

HISTORY = load_json(
    HISTORY_FILE,
    HISTORY
)


# ============================================================
# BASIC HELPERS
# ============================================================

def now_iso() -> str:

    return datetime.now(
        timezone.utc
    ).isoformat()


def unix_now() -> int:

    return int(time.time())


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


def clamp(
    value: float,
    low: float,
    high: float
) -> float:

    return max(
        low,
        min(high, value)
    )


def pct_change(
    old: float,
    new: float
) -> float:

    if old <= 0:
        return 0.0

    return (
        (new - old)
        / old
    ) * 100.0


def save_json(
    path: str,
    data: Any
) -> None:

    temporary = (
        path
        + ".tmp"
    )

    with open(
        temporary,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=2
        )

    os.replace(
        temporary,
        path
    )


# ============================================================
# HTTP
# ============================================================

def http_get(
    url: str,
    timeout: int = 15
) -> Optional[Any]:

    try:

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "RunnerBotV1/1.0"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            body = (
                response
                .read()
                .decode("utf-8")
            )

            if not body:
                return None

            return json.loads(body)

    except Exception as e:

        print(
            f"HTTP ERROR: {e}"
        )

        return None


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_call(
    method: str,
    payload: Dict[str, Any]
) -> Optional[Any]:

    if not TELEGRAM_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_TOKEN}/"
        f"{method}"
    )

    try:

        data = urllib.parse.urlencode(
            payload
        ).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type":
                    "application/"
                    "x-www-form-urlencoded"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:

            body = (
                response
                .read()
                .decode("utf-8")
            )

            return json.loads(body)

    except Exception as e:

        print(
            f"TELEGRAM ERROR: {e}"
        )

        return None


def telegram_message(
    chat_id: str,
    text: str
) -> None:

    telegram_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview":
                "true",
        }
    )


# ============================================================
# DEXSCREENER DISCOVERY
#
# NO SEARCH QUERIES.
#
# We use DexScreener's latest feeds.
# ============================================================

def get_latest_token_profiles() -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}"
        "/token-profiles/latest/v1"
    )

    data = http_get(url)

    if not isinstance(
        data,
        list
    ):

        return []

    return data


def get_latest_boosts() -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}"
        "/token-boosts/latest/v1"
    )

    data = http_get(url)

    if not isinstance(
        data,
        list
    ):

        return []

    return data


def get_top_boosts() -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}"
        "/token-boosts/top/v1"
    )

    data = http_get(url)

    if not isinstance(
        data,
        list
    ):

        return []

    return data


def get_community_takeovers() -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}"
        "/community-takeovers/latest/v1"
    )

    data = http_get(url)

    if not isinstance(
        data,
        list
    ):

        return []

    return data


# ============================================================
# TOKEN ADDRESS EXTRACTION
# ============================================================

def extract_token_addresses(
    records: List[Dict[str, Any]]
) -> List[str]:

    addresses = []

    for record in records:

        if not isinstance(
            record,
            dict
        ):

            continue

        chain = (
            record.get("chainId")
            or ""
        )

        if chain != "solana":
            continue

        address = (
            record.get("tokenAddress")
            or record.get("address")
        )

        if not address:
            continue

        if address not in addresses:

            addresses.append(
                address
            )

    return addresses


# ============================================================
# TOKEN PAIRS
# ============================================================

def get_token_pairs(
    token_address: str
) -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}"
        "/token-pairs/v1/solana/"
        f"{token_address}"
    )

    data = http_get(url)

    if not isinstance(
        data,
        list
    ):

        return []

    return data


# ============================================================
# PAIR AGE
# ============================================================

def pair_age_hours(
    pair: Dict[str, Any]
) -> Optional[float]:

    created = pair.get(
        "pairCreatedAt"
    )

    if created is None:
        return None

    try:

        created_ms = int(
            created
        )

        age_seconds = (
            time.time() * 1000
            - created_ms
        ) / 1000

        return max(
            0.0,
            age_seconds / 3600
        )

    except Exception:

        return None


# ============================================================
# NORMALIZE PAIR
# ============================================================

def normalize_pair(
    pair: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    if not isinstance(
        pair,
        dict
    ):

        return None

    if pair.get(
        "chainId"
    ) != "solana":

        return None

    base = (
        pair.get("baseToken")
        or {}
    )

    token_address = (
        base.get("address")
    )

    if not token_address:
        return None

    liquidity_object = (
        pair.get("liquidity")
        or {}
    )

    volume_object = (
        pair.get("volume")
        or {}
    )

    transactions = (
        pair.get("txns")
        or {}
    )

    m5 = (
        transactions.get("m5")
        or {}
    )

    price_change = (
        pair.get("priceChange")
        or {}
    )

    mc = safe_float(
        pair.get("marketCap")
    )

    if mc <= 0:

        mc = safe_float(
            pair.get("fdv")
        )

    liquidity = safe_float(
        liquidity_object.get("usd")
    )

    volume_5m = safe_float(
        volume_object.get("m5")
    )

    buys_5m = safe_int(
        m5.get("buys")
    )

    sells_5m = safe_int(
        m5.get("sells")
    )

    price_usd = safe_float(
        pair.get("priceUsd")
    )

    price_change_5m = safe_float(
        price_change.get("m5")
    )

    return {

        "token_address":
            token_address,

        "symbol":
            str(
                base.get("symbol")
                or "UNKNOWN"
            ),

        "name":
            str(
                base.get("name")
                or "UNKNOWN"
            ),

        "pair_address":
            pair.get(
                "pairAddress"
            )
            or "",

        "dex":
            pair.get(
                "dexId"
            )
            or "",

        "mc":
            mc,

        "liquidity":
            liquidity,

        "volume_5m":
            volume_5m,

        "buys_5m":
            buys_5m,

        "sells_5m":
            sells_5m,

        "price_usd":
            price_usd,

        "price_change_5m":
            price_change_5m,

        "age_hours":
            pair_age_hours(pair),
    }


# ============================================================
# CHOOSE BEST SOLANA PAIR
# ============================================================

def choose_best_pair(
    pairs: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:

    normalized = []

    for pair in pairs:

        item = normalize_pair(
            pair
        )

        if item is None:
            continue

        if item["mc"] <= 0:
            continue

        if item["liquidity"] <= 0:
            continue

        normalized.append(
            item
        )

    if not normalized:
        return None

    normalized.sort(
        key=lambda x: (
            x["liquidity"],
            x["mc"]
        ),
        reverse=True
    )

    return normalized[0]


# ============================================================
# DISCOVERY
# ============================================================

def discover_candidates() -> List[Dict[str, Any]]:

    print()
    print("=" * 70)
    print("V1 DISCOVERY — NO SEARCH QUERIES")
    print(now_iso())
    print("=" * 70)

    profile_records = (
        get_latest_token_profiles()
    )

    latest_boost_records = (
        get_latest_boosts()
    )

    top_boost_records = (
        get_top_boosts()
    )

    takeover_records = (
        get_community_takeovers()
    )

    all_records = (
        profile_records
        + latest_boost_records
        + top_boost_records
        + takeover_records
    )

    addresses = (
        extract_token_addresses(
            all_records
        )
    )

    print(
        f"Latest profiles: "
        f"{len(profile_records)}"
    )

    print(
        f"Latest boosts: "
        f"{len(latest_boost_records)}"
    )

    print(
        f"Top boosts: "
        f"{len(top_boost_records)}"
    )

    print(
        f"Community takeovers: "
        f"{len(takeover_records)}"
    )

    print(
        f"Unique Solana tokens: "
        f"{len(addresses)}"
    )

    candidates = []

    for address in addresses:

        try:

            pairs = get_token_pairs(
                address
            )

            item = choose_best_pair(
                pairs
            )

            if item is None:
                continue

            if item["mc"] < MIN_MC:
                continue

            if item["mc"] > MAX_MC:
                continue

            if (
                item["liquidity"]
                < MIN_LIQUIDITY
            ):

                continue

            candidates.append(
                item
            )

        except Exception as e:

            print(
                f"Discovery error "
                f"{address}: {e}"
            )

        time.sleep(0.08)

    candidates.sort(
        key=lambda x: (
            x["volume_5m"],
            x["liquidity"]
        ),
        reverse=True
    )

    print(
        f"Broad candidates: "
        f"{len(candidates)}"
    )

    for item in candidates[:50]:

        age = item[
            "age_hours"
        ]

        age_text = (
            f"{age:.1f}h"
            if age is not None
            else "N/A"
        )

        print(
            f"  {item['symbol']:<14}"
            f"MC ${item['mc']:,.0f} | "
            f"Liq ${item['liquidity']:,.0f} | "
            f"5m Vol ${item['volume_5m']:,.0f} | "
            f"Age {age_text}"
        )

    return candidates


# ============================================================
# SNAPSHOT
# ============================================================

def make_snapshot(
    item: Dict[str, Any]
) -> Dict[str, Any]:

    buys = item[
        "buys_5m"
    ]

    sells = item[
        "sells_5m"
    ]

    if sells > 0:

        bs_ratio = (
            buys / sells
        )

    elif buys > 0:

        bs_ratio = float(
            buys
        )

    else:

        bs_ratio = 0.0

    volume_mc_pct = 0.0

    if item["mc"] > 0:

        volume_mc_pct = (
            item["volume_5m"]
            / item["mc"]
        ) * 100

    return {

        "timestamp":
            now_iso(),

        "unix":
            unix_now(),

        "mc":
            item["mc"],

        "liquidity":
            item["liquidity"],

        "age_hours":
            item["age_hours"],

        "price_usd":
            item["price_usd"],

        "price_change_5m":
            item["price_change_5m"],

        "buys_5m":
            item["buys_5m"],

        "sells_5m":
            item["sells_5m"],

        "bs_ratio":
            bs_ratio,

        "volume_5m":
            item["volume_5m"],

        "volume_mc_pct":
            volume_mc_pct,

        "pair_address":
            item["pair_address"],

        "dex":
            item["dex"],

        "symbol":
            item["symbol"],

        "name":
            item["name"],
    }


# ============================================================
# FEATURE CHANGES
# ============================================================

def calculate_features(
    previous: Optional[Dict[str, Any]],
    current: Dict[str, Any]
) -> Dict[str, float]:

    if previous is None:

        return {

            "mc_change": 0.0,

            "liquidity_change": 0.0,

            "price_change_delta": 0.0,

            "volume_change": 0.0,

            "buys_change": 0.0,

            "sells_change": 0.0,

            "bs_change": 0.0,

            "volume_mc_change": 0.0,
        }

    return {

        "mc_change":
            pct_change(
                previous["mc"],
                current["mc"]
            ),

        "liquidity_change":
            pct_change(
                previous["liquidity"],
                current["liquidity"]
            ),

        "price_change_delta":
            current[
                "price_change_5m"
            ]
            -
            previous[
                "price_change_5m"
            ],

        "volume_change":
            pct_change(
                previous["volume_5m"],
                current["volume_5m"]
            ),

        "buys_change":
            pct_change(
                previous["buys_5m"],
                current["buys_5m"]
            )
            if previous["buys_5m"] > 0
            else 0.0,

        "sells_change":
            pct_change(
                previous["sells_5m"],
                current["sells_5m"]
            )
            if previous["sells_5m"] > 0
            else 0.0,

        "bs_change":
            current["bs_ratio"]
            -
            previous["bs_ratio"],

        "volume_mc_change":
            current["volume_mc_pct"]
            -
            previous["volume_mc_pct"],
    }


# ============================================================
# IDEAL RUNNER SCORE
# ============================================================

def score_runner(
    snapshots: List[Dict[str, Any]]
) -> Dict[str, Any]:

    if not snapshots:

        return {

            "score": 0,

            "classification":
                "UNKNOWN",

            "structure": 0,

            "flow": 0,

            "volume": 0,

            "price": 0,

            "liquidity": 0,

            "persistence": 0,
        }

    current = snapshots[-1]

    previous = (
        snapshots[-2]
        if len(snapshots) >= 2
        else None
    )


    # ========================================================
    # STRUCTURE
    # ========================================================

    structure = 0.0

    mc = current["mc"]

    liquidity = current[
        "liquidity"
    ]

    if mc > 0:

        liquidity_ratio = (
            liquidity / mc
        )

        if liquidity_ratio >= 0.20:
            structure = 20

        elif liquidity_ratio >= 0.12:
            structure = 16

        elif liquidity_ratio >= 0.08:
            structure = 12

        elif liquidity_ratio >= 0.05:
            structure = 8

        elif liquidity_ratio >= 0.03:
            structure = 4


    # ========================================================
    # FLOW
    # ========================================================

    flow = 0.0

    buys = current[
        "buys_5m"
    ]

    sells = current[
        "sells_5m"
    ]

    bs = current[
        "bs_ratio"
    ]

    if bs >= 3.0:
        flow += 25

    elif bs >= 2.0:
        flow += 22

    elif bs >= 1.5:
        flow += 18

    elif bs >= 1.2:
        flow += 12

    elif bs >= 1.0:
        flow += 7

    if buys > sells:
        flow += 3

    if previous:

        if (
            current["bs_ratio"]
            >
            previous["bs_ratio"]
        ):

            flow += 2

        if (
            current["buys_5m"]
            >
            previous["buys_5m"]
        ):

            flow += 2

    flow = clamp(
        flow,
        0,
        30
    )


    # ========================================================
    # VOLUME
    # ========================================================

    volume = 0.0

    volume_mc = current[
        "volume_mc_pct"
    ]

    if volume_mc >= 30:
        volume += 20

    elif volume_mc >= 20:
        volume += 17

    elif volume_mc >= 12:
        volume += 14

    elif volume_mc >= 7:
        volume += 10

    elif volume_mc >= 3:
        volume += 6

    if previous:

        if (
            current["volume_5m"]
            >
            previous["volume_5m"]
        ):

            volume += 5

        if (
            current["volume_mc_pct"]
            >
            previous["volume_mc_pct"]
        ):

            volume += 3

    volume = clamp(
        volume,
        0,
        25
    )


    # ========================================================
    # PRICE
    # ========================================================

    price = 0.0

    price_change = current[
        "price_change_5m"
    ]

    if (
        3
        <= price_change
        <= 25
    ):

        price += 15

    elif (
        0
        < price_change
        < 3
    ):

        price += 9

    elif (
        25
        < price_change
        <= 50
    ):

        price += 10

    elif price_change > 50:

        price += 3

    if previous:

        if price_change > 0:

            price += 3

        if (
            current["mc"]
            >
            previous["mc"]
        ):

            price += 2

    price = clamp(
        price,
        0,
        20
    )


    # ========================================================
    # LIQUIDITY MOMENTUM
    # ========================================================

    liquidity_score = 0.0

    if previous:

        liquidity_change = pct_change(
            previous["liquidity"],
            current["liquidity"]
        )

        if liquidity_change >= 5:
            liquidity_score = 10

        elif liquidity_change >= 1:
            liquidity_score = 8

        elif liquidity_change >= -2:
            liquidity_score = 6

        elif liquidity_change >= -5:
            liquidity_score = 3

        else:
            liquidity_score = 0

    else:

        liquidity_score = 6


    # ========================================================
    # PERSISTENCE
    # ========================================================

    persistence = 0.0

    if len(snapshots) >= 2:

        aligned = 0

        for snap in snapshots[-3:]:

            local_bs = snap[
                "bs_ratio"
            ]

            local_price = snap[
                "price_change_5m"
            ]

            local_volume = snap[
                "volume_mc_pct"
            ]

            signals = 0

            if local_bs > 1:
                signals += 1

            if local_price > 0:
                signals += 1

            if local_volume > 0:
                signals += 1

            if signals >= 2:
                aligned += 1

        if aligned >= 3:
            persistence = 10

        elif aligned >= 2:
            persistence = 7

        elif aligned >= 1:
            persistence = 4


    # ========================================================
    # FINAL SCORE
    # ========================================================

    total = (
        structure
        + flow
        + volume
        + price
        + liquidity_score
        + persistence
    )

    total = clamp(
        total,
        0,
        100
    )

    if len(snapshots) < MIN_OBSERVATIONS:

        classification = "WATCH"

    elif total >= IDEAL_SCORE:

        classification = (
            "IDEAL RUNNER"
        )

    elif total >= RUNNER_SCORE:

        classification = "RUNNER"

    elif total >= WATCH_SCORE:

        classification = "WATCH"

    else:

        classification = "OBSERVE"


    return {

        "score":
            round(total, 1),

        "classification":
            classification,

        "structure":
            round(structure, 1),

        "flow":
            round(flow, 1),

        "volume":
            round(volume, 1),

        "price":
            round(price, 1),

        "liquidity":
            round(
                liquidity_score,
                1
            ),

        "persistence":
            round(
                persistence,
                1
            ),
    }


# ============================================================
# HISTORY
# ============================================================

def create_history_record(
    item: Dict[str, Any]
) -> Dict[str, Any]:

    return {

        "token_address":
            item["token_address"],

        "symbol":
            item["symbol"],

        "name":
            item["name"],

        "discovered_at":
            now_iso(),

        "discovery":
            make_snapshot(item),

        "snapshots": [],

        "alerts": [],

        "outcome": {

            "status":
                "TRACKING",

            "classification":
                None,

            "max_mc":
                None,

            "max_mc_multiple":
                None,

            "min_liquidity":
                None,

            "liquidity_ratio":
                None,
        },
    }


def get_history_record(
    token_address: str
) -> Optional[Dict[str, Any]]:

    for record in HISTORY[
        "records"
    ]:

        if (
            record[
                "token_address"
            ]
            ==
            token_address
        ):

            return record

    return None


def append_history_snapshot(
    token_address: str,
    snapshot: Dict[str, Any]
) -> None:

    record = get_history_record(
        token_address
    )

    if record is None:
        return

    record[
        "snapshots"
    ].append(
        snapshot
    )


# ============================================================
# TRACK NEW CANDIDATES
# ============================================================

def track_new_candidates(
    candidates: List[Dict[str, Any]]
) -> None:

    added = 0

    for item in candidates:

        address = item[
            "token_address"
        ]

        if address in STATE[
            "tracking"
        ]:

            continue

        STATE[
            "tracking"
        ][address] = {

            "token_address":
                address,

            "symbol":
                item["symbol"],

            "name":
                item["name"],

            "started_tracking":
                time.time(),

            "snapshots": [],

            "latest_score":
                None,

            "latest_item":
                item,

            "runner_alerted":
                False,

            "ideal_alerted":
                False,
        }

        HISTORY[
            "records"
        ].append(
            create_history_record(
                item
            )
        )

        added += 1

    print(
        f"NEW TRACKING: {added}"
    )


# ============================================================
# ALERT FORMAT
# ============================================================

def format_alert(
    item: Dict[str, Any],
    score: Dict[str, Any],
    snapshots: List[Dict[str, Any]]
) -> str:

    current = snapshots[-1]

    age = current[
        "age_hours"
    ]

    age_text = (
        f"{age:.1f}h"
        if age is not None
        else "N/A"
    )

    return f"""
🔥 {score["classification"]}

{item["symbol"]}

MC: ${current["mc"]:,.0f}
Liquidity: ${current["liquidity"]:,.0f}
Age: {age_text}

BUYING FLOW
Buys: {current["buys_5m"]}
Sells: {current["sells_5m"]}
B/S: {current["bs_ratio"]:.2f}

MOMENTUM
5m Price: {current["price_change_5m"]:+.2f}%
5m Volume: ${current["volume_5m"]:,.0f}
Volume/MC: {current["volume_mc_pct"]:.2f}%

RUNNER PROFILE
Score: {score["score"]:.1f}/100

Structure: {score["structure"]:.1f}
Flow: {score["flow"]:.1f}
Volume: {score["volume"]:.1f}
Price: {score["price"]:.1f}
Liquidity: {score["liquidity"]:.1f}
Persistence: {score["persistence"]:.1f}

Observations: {len(snapshots)}

DEX:
https://dexscreener.com/solana/{current["pair_address"]}
""".strip()


# ============================================================
# SEND ALERT
# ============================================================

def send_alert(
    message: str
) -> None:

    for chat_id in STATE[
        "subscribers"
    ]:

        telegram_message(
            chat_id,
            message
        )


# ============================================================
# VALIDATE ONE TOKEN
# ============================================================

def validate_candidate(
    token_address: str
) -> None:

    tracking = STATE[
        "tracking"
    ].get(
        token_address
    )

    if not tracking:
        return

    pairs = get_token_pairs(
        token_address
    )

    item = choose_best_pair(
        pairs
    )

    if item is None:
        return

    snapshot = make_snapshot(
        item
    )

    snapshots = tracking[
        "snapshots"
    ]

    previous = (
        snapshots[-1]
        if snapshots
        else None
    )

    snapshot[
        "features"
    ] = calculate_features(
        previous,
        snapshot
    )

    snapshots.append(
        snapshot
    )

    score = score_runner(
        snapshots
    )

    tracking[
        "latest_score"
    ] = score

    tracking[
        "latest_item"
    ] = item

    append_history_snapshot(
        token_address,
        snapshot
    )

    already_ideal = tracking[
        "ideal_alerted"
    ]

    already_runner = tracking[
        "runner_alerted"
    ]

    alert_type = None

    if (
        score["classification"]
        ==
        "IDEAL RUNNER"
        and not already_ideal
    ):

        alert_type = (
            "IDEAL RUNNER"
        )

        tracking[
            "ideal_alerted"
        ] = True

    elif (
        score["classification"]
        ==
        "RUNNER"
        and not already_runner
    ):

        alert_type = (
            "RUNNER"
        )

        tracking[
            "runner_alerted"
        ] = True

    if alert_type:

        message = format_alert(
            item,
            score,
            snapshots
        )

        send_alert(
            message
        )

        record = get_history_record(
            token_address
        )

        if record:

            record[
                "alerts"
            ].append({

                "timestamp":
                    now_iso(),

                "type":
                    alert_type,

                "score":
                    score,

                "snapshot":
                    snapshot,
            })

        print()
        print(
            "=" * 70
        )

        print(
            f"🔥 ALERT: "
            f"{alert_type}"
        )

        print(
            f"{item['symbol']} | "
            f"Score "
            f"{score['score']:.1f}/100"
        )

        print(
            "=" * 70
        )

    else:

        print(
            f"VALIDATION | "
            f"{item['symbol']} | "
            f"{score['classification']} | "
            f"{score['score']:.1f}/100 | "
            f"B/S "
            f"{snapshot['bs_ratio']:.2f} | "
            f"5m "
            f"{snapshot['price_change_5m']:+.2f}% | "
            f"Vol/MC "
            f"{snapshot['volume_mc_pct']:.2f}%"
        )


# ============================================================
# OUTCOME
# ============================================================

def finalize_outcome(
    token_address: str,
    tracking: Dict[str, Any]
) -> None:

    snapshots = tracking[
        "snapshots"
    ]

    record = get_history_record(
        token_address
    )

    if record is None:
        return

    if not snapshots:

        record[
            "outcome"
        ]["status"] = (
            "NO_DATA"
        )

        return

    alerts = record.get(
        "alerts",
        []
    )

    if not alerts:

        record[
            "outcome"
        ]["status"] = (
            "NO_ALERT"
        )

        return

    alert_snapshot = alerts[
        0
    ].get(
        "snapshot"
    )

    if not alert_snapshot:
        return

    alert_mc = alert_snapshot[
        "mc"
    ]

    alert_liquidity = (
        alert_snapshot[
            "liquidity"
        ]
    )

    max_mc = max(
        s["mc"]
        for s in snapshots
    )

    min_liquidity = min(
        s["liquidity"]
        for s in snapshots
    )

    mc_multiple = (
        max_mc / alert_mc
        if alert_mc > 0
        else 0
    )

    liquidity_ratio = (
        min_liquidity
        / alert_liquidity
        if alert_liquidity > 0
        else 0
    )

    if (
        mc_multiple >= 1.50
        and liquidity_ratio >= 0.70
    ):

        outcome = (
            "CONTINUATION"
        )

    elif (
        mc_multiple < 0.80
        or liquidity_ratio < 0.50
    ):

        outcome = (
            "FAILURE"
        )

    else:

        outcome = "MIXED"

    record[
        "outcome"
    ] = {

        "status":
            "COMPLETE",

        "classification":
            outcome,

        "max_mc":
            max_mc,

        "max_mc_multiple":
            mc_multiple,

        "min_liquidity":
            min_liquidity,

        "liquidity_ratio":
            liquidity_ratio,

        "completed_at":
            now_iso(),
    }

    print(
        f"OUTCOME | "
        f"{tracking['symbol']} | "
        f"{outcome} | "
        f"MC x{mc_multiple:.2f} | "
        f"Liq {liquidity_ratio:.2f}"
    )


# ============================================================
# REMOVE OLD TRACKING
# ============================================================

def prune_tracking() -> None:

    current_time = time.time()

    remove = []

    for (
        address,
        tracking
    ) in STATE[
        "tracking"
    ].items():

        elapsed_hours = (
            current_time
            -
            tracking[
                "started_tracking"
            ]
        ) / 3600

        if (
            elapsed_hours
            >= TRACKING_HOURS
        ):

            finalize_outcome(
                address,
                tracking
            )

            remove.append(
                address
            )

    for address in remove:

        del STATE[
            "tracking"
        ][address]


# ============================================================
# TELEGRAM UPDATES
# ============================================================

def process_updates() -> None:

    if not TELEGRAM_TOKEN:
        return

    offset = STATE.get(
        "telegram_offset",
        0
    )

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_TOKEN}/getUpdates"
        f"?timeout=1"
        f"&offset={offset}"
    )

    data = http_get(
        url,
        timeout=5
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

            STATE[
                "telegram_offset"
            ] = (
                update_id + 1
            )

        message = update.get(
            "message"
        )

        if not message:
            continue

        chat = (
            message.get("chat")
            or {}
        )

        chat_id = str(
            chat.get("id")
        )

        text = (
            message.get("text")
            or ""
        ).strip()

        if not chat_id:
            continue

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        if text.startswith(
            "/start"
        ):

            if (
                chat_id
                not in STATE[
                    "subscribers"
                ]
            ):

                STATE[
                    "subscribers"
                ].append(
                    chat_id
                )

            telegram_message(
                chat_id,
                "🔥 Runner Bot V1 "
                "is online.\n\n"
                "No search queries are "
                "being used.\n\n"
                "I am monitoring Solana "
                "tokens through live "
                "DexScreener discovery "
                "feeds and evaluating "
                "flow, volume, price, "
                "liquidity and "
                "persistence."
            )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        elif text.startswith(
            "/status"
        ):

            tracking_count = len(
                STATE[
                    "tracking"
                ]
            )

            history_count = len(
                HISTORY[
                    "records"
                ]
            )

            telegram_message(
                chat_id,
                "RUNNER BOT V1\n\n"
                f"Tracking: "
                f"{tracking_count}\n"
                f"History: "
                f"{history_count}\n"
                f"Subscribers: "
                f"{len(STATE['subscribers'])}"
            )

        # ----------------------------------------------------
        # HISTORY
        # ----------------------------------------------------

        elif text.startswith(
            "/history"
        ):

            total = len(
                HISTORY[
                    "records"
                ]
            )

            alerts = sum(
                len(
                    record.get(
                        "alerts",
                        []
                    )
                )
                for record
                in HISTORY[
                    "records"
                ]
            )

            telegram_message(
                chat_id,
                "RUNNER BOT V1 HISTORY\n\n"
                f"Records: {total}\n"
                f"Alerts: {alerts}"
            )

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        elif text.startswith(
            "/stop"
        ):

            if (
                chat_id
                in STATE[
                    "subscribers"
                ]
            ):

                STATE[
                    "subscribers"
                ].remove(
                    chat_id
                )

            telegram_message(
                chat_id,
                "Runner alerts disabled."
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "RUNNER BOT V1"
    )

    print(
        "FRESH IDEAL RUNNER ENGINE"
    )

    print(
        "NO SEARCH QUERIES"
    )

    print("=" * 70)

    print(
        f"MC research range: "
        f"${MIN_MC:,} - "
        f"${MAX_MC:,}"
    )

    print(
        f"Minimum liquidity: "
        f"${MIN_LIQUIDITY:,}"
    )

    print(
        f"Watch score: "
        f"{WATCH_SCORE}"
    )

    print(
        f"Runner score: "
        f"{RUNNER_SCORE}"
    )

    print(
        f"Ideal runner score: "
        f"{IDEAL_SCORE}"
    )

    print(
        "Discovery: "
        "DexScreener live feeds"
    )

    print(
        "Search queries: NONE"
    )

    print(
        "Telegram: "
        + (
            "connected"
            if TELEGRAM_TOKEN
            else
            "NOT CONNECTED"
        )
    )

    print("=" * 70)

    while True:

        try:

            current_time = time.time()

            # ------------------------------------------------
            # TELEGRAM
            # ------------------------------------------------

            process_updates()

            # ------------------------------------------------
            # DISCOVERY
            # ------------------------------------------------

            if (
                current_time
                -
                STATE[
                    "last_discovery"
                ]
                >=
                DISCOVERY_SECONDS
            ):

                candidates = (
                    discover_candidates()
                )

                track_new_candidates(
                    candidates
                )

                STATE[
                    "last_discovery"
                ] = current_time

                save_json(
                    STATE_FILE,
                    STATE
                )

                save_json(
                    HISTORY_FILE,
                    HISTORY
                )

            # ------------------------------------------------
            # VALIDATION
            # ------------------------------------------------

            if (
                current_time
                -
                STATE[
                    "last_validation"
                ]
                >=
                VALIDATION_SECONDS
            ):

                addresses = list(
                    STATE[
                        "tracking"
                    ].keys()
                )

                print()
                print(
                    f"VALIDATING "
                    f"{len(addresses)} "
                    f"candidates..."
                )

                for address in addresses:

                    try:

                        validate_candidate(
                            address
                        )

                    except Exception as e:

                        print(
                            f"VALIDATION ERROR "
                            f"{address}: "
                            f"{e}"
                        )

                    time.sleep(
                        0.15
                    )

                STATE[
                    "last_validation"
                ] = current_time

                save_json(
                    STATE_FILE,
                    STATE
                )

                save_json(
                    HISTORY_FILE,
                    HISTORY
                )

            # ------------------------------------------------
            # CLEANUP
            # ------------------------------------------------

            prune_tracking()

            save_json(
                STATE_FILE,
                STATE
            )

            save_json(
                HISTORY_FILE,
                HISTORY
            )

            time.sleep(
                SCAN_SECONDS
            )

        except KeyboardInterrupt:

            print()
            print(
                "Runner Bot V1 stopped."
            )

            save_json(
                STATE_FILE,
                STATE
            )

            save_json(
                HISTORY_FILE,
                HISTORY
            )

            break

        except Exception as e:

            print(
                f"MAIN LOOP ERROR: "
                f"{e}"
            )

            time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
