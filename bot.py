import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V4.4 - FLOW PRESSURE
# ============================================================

BOT_VERSION = "V4.4-FLOW-PRESSURE"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

# Keep this filename so existing history is preserved.
STATE_FILE = "runner_state_v42.json"


# ============================================================
# ENVIRONMENT
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

ALERTS_ENABLED = (
    os.getenv(
        "HEATING_ALERTS_ENABLED",
        "true"
    ).lower()
    in ("1", "true", "yes", "on")
)

# FIX:
# GitHub Actions provides SCAN_INTERVAL_SECONDS as an
# environment variable. Python must explicitly read it.
SCAN_INTERVAL_SECONDS = int(
    os.getenv(
        "SCAN_INTERVAL_SECONDS",
        "15"
    )
)


# ============================================================
# CORE RUNNER FILTERS
# ============================================================

IDEAL_MIN_MC = 5_000
IDEAL_MAX_MC = 80_000

MAX_MC = 150_000

OBSERVE_MIN_MC = 5_000
OBSERVE_MAX_MC = 175_000

MIN_LIQUIDITY = 5_000
PREFERRED_LIQUIDITY = 8_000

MAX_LIQUIDITY_MC_RATIO = 0.90

MAX_PAIR_AGE_HOURS = 48


# ============================================================
# FLOW PRESSURE PROXY
# ============================================================

# IMPORTANT:
#
# DEX Screener does not provide true dollar net-flow data
# in the endpoint used by this bot.
#
# Therefore:
#
# Flow Proxy =
# 5m Volume × ((Buys - Sells) / Total Transactions)
#
# This is a BUY-PRESSURE PROXY.
#
# It is NOT true dollar net flow.
#
# Real dollar net flow would require a trade-level provider
# such as Birdeye/Bitquery/etc.
# ============================================================

MIN_FLOW_PROXY_USD = 2_000

FLOW_MC_WATCH_PCT = 5.0
FLOW_MC_QUALIFY_PCT = 8.0
FLOW_MC_STRONG_PCT = 15.0
FLOW_MC_HOT_PCT = 20.0

MIN_VOLUME_TO_FLOW_RATIO = 1.5

FLOW_LOOKBACK_SECONDS = 60
FLOW_ACCELERATION_PCT = 20.0


# ============================================================
# VOLUME / ACTIVITY
# ============================================================

MIN_VOLUME_5M = 2_000

ACTIVITY_EXPANSION_PCT = 20.0
ACTIVITY_LOOKBACK_SECONDS = 60

MIN_TX_5M = 8


# ============================================================
# STRUCTURE
# ============================================================

MAX_CONSOLIDATION_RANGE_PCT = 18.0

MIN_OBSERVATIONS = 6

MAX_STORED_TOKENS = 1500
MAX_HISTORY_PER_TOKEN = 300


# ============================================================
# DISCOVERY
# ============================================================

DISCOVERY_INTERVAL_SECONDS = 60
MAX_DISCOVERY_TOKENS = 500

DEX_BATCH_SIZE = 25


# ============================================================
# CONFIRMATION
# ============================================================

CONFIRMATION_REQUIRED_SCANS = 2

MAX_IGNITION_DRAWDOWN_PCT = -5.0

CONFIRMATION_MC_GROWTH_PCT = 5.0

PENDING_EXPIRY_SECONDS = 5 * 60


# ============================================================
# ALERT CONTROL
# ============================================================

ALERT_COOLDOWN_SECONDS = 10 * 60

GLOBAL_ALERT_COOLDOWN_SECONDS = 60


# ============================================================
# OUTCOME TRACKING
# ============================================================

OUTCOME_WINDOWS_SECONDS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "60m": 60 * 60,
}


# ============================================================
# HTTP
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; RunnerBot/4.4; +https://dexscreener.com)"
)


def http_get_json(
    url: str,
    retries: int = 3,
    timeout: int = 15
) -> Optional[Any]:

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    for attempt in range(retries):

        try:

            req = urllib.request.Request(
                url,
                headers=headers,
                method="GET",
            )

            with urllib.request.urlopen(
                req,
                timeout=timeout
            ) as response:

                raw = response.read().decode(
                    "utf-8"
                )

                return json.loads(raw)

        except Exception as exc:

            if attempt == retries - 1:

                print(
                    f"[HTTP ERROR] {url} | {exc}",
                    flush=True
                )

            time.sleep(1 + attempt)

    return None


# ============================================================
# STATE
# ============================================================

def default_state() -> Dict[str, Any]:

    return {
        "version": BOT_VERSION,
        "tokens": {},
        "alerts": {},
        "pending_ignitions": {},
        "subscribers": [],
        "last_discovery": 0,
        "discovered_tokens": [],
        "telegram_offset": 0,
        "last_alert_time": 0,
    }


def load_state() -> Dict[str, Any]:

    if not os.path.exists(STATE_FILE):
        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        if not isinstance(state, dict):
            return default_state()

        defaults = default_state()

        for key, value in defaults.items():
            state.setdefault(key, value)

        return state

    except Exception as exc:

        print(
            f"[STATE ERROR] Could not load state: {exc}",
            flush=True
        )

        return default_state()


state = load_state()


def save_state() -> None:

    tmp_file = STATE_FILE + ".tmp"

    try:

        with open(
            tmp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                indent=2
            )

        os.replace(
            tmp_file,
            STATE_FILE
        )

    except Exception as exc:

        print(
            f"[STATE ERROR] Could not save state: {exc}",
            flush=True
        )


# ============================================================
# TIME
# ============================================================

def now_ts() -> float:
    return time.time()


def iso_now() -> str:

    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# NUMBER HELPERS
# ============================================================

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


def pct_change(
    old: float,
    new: float
) -> float:

    if old <= 0:
        return 0.0

    return (
        (new - old)
        / old
        * 100.0
    )


def money(
    value: float
) -> str:

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def ratio_text(
    value: float
) -> str:

    if value <= 0:
        return "0.00"

    if value >= 100:
        return f"{value:.0f}"

    return f"{value:.2f}"


# ============================================================
# DISCOVERY
# ============================================================

DISCOVERY_ENDPOINTS = [
    "/token-profiles/latest/v1",
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
]


def discover_tokens() -> List[str]:

    found = set()

    for endpoint in DISCOVERY_ENDPOINTS:

        data = http_get_json(
            DEX_BASE + endpoint
        )

        if not isinstance(data, list):
            continue

        for item in data:

            if not isinstance(item, dict):
                continue

            chain_id = str(
                item.get(
                    "chainId",
                    ""
                )
            ).lower()

            if chain_id != "solana":
                continue

            address = (
                item.get("tokenAddress")
                or item.get("address")
            )

            if address:
                found.add(address)

            if len(found) >= MAX_DISCOVERY_TOKENS:
                break

    return list(found)[:MAX_DISCOVERY_TOKENS]


