import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V3.8
# ============================================================

BOT_VERSION = "v3.8"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

STATE_FILE = "runner_state.json"


# ============================================================
# Runner range
# ============================================================

MIN_MC = 20_000
MAX_MC = 200_000

MIN_LIQUIDITY = 10_000

# 5m volume remains a FEATURE, not a hard gate.
REFERENCE_VOLUME_5M = 5_000


# ============================================================
# Observation range
# ============================================================

OBSERVE_MIN_MC = 10_000
OBSERVE_MAX_MC = 250_000


# ============================================================
# History
# ============================================================

MAX_STORED_TOKENS = 750
MAX_HISTORY_PER_TOKEN = 240
MIN_OBSERVATIONS = 6


# ============================================================
# Consolidation activity gate
# ============================================================

MIN_CONSOLIDATION_VOLUME_5M = 500
MIN_CONSOLIDATION_TX = 5
MIN_CONSOLIDATION_ACTIVE_OBS = 3


# ============================================================
# Activity measurement
#
# DEX Screener gives us rolling 5m volume/transactions.
# Because scans happen every 15s, we compare those rolling
# windows at approximately 1m and 2m intervals.
# ============================================================

ACTIVITY_LOOKBACK_1M = 4
ACTIVITY_LOOKBACK_2M = 8

ACTIVITY_EXPANSION_PCT = 20.0


# ============================================================
# Timing
# ============================================================

SCAN_INTERVAL_SECONDS = float(
    os.getenv("DEX_SCAN_INTERVAL_SECONDS", "15")
)

DISCOVERY_INTERVAL_SECONDS = 60

HTTP_TIMEOUT = 12
HTTP_MAX_RETRIES = 3
HTTP_BACKOFF_BASE = 2.0


# ============================================================
# Telegram
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

HEATING_ALERTS_ENABLED = (
    os.getenv(
        "HEATING_ALERTS_ENABLED",
        "false"
    ).lower()
    == "true"
)


# ============================================================
# Discovery
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
]

MAX_DISCOVERY_PAIRS = 300


# ============================================================
# Outcome tracking
# ============================================================

OUTCOME_WINDOWS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
}

CONTINUING_MC_CHANGE = 10.0
FAILED_MC_CHANGE = -15.0

# Final research label.
#
# This is deliberately conservative:
#
# RUNNER:
#   +30% or more by +30m
#
# FAILED:
#   -15% or worse by +30m
#
# UNCLEAR:
#   everything else
#
# We also record peak MC separately so a temporary spike
# cannot be confused with sustained continuation.
FINAL_RUNNER_CHANGE = 30.0


# ============================================================
# Helpers
# ============================================================

def now_ts() -> float:
    return time.time()


def numeric(value: Any) -> float:
    try:
        if value is None:
            return 0.0

        return float(value)

    except Exception:
        return 0.0


def safe_symbol(value: Any) -> str:

    if value is None:
        return "?"

    text = str(value).strip()

    return text[:32] if text else "?"


def format_usd(value: float) -> str:

    value = numeric(value)

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.2f}K"

    return f"${value:.2f}"


def shorten_address(address: str) -> str:

    if not address:
        return "?"

    if len(address) <= 14:
        return address

    return f"{address[:6]}...{address[-6:]}"


def pct_change(old: float, new: float) -> float:

    if old <= 0:
        return 0.0

    return ((new - old) / old) * 100.0


def clamp(
    value: float,
    low: float,
    high: float,
) -> float:

    return max(
        low,
        min(high, value),
    )


# ============================================================
# HTTP
# ============================================================