# ============================================================
# DEXSCREENER TOKEN PAIRS
# ============================================================

def get_token_pairs_batch(
    token_addresses: List[str]
) -> List[Dict[str, Any]]:

    all_pairs = []

    for start in range(
        0,
        len(token_addresses),
        DEX_BATCH_SIZE
    ):

        batch = token_addresses[
            start:start + DEX_BATCH_SIZE
        ]

        if not batch:
            continue

        joined = ",".join(batch)

        url = (
            f"{DEX_BASE}"
            f"/latest/dex/tokens/"
            f"{urllib.parse.quote(joined, safe=',')}"
        )

        data = http_get_json(url)

        if not isinstance(data, dict):
            continue

        pairs = data.get(
            "pairs",
            []
        )

        if not isinstance(pairs, list):
            continue

        for pair in pairs:

            if not isinstance(pair, dict):
                continue

            if str(
                pair.get(
                    "chainId",
                    ""
                )
            ).lower() != "solana":
                continue

            all_pairs.append(pair)

    return all_pairs


# ============================================================
# BEST PAIR SELECTION
# ============================================================

def choose_best_pairs(
    pairs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:

    best = {}

    for pair in pairs:

        base = pair.get(
            "baseToken"
        ) or {}

        address = base.get(
            "address"
        )

        if not address:
            continue

        liquidity = safe_float(
            (
                pair.get("liquidity")
                or {}
            ).get("usd")
        )

        current = best.get(address)

        if current is None:

            best[address] = pair
            continue

        current_liq = safe_float(
            (
                current.get("liquidity")
                or {}
            ).get("usd")
        )

        if liquidity > current_liq:
            best[address] = pair

    return list(best.values())


# ============================================================
# PAIR -> SNAPSHOT
# ============================================================

def pair_to_snapshot(
    pair: Dict[str, Any]
) -> Dict[str, Any]:

    base = pair.get(
        "baseToken"
    ) or {}

    address = str(
        base.get(
            "address",
            ""
        )
    )

    symbol = str(
        base.get(
            "symbol",
            "?"
        )
    )

    name = str(
        base.get(
            "name",
            symbol
        )
    )

    liquidity = safe_float(
        (
            pair.get("liquidity")
            or {}
        ).get("usd")
    )

    volume = pair.get(
        "volume"
    ) or {}

    volume_5m = safe_float(
        volume.get("m5")
    )

    volume_1h = safe_float(
        volume.get("h1")
    )

    txns = pair.get(
        "txns"
    ) or {}

    tx_5m = txns.get(
        "m5"
    ) or {}

    buys = safe_int(
        tx_5m.get("buys")
    )

    sells = safe_int(
        tx_5m.get("sells")
    )

    total_tx = buys + sells

    buy_pct = (
        buys / total_tx * 100
        if total_tx > 0
        else 0.0
    )

    sell_pct = (
        sells / total_tx * 100
        if total_tx > 0
        else 0.0
    )

    if sells > 0:
        buy_sell_ratio = buys / sells
    elif buys > 0:
        buy_sell_ratio = float(buys)
    else:
        buy_sell_ratio = 0.0

    # ========================================================
    # FLOW PRESSURE PROXY
    # ========================================================

    if total_tx > 0:

        imbalance = (
            buys - sells
        ) / total_tx

    else:

        imbalance = 0.0

    flow_proxy_5m = (
        volume_5m * imbalance
    )

    market_cap = safe_float(
        pair.get("marketCap")
    )

    fdv = safe_float(
        pair.get("fdv")
    )

    if market_cap <= 0:
        market_cap = fdv

    flow_pressure_pct = (
        flow_proxy_5m
        / market_cap
        * 100
        if market_cap > 0
        else 0.0
    )

    volume_flow_ratio = (
        volume_5m
        / abs(flow_proxy_5m)
        if abs(flow_proxy_5m) > 0
        else 0.0
    )

    price = safe_float(
        pair.get("priceUsd")
    )

    price_change = pair.get(
        "priceChange"
    ) or {}

    change_5m = safe_float(
        price_change.get("m5")
    )

    change_1h = safe_float(
        price_change.get("h1")
    )

    liquidity_mc_ratio = (
        liquidity / market_cap
        if market_cap > 0
        else 0.0
    )

    created_ms = safe_float(
        pair.get("pairCreatedAt")
    )

    if created_ms > 0:

        age_hours = (
            max(
                0,
                time.time() * 1000 - created_ms
            )
            / 1000
            / 3600
        )

    else:

        age_hours = 999999.0

    return {

        "timestamp": now_ts(),
        "timestamp_iso": iso_now(),

        "address": address,
        "symbol": symbol,
        "name": name,

        "mc": market_cap,
        "fdv": fdv,

        "liquidity": liquidity,

        "liquidity_mc_ratio": (
            liquidity_mc_ratio
        ),

        "volume_5m": volume_5m,
        "volume_1h": volume_1h,

        "buys": buys,
        "sells": sells,
        "tx_5m": total_tx,

        "buy_pct": buy_pct,
        "sell_pct": sell_pct,
        "buy_sell_ratio": buy_sell_ratio,

        "price": price,

        "change_5m": change_5m,
        "change_1h": change_1h,

        "flow_proxy_5m": flow_proxy_5m,
        "flow_pressure_pct": flow_pressure_pct,

        "volume_flow_ratio": volume_flow_ratio,

        "age_hours": age_hours,

        "pair_url": pair.get(
            "url",
            ""
        ),
    }


# ============================================================
# HISTORY
# ============================================================

def get_token_state(
    address: str
) -> Dict[str, Any]:

    tokens = state["tokens"]

    if address not in tokens:

        tokens[address] = {
            "symbol": "",
            "history": [],
        }

    return tokens[address]


def record_snapshot(
    snapshot: Dict[str, Any]
) -> None:

    address = snapshot["address"]

    token = get_token_state(address)

    token["symbol"] = snapshot["symbol"]

    history = token.setdefault(
        "history",
        []
    )

    history.append(snapshot)

    if len(history) > MAX_HISTORY_PER_TOKEN:

        del history[
            :-MAX_HISTORY_PER_TOKEN
        ]

    if len(state["tokens"]) > MAX_STORED_TOKENS:

        oldest_address = None
        oldest_time = float("inf")

        for addr, item in state["tokens"].items():

            history_item = item.get(
                "history",
                []
            )

            if not history_item:
                continue

            timestamp = safe_float(
                history_item[-1].get(
                    "timestamp"
                )
            )

            if timestamp < oldest_time:

                oldest_time = timestamp
                oldest_address = addr

        if oldest_address:

            del state["tokens"][
                oldest_address
            ]


# ============================================================
# STRUCTURE
# ============================================================

def calculate_structure(
    history: List[Dict[str, Any]]
) -> Dict[str, Any]:

    if len(history) < MIN_OBSERVATIONS:

        return {
            "breakout": False,
            "consolidation": False,
            "range_pct": 0.0,
            "higher_high": False,
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
            "range_pct": 0.0,
            "higher_high": False,
        }

    current = prices[-1]

    previous = prices[:-1]

    previous_max = max(previous)
    previous_min = min(previous)

    breakout = current > previous_max
    higher_high = current > previous[-1]

    lowest = min(prices)
    highest = max(prices)

    range_pct = (
        (highest - lowest)
        / lowest
        * 100
        if lowest > 0
        else 0.0
    )

    consolidation = (
        range_pct
        <= MAX_CONSOLIDATION_RANGE_PCT
    )

    return {
        "breakout": breakout,
        "consolidation": consolidation,
        "range_pct": range_pct,
        "higher_high": higher_high,
        "previous_max": previous_max,
        "previous_min": previous_min,
    }


# ============================================================
# LOOKBACK
# ============================================================

def find_previous_snapshot(
    history: List[Dict[str, Any]],
    seconds_back: int
) -> Optional[Dict[str, Any]]:

    if len(history) < 2:
        return None

    current_time = safe_float(
        history[-1].get("timestamp")
    )

    target_time = (
        current_time - seconds_back
    )

    for item in reversed(
        history[:-1]
    ):

        item_time = safe_float(
            item.get("timestamp")
        )

        if item_time <= target_time:
            return item

    return None


# ============================================================
# ACTIVITY EXPANSION
# ============================================================

def activity_expanding(
    history: List[Dict[str, Any]]
) -> bool:

    previous = find_previous_snapshot(
        history,
        ACTIVITY_LOOKBACK_SECONDS
    )

    if previous is None:
        return False

    current = history[-1]

    old_volume = safe_float(
        previous.get("volume_5m")
    )

    old_tx = safe_float(
        previous.get("tx_5m")
    )

    new_volume = safe_float(
        current.get("volume_5m")
    )

    new_tx = safe_float(
        current.get("tx_5m")
    )

    volume_growth = (
        (new_volume - old_volume)
        / old_volume
        * 100
        if old_volume > 0
        else 0.0
    )

    tx_growth = (
        (new_tx - old_tx)
        / old_tx
        * 100
        if old_tx > 0
        else 0.0
    )

    return (
        volume_growth >= ACTIVITY_EXPANSION_PCT
        or
        tx_growth >= ACTIVITY_EXPANSION_PCT
    )


# ============================================================
# FLOW ACCELERATION
# ============================================================

def flow_accelerating(
    history: List[Dict[str, Any]]
) -> bool:

    previous = find_previous_snapshot(
        history,
        FLOW_LOOKBACK_SECONDS
    )

    if previous is None:
        return False

    current = history[-1]

    old_flow = safe_float(
        previous.get("flow_proxy_5m")
    )

    new_flow = safe_float(
        current.get("flow_proxy_5m")
    )

    if old_flow <= 0:

        return new_flow >= MIN_FLOW_PROXY_USD

    growth = (
        (new_flow - old_flow)
        / old_flow
        * 100
    )

    return growth >= FLOW_ACCELERATION_PCT


# ============================================================
# SAFETY / QUALITY
# ============================================================

def safety_checks(
    snapshot: Dict[str, Any]
) -> Dict[str, Any]:

    liquidity = snapshot["liquidity"]
    mc = snapshot["mc"]
    age = snapshot["age_hours"]
    liquidity_ratio = snapshot[
        "liquidity_mc_ratio"
    ]

    checks = {

        "liquidity": (
            liquidity >= MIN_LIQUIDITY
        ),

        "liquidity_ratio": (
            liquidity_ratio
            <= MAX_LIQUIDITY_MC_RATIO
        ),

        "age": (
            age <= MAX_PAIR_AGE_HOURS
        ),

        "market_cap": (
            mc >= IDEAL_MIN_MC
            and
            mc <= MAX_MC
        ),
    }

    return {
        "hard_pass": all(
            checks.values()
        ),
        "checks": checks,
    }


# ============================================================
# SCORING
# ============================================================

def score_token(
    snapshot: Dict[str, Any],
    structure: Dict[str, Any],
    activity: bool,
    flow_acceleration: bool
) -> int:

    score = 0

    mc = snapshot["mc"]
    liquidity = snapshot["liquidity"]
    volume = snapshot["volume_5m"]
    flow = snapshot["flow_proxy_5m"]
    flow_pct = snapshot["flow_pressure_pct"]
    ratio = snapshot["buy_sell_ratio"]
    tx = snapshot["tx_5m"]
    age = snapshot["age_hours"]
    change_5m = snapshot["change_5m"]

    # FLOW / MC
    if flow_pct >= FLOW_MC_HOT_PCT:
        score += 25
    elif flow_pct >= FLOW_MC_STRONG_PCT:
        score += 20
    elif flow_pct >= FLOW_MC_QUALIFY_PCT:
        score += 15
    elif flow_pct >= FLOW_MC_WATCH_PCT:
        score += 8

    # ABSOLUTE FLOW
    if flow >= 5_000:
        score += 15
    elif flow >= 3_000:
        score += 12
    elif flow >= MIN_FLOW_PROXY_USD:
        score += 8

    # MARKET CAP
    if IDEAL_MIN_MC <= mc <= IDEAL_MAX_MC:
        score += 10
    elif mc <= MAX_MC:
        score += 5

    # LIQUIDITY
    if liquidity >= 15_000:
        score += 10
    elif liquidity >= 10_000:
        score += 8
    elif liquidity >= MIN_LIQUIDITY:
        score += 5

    # VOLUME / FLOW
    volume_flow_ratio = snapshot[
        "volume_flow_ratio"
    ]

    if volume_flow_ratio >= 2.5:
        score += 8
    elif volume_flow_ratio >= 2.0:
        score += 6
    elif volume_flow_ratio >= MIN_VOLUME_TO_FLOW_RATIO:
        score += 4

    # FLOW ACCELERATION
    if flow_acceleration:
        score += 8

    # BUY / SELL
    if ratio >= 2.5:
        score += 8
    elif ratio >= 2.0:
        score += 6
    elif ratio >= 1.5:
        score += 4
    elif ratio >= 1.2:
        score += 2

    # TRANSACTIONS
    if tx >= 50:
        score += 5
    elif tx >= 25:
        score += 3
    elif tx >= MIN_TX_5M:
        score += 1

    # AGE
    if age <= 12:
        score += 5
    elif age <= 24:
        score += 4
    elif age <= 48:
        score += 2

    # BREAKOUT
    if structure["breakout"]:
        score += 10

    # ACTIVITY
    if activity:
        score += 10

    # MOMENTUM
    if 5 <= change_5m <= 35:
        score += 5
    elif change_5m > 35:
        score += 2
    elif change_5m >= 0:
        score += 2

    # WEAKNESS PENALTY
    if change_5m <= -10:
        score -= 20
    elif change_5m <= -5:
        score -= 10
    elif change_5m < 0:
        score -= 5

    return max(
        0,
        min(100, score)
    )


# ============================================================
# ANALYSIS
# ============================================================

def analyze(
    snapshot: Dict[str, Any]
) -> Dict[str, Any]:

    address = snapshot["address"]

    token = get_token_state(address)

    history = token.get(
        "history",
        []
    )

    structure = calculate_structure(
        history
    )

    activity = activity_expanding(
        history
    )

    flow_acceleration = flow_accelerating(
        history
    )

    safety = safety_checks(
        snapshot
    )

    score = score_token(
        snapshot,
        structure,
        activity,
        flow_acceleration
    )

    flow_qualified = (
        snapshot["flow_proxy_5m"]
        >= MIN_FLOW_PROXY_USD

        and

        snapshot["flow_pressure_pct"]
        >= FLOW_MC_QUALIFY_PCT

        and

        snapshot["volume_flow_ratio"]
        >= MIN_VOLUME_TO_FLOW_RATIO
    )

    if (
        len(history) >= MIN_OBSERVATIONS
        and score >= 70
        and flow_qualified
        and (
            structure["breakout"]
            or activity
        )
        and snapshot["change_5m"]
        > MAX_IGNITION_DRAWDOWN_PCT
        and safety["hard_pass"]
    ):

        setup = "IGNITION"

    elif (
        flow_qualified
        and structure["breakout"]
    ):

        setup = "FLOW BREAKOUT"

    elif (
        flow_qualified
        and activity
    ):

        setup = "FLOW EXPANSION"

    elif flow_qualified:

        setup = "FLOW WATCH"

    elif activity:

        setup = "EXPANSION"

    elif structure["consolidation"]:

        setup = "CONSOLIDATION"

    else:

        setup = "OBSERVING"

    return {

        "score": score,

        "setup": setup,

        "breakout": structure["breakout"],

        "consolidation": structure["consolidation"],

        "range_pct": structure["range_pct"],

        "activity": activity,

        "flow_acceleration": flow_acceleration,

        "flow_qualified": flow_qualified,

        "safety_pass": safety["hard_pass"],

        "history_count": len(history),
    }


# ============================================================
# CONFIRMATION
# ============================================================

def valid_current_ignition(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any]
) -> bool:

    return (
        analysis["score"] >= 70

        and analysis["flow_qualified"]

        and (
            analysis["breakout"]
            or analysis["activity"]
        )

        and analysis["safety_pass"]

        and snapshot["change_5m"]
        > MAX_IGNITION_DRAWDOWN_PCT
    )


def create_pending_ignition(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any]
) -> None:

    address = snapshot["address"]

    state["pending_ignitions"][address] = {

        "address": address,

        "symbol": snapshot["symbol"],

        "ignition_time": now_ts(),

        "ignition_time_iso": iso_now(),

        "ignition_mc": snapshot["mc"],

        "ignition_liquidity": snapshot["liquidity"],

        "ignition_volume_5m": snapshot["volume_5m"],

        "ignition_flow_proxy": snapshot[
            "flow_proxy_5m"
        ],

        "ignition_flow_pressure": snapshot[
            "flow_pressure_pct"
        ],

        "ignition_score": analysis["score"],

        "ignition_change_5m": snapshot[
            "change_5m"
        ],

        "confirmations": 1,

        "last_confirmation_time": now_ts(),

        "pair_url": snapshot["pair_url"],
    }

    print(
        f"[PENDING] "
        f"{snapshot['symbol']} | "
        f"First valid flow ignition | "
        f"Confirmation=1/2 | "
        f"MC={money(snapshot['mc'])} | "
        f"Flow/MC="
        f"{snapshot['flow_pressure_pct']:.1f}%",
        flush=True
    )


def expire_pending_ignitions() -> None:

    current_time = now_ts()

    expired = []

    for address, pending in (
        state["pending_ignitions"].items()
    ):

        created = safe_float(
            pending.get(
                "ignition_time"
            )
        )

        if (
            current_time - created
            > PENDING_EXPIRY_SECONDS
        ):

            expired.append(address)

    for address in expired:

        pending = state[
            "pending_ignitions"
        ].pop(
            address,
            None
        )

        if pending:

            print(
                f"[EXPIRED] "
                f"{pending.get('symbol', '?')} | "
                f"Ignition confirmation expired",
                flush=True
            )