def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:

    request_headers = {
        "User-Agent": "runner-bot/3.8",
        "Accept": "application/json",
    }

    if headers:
        request_headers.update(headers)

    for attempt in range(HTTP_MAX_RETRIES):

        try:

            request = urllib.request.Request(
                url,
                data=payload,
                headers=request_headers,
                method=method,
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

        except urllib.error.HTTPError as exc:

            if exc.code == 429:

                retry_after = exc.headers.get(
                    "Retry-After"
                )

                if retry_after:

                    try:
                        wait = float(
                            retry_after
                        )

                    except Exception:
                        wait = (
                            HTTP_BACKOFF_BASE
                            * (2 ** attempt)
                        )

                else:
                    wait = (
                        HTTP_BACKOFF_BASE
                        * (2 ** attempt)
                    )

                wait = clamp(
                    wait,
                    1.0,
                    30.0,
                )

                print(
                    "HTTP 429 rate limit | "
                    f"waiting {wait:.1f}s | "
                    f"attempt "
                    f"{attempt + 1}/"
                    f"{HTTP_MAX_RETRIES}"
                )

                time.sleep(wait)
                continue

            print(
                f"HTTP error {exc.code}: {url}"
            )

            return None

        except Exception as exc:

            print(
                f"HTTP error: {exc}"
            )

            if (
                attempt
                < HTTP_MAX_RETRIES - 1
            ):

                time.sleep(
                    HTTP_BACKOFF_BASE
                    * (2 ** attempt)
                )

                continue

            return None

    return None


# ============================================================
# Data model
# ============================================================

@dataclass
class TokenSnapshot:

    timestamp: float

    address: str
    symbol: str

    market_cap: float
    liquidity: float
    price: float

    volume_5m: float
    volume_1h: float

    buys_5m: int
    sells_5m: int

    buys_1h: int
    sells_1h: int

    price_change_5m: float
    price_change_1h: float

    pair_created_at: int


# ============================================================
# Global state
# ============================================================

histories: Dict[
    str,
    List[Dict[str, Any]]
] = {}

ignition_events: List[
    Dict[str, Any]
] = []

telegram_subscribers: List[int] = []

discovery_cache: List[
    Dict[str, Any]
] = []

last_discovery_time = 0.0

last_update_id = 0


# ============================================================
# Persistence
# ============================================================

def load_state() -> None:

    global histories
    global ignition_events
    global telegram_subscribers

    if not os.path.exists(
        STATE_FILE
    ):

        print(
            "No persistent state found. "
            "Starting fresh."
        )

        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(file)

        histories = (
            data.get(
                "histories",
                {}
            )
            or {}
        )

        ignition_events = (
            data.get(
                "ignition_events",
                []
            )
            or []
        )

        telegram_subscribers = [
            int(x)
            for x in (
                data.get(
                    "telegram_subscribers",
                    []
                )
                or []
            )
        ]

        # Remove historical ignition events
        # outside our intended runner range.
        before = len(
            ignition_events
        )

        ignition_events = [
            event
            for event in ignition_events
            if (
                MIN_MC
                <= numeric(
                    event.get(
                        "ignition_mc"
                    )
                )
                <= MAX_MC
            )
            and
            numeric(
                event.get(
                    "ignition_liquidity"
                )
            )
            >= MIN_LIQUIDITY
        ]

        removed = (
            before
            - len(ignition_events)
        )

        if removed:

            print(
                f"Removed {removed} "
                "out-of-range historical "
                "ignition event(s)"
            )

        print(
            "Persistent state loaded: "
            f"{len(histories)} tokens | "
            f"{len(ignition_events)} "
            "ignition events"
        )

    except Exception as exc:

        print(
            f"State load error: {exc}"
        )

        histories = {}
        ignition_events = []
        telegram_subscribers = []


def save_state() -> None:

    global histories

    try:

        if len(histories) > MAX_STORED_TOKENS:

            ranked = sorted(
                histories.items(),
                key=lambda item: (
                    item[1][-1]["timestamp"]
                    if item[1]
                    else 0
                ),
                reverse=True,
            )

            histories = dict(
                ranked[
                    :MAX_STORED_TOKENS
                ]
            )

        data = {
            "version": BOT_VERSION,
            "saved_at": now_ts(),
            "histories": histories,
            "ignition_events": ignition_events,
            "telegram_subscribers": telegram_subscribers,
        }

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                data,
                file,
                separators=(",", ":"),
            )

        print(
            "Persistent state saved: "
            f"{len(histories)} tokens | "
            f"{len(ignition_events)} "
            "ignition events"
        )

    except Exception as exc:

        print(
            f"State save error: {exc}"
        )


# ============================================================
# Pair extraction
# ============================================================

def pair_to_snapshot(
    pair: Dict[str, Any],
) -> Optional[TokenSnapshot]:

    try:

        chain_id = str(
            pair.get(
                "chainId",
                ""
            )
        ).lower()

        if chain_id != "solana":
            return None

        base_token = (
            pair.get(
                "baseToken"
            )
            or {}
        )

        address = str(
            base_token.get(
                "address",
                ""
            )
        ).strip()

        if not address:
            return None

        symbol = safe_symbol(
            base_token.get(
                "symbol"
            )
        )

        market_cap = numeric(
            pair.get(
                "marketCap"
            )
        )

        if market_cap <= 0:

            market_cap = numeric(
                pair.get(
                    "fdv"
                )
            )

        liquidity_obj = (
            pair.get(
                "liquidity"
            )
            or {}
        )

        liquidity = numeric(
            liquidity_obj.get(
                "usd"
            )
        )

        volume_obj = (
            pair.get(
                "volume"
            )
            or {}
        )

        volume_5m = numeric(
            volume_obj.get(
                "m5"
            )
        )

        volume_1h = numeric(
            volume_obj.get(
                "h1"
            )
        )

        txns = (
            pair.get(
                "txns"
            )
            or {}
        )

        tx_5m = (
            txns.get(
                "m5"
            )
            or {}
        )

        tx_1h = (
            txns.get(
                "h1"
            )
            or {}
        )

        buys_5m = int(
            numeric(
                tx_5m.get(
                    "buys"
                )
            )
        )

        sells_5m = int(
            numeric(
                tx_5m.get(
                    "sells"
                )
            )
        )

        buys_1h = int(
            numeric(
                tx_1h.get(
                    "buys"
                )
            )
        )

        sells_1h = int(
            numeric(
                tx_1h.get(
                    "sells"
                )
            )
        )

        price = numeric(
            pair.get(
                "priceUsd"
            )
        )

        changes = (
            pair.get(
                "priceChange"
            )
            or {}
        )

        price_change_5m = numeric(
            changes.get(
                "m5"
            )
        )

        price_change_1h = numeric(
            changes.get(
                "h1"
            )
        )

        pair_created_at = int(
            numeric(
                pair.get(
                    "pairCreatedAt"
                )
            )
        )

        return TokenSnapshot(
            timestamp=now_ts(),
            address=address,
            symbol=symbol,
            market_cap=market_cap,
            liquidity=liquidity,
            price=price,
            volume_5m=volume_5m,
            volume_1h=volume_1h,
            buys_5m=buys_5m,
            sells_5m=sells_5m,
            buys_1h=buys_1h,
            sells_1h=sells_1h,
            price_change_5m=price_change_5m,
            price_change_1h=price_change_1h,
            pair_created_at=pair_created_at,
        )

    except Exception:

        return None


# ============================================================
# Discovery
# ============================================================

def extract_pairs(
    data: Optional[Dict[str, Any]]
) -> List[Dict[str, Any]]:

    if not data:
        return []

    pairs = data.get(
        "pairs"
    )

    if not pairs:
        return []

    if not isinstance(
        pairs,
        list
    ):
        return []

    return pairs