# ============================================================
# ALERT COOLDOWN
# ============================================================

def token_on_cooldown(
    address: str
) -> bool:

    alert = state["alerts"].get(
        address
    )

    if not alert:
        return False

    alert_time = safe_float(
        alert.get("alert_time")
    )

    return (
        now_ts() - alert_time
        < ALERT_COOLDOWN_SECONDS
    )


def global_alert_on_cooldown() -> bool:

    last_alert = safe_float(
        state.get(
            "last_alert_time",
            0
        )
    )

    return (
        now_ts() - last_alert
        < GLOBAL_ALERT_COOLDOWN_SECONDS
    )


# ============================================================
# CONFIRMATION ENGINE
# ============================================================

def process_confirmation(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any]
) -> Optional[str]:

    address = snapshot["address"]

    current_valid = valid_current_ignition(
        snapshot,
        analysis
    )

    # --------------------------------------------------------
    # IMPORTANT FIX:
    #
    # An old alert does NOT permanently block the token.
    # It only blocks it during the configured cooldown.
    # --------------------------------------------------------

    if token_on_cooldown(address):

        update_alert_outcome(snapshot)

        return None

    pending = state[
        "pending_ignitions"
    ].get(address)

    # --------------------------------------------------------
    # No pending ignition
    # --------------------------------------------------------

    if pending is None:

        if current_valid:

            create_pending_ignition(
                snapshot,
                analysis
            )

        return None

    # --------------------------------------------------------
    # Pending exists
    # --------------------------------------------------------

    ignition_mc = safe_float(
        pending.get(
            "ignition_mc"
        )
    )

    current_mc = snapshot["mc"]

    mc_growth = pct_change(
        ignition_mc,
        current_mc
    )

    # --------------------------------------------------------
    # Strict consecutive confirmation
    # --------------------------------------------------------

    if current_valid:

        previous_confirmations = safe_int(
            pending.get(
                "confirmations"
            ),
            1
        )

        pending["confirmations"] = (
            previous_confirmations + 1
        )

        pending["last_confirmation_time"] = (
            now_ts()
        )

        confirmations = pending[
            "confirmations"
        ]

        print(
            f"[CONFIRM CHECK] "
            f"{snapshot['symbol']} | "
            f"Confirmation="
            f"{confirmations}/"
            f"{CONFIRMATION_REQUIRED_SCANS} | "
            f"Score="
            f"{analysis['score']} | "
            f"Flow/MC="
            f"{snapshot['flow_pressure_pct']:.1f}% | "
            f"Flow="
            f"{money(snapshot['flow_proxy_5m'])} | "
            f"Breakout="
            f"{'YES' if analysis['breakout'] else 'NO'} | "
            f"Activity="
            f"{'YES' if analysis['activity'] else 'NO'}",
            flush=True
        )

        if (
            confirmations
            >= CONFIRMATION_REQUIRED_SCANS
        ):

            if global_alert_on_cooldown():

                print(
                    f"[GLOBAL COOLDOWN] "
                    f"{snapshot['symbol']} | "
                    f"Confirmation valid but "
                    f"global alert spacing active",
                    flush=True
                )

                return None

            return confirm_runner(
                snapshot,
                analysis,
                pending,
                "TWO_CONSECUTIVE_FLOW_VALID_SCANS"
            )

        return None

    # --------------------------------------------------------
    # MC growth path
    #
    # Growth alone NEVER confirms.
    # Current flow + structure must still qualify.
    # --------------------------------------------------------

    secondary_valid = (

        mc_growth
        >= CONFIRMATION_MC_GROWTH_PCT

        and analysis["score"] >= 70

        and analysis["flow_qualified"]

        and (
            analysis["breakout"]
            or analysis["activity"]
        )

        and analysis["safety_pass"]

        and snapshot["change_5m"]
        > MAX_IGNITION_DRAWDOWN_PCT
    )

    if secondary_valid:

        if global_alert_on_cooldown():

            print(
                f"[GLOBAL COOLDOWN] "
                f"{snapshot['symbol']} | "
                f"MC-growth confirmation valid "
                f"but alert spacing active",
                flush=True
            )

            return None

        print(
            f"[CONFIRM CHECK] "
            f"{snapshot['symbol']} | "
            f"MC growth="
            f"{mc_growth:+.1f}% | "
            f"Flow/MC="
            f"{snapshot['flow_pressure_pct']:.1f}% | "
            f"Current flow/structure confirmed",
            flush=True
        )

        return confirm_runner(
            snapshot,
            analysis,
            pending,
            "MC_GROWTH_WITH_FLOW_CONFIRMATION"
        )

    print(
        f"[PENDING] "
        f"{snapshot['symbol']} | "
        f"Current setup not confirmed | "
        f"Score="
        f"{analysis['score']} | "
        f"Flow/MC="
        f"{snapshot['flow_pressure_pct']:.1f}% | "
        f"MC since ignition="
        f"{mc_growth:+.1f}%",
        flush=True
    )

    return None


# ============================================================
# ALERT REGISTRATION
# ============================================================

def register_alert(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
    pending: Dict[str, Any],
    confirmation_type: str
) -> None:

    address = snapshot["address"]

    # Preserve existing outcome history while replacing the
    # active alert record after the cooldown expires.
    previous = state[
        "alerts"
    ].get(address)

    state["alerts"][address] = {

        "address": address,

        "symbol": snapshot["symbol"],

        "alert_time": now_ts(),

        "alert_time_iso": iso_now(),

        "ignition_mc": pending.get(
            "ignition_mc",
            snapshot["mc"]
        ),

        "start_mc": snapshot["mc"],

        "peak_mc": snapshot["mc"],

        "min_mc": snapshot["mc"],

        "score": analysis["score"],

        "confirmation_type": confirmation_type,

        "flow_proxy": snapshot[
            "flow_proxy_5m"
        ],

        "flow_pressure_pct": snapshot[
            "flow_pressure_pct"
        ],

        "liquidity": snapshot[
            "liquidity"
        ],

        "checks": {
            "5m": None,
            "15m": None,
            "30m": None,
            "60m": None,
        },

        "pair_url": snapshot["pair_url"],
    }

    if previous:

        state["alerts"][address][
            "previous_alert_time"
        ] = previous.get(
            "alert_time"
        )

    state["last_alert_time"] = now_ts()


# ============================================================
# OUTCOME TRACKING
# ============================================================

def update_alert_outcome(
    snapshot: Dict[str, Any]
) -> None:

    address = snapshot["address"]

    alert = state["alerts"].get(
        address
    )

    if not alert:
        return

    current_mc = snapshot["mc"]

    alert["peak_mc"] = max(
        safe_float(
            alert.get("peak_mc")
        ),
        current_mc
    )

    old_min = safe_float(
        alert.get("min_mc")
    )

    if old_min <= 0:

        alert["min_mc"] = current_mc

    else:

        alert["min_mc"] = min(
            old_min,
            current_mc
        )

    alert_time = safe_float(
        alert.get("alert_time")
    )

    elapsed = (
        now_ts() - alert_time
    )

    start_mc = safe_float(
        alert.get("start_mc")
    )

    if start_mc <= 0:
        return

    for label, seconds in (
        OUTCOME_WINDOWS_SECONDS.items()
    ):

        if (
            elapsed >= seconds
            and
            alert["checks"].get(label) is None
        ):

            current_growth = pct_change(
                start_mc,
                current_mc
            )

            peak_growth = pct_change(
                start_mc,
                safe_float(
                    alert.get("peak_mc")
                )
            )

            drawdown = pct_change(
                safe_float(
                    alert.get("peak_mc")
                ),
                current_mc
            )

            if current_growth >= 20:
                outcome = "RUNNER"

            elif current_growth >= 5:
                outcome = "FOLLOW_THROUGH"

            elif current_growth > -15:
                outcome = "UNCLEAR"

            else:
                outcome = "FAILED"

            alert["checks"][label] = {

                "checked_at": iso_now(),

                "mc": current_mc,

                "growth_pct": current_growth,

                "peak_growth_pct": peak_growth,

                "drawdown_from_peak_pct": drawdown,

                "outcome": outcome,
            }

            print(
                f"[OUTCOME] "
                f"{snapshot['symbol']} | "
                f"{label} | "
                f"MC={money(current_mc)} | "
                f"Growth="
                f"{current_growth:+.1f}% | "
                f"Peak="
                f"{peak_growth:+.1f}% | "
                f"Drawdown="
                f"{drawdown:+.1f}% | "
                f"{outcome}",
                flush=True
            )


# ============================================================
# CONFIRM RUNNER
# ============================================================