def discover_pairs(
    force: bool = False
) -> List[Dict[str, Any]]:

    global discovery_cache
    global last_discovery_time

    current_time = now_ts()

    if (
        not force
        and discovery_cache
        and
        current_time
        - last_discovery_time
        < DISCOVERY_INTERVAL_SECONDS
    ):

        return discovery_cache

    discovered: Dict[
        str,
        Dict[str, Any]
    ] = {}

    request_count = 0

    endpoints = [
        f"{DEX_BASE}/token-profiles/latest/v1",
        f"{DEX_BASE}/token-boosts/latest/v1",
        f"{DEX_BASE}/token-boosts/top/v1",
    ]

    profile_addresses: List[str] = []

    for endpoint in endpoints:

        data = http_json(
            endpoint
        )

        request_count += 1

        if not data:
            continue

        items = (
            data
            if isinstance(
                data,
                list
            )
            else data.get(
                "tokens",
                []
            )
        )

        if not isinstance(
            items,
            list
        ):
            continue

        for item in items:

            if not isinstance(
                item,
                dict
            ):
                continue

            chain_id = str(
                item.get(
                    "chainId",
                    ""
                )
            ).lower()

            if chain_id != "solana":
                continue

            address = str(
                item.get(
                    "tokenAddress",
                    ""
                )
            ).strip()

            if address:
                profile_addresses.append(
                    address
                )

    for term in SEARCH_TERMS:

        encoded = urllib.parse.quote(
            term
        )

        url = (
            f"{DEX_BASE}/latest/dex/search"
            f"?q={encoded}"
        )

        data = http_json(
            url
        )

        request_count += 1

        for pair in extract_pairs(
            data
        ):

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
            ).lower() != "solana":

                continue

            base = (
                pair.get(
                    "baseToken"
                )
                or {}
            )

            address = str(
                base.get(
                    "address",
                    ""
                )
            ).strip()

            if not address:
                continue

            liquidity = numeric(
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
                discovered.get(
                    address
                )
            )

            if (
                existing is None
                or
                liquidity
                >
                numeric(
                    (
                        existing.get(
                            "liquidity"
                        )
                        or {}
                    ).get(
                        "usd"
                    )
                )
            ):

                discovered[
                    address
                ] = pair

        time.sleep(
            0.15
        )

    unique_profile_addresses = list(
        dict.fromkeys(
            profile_addresses
        )
    )

    batch_size = 25

    for start in range(
        0,
        len(
            unique_profile_addresses
        ),
        batch_size,
    ):

        batch = (
            unique_profile_addresses[
                start:
                start + batch_size
            ]
        )

        if not batch:
            continue

        joined = ",".join(
            batch
        )

        url = (
            f"{DEX_BASE}/latest/dex/tokens/"
            f"{urllib.parse.quote(joined)}"
        )

        data = http_json(
            url
        )

        request_count += 1

        for pair in extract_pairs(
            data
        ):

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
            ).lower() != "solana":

                continue

            base = (
                pair.get(
                    "baseToken"
                )
                or {}
            )

            address = str(
                base.get(
                    "address",
                    ""
                )
            ).strip()

            if not address:
                continue

            liquidity = numeric(
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
                discovered.get(
                    address
                )
            )

            if (
                existing is None
                or
                liquidity
                >
                numeric(
                    (
                        existing.get(
                            "liquidity"
                        )
                        or {}
                    ).get(
                        "usd"
                    )
                )
            ):

                discovered[
                    address
                ] = pair

        time.sleep(
            0.2
        )

    pairs = list(
        discovered.values()
    )

    pairs.sort(
        key=lambda pair: numeric(
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

    pairs = pairs[
        :MAX_DISCOVERY_PAIRS
    ]

    discovery_cache = pairs

    last_discovery_time = (
        current_time
    )

    print(
        f"{len(pairs)} Solana pairs "
        "discovered | "
        f"discovery HTTP requests="
        f"{request_count}"
    )

    return pairs


# ============================================================
# Snapshot recording
# ============================================================

def snapshot_dict(
    snapshot: TokenSnapshot
) -> Dict[str, Any]:

    return asdict(
        snapshot
    )


def record_snapshot(
    snapshot: TokenSnapshot,
) -> bool:

    if not (
        OBSERVE_MIN_MC
        <= snapshot.market_cap
        <= OBSERVE_MAX_MC
    ):

        return False

    # Don't store completely dead observations.
    has_activity = (
        snapshot.volume_5m > 0
        or snapshot.buys_5m > 0
        or snapshot.sells_5m > 0
    )

    if not has_activity:
        return False

    address = snapshot.address

    history = histories.setdefault(
        address,
        []
    )

    history.append(
        snapshot_dict(
            snapshot
        )
    )

    if (
        len(history)
        > MAX_HISTORY_PER_TOKEN
    ):

        del history[
            :
            len(history)
            - MAX_HISTORY_PER_TOKEN
        ]

    return True


# ============================================================
# Activity calculations
# ============================================================

def transaction_count_from_item(
    item: Dict[str, Any]
) -> int:

    return (
        int(
            numeric(
                item.get(
                    "buys_5m"
                )
            )
        )
        +
        int(
            numeric(
                item.get(
                    "sells_5m"
                )
            )
        )
    )


def activity_change(
    current_value: float,
    previous_value: float,
) -> float:

    if previous_value <= 0:

        if current_value > 0:
            return 100.0

        return 0.0

    return pct_change(
        previous_value,
        current_value,
    )


def get_activity_metrics(
    history: List[Dict[str, Any]],
    snapshot: TokenSnapshot,
) -> Dict[str, float]:

    current_volume = (
        snapshot.volume_5m
    )

    current_tx = (
        snapshot.buys_5m
        + snapshot.sells_5m
    )

    volume_change_1m = 0.0
    volume_change_2m = 0.0

    tx_change_1m = 0.0
    tx_change_2m = 0.0

    if len(history) > ACTIVITY_LOOKBACK_1M:

        previous_1m = history[
            -(
                ACTIVITY_LOOKBACK_1M
            )
        ]

        volume_change_1m = (
            activity_change(
                current_volume,
                numeric(
                    previous_1m.get(
                        "volume_5m"
                    )
                )
            )
        )

        tx_change_1m = (
            activity_change(
                current_tx,
                transaction_count_from_item(
                    previous_1m
                )
            )
        )

    if len(history) > ACTIVITY_LOOKBACK_2M:

        previous_2m = history[
            -(
                ACTIVITY_LOOKBACK_2M
            )
        ]

        volume_change_2m = (
            activity_change(
                current_volume,
                numeric(
                    previous_2m.get(
                        "volume_5m"
                    )
                )
            )
        )

        tx_change_2m = (
            activity_change(
                current_tx,
                transaction_count_from_item(
                    previous_2m
                )
            )
        )

    # We use the strongest confirmed acceleration
    # across the two time horizons.
    volume_acceleration = max(
        volume_change_1m,
        volume_change_2m,
    )

    tx_acceleration = max(
        tx_change_1m,
        tx_change_2m,
    )

    return {
        "volume_change_1m":
            volume_change_1m,

        "volume_change_2m":
            volume_change_2m,

        "tx_change_1m":
            tx_change_1m,

        "tx_change_2m":
            tx_change_2m,

        "volume_acceleration":
            volume_acceleration,

        "tx_acceleration":
            tx_acceleration,
    }


# ============================================================
# Analysis
# ============================================================

def analyze(
    snapshot: TokenSnapshot,
) -> Dict[str, Any]:

    address = snapshot.address

    history = histories.get(
        address,
        []
    )

    if len(history) < MIN_OBSERVATIONS:

        return {
            "state": "OBSERVING",
            "score": 0,
            "reasons": [
                "insufficient observations"
            ],
            "breakout": False,
            "activity_expansion": False,
            "mc_move": 0.0,
            "liq_move": 0.0,
            "range": 0.0,
            "vol_change": 0.0,
            "tx_change": 0.0,
            "vol_change_1m": 0.0,
            "vol_change_2m": 0.0,
            "tx_change_1m": 0.0,
            "tx_change_2m": 0.0,
            "buy_sell_ratio": 0.0,
            "runner_market_cap": False,
            "runner_liquidity": False,
            "consolidation": False,
        }

    recent = history[
        -MIN_OBSERVATIONS:
    ]

    recent_5 = history[-5:]

    oldest = recent[0]

    mc_move = pct_change(
        numeric(
            oldest.get(
                "market_cap"
            )
        ),
        snapshot.market_cap,
    )

    # Ignore meaningless liquidity percentage
    # changes when the previous liquidity was tiny.
    old_liquidity = numeric(
        oldest.get(
            "liquidity"
        )
    )

    if old_liquidity >= 2_000:

        liq_move = pct_change(
            old_liquidity,
            snapshot.liquidity,
        )

    else:

        liq_move = 0.0

    mcs = [
        numeric(
            item.get(
                "market_cap"
            )
        )
        for item in recent
        if numeric(
            item.get(
                "market_cap"
            )
        ) > 0
    ]

    if mcs:

        low_mc = min(mcs)
        high_mc = max(mcs)

        if low_mc > 0:

            range_pct = (
                (
                    high_mc
                    - low_mc
                )
                / low_mc
            ) * 100.0

        else:
            range_pct = 0.0

    else:
        range_pct = 0.0

    # --------------------------------------------------------
    # NEW V3.8 activity measurement
    # --------------------------------------------------------

    activity = get_activity_metrics(
        history,
        snapshot,
    )

    vol_change_1m = activity[
        "volume_change_1m"
    ]

    vol_change_2m = activity[
        "volume_change_2m"
    ]

    tx_change_1m = activity[
        "tx_change_1m"
    ]

    tx_change_2m = activity[
        "tx_change_2m"
    ]

    volume_acceleration = activity[
        "volume_acceleration"
    ]

    tx_acceleration = activity[
        "tx_acceleration"
    ]

    # Compatibility fields.
    vol_change = volume_acceleration
    tx_change = tx_acceleration

    activity_expansion = (
        volume_acceleration
        >= ACTIVITY_EXPANSION_PCT
        or
        tx_acceleration
        >= ACTIVITY_EXPANSION_PCT
    )

    # --------------------------------------------------------
    # Local breakout
    # --------------------------------------------------------

    prior_mcs = [
        numeric(
            item.get(
                "market_cap"
            )
        )
        for item in recent[:-1]
        if numeric(
            item.get(
                "market_cap"
            )
        ) > 0
    ]

    prior_high = (
        max(prior_mcs)
        if prior_mcs
        else 0.0
    )

    breakout = (
        prior_high > 0
        and
        snapshot.market_cap
        > prior_high * 1.02
    )

    # --------------------------------------------------------
    # Buy pressure
    # --------------------------------------------------------

    total_tx = (
        snapshot.buys_5m
        + snapshot.sells_5m
    )

    if snapshot.sells_5m > 0:

        buy_sell_ratio = (
            snapshot.buys_5m
            /
            snapshot.sells_5m
        )

    elif snapshot.buys_5m > 0:

        buy_sell_ratio = float(
            snapshot.buys_5m
        )

    else:

        buy_sell_ratio = 0.0

    # Keep the original scoring concept.
    buy_pressure = (
        snapshot.buys_5m
        > snapshot.sells_5m
    )

    strong_buy_pressure = (
        buy_sell_ratio >= 1.5
    )

    # --------------------------------------------------------
    # Liquidity
    # --------------------------------------------------------

    liquidity_stable = (
        liq_move >= -15.0
    )

    # --------------------------------------------------------
    # Score
    #
    # Existing scoring retained.
    # --------------------------------------------------------

    score = 0

    reasons: List[str] = []

    if (
        len(recent_5) >= 5
        and range_pct <= 18.0
        and abs(mc_move) <= 18.0
    ):

        score += 2

        reasons.append(
            "base"
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

    if buy_pressure:

        score += 1

        reasons.append(
            "buy pressure"
        )

    if strong_buy_pressure:

        score += 2

        reasons.append(
            "strong buy pressure"
        )

    if liquidity_stable:

        score += 1

        reasons.append(
            "liquidity stable"
        )

    if (
        snapshot.volume_5m
        >= REFERENCE_VOLUME_5M
    ):

        score += 1

        reasons.append(
            "5m volume > $5K"
        )

    if (
        snapshot.sells_5m
        > snapshot.buys_5m
        and total_tx > 0
    ):

        reasons.append(
            "sell pressure"
        )

    # --------------------------------------------------------
    # Consolidation gate
    # --------------------------------------------------------

    def meaningful_activity(
        item: Dict[str, Any]
    ) -> bool:

        volume = numeric(
            item.get(
                "volume_5m"
            )
        )

        buys = int(
            numeric(
                item.get(
                    "buys_5m"
                )
            )
        )

        sells = int(
            numeric(
                item.get(
                    "sells_5m"
                )
            )
        )

        return (
            volume
            >= MIN_CONSOLIDATION_VOLUME_5M
            and
            buys + sells
            >= MIN_CONSOLIDATION_TX
        )

    meaningful_recent = [
        item
        for item in recent_5
        if meaningful_activity(
            item
        )
    ]

    consolidation = (
        len(recent_5) >= 5
        and
        len(
            meaningful_recent
        )
        >= MIN_CONSOLIDATION_ACTIVE_OBS
        and
        meaningful_activity(
            snapshot_dict(
                snapshot
            )
        )
        and
        range_pct <= 18.0
        and
        abs(mc_move) <= 18.0
    )

    runner_market_cap = (
        MIN_MC
        <= snapshot.market_cap
        <= MAX_MC
    )

    runner_liquidity = (
        snapshot.liquidity
        >= MIN_LIQUIDITY
    )

    ignition = (
        runner_market_cap
        and
        runner_liquidity
        and
        breakout
        and
        activity_expansion
        and
        score >= 7
    )

    if ignition:

        state = "IGNITION"

    elif (
        breakout
        and runner_market_cap
    ):

        state = "STRUCTURE BREAK"

    elif activity_expansion:

        state = "EXPANSION"

    elif consolidation:

        state = "CONSOLIDATION"

    else:

        state = "OBSERVING"

    if (
        state == "CONSOLIDATION"
        and
        not meaningful_activity(
            snapshot_dict(
                snapshot
            )
        )
    ):

        state = "OBSERVING"

        reasons.append(
            "insufficient activity"
        )

    return {
        "state": state,
        "score": score,
        "reasons": reasons,

        "breakout": breakout,
        "activity_expansion":
            activity_expansion,

        "mc_move": mc_move,
        "liq_move": liq_move,
        "range": range_pct,

        "vol_change":
            vol_change,

        "tx_change":
            tx_change,

        "vol_change_1m":
            vol_change_1m,

        "vol_change_2m":
            vol_change_2m,

        "tx_change_1m":
            tx_change_1m,

        "tx_change_2m":
            tx_change_2m,

        "volume_acceleration":
            volume_acceleration,

        "tx_acceleration":
            tx_acceleration,

        "buy_sell_ratio":
            buy_sell_ratio,

        "runner_market_cap":
            runner_market_cap,

        "runner_liquidity":
            runner_liquidity,

        "consolidation":
            consolidation,
    }


# ============================================================
# Ignition event handling
# ============================================================

def ignition_already_exists(
    address: str,
    timestamp: float,
) -> bool:

    for event in ignition_events:

        if (
            event.get(
                "address"
            )
            != address
        ):
            continue

        previous = numeric(
            event.get(
                "ignition_timestamp"
            )
        )

        if (
            previous > 0
            and
            timestamp - previous
            < 30 * 60
        ):

            return True

    return False


def create_ignition_event(
    snapshot: TokenSnapshot,
    analysis: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    if not (
        MIN_MC
        <= snapshot.market_cap
        <= MAX_MC
    ):

        return None

    if (
        snapshot.liquidity
        < MIN_LIQUIDITY
    ):

        return None

    timestamp = (
        snapshot.timestamp
    )

    if ignition_already_exists(
        snapshot.address,
        timestamp,
    ):

        return None

    event = {

        "event_id": (
            f"{snapshot.address}:"
            f"{int(timestamp)}"
        ),

        "address":
            snapshot.address,

        "symbol":
            snapshot.symbol,

        "ignition_timestamp":
            timestamp,

        "ignition_mc":
            snapshot.market_cap,

        "ignition_liquidity":
            snapshot.liquidity,

        "ignition_volume_5m":
            snapshot.volume_5m,

        "ignition_buys_5m":
            snapshot.buys_5m,

        "ignition_sells_5m":
            snapshot.sells_5m,

        "ignition_score":
            analysis["score"],

        "ignition_range":
            analysis["range"],

        "ignition_mc_move":
            analysis["mc_move"],

        "ignition_liq_move":
            analysis["liq_move"],

        "ignition_volume_acceleration":
            analysis[
                "volume_acceleration"
            ],

        "ignition_tx_acceleration":
            analysis[
                "tx_acceleration"
            ],

        "ignition_volume_change_1m":
            analysis[
                "vol_change_1m"
            ],

        "ignition_volume_change_2m":
            analysis[
                "vol_change_2m"
            ],

        "ignition_tx_change_1m":
            analysis[
                "tx_change_1m"
            ],

        "ignition_tx_change_2m":
            analysis[
                "tx_change_2m"
            ],

        "outcomes": {},

        "peak_mc":
            snapshot.market_cap,

        "peak_mc_change_pct":
            0.0,

        "max_drawdown_pct":
            0.0,

        "final_status":
            "PENDING",
    }

    ignition_events.append(
        event
    )

    print(
        "🔥 IGNITION DETECTED | "
        f"{snapshot.symbol} | "
        f"CA="
        f"{shorten_address(snapshot.address)} | "
        f"MC="
        f"{format_usd(snapshot.market_cap)} | "
        f"5mVol="
        f"{format_usd(snapshot.volume_5m)} | "
        "buys/sells="
        f"{snapshot.buys_5m}/"
        f"{snapshot.sells_5m} | "
        f"score={analysis['score']} | "
        f"1mVol="
        f"{analysis['vol_change_1m']:+.1f}% | "
        f"1mTx="
        f"{analysis['tx_change_1m']:+.1f}%"
    )

    return event


# ============================================================
# Outcome helpers
# ============================================================

def classify_outcome(
    ignition_mc: float,
    current_mc: float,
) -> str:

    change = pct_change(
        ignition_mc,
        current_mc,
    )

    if (
        change
        >= CONTINUING_MC_CHANGE
    ):

        return "CONTINUING"

    if (
        change
        <= FAILED_MC_CHANGE
    ):

        return "FAILED"

    return "UNCLEAR"


def find_snapshot_near_target(
    history: List[Dict[str, Any]],
    target_time: float,
    tolerance: float,
) -> Optional[Dict[str, Any]]:

    if not history:
        return None

    candidates = [
        item
        for item in history
        if abs(
            numeric(
                item.get(
                    "timestamp"
                )
            )
            - target_time
        )
        <= tolerance
    ]

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda item:
        abs(
            numeric(
                item.get(
                    "timestamp"
                )
            )
            - target_time
        )
    )


def update_event_peak(
    event: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> bool:

    ignition_time = numeric(
        event.get(
            "ignition_timestamp"
        )
    )

    ignition_mc = numeric(
        event.get(
            "ignition_mc"
        )
    )

    if (
        ignition_time <= 0
        or ignition_mc <= 0
    ):

        return False

    post_ignition = [
        item
        for item in history
        if numeric(
            item.get(
                "timestamp"
            )
        )
        >= ignition_time
    ]

    if not post_ignition:
        return False

    peak_mc = max(
        numeric(
            item.get(
                "market_cap"
            )
        )
        for item in post_ignition
    )

    peak_change = pct_change(
        ignition_mc,
        peak_mc,
    )

    minimum_mc = min(
        numeric(
            item.get(
                "market_cap"
            )
        )
        for item in post_ignition
        if numeric(
            item.get(
                "market_cap"
            )
        ) > 0
    )

    minimum_change = pct_change(
        ignition_mc,
        minimum_mc,
    )

    old_peak = numeric(
        event.get(
            "peak_mc"
        )
    )

    old_drawdown = numeric(
        event.get(
            "max_drawdown_pct"
        )
    )

    changed = False

    if peak_mc > old_peak:

        event["peak_mc"] = peak_mc

        event[
            "peak_mc_change_pct"
        ] = peak_change

        changed = True

    if (
        minimum_change
        < old_drawdown
    ):

        event[
            "max_drawdown_pct"
        ] = minimum_change

        changed = True

    return changed


def final_event_status(
    event: Dict[str, Any],
) -> str:

    outcomes = (
        event.get(
            "outcomes",
            {}
        )
        or {}
    )

    thirty = outcomes.get(
        "30m"
    )

    if thirty:

        change = numeric(
            thirty.get(
                "mc_change_pct"
            )
        )

        if (
            change
            >= FINAL_RUNNER_CHANGE
        ):

            return "RUNNER"

        if (
            change
            <= FAILED_MC_CHANGE
        ):

            return "FAILED"

    peak_change = numeric(
        event.get(
            "peak_mc_change_pct"
        )
    )

    drawdown = numeric(
        event.get(
            "max_drawdown_pct"
        )
    )

    # A large temporary expansion is NOT
    # automatically called a runner.
    #
    # We only use peak as a secondary signal
    # when there is no 30m outcome yet.
    if (
        peak_change
        >= 50.0
        and
        drawdown
        > -25.0
    ):

        return "CONTINUING"

    return "UNCLEAR"


# ============================================================
# Outcome tracking
# ============================================================

def update_ignition_outcomes() -> None:

    current_time = now_ts()

    changed = False

    for event in ignition_events:

        address = event.get(
            "address"
        )

        ignition_time = numeric(
            event.get(
                "ignition_timestamp"
            )
        )

        ignition_mc = numeric(
            event.get(
                "ignition_mc"
            )
        )

        if (
            not address
            or ignition_time <= 0
            or ignition_mc <= 0
        ):

            continue

        history = histories.get(
            address,
            []
        )

        if not history:
            continue

        if update_event_peak(
            event,
            history,
        ):

            changed = True

        outcomes = event.setdefault(
            "outcomes",
            {}
        )

        for label, seconds in (
            OUTCOME_WINDOWS.items()
        ):

            if label in outcomes:
                continue

            target_time = (
                ignition_time
                + seconds
            )

            if (
                current_time
                < target_time
            ):

                continue

            # Tolerance increases with
            # the observation window.
            if label == "5m":
                tolerance = 60.0

            elif label == "15m":
                tolerance = 90.0

            else:
                tolerance = 120.0

            target_snapshot = (
                find_snapshot_near_target(
                    history,
                    target_time,
                    tolerance,
                )
            )

            if not target_snapshot:
                continue

            target_mc = numeric(
                target_snapshot.get(
                    "market_cap"
                )
            )

            if target_mc <= 0:
                continue

            change = pct_change(
                ignition_mc,
                target_mc,
            )

            status = classify_outcome(
                ignition_mc,
                target_mc,
            )

            outcomes[label] = {

                "timestamp":
                    numeric(
                        target_snapshot.get(
                            "timestamp"
                        )
                    ),

                "mc":
                    target_mc,

                "mc_change_pct":
                    change,

                "status":
                    status,
            }

            print(
                f"📊 OUTCOME {label} | "
                f"{event.get('symbol', '?')} | "
                f"MC="
                f"{format_usd(target_mc)} | "
                f"change="
                f"{change:+.1f}% | "
                f"{status}"
            )

            changed = True

        old_final = event.get(
            "final_status",
            "PENDING"
        )

        new_final = final_event_status(
            event
        )

        if (
            new_final != old_final
        ):

            event[
                "final_status"
            ] = new_final

            if (
                new_final
                in (
                    "RUNNER",
                    "FAILED",
                )
            ):

                print(
                    "🏁 FINAL EVENT | "
                    f"{event.get('symbol', '?')} | "
                    f"status={new_final} | "
                    f"peak="
                    f"{event.get('peak_mc_change_pct', 0.0):+.1f}% | "
                    f"drawdown="
                    f"{event.get('max_drawdown_pct', 0.0):+.1f}%"
                )

            changed = True

    if changed:
        save_state()


# ============================================================
# Telegram
# ============================================================

def telegram_request(
    method: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
) -> Optional[
    Dict[str, Any]
]:

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

    if params:

        query = urllib.parse.urlencode(
            params
        )

        url = (
            f"{url}?{query}"
        )

    return http_json(
        url
    )


def send_telegram_message(
    chat_id: int,
    text: str,
) -> bool:

    result = telegram_request(
        "sendMessage",
        {
            "chat_id":
                chat_id,

            "text":
                text,
        },
    )

    return bool(
        result
        and result.get(
            "ok"
        )
    )


def send_alert(
    snapshot: TokenSnapshot,
    analysis: Dict[str, Any],
) -> None:

    if not HEATING_ALERTS_ENABLED:
        return

    if not telegram_subscribers:
        return

    message = (
        "🔥 IGNITION DETECTED\n\n"
        f"{snapshot.symbol}\n"
        f"MC: "
        f"{format_usd(snapshot.market_cap)}\n"
        f"Liquidity: "
        f"{format_usd(snapshot.liquidity)}\n"
        f"5m Volume: "
        f"{format_usd(snapshot.volume_5m)}\n"
        f"Buys/Sells: "
        f"{snapshot.buys_5m}/"
        f"{snapshot.sells_5m}\n"
        f"Score: "
        f"{analysis['score']}\n"
        f"1m activity: "
        f"{analysis['vol_change_1m']:+.1f}% volume / "
        f"{analysis['tx_change_1m']:+.1f}% tx\n"
        f"CA: "
        f"{snapshot.address}\n\n"
        "Research signal only. "
        "Always verify the token yourself."
    )

    for chat_id in list(
        telegram_subscribers
    ):

        try:

            send_telegram_message(
                chat_id,
                message,
            )

        except Exception as exc:

            print(
                "Telegram alert error: "
                f"{exc}"
            )


def process_telegram_updates() -> None:

    global last_update_id
    global telegram_subscribers

    if not TELEGRAM_BOT_TOKEN:
        return

    result = telegram_request(
        "getUpdates",
        {
            "offset":
                last_update_id + 1,

            "timeout":
                1,
        },
    )

    if (
        not result
        or not result.get(
            "ok"
        )
    ):

        return

    updates = result.get(
        "result",
        []
    )

    for update in updates:

        update_id = int(
            update.get(
                "update_id",
                0
            )
        )

        if (
            update_id
            > last_update_id
        ):

            last_update_id = (
                update_id
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

        if chat_id is None:
            continue

        text = str(
            message.get(
                "text",
                ""
            )
        ).strip()

        if text.startswith(
            "/start"
        ):

            if (
                chat_id
                not in telegram_subscribers
            ):

                telegram_subscribers.append(
                    int(chat_id)
                )

                save_state()

            send_telegram_message(
                int(chat_id),
                (
                    "Runner bot is online.\n"
                    "You are subscribed to "
                    "runner alerts."
                ),
            )


# ============================================================
# Research summary
# ============================================================

def print_research_summary() -> None:

    if not ignition_events:
        return

    runners = 0
    failed = 0
    unclear = 0
    pending = 0

    for event in ignition_events:

        status = event.get(
            "final_status",
            "PENDING"
        )

        if status == "RUNNER":
            runners += 1

        elif status == "FAILED":
            failed += 1

        elif status == "UNCLEAR":
            unclear += 1

        else:
            pending += 1

    print(
        "📈 IGNITION RESEARCH SUMMARY | "
        f"total={len(ignition_events)} | "
        f"RUNNER={runners} | "
        f"FAILED={failed} | "
        f"UNCLEAR={unclear} | "
        f"PENDING={pending}"
    )


# ============================================================
# Main scan
# ============================================================

def scan_once() -> None:

    pairs = discover_pairs()

    unique_tokens: Dict[
        str,
        TokenSnapshot
    ] = {}

    histories_recorded = 0

    in_runner_range = 0
    reached_liquidity = 0

    mc_reject = 0
    liquidity_reject = 0
    volume_reference_count = 0
    missing_data = 0
    ignition_count = 0

    for pair in pairs:

        snapshot = pair_to_snapshot(
            pair
        )

        if snapshot is None:

            missing_data += 1

            continue

        existing = (
            unique_tokens.get(
                snapshot.address
            )
        )

        if (
            existing is None
            or
            snapshot.liquidity
            > existing.liquidity
        ):

            unique_tokens[
                snapshot.address
            ] = snapshot

    for snapshot in (
        unique_tokens.values()
    ):

        if record_snapshot(
            snapshot
        ):

            histories_recorded += 1

        if not (
            MIN_MC
            <= snapshot.market_cap
            <= MAX_MC
        ):

            mc_reject += 1

            continue

        in_runner_range += 1

        if (
            snapshot.liquidity
            < MIN_LIQUIDITY
        ):

            liquidity_reject += 1

            continue

        reached_liquidity += 1

        if (
            snapshot.volume_5m
            >= REFERENCE_VOLUME_5M
        ):

            volume_reference_count += 1

        analysis = analyze(
            snapshot
        )

        history = histories.get(
            snapshot.address,
            []
        )

        if (
            len(history)
            < MIN_OBSERVATIONS
        ):

            continue

        reasons = ",".join(
            analysis[
                "reasons"
            ]
        )

        print(
            f"TRACKING "
            f"{snapshot.symbol} | "
            f"CA="
            f"{shorten_address(snapshot.address)} | "
            f"state="
            f"{analysis['state']} | "
            f"obs={len(history)} | "
            f"MC="
            f"{format_usd(snapshot.market_cap)} | "
            f"5mVol="
            f"{format_usd(snapshot.volume_5m)} | "
            "buys/sells="
            f"{snapshot.buys_5m}/"
            f"{snapshot.sells_5m} | "
            "1mVol="
            f"{analysis['vol_change_1m']:+.1f}% | "
            "2mVol="
            f"{analysis['vol_change_2m']:+.1f}% | "
            "1mTx="
            f"{analysis['tx_change_1m']:+.1f}% | "
            "2mTx="
            f"{analysis['tx_change_2m']:+.1f}% | "
            "MCmove="
            f"{analysis['mc_move']:+.1f}% | "
            "liqMove="
            f"{analysis['liq_move']:+.1f}% | "
            "range="
            f"{analysis['range']:.1f}% | "
            f"breakout="
            f"{analysis['breakout']} | "
            f"score="
            f"{analysis['score']} | "
            f"reasons={reasons}"
        )

        if (
            analysis["state"]
            == "IGNITION"
        ):

            event = (
                create_ignition_event(
                    snapshot,
                    analysis,
                )
            )

            if event:

                ignition_count += 1

                send_alert(
                    snapshot,
                    analysis,
                )

    print(
        "DEX scan complete: "
        f"{len(pairs)} Solana pairs "
        "discovered | "
        f"{len(unique_tokens)} "
        "unique tokens | "
        f"{histories_recorded} "
        "histories recorded | "
        f"{in_runner_range} "
        "in runner range | "
        f"{reached_liquidity} "
        "reached liquidity filter | "
        f"MC outside range="
        f"{mc_reject} | "
        "liquidity below minimum="
        f"{liquidity_reject} | "
        "volume >= reference="
        f"{volume_reference_count} | "
        f"missing data="
        f"{missing_data} | "
        f"ignition="
        f"{ignition_count}"
    )

    update_ignition_outcomes()

    print_research_summary()

    save_state()


# ============================================================
# Main
# ============================================================

def main() -> None:

    print(
        f"Runner Engine {BOT_VERSION} "
        "is online"
    )

    print(
        "Runner range: "
        f"MC={format_usd(MIN_MC)}-"
        f"{format_usd(MAX_MC)} | "
        "minimum liquidity="
        f"{format_usd(MIN_LIQUIDITY)} | "
        "5m volume is a FEATURE, "
        "not a hard gate | "
        f"scan interval="
        f"{SCAN_INTERVAL_SECONDS:.1f}s"
    )

    print(
        "Observation band: "
        f"{format_usd(OBSERVE_MIN_MC)}-"
        f"{format_usd(OBSERVE_MAX_MC)}"
    )

    print(
        "Consolidation activity gate: "
        "5m volume >= "
        f"{format_usd(MIN_CONSOLIDATION_VOLUME_5M)} | "
        "transactions >= "
        f"{MIN_CONSOLIDATION_TX} | "
        f"{MIN_CONSOLIDATION_ACTIVE_OBS}/5 "
        "recent observations"
    )

    print(
        "V3.8 activity measurement: "
        "1m + 2m rolling-window acceleration"
    )

    print(
        "Ignition outcome tracking: "
        "+5m / +15m / +30m | "
        "peak MC | max drawdown"
    )

    print(
        "Final research labels: "
        "RUNNER / FAILED / UNCLEAR"
    )

    print(
        "Telegram alerts enabled: "
        f"{HEATING_ALERTS_ENABLED}"
    )

    load_state()

    discover_pairs(
        force=True
    )

    while True:

        cycle_start = now_ts()

        try:

            process_telegram_updates()

        except Exception as exc:

            print(
                "Telegram polling error: "
                f"{exc}"
            )

        try:

            scan_once()

        except Exception as exc:

            print(
                f"Scan error: {exc}"
            )

        try:

            process_telegram_updates()

        except Exception as exc:

            print(
                "Telegram polling error: "
                f"{exc}"
            )

        elapsed = (
            now_ts()
            - cycle_start
        )

        sleep_for = max(
            1.0,
            SCAN_INTERVAL_SECONDS
            - elapsed,
        )

        time.sleep(
            sleep_for
        )


if __name__ == "__main__":
    main()