def confirm_runner(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
    pending: Dict[str, Any],
    confirmation_type: str
) -> str:

    address = snapshot["address"]

    ignition_mc = safe_float(
        pending.get(
            "ignition_mc",
            snapshot["mc"]
        )
    )

    current_mc = snapshot["mc"]

    mc_growth = pct_change(
        ignition_mc,
        current_mc
    )

    register_alert(
        snapshot,
        analysis,
        pending,
        confirmation_type
    )

    state[
        "pending_ignitions"
    ].pop(
        address,
        None
    )

    save_state()

    print(
        f"[CONFIRMED RUNNER] "
        f"{snapshot['symbol']} | "
        f"MC={money(current_mc)} | "
        f"Flow="
        f"{money(snapshot['flow_proxy_5m'])} | "
        f"Flow/MC="
        f"{snapshot['flow_pressure_pct']:.1f}% | "
        f"MC since ignition="
        f"{mc_growth:+.1f}% | "
        f"Score="
        f"{analysis['score']} | "
        f"Confirmation="
        f"{confirmation_type}",
        flush=True
    )

    return build_confirmation_message(
        snapshot,
        analysis,
        pending,
        mc_growth,
        confirmation_type
    )


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def build_confirmation_message(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
    pending: Dict[str, Any],
    mc_growth: float,
    confirmation_type: str
) -> str:

    flow_pct = snapshot[
        "flow_pressure_pct"
    ]

    if flow_pct >= FLOW_MC_HOT_PCT:
        flow_label = "🔥 HOT"

    elif flow_pct >= FLOW_MC_STRONG_PCT:
        flow_label = "🟠 STRONG"

    else:
        flow_label = "🟢 QUALIFIED"

    safety = safety_checks(
        snapshot
    )

    safety_count = sum(
        1
        for value in safety["checks"].values()
        if value
    )

    safety_total = len(
        safety["checks"]
    )

    return (

        "🚨 CONFIRMED RUNNER\n\n"

        f"🪙 {snapshot['symbol']}\n\n"

        f"📊 Runner Score: "
        f"{analysis['score']}/100\n"

        f"💰 MC: "
        f"{money(snapshot['mc'])}\n"

        f"💧 Liquidity: "
        f"{money(snapshot['liquidity'])}\n"

        f"📈 5m Volume: "
        f"{money(snapshot['volume_5m'])}\n"

        f"🕐 Age: "
        f"{snapshot['age_hours']:.1f}h\n\n"

        f"💸 5m Flow Proxy: "
        f"{money(snapshot['flow_proxy_5m'])}\n"

        f"📊 Flow/MC: "
        f"{flow_pct:+.1f}% "
        f"{flow_label}\n"

        f"⚖️ Volume/Flow: "
        f"{snapshot['volume_flow_ratio']:.2f}x\n\n"

        f"🟢 Buys: "
        f"{snapshot['buys']}\n"

        f"🔴 Sells: "
        f"{snapshot['sells']}\n"

        f"⚖️ Buy/Sell: "
        f"{ratio_text(snapshot['buy_sell_ratio'])}\n"

        f"📈 5m Change: "
        f"{snapshot['change_5m']:+.1f}%\n\n"

        f"🚀 Breakout: "
        f"{'YES' if analysis['breakout'] else 'NO'}\n"

        f"⚡ Activity expansion: "
        f"{'YES' if analysis['activity'] else 'NO'}\n"

        f"📈 Flow accelerating: "
        f"{'YES' if analysis['flow_acceleration'] else 'NO'}\n\n"

        f"🛡️ Basic Safety: "
        f"{safety_count}/{safety_total}\n"

        f"📈 MC since ignition: "
        f"{mc_growth:+.1f}%\n"

        f"👀 Observations: "
        f"{analysis['history_count']}\n\n"

        f"🔎 Confirmation: "
        f"{confirmation_type}\n\n"

        f"⚠️ Flow Proxy = "
        f"DEX Screener volume × "
        f"buy/sell imbalance.\n"

        f"⚠️ This is NOT true dollar "
        f"net flow until a dedicated "
        f"flow API is connected.\n\n"

        f"{snapshot['pair_url']}"
    )


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_api(
    method: str,
    payload: Optional[
        Dict[str, Any]
    ] = None
) -> Optional[Dict[str, Any]]:

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}"
        f"/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        f"/{method}"
    )

    data = urllib.parse.urlencode(
        payload or {}
    ).encode()

    try:

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": USER_AGENT
            },
            method="POST",
        )

        with urllib.request.urlopen(
            req,
            timeout=15
        ) as response:

            return json.loads(
                response.read().decode()
            )

    except Exception as exc:

        print(
            f"[TELEGRAM ERROR] {exc}",
            flush=True
        )

        return None


def send_telegram(
    chat_id: str,
    message: str
) -> bool:

    result = telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "false",
        }
    )

    return bool(
        result
        and result.get("ok")
    )


def broadcast(
    message: str
) -> None:

    if not ALERTS_ENABLED:

        print(
            "[TELEGRAM] Alerts disabled",
            flush=True
        )

        return

    subscribers = list(
        state.get(
            "subscribers",
            []
        )
    )

    if not subscribers:

        print(
            "[TELEGRAM] No subscribers",
            flush=True
        )

        return

    for chat_id in subscribers:

        send_telegram(
            str(chat_id),
            message
        )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def handle_update(
    update: Dict[str, Any]
) -> None:

    message = update.get(
        "message"
    )

    if not message:
        return

    chat = message.get(
        "chat"
    ) or {}

    chat_id = chat.get(
        "id"
    )

    if chat_id is None:
        return

    text = str(
        message.get(
            "text",
            ""
        )
    ).strip()

    if text.startswith("/start"):

        if chat_id not in state["subscribers"]:

            state["subscribers"].append(
                chat_id
            )

        save_state()

        send_telegram(
            str(chat_id),
            (
                "🔥 Runner Bot is online.\n\n"
                "You are subscribed to "
                "CONFIRMED RUNNER alerts.\n\n"
                "V4.4 Flow Pressure mode is ON."
            )
        )

    elif text.startswith("/stop"):

        state["subscribers"] = [
            x
            for x in state["subscribers"]
            if x != chat_id
        ]

        save_state()

        send_telegram(
            str(chat_id),
            "Alerts stopped."
        )

    elif text.startswith("/status"):

        pending = len(
            state["pending_ignitions"]
        )

        alerts = len(
            state["alerts"]
        )

        tokens = len(
            state["tokens"]
        )

        send_telegram(
            str(chat_id),
            (
                f"🔥 Runner Bot "
                f"{BOT_VERSION}\n\n"

                f"Tracked tokens: "
                f"{tokens}\n"

                f"Pending ignitions: "
                f"{pending}\n"

                f"Active alert records: "
                f"{alerts}\n"

                f"Scan interval: "
                f"{SCAN_INTERVAL_SECONDS}s\n\n"

                f"MC ideal: "
                f"${IDEAL_MIN_MC:,}–"
                f"${IDEAL_MAX_MC:,}\n"

                f"MC maximum: "
                f"${MAX_MC:,}\n"

                f"Min liquidity: "
                f"${MIN_LIQUIDITY:,}\n"

                f"Min flow proxy: "
                f"${MIN_FLOW_PROXY_USD:,}\n"

                f"Qualified Flow/MC: "
                f"{FLOW_MC_QUALIFY_PCT}%+\n"

                f"Hot Flow/MC: "
                f"{FLOW_MC_HOT_PCT}%+\n\n"

                f"Strict confirmation: ON\n"

                f"Confirmation scans: "
                f"{CONFIRMATION_REQUIRED_SCANS}\n\n"

                f"⚠️ Flow source: "
                f"DEX Screener proxy"
            )
        )

    elif text.startswith("/alerts"):

        alerts = state.get(
            "alerts",
            {}
        )

        if not alerts:

            send_telegram(
                str(chat_id),
                "No confirmed runner alerts yet."
            )

            return

        lines = [
            "🚨 CONFIRMED ALERTS\n"
        ]

        for item in list(
            alerts.values()
        )[-10:]:

            start_mc = safe_float(
                item.get("start_mc")
            )

            score = item.get(
                "score",
                0
            )

            flow_pct = safe_float(
                item.get(
                    "flow_pressure_pct"
                )
            )

            lines.append(
                f"{item.get('symbol', '?')} "
                f"| MC {money(start_mc)} "
                f"| Score {score} "
                f"| Flow/MC {flow_pct:.1f}%"
            )

        send_telegram(
            str(chat_id),
            "\n".join(lines)
        )

    elif text.startswith("/scan"):

        send_telegram(
            str(chat_id),
            "Running a manual scan..."
        )

        scan_once()


# ============================================================
# TELEGRAM POLLING
# ============================================================

def telegram_poll() -> None:

    if not TELEGRAM_BOT_TOKEN:
        return

    offset = state.get(
        "telegram_offset",
        0
    )

    result = telegram_api(
        "getUpdates",
        {
            "timeout": 1,
            "offset": offset,
        }
    )

    if not result:
        return

    if not result.get("ok"):
        return

    updates = result.get(
        "result",
        []
    )

    for update in updates:

        update_id = update.get(
            "update_id"
        )

        if update_id is not None:

            state["telegram_offset"] = (
                update_id + 1
            )

        try:

            handle_update(update)

        except Exception as exc:

            print(
                f"[TELEGRAM UPDATE ERROR] "
                f"{exc}",
                flush=True
            )

    save_state()


# ============================================================
# CANDIDATE FILTER DIAGNOSTICS
# ============================================================

def print_filter_reason(
    snapshot: Dict[str, Any]
) -> bool:

    symbol = snapshot["symbol"]
    mc = snapshot["mc"]
    liquidity = snapshot["liquidity"]
    age = snapshot["age_hours"]
    flow = snapshot["flow_proxy_5m"]
    flow_pct = snapshot["flow_pressure_pct"]
    volume = snapshot["volume_5m"]
    ratio = snapshot["buy_sell_ratio"]
    liquidity_ratio = snapshot[
        "liquidity_mc_ratio"
    ]

    if mc < OBSERVE_MIN_MC:

        print(
            f"[FILTER] "
            f"{symbol}: MC below "
            f"${OBSERVE_MIN_MC:,}",
            flush=True
        )

        return False

    if mc > OBSERVE_MAX_MC:

        print(
            f"[FILTER] "
            f"{symbol}: MC above "
            f"${OBSERVE_MAX_MC:,}",
            flush=True
        )

        return False

    if mc > MAX_MC:

        print(
            f"[FILTER] "
            f"{symbol}: MC above hard maximum "
            f"${MAX_MC:,}",
            flush=True
        )

        return False

    if liquidity < MIN_LIQUIDITY:

        print(
            f"[FILTER] "
            f"{symbol}: liquidity "
            f"{money(liquidity)} < "
            f"{money(MIN_LIQUIDITY)}",
            flush=True
        )

        return False

    if liquidity_ratio > MAX_LIQUIDITY_MC_RATIO:

        print(
            f"[FILTER] "
            f"{symbol}: liquidity/MC="
            f"{liquidity_ratio * 100:.1f}% "
            f"> 90%",
            flush=True
        )

        return False

    if age > MAX_PAIR_AGE_HOURS:

        print(
            f"[FILTER] "
            f"{symbol}: age="
            f"{age:.1f}h > "
            f"{MAX_PAIR_AGE_HOURS}h",
            flush=True
        )

        return False

    if flow < MIN_FLOW_PROXY_USD:

        print(
            f"[FLOW WAIT] "
            f"{symbol}: Flow proxy="
            f"{money(flow)} < "
            f"${MIN_FLOW_PROXY_USD:,} | "
            f"Flow/MC="
            f"{flow_pct:.1f}% | "
            f"Buy/Sell="
            f"{ratio:.2f}",
            flush=True
        )

        return False

    if flow_pct < FLOW_MC_QUALIFY_PCT:

        print(
            f"[FLOW WAIT] "
            f"{symbol}: Flow/MC="
            f"{flow_pct:.1f}% < "
            f"{FLOW_MC_QUALIFY_PCT}% | "
            f"Flow="
            f"{money(flow)}",
            flush=True
        )

        return False

    if volume < MIN_VOLUME_5M:

        print(
            f"[FLOW WAIT] "
            f"{symbol}: 5m volume="
            f"{money(volume)} < "
            f"{money(MIN_VOLUME_5M)}",
            flush=True
        )

        return False

    volume_flow_ratio = snapshot[
        "volume_flow_ratio"
    ]

    if (
        volume_flow_ratio
        < MIN_VOLUME_TO_FLOW_RATIO
    ):

        print(
            f"[FLOW WAIT] "
            f"{symbol}: volume/flow="
            f"{volume_flow_ratio:.2f}x < "
            f"{MIN_VOLUME_TO_FLOW_RATIO:.1f}x",
            flush=True
        )

        return False

    return True


# ============================================================
# SCAN
# ============================================================

def scan_once() -> None:

    print(
        "=" * 70,
        flush=True
    )

    print(
        f"[SCAN] "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        flush=True
    )

    current_time = now_ts()

    # --------------------------------------------------------
    # Discovery
    # --------------------------------------------------------

    if (
        current_time
        - safe_float(
            state.get(
                "last_discovery",
                0
            )
        )
        >= DISCOVERY_INTERVAL_SECONDS
    ):

        discovered = discover_tokens()

        if discovered:

            state[
                "discovered_tokens"
            ] = discovered

        state[
            "last_discovery"
        ] = current_time

        save_state()

    token_addresses = state.get(
        "discovered_tokens",
        []
    )

    if not token_addresses:

        token_addresses = discover_tokens()

        state[
            "discovered_tokens"
        ] = token_addresses

    # --------------------------------------------------------
    # Pairs
    # --------------------------------------------------------

    pairs = get_token_pairs_batch(
        token_addresses
    )

    print(
        f"[PAIRS] "
        f"{len(pairs)} Solana pairs received",
        flush=True
    )

    strongest = choose_best_pairs(
        pairs
    )

    print(
        f"[PAIRS] "
        f"{len(strongest)} strongest pools selected",
        flush=True
    )

    expire_pending_ignitions()

    # --------------------------------------------------------
    # Process
    # --------------------------------------------------------

    for pair in strongest:

        try:

            snapshot = pair_to_snapshot(
                pair
            )

            address = snapshot[
                "address"
            ]

            if not address:
                continue

            record_snapshot(
                snapshot
            )

            token = get_token_state(
                address
            )

            observations = len(
                token.get(
                    "history",
                    []
                )
            )

            print(
                f"[CANDIDATE] "
                f"{snapshot['symbol']} | "
                f"MC={money(snapshot['mc'])} | "
                f"Liq={money(snapshot['liquidity'])} | "
                f"Age={snapshot['age_hours']:.1f}h | "
                f"5mVol={money(snapshot['volume_5m'])} | "
                f"Flow={money(snapshot['flow_proxy_5m'])} | "
                f"Flow/MC="
                f"{snapshot['flow_pressure_pct']:.1f}% | "
                f"Buy/Sell="
                f"{snapshot['buy_sell_ratio']:.2f} | "
                f"Obs="
                f"{observations}/{MIN_OBSERVATIONS}",
                flush=True
            )

            # ------------------------------------------------
            # Basic filters
            # ------------------------------------------------

            if not print_filter_reason(
                snapshot
            ):

                continue

            # ------------------------------------------------
            # Observation requirement
            # ------------------------------------------------

            if observations < MIN_OBSERVATIONS:

                print(
                    f"[WAIT] "
                    f"{snapshot['symbol']} | "
                    f"Need "
                    f"{MIN_OBSERVATIONS - observations} "
                    f"more observations",
                    flush=True
                )

                continue

            # ------------------------------------------------
            # Analysis
            # ------------------------------------------------

            analysis = analyze(
                snapshot
            )

            pending = state[
                "pending_ignitions"
            ].get(
                address
            )

            confirmation_text = ""

            if pending:

                ignition_mc = safe_float(
                    pending.get(
                        "ignition_mc"
                    )
                )

                growth = pct_change(
                    ignition_mc,
                    snapshot["mc"]
                )

                confirmation_text = (
                    f" | CONFIRM="
                    f"{pending.get('confirmations', 1)}/"
                    f"{CONFIRMATION_REQUIRED_SCANS}"
                    f" | MC since ignition="
                    f"{growth:+.1f}%"
                )

            print(
                f"[TRACK] "
                f"{snapshot['symbol']} | "
                f"Score="
                f"{analysis['score']}/100 | "
                f"State="
                f"{analysis['setup']} | "
                f"MC="
                f"{money(snapshot['mc'])} | "
                f"Liq="
                f"{money(snapshot['liquidity'])} | "
                f"Flow="
                f"{money(snapshot['flow_proxy_5m'])} | "
                f"Flow/MC="
                f"{snapshot['flow_pressure_pct']:.1f}% | "
                f"5mVol="
                f"{money(snapshot['volume_5m'])}"
                f"{confirmation_text}",
                flush=True
            )

            # ------------------------------------------------
            # Confirmation
            # ------------------------------------------------

            message = process_confirmation(
                snapshot,
                analysis
            )

            if message:
                broadcast(message)

            save_state()

        except Exception as exc:

            print(
                f"[TOKEN ERROR] "
                f"{exc}",
                flush=True
            )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    print(
        "=" * 70,
        flush=True
    )

    print(
        f"🔥 RUNNER BOT "
        f"{BOT_VERSION}",
        flush=True
    )

    print(
        f"Scan interval: "
        f"{SCAN_INTERVAL_SECONDS}s",
        flush=True
    )

    print(
        f"MC sweet spot: "
        f"${IDEAL_MIN_MC:,} - "
        f"${IDEAL_MAX_MC:,}",
        flush=True
    )

    print(
        f"MC hard maximum: "
        f"${MAX_MC:,}",
        flush=True
    )

    print(
        f"Minimum liquidity: "
        f"${MIN_LIQUIDITY:,}",
        flush=True
    )

    print(
        f"Maximum liquidity/MC: "
        f"{MAX_LIQUIDITY_MC_RATIO * 100:.0f}%",
        flush=True
    )

    print(
        f"Maximum pair age: "
        f"{MAX_PAIR_AGE_HOURS}h",
        flush=True
    )

    print(
        f"Minimum flow proxy: "
        f"${MIN_FLOW_PROXY_USD:,}",
        flush=True
    )

    print(
        f"Qualified Flow/MC: "
        f"{FLOW_MC_QUALIFY_PCT}%",
        flush=True
    )

    print(
        f"Strong Flow/MC: "
        f"{FLOW_MC_STRONG_PCT}%",
        flush=True
    )

    print(
        f"Hot Flow/MC: "
        f"{FLOW_MC_HOT_PCT}%",
        flush=True
    )

    print(
        f"Volume/Flow minimum: "
        f"{MIN_VOLUME_TO_FLOW_RATIO:.1f}x",
        flush=True
    )

    print(
        f"Minimum observations: "
        f"{MIN_OBSERVATIONS}",
        flush=True
    )

    print(
        "Strict confirmation: ON",
        flush=True
    )

    print(
        f"Confirmation scans: "
        f"{CONFIRMATION_REQUIRED_SCANS}",
        flush=True
    )

    print(
        "⚠️ Real dollar net flow: NOT CONNECTED",
        flush=True
    )

    print(
        "⚡ DEX Screener flow-pressure proxy: ON",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARNING] "
            "TELEGRAM_BOT_TOKEN is not configured.",
            flush=True
        )

    last_scan = 0.0

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

            time.sleep(1)

        except KeyboardInterrupt:

            print(
                "\n[STOP] Runner Bot stopped.",
                flush=True
            )

            save_state()

            break

        except Exception as exc:

            print(
                f"[MAIN ERROR] "
                f"{exc}",
                flush=True
            )

            save_state()

            time.sleep(5)


if __name__ == "__main__":
    main()
