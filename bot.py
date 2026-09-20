import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V4.6
# EARLY MC BIAS + PERSISTENT REJECTION TRACKING
# ============================================================

BOT_VERSION = "V4.6-EARLY-MC-TRACKING"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

STATE_FILE = "runner_state_v46.json"


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

SCAN_INTERVAL_SECONDS = int(
    os.getenv(
        "SCAN_INTERVAL_SECONDS",
        "15"
    )
)


# ============================================================
# EXACT V4.6 DECISION-TREE SETTINGS
# ============================================================

# ------------------------------------------------------------
# 1. BASIC SANITY
# ------------------------------------------------------------

MIN_SANITY_MC = 1_000
MAX_SANITY_ABS_FLOW_MC_PCT = 150.0


# ------------------------------------------------------------
# 2. LIQUIDITY
# ------------------------------------------------------------

MIN_LIQUIDITY = 6_000

EARLY_PUMPFUN_MAX_AGE_HOURS = 1.5
EARLY_PUMPFUN_MIN_VOLUME_5M = 8_000
EARLY_PUMPFUN_MIN_FLOW = 3_000
EARLY_PUMPFUN_MIN_FLOW_MC_PCT = 12.0


# ------------------------------------------------------------
# 3. AGE
# ------------------------------------------------------------

MAX_AGE_HOURS = 48.0


# ------------------------------------------------------------
# 4. MARKET CAP — V4.6 EARLY BIAS
# ------------------------------------------------------------

MIN_MC = 7_000

MAX_MC_NORMAL = 90_000

MAX_MC_EXTENDED = 140_000

# Extended MC now requires stronger flow pressure.
EXTENDED_MC_MIN_FLOW_PCT = 14.0


# ------------------------------------------------------------
# 5. CORE MOMENTUM
# ------------------------------------------------------------

MIN_FLOW = 1_500
MIN_FLOW_MC_PCT = 8.0


# ------------------------------------------------------------
# 6. SOFT METRICS
# ------------------------------------------------------------

PREFERRED_VOLUME_FLOW_RATIO = 1.25
PREFERRED_BUY_SELL_RATIO = 1.50


# ------------------------------------------------------------
# 7. SIGNAL STRENGTH
# ------------------------------------------------------------

ULTRA_FLOW_MC = 25.0
ULTRA_FLOW_ABS = 25_000

STRONG_FLOW_MC = 15.0
STRONG_FLOW_ABS = 15_000

NORMAL_OBSERVATIONS = 3
STRONG_OBSERVATIONS = 2
ULTRA_OBSERVATIONS = 1

PENDING_EXPIRY_SECONDS = 5 * 60


# ============================================================
# ALERT COOLDOWNS
# ============================================================

ALERT_COOLDOWN_SECONDS = 10 * 60
GLOBAL_ALERT_COOLDOWN_SECONDS = 60


# ============================================================
# HISTORY / DISCOVERY
# ============================================================

MAX_STORED_TOKENS = 1500
MAX_HISTORY_PER_TOKEN = 300

MAX_DISCOVERY_TOKENS = 500
DEX_BATCH_SIZE = 25


# ============================================================
# TRACKING
# ============================================================

# Persistent rejection history.
MAX_REJECTION_HISTORY = 5000

# Persistent scan-level history.
MAX_SCAN_TRACKING_HISTORY = 1000


# ============================================================
# DEBUGGING
# ============================================================

DEBUG_MODE = (
    os.getenv(
        "RUNNER_DEBUG",
        "true"
    ).lower()
    in ("1", "true", "yes", "on")
)

DEBUG_TOP_CANDIDATES = 10

# Performance/noise pre-filter ONLY.
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
) -> Optional[Any]:

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RunnerBot/4.6",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=timeout,
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

            if not raw:
                return None

            return json.loads(raw)

    except Exception as exc:

        print(
            f"[HTTP ERROR] {exc}"
        )

        return None


# ============================================================
# TIME / SAFE HELPERS
# ============================================================

def now_ts() -> int:
    return int(time.time())


def iso_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:

    try:
        if value is None:
            return default

        return float(value)

    except Exception:
        return default


def safe_int(
    value: Any,
    default: int = 0,
) -> int:

    try:
        if value is None:
            return default

        return int(value)

    except Exception:
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
    ) * 100.0


# ============================================================
# STATE
# ============================================================

def default_tracking_state() -> Dict[str, Any]:

    return {
        "total_scans": 0,

        "total_discovered": 0,

        "total_prefiltered": 0,

        "total_processed": 0,

        "total_rejected": 0,

        "total_qualified": 0,

        "rejection_counts": {},

        "qualification_counts": {},

        "recent_rejections": [],

        "recent_qualifications": [],

        "recent_scans": [],

        "created_at": iso_now(),

        "updated_at": iso_now(),
    }


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

        # Persistent V4.6 tracking.
        "tracking": default_tracking_state(),
    }


def load_state() -> Dict[str, Any]:

    if not os.path.exists(
        STATE_FILE
    ):
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

        # ----------------------------------------------------
        # Ensure tracking exists.
        # ----------------------------------------------------

        if not isinstance(
            state.get("tracking"),
            dict,
        ):

            state["tracking"] = (
                default_tracking_state()
            )

        tracking_base = (
            default_tracking_state()
        )

        for key, value in tracking_base.items():

            if key not in state["tracking"]:
                state["tracking"][key] = value

        # ----------------------------------------------------
        # Compatibility cleanup.
        # ----------------------------------------------------

        if not isinstance(
            state["tracking"].get(
                "rejection_counts"
            ),
            dict,
        ):

            state["tracking"][
                "rejection_counts"
            ] = {}

        if not isinstance(
            state["tracking"].get(
                "qualification_counts"
            ),
            dict,
        ):

            state["tracking"][
                "qualification_counts"
            ] = {}

        if not isinstance(
            state["tracking"].get(
                "recent_rejections"
            ),
            list,
        ):

            state["tracking"][
                "recent_rejections"
            ] = []

        if not isinstance(
            state["tracking"].get(
                "recent_qualifications"
            ),
            list,
        ):

            state["tracking"][
                "recent_qualifications"
            ] = []

        if not isinstance(
            state["tracking"].get(
                "recent_scans"
            ),
            list,
        ):

            state["tracking"][
                "recent_scans"
            ] = []

        state["version"] = BOT_VERSION

        return state

    except Exception as exc:

        print(
            f"[STATE] Failed to load state: {exc}"
        )

        return default_state()


STATE = load_state()


def save_state() -> None:

    STATE["version"] = BOT_VERSION
    STATE["updated_at"] = iso_now()

    if isinstance(
        STATE.get("tracking"),
        dict,
    ):

        STATE["tracking"][
            "updated_at"
        ] = iso_now()

    try:

        tmp_file = (
            STATE_FILE
            + ".tmp"
        )

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

        print(
            f"[STATE] Save error: {exc}"
        )


# ============================================================
# PERSISTENT TRACKING
# ============================================================

def tracking_state() -> Dict[str, Any]:

    tracking = STATE.setdefault(
        "tracking",
        default_tracking_state(),
    )

    return tracking


def increment_tracking_counter(
    key: str,
    amount: int = 1,
) -> None:

    tracking = tracking_state()

    tracking[key] = (
        safe_int(
            tracking.get(key),
            0,
        )
        + amount
    )


def increment_rejection_reason(
    reason: str,
) -> None:

    tracking = tracking_state()

    counts = tracking.setdefault(
        "rejection_counts",
        {},
    )

    counts[reason] = (
        safe_int(
            counts.get(reason),
            0,
        )
        + 1
    )


def increment_qualification_reason(
    strength: str,
) -> None:

    tracking = tracking_state()

    counts = tracking.setdefault(
        "qualification_counts",
        {},
    )

    counts[strength] = (
        safe_int(
            counts.get(strength),
            0,
        )
        + 1
    )


def add_rejection_tracking(
    snapshot: Dict[str, Any],
    reason: str,
) -> None:

    tracking = tracking_state()

    record = {
        "timestamp": now_ts(),
        "iso_time": iso_now(),

        "symbol": snapshot.get(
            "symbol",
            "UNKNOWN",
        ),

        "address": snapshot.get(
            "address",
            "",
        ),

        "reason": reason,

        "market_cap": safe_float(
            snapshot.get(
                "market_cap"
            ),
            0.0,
        ),

        "liquidity": (
            safe_float(
                snapshot.get(
                    "liquidity"
                ),
                0.0,
            )
            if snapshot.get(
                "liquidity"
            ) is not None
            else None
        ),

        "liquidity_valid": bool(
            snapshot.get(
                "liquidity_data_valid",
                False,
            )
        ),

        "age_hours": (
            safe_float(
                snapshot.get(
                    "age_hours"
                ),
                0.0,
            )
            if snapshot.get(
                "age_hours"
            ) is not None
            else None
        ),

        "volume_5m": safe_float(
            snapshot.get(
                "volume_5m"
            ),
            0.0,
        ),

        "flow_proxy_5m": safe_float(
            snapshot.get(
                "flow_proxy_5m"
            ),
            0.0,
        ),

        "flow_mc_pct": safe_float(
            snapshot.get(
                "flow_pressure_pct"
            ),
            0.0,
        ),

        "volume_flow_ratio": safe_float(
            snapshot.get(
                "volume_flow_ratio"
            ),
            0.0,
        ),

        "buy_sell_ratio": safe_float(
            snapshot.get(
                "buy_sell_ratio"
            ),
            0.0,
        ),

        "buys_5m": safe_int(
            snapshot.get(
                "buys_5m"
            ),
            0,
        ),

        "sells_5m": safe_int(
            snapshot.get(
                "sells_5m"
            ),
            0,
        ),

        "tx_5m": safe_int(
            snapshot.get(
                "tx_5m"
            ),
            0,
        ),

        "price_change_5m": safe_float(
            snapshot.get(
                "price_change_5m"
            ),
            0.0,
        ),

        "dex": snapshot.get(
            "dex_id",
            "unknown",
        ),

        "pair_address": snapshot.get(
            "pair_address",
            "",
        ),

        "url": snapshot.get(
            "url",
            "",
        ),
    }

    history = tracking.setdefault(
        "recent_rejections",
        [],
    )

    history.append(record)

    if (
        len(history)
        > MAX_REJECTION_HISTORY
    ):

        del history[
            :-MAX_REJECTION_HISTORY
        ]

    increment_tracking_counter(
        "total_rejected"
    )

    increment_rejection_reason(
        reason
    )


def add_qualification_tracking(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any],
) -> None:

    tracking = tracking_state()

    strength = str(
        analysis.get(
            "signal_strength",
            "NORMAL",
        )
    )

    record = {
        "timestamp": now_ts(),
        "iso_time": iso_now(),

        "symbol": snapshot.get(
            "symbol",
            "UNKNOWN",
        ),

        "address": snapshot.get(
            "address",
            "",
        ),

        "strength": strength,

        "market_cap": safe_float(
            snapshot.get(
                "market_cap"
            ),
            0.0,
        ),

        "liquidity": (
            safe_float(
                snapshot.get(
                    "liquidity"
                ),
                0.0,
            )
            if snapshot.get(
                "liquidity"
            ) is not None
            else None
        ),

        "age_hours": (
            safe_float(
                snapshot.get(
                    "age_hours"
                ),
                0.0,
            )
            if snapshot.get(
                "age_hours"
            ) is not None
            else None
        ),

        "volume_5m": safe_float(
            snapshot.get(
                "volume_5m"
            ),
            0.0,
        ),

        "flow_proxy_5m": safe_float(
            snapshot.get(
                "flow_proxy_5m"
            ),
            0.0,
        ),

        "flow_mc_pct": safe_float(
            snapshot.get(
                "flow_pressure_pct"
            ),
            0.0,
        ),

        "volume_flow_ratio": safe_float(
            snapshot.get(
                "volume_flow_ratio"
            ),
            0.0,
        ),

        "buy_sell_ratio": safe_float(
            snapshot.get(
                "buy_sell_ratio"
            ),
            0.0,
        ),

        "extended_mc": bool(
            analysis.get(
                "extended_mc",
                False,
            )
        ),

        "liquidity_fallback": bool(
            analysis.get(
                "liquidity_fallback",
                False,
            )
        ),

        "score": safe_int(
            analysis.get(
                "score"
            ),
            0,
        ),
    }

    history = tracking.setdefault(
        "recent_qualifications",
        [],
    )

    history.append(record)

    if (
        len(history)
        > MAX_REJECTION_HISTORY
    ):

        del history[
            :-MAX_REJECTION_HISTORY
        ]

    increment_tracking_counter(
        "total_qualified"
    )

    increment_qualification_reason(
        strength
    )


def record_prefiltered_count(
    count: int,
) -> None:

    if count <= 0:
        return

    increment_tracking_counter(
        "total_prefiltered",
        count,
    )


def record_discovered_count(
    count: int,
) -> None:

    if count <= 0:
        return

    increment_tracking_counter(
        "total_discovered",
        count,
    )


def record_processed_count(
    count: int,
) -> None:

    if count <= 0:
        return

    increment_tracking_counter(
        "total_processed",
        count,
    )


def record_scan_summary(
    discovered: int,
    prefiltered: int,
    processed: int,
    rejected: int,
    qualified: int,
) -> None:

    tracking = tracking_state()

    increment_tracking_counter(
        "total_scans"
    )

    record = {
        "timestamp": now_ts(),
        "iso_time": iso_now(),

        "discovered": discovered,
        "prefiltered": prefiltered,
        "processed": processed,
        "rejected": rejected,
        "qualified": qualified,
    }

    history = tracking.setdefault(
        "recent_scans",
        [],
    )

    history.append(record)

    if (
        len(history)
        > MAX_SCAN_TRACKING_HISTORY
    ):

        del history[
            :-MAX_SCAN_TRACKING_HISTORY
        ]


def print_tracking_summary() -> None:

    tracking = tracking_state()

    total_discovered = safe_int(
        tracking.get(
            "total_discovered"
        ),
        0,
    )

    total_prefiltered = safe_int(
        tracking.get(
            "total_prefiltered"
        ),
        0,
    )

    total_processed = safe_int(
        tracking.get(
            "total_processed"
        ),
        0,
    )

    total_rejected = safe_int(
        tracking.get(
            "total_rejected"
        ),
        0,
    )

    total_qualified = safe_int(
        tracking.get(
            "total_qualified"
        ),
        0,
    )

    print(
        "\n"
        + "=" * 75
    )

    print(
        "[V4.6 PERSISTENT TRACKING]"
    )

    print(
        f"Scans:       "
        f"{safe_int(tracking.get('total_scans'), 0):,}"
    )

    print(
        f"Discovered:  "
        f"{total_discovered:,}"
    )

    print(
        f"Pre-filtered:"
        f" {total_prefiltered:,}"
    )

    print(
        f"Processed:   "
        f"{total_processed:,}"
    )

    print(
        f"Rejected:    "
        f"{total_rejected:,}"
    )

    print(
        f"Qualified:   "
        f"{total_qualified:,}"
    )

    if total_processed > 0:

        rejection_rate = (
            total_rejected
            / total_processed
        ) * 100.0

        qualification_rate = (
            total_qualified
            / total_processed
        ) * 100.0

        print(
            f"Rejection rate:    "
            f"{rejection_rate:.2f}%"
        )

        print(
            f"Qualification rate:"
            f" {qualification_rate:.2f}%"
        )

    print(
        "\nREJECTION BREAKDOWN"
    )

    counts = tracking.get(
        "rejection_counts",
        {},
    )

    if counts:

        sorted_counts = sorted(
            counts.items(),
            key=lambda item:
                item[1],
            reverse=True,
        )

        for reason, count in sorted_counts:

            percentage = (
                (
                    count
                    / total_rejected
                ) * 100.0
                if total_rejected > 0
                else 0.0
            )

            print(
                f"{reason:<40} "
                f"{count:>7,} "
                f"({percentage:>5.1f}%)"
            )

    else:

        print(
            "No rejection data yet."
        )

    print(
        "\nQUALIFICATION BREAKDOWN"
    )

    qualification_counts = (
        tracking.get(
            "qualification_counts",
            {},
        )
    )

    if qualification_counts:

        sorted_qualifications = sorted(
            qualification_counts.items(),
            key=lambda item:
                item[1],
            reverse=True,
        )

        for strength, count in (
            sorted_qualifications
        ):

            print(
                f"{strength:<40} "
                f"{count:>7,}"
            )

    else:

        print(
            "No qualified tokens yet."
        )

    print(
        "=" * 75
    )


def tracking_text() -> str:

    tracking = tracking_state()

    total_discovered = safe_int(
        tracking.get(
            "total_discovered"
        ),
        0,
    )

    total_prefiltered = safe_int(
        tracking.get(
            "total_prefiltered"
        ),
        0,
    )

    total_processed = safe_int(
        tracking.get(
            "total_processed"
        ),
        0,
    )

    total_rejected = safe_int(
        tracking.get(
            "total_rejected"
        ),
        0,
    )

    total_qualified = safe_int(
        tracking.get(
            "total_qualified"
        ),
        0,
    )

    rejection_rate = (
        (
            total_rejected
            / total_processed
        ) * 100.0
        if total_processed > 0
        else 0.0
    )

    qualification_rate = (
        (
            total_qualified
            / total_processed
        ) * 100.0
        if total_processed > 0
        else 0.0
    )

    lines = [
        f"📊 Runner Bot {BOT_VERSION}",
        "",
        f"Scans: {safe_int(tracking.get('total_scans'), 0):,}",
        f"Discovered: {total_discovered:,}",
        f"Pre-filtered: {total_prefiltered:,}",
        f"Decision-tree processed: {total_processed:,}",
        f"Rejected: {total_rejected:,}",
        f"Qualified: {total_qualified:,}",
        "",
        f"Rejection rate: {rejection_rate:.2f}%",
        f"Qualification rate: {qualification_rate:.2f}%",
        "",
        "TOP REJECTION REASONS:",
    ]

    counts = tracking.get(
        "rejection_counts",
        {},
    )

    sorted_counts = sorted(
        counts.items(),
        key=lambda item:
            item[1],
        reverse=True,
    )

    for reason, count in sorted_counts[:10]:

        percentage = (
            (
                count
                / total_rejected
            ) * 100.0
            if total_rejected > 0
            else 0.0
        )

        lines.append(
            f"• {reason}: "
            f"{count:,} "
            f"({percentage:.1f}%)"
        )

    if not sorted_counts:

        lines.append(
            "• No rejection data yet."
        )

    lines.extend(
        [
            "",
            "V4.6 MC:",
            "• Min: $7K",
            "• Normal max: $90K",
            "• Extended max: $140K",
            "• Extended Flow/MC: 14%",
        ]
    )

    return "\n".join(lines)


# ============================================================
# TOKEN HELPERS
# ============================================================

def token_key(
    address: str,
) -> str:

    return address.lower().strip()


def get_token_state(
    address: str,
) -> Dict[str, Any]:

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
            items = data.get(
                "tokens",
                [],
            )

        else:
            items = []

        for item in items:

            if not isinstance(
                item,
                dict,
            ):
                continue

            chain = str(
                item.get(
                    "chainId",
                    "",
                )
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

            if (
                len(addresses)
                >= MAX_DISCOVERY_TOKENS
            ):
                break

        if (
            len(addresses)
            >= MAX_DISCOVERY_TOKENS
        ):
            break

    print(
        f"[DISCOVERY] Found "
        f"{len(addresses)} Solana tokens"
    )

    return addresses


# ============================================================
# PAIR FETCHING
# ============================================================

def get_token_pairs_batch(
    addresses: List[str],
) -> List[Dict[str, Any]]:

    all_pairs: List[
        Dict[str, Any]
    ] = []

    for start in range(
        0,
        len(addresses),
        DEX_BATCH_SIZE,
    ):

        batch = addresses[
            start:
            start + DEX_BATCH_SIZE
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

        if not isinstance(
            data,
            dict,
        ):
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

            if not isinstance(
                pair,
                dict,
            ):
                continue

            if str(
                pair.get(
                    "chainId",
                    "",
                )
            ).lower() != "solana":
                continue

            all_pairs.append(pair)

    return all_pairs


# ============================================================
# PAIR HELPERS
# ============================================================

def pair_liquidity(
    pair: Dict[str, Any],
) -> float:

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


def pair_volume_5m(
    pair: Dict[str, Any],
) -> float:

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

    value = liquidity.get(
        "usd"
    )

    if value is None:
        return False

    return (
        safe_float(
            value,
            0.0,
        )
        > 0
    )


# ============================================================
# PAIR SELECTION
# ============================================================

def choose_best_pairs(
    pairs: List[Dict[str, Any]],
) -> Dict[
    str,
    Dict[str, Any]
]:

    grouped: Dict[
        str,
        List[Dict[str, Any]]
    ] = {}

    for pair in pairs:

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

    base = (
        pair.get(
            "baseToken"
        )
        or {}
    )

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

    txns = (
        pair.get(
            "txns"
        )
        or {}
    )

    m5_txns = (
        txns.get(
            "m5"
        )
        or {}
    )

    buys = safe_int(
        m5_txns.get(
            "buys"
        ),
        0,
    )

    sells = safe_int(
        m5_txns.get(
            "sells"
        ),
        0,
    )

    total_tx = buys + sells

    volume = (
        pair.get(
            "volume"
        )
        or {}
    )

    volume_5m = safe_float(
        volume.get(
            "m5"
        ),
        0.0,
    )

    price_change = (
        pair.get(
            "priceChange"
        )
        or {}
    )

    price_change_5m = safe_float(
        price_change.get(
            "m5"
        ),
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
        and liquidity_obj.get(
            "usd"
        ) is not None
        and safe_float(
            liquidity_obj.get(
                "usd"
            ),
            0.0,
        ) > 0
    )

    if isinstance(
        liquidity_obj,
        dict,
    ):

        raw_liquidity = (
            liquidity_obj.get(
                "usd"
            )
        )

        liquidity = (
            safe_float(
                raw_liquidity,
                0.0,
            )
            if raw_liquidity is not None
            else None
        )

    else:

        liquidity = None

    market_cap = safe_float(
        pair.get(
            "marketCap"
        ),
        0.0,
    )

    if market_cap <= 0:

        market_cap = safe_float(
            pair.get(
                "fdv"
            ),
            0.0,
        )

    # ========================================================
    # FLOW PROXY
    #
    # 5m Volume × ((Buys - Sells) / Total Transactions)
    #
    # NOT actual dollar net flow.
    # ========================================================

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

        created_seconds = (
            pair_created_at / 1000.0
            if pair_created_at
            > 10_000_000_000
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

    dex_id = str(
        pair.get(
            "dexId",
            "unknown",
        )
    )

    quote_symbol = (
        (
            pair.get(
                "quoteToken"
            )
            or {}
        ).get(
            "symbol",
            "",
        )
    )

    return {
        "address": address,
        "symbol": symbol,
        "name": name,

        "market_cap": market_cap,

        "liquidity": liquidity,
        "liquidity_data_valid": liquidity_data_valid,

        "volume_5m": volume_5m,

        "buys_5m": buys,
        "sells_5m": sells,
        "tx_5m": total_tx,

        "buy_sell_ratio": buy_sell_ratio,

        "flow_proxy_5m": flow_proxy,
        "flow_pressure_pct": flow_pressure_pct,
        "volume_flow_ratio": volume_flow_ratio,

        "price_change_5m": price_change_5m,

        "age_hours": age_hours,

        "pair_address": pair.get(
            "pairAddress",
            "",
        ),

        "dex_id": dex_id,

        "quote_symbol": quote_symbol,

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

    address = snapshot[
        "address"
    ]

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

    if (
        len(history)
        > MAX_HISTORY_PER_TOKEN
    ):

        del history[
            :-MAX_HISTORY_PER_TOKEN
        ]

    if (
        len(STATE["tokens"])
        > MAX_STORED_TOKENS
    ):

        oldest = sorted(
            STATE["tokens"].items(),
            key=lambda item:
                item[1].get(
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
# DECISION TOKEN
# ============================================================

def build_decision_token(
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:

    return {
        "mc": safe_float(
            snapshot.get(
                "market_cap"
            ),
            0.0,
        ),

        "liquidity": snapshot.get(
            "liquidity"
        ),

        "age_hours": (
            snapshot.get(
                "age_hours"
            )
            if snapshot.get(
                "age_hours"
            ) is not None
            else 999
        ),

        "volume_5m": safe_float(
            snapshot.get(
                "volume_5m"
            ),
            0.0,
        ),

        "net_flow_5m": safe_float(
            snapshot.get(
                "flow_proxy_5m"
            ),
            0.0,
        ),

        "flow_mc_pct": safe_float(
            snapshot.get(
                "flow_pressure_pct"
            ),
            0.0,
        ),

        "buy_sell_ratio": safe_float(
            snapshot.get(
                "buy_sell_ratio"
            ),
            0.0,
        ),

        "vol_flow_ratio": safe_float(
            snapshot.get(
                "volume_flow_ratio"
            ),
            0.0,
        ),

        "dex": str(
            snapshot.get(
                "dex_id",
                ""
            )
        ).lower(),
    }


# ============================================================
# EXACT V4.6 QUALIFICATION ENGINE
# ============================================================

def evaluate_token(
    token: Dict[str, Any],
) -> Tuple[
    bool,
    Optional[str],
    str,
    int,
]:

    mc = token.get(
        "mc",
        0,
    )

    liq = token.get(
        "liquidity"
    )

    age_h = token.get(
        "age_hours",
        999,
    )

    vol_5m = token.get(
        "volume_5m",
        0,
    )

    flow = token.get(
        "net_flow_5m",
        0,
    )

    flow_mc = token.get(
        "flow_mc_pct",
        0,
    )

    dex = str(
        token.get(
            "dex",
            ""
        )
    ).lower()

    is_pumpfun = dex in [
        "pumpfun",
        "pump.fun",
        "pump",
    ]

    # --------------------------------------------------------
    # 1. BASIC SANITY
    # --------------------------------------------------------

    if (
        mc < MIN_SANITY_MC
        or abs(flow_mc)
        > MAX_SANITY_ABS_FLOW_MC_PCT
    ):

        return (
            False,
            None,
            "DATA_SANITY_FAIL",
            0,
        )

    # --------------------------------------------------------
    # 2. LIQUIDITY
    # --------------------------------------------------------

    if (
        liq is not None
        and liq >= MIN_LIQUIDITY
    ):

        liquidity_reason = "OK"

    elif (
        (liq is None or liq == 0)
        and is_pumpfun
    ):

        if (
            age_h <= EARLY_PUMPFUN_MAX_AGE_HOURS
            and vol_5m >= EARLY_PUMPFUN_MIN_VOLUME_5M
            and flow >= EARLY_PUMPFUN_MIN_FLOW
            and flow_mc >= EARLY_PUMPFUN_MIN_FLOW_MC_PCT
        ):

            liquidity_reason = (
                "EARLY_PUMPFUN_FALLBACK"
            )

        else:

            return (
                False,
                None,
                "LIQUIDITY_DATA_UNAVAILABLE",
                0,
            )

    else:

        return (
            False,
            None,
            "LIQUIDITY_DATA_UNAVAILABLE",
            0,
        )

    # --------------------------------------------------------
    # 3. AGE
    # --------------------------------------------------------

    if age_h > MAX_AGE_HOURS:

        return (
            False,
            None,
            "AGE_OVER_48H",
            0,
        )

    # --------------------------------------------------------
    # 4. MARKET CAP — V4.6
    # --------------------------------------------------------

    if mc < MIN_MC:

        return (
            False,
            None,
            "MC_BELOW_7K",
            0,
        )

    if mc > MAX_MC_EXTENDED:

        return (
            False,
            None,
            "MC_ABOVE_140K",
            0,
        )

    extended_mc = (
        mc > MAX_MC_NORMAL
    )

    if (
        extended_mc
        and flow_mc < EXTENDED_MC_MIN_FLOW_PCT
    ):

        return (
            False,
            None,
            "MC_EXTENDED_BUT_FLOWMC_BELOW_14PCT",
            0,
        )

    # --------------------------------------------------------
    # 5. CORE MOMENTUM
    # --------------------------------------------------------

    if flow < MIN_FLOW:

        return (
            False,
            None,
            "FLOW_BELOW_1500",
            0,
        )

    if flow_mc < MIN_FLOW_MC_PCT:

        return (
            False,
            None,
            "FLOW_MC_BELOW_8PCT",
            0,
        )

    # Volume/Flow and Buy/Sell remain SOFT.

    # --------------------------------------------------------
    # 6. STRENGTH
    # --------------------------------------------------------

    if (
        flow_mc >= ULTRA_FLOW_MC
        or flow >= ULTRA_FLOW_ABS
    ):

        strength = "ULTRA"
        needed_obs = ULTRA_OBSERVATIONS

    elif (
        flow_mc >= STRONG_FLOW_MC
        or flow >= STRONG_FLOW_ABS
    ):

        strength = "STRONG"
        needed_obs = STRONG_OBSERVATIONS

    else:

        strength = "NORMAL"
        needed_obs = NORMAL_OBSERVATIONS

    return (
        True,
        strength,
        (
            f"QUALIFIED_{strength} | "
            f"Liq={liquidity_reason}"
        ),
        needed_obs,
    )


# ============================================================
# CANDIDATE EVALUATION
# ============================================================

def evaluate_snapshot(
    snapshot: Dict[str, Any],
) -> Tuple[
    bool,
    Optional[str],
    str,
    int,
]:

    return evaluate_token(
        build_decision_token(
            snapshot
        )
    )


# ============================================================
# RANKING
# ============================================================

def ranking_key(
    snapshot: Dict[str, Any],
) -> Tuple[
    float,
    float,
    float,
]:

    flow_mc = safe_float(
        snapshot.get(
            "flow_pressure_pct"
        ),
        0.0,
    )

    absolute_flow = abs(
        safe_float(
            snapshot.get(
                "flow_proxy_5m"
            ),
            0.0,
        )
    )

    buy_sell = safe_float(
        snapshot.get(
            "buy_sell_ratio"
        ),
        0.0,
    )

    return (
        flow_mc,
        absolute_flow,
        buy_sell,
    )


# ============================================================
# NEAR MISSES / REJECTIONS
# ============================================================

def print_near_misses(
    near_misses: List[
        Tuple[
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
        key=lambda item:
            ranking_key(
                item[1]
            ),
        reverse=True,
    )

    limit = min(
        DEBUG_TOP_CANDIDATES,
        len(near_misses),
    )

    print(
        f"\n[REJECTIONS] "
        f"Top {limit} of {len(near_misses)} "
        f"processed rejected candidates"
    )

    for index, (
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

        buy_sell = safe_float(
            snapshot.get(
                "buy_sell_ratio"
            ),
            0.0,
        )

        ratio = safe_float(
            snapshot.get(
                "volume_flow_ratio"
            ),
            0.0,
        )

        age = snapshot.get(
            "age_hours"
        )

        liquidity = snapshot.get(
            "liquidity"
        )

        if (
            snapshot.get(
                "liquidity_data_valid",
                False,
            )
            and liquidity is not None
        ):

            liq_text = (
                f"${safe_float(liquidity):,.0f}"
            )

        else:

            liq_text = "N/A"

        age_text = (
            f"{safe_float(age):.1f}h"
            if age is not None
            else "N/A"
        )

        print(
            f"{index}. {symbol} | "
            f"Reason={reason} | "
            f"MC=${mc:,.0f} | "
            f"Liq={liq_text} | "
            f"Age={age_text} | "
            f"Flow/MC={flow_pct:.1f}% | "
            f"Flow=${flow:,.0f} | "
            f"Vol=${volume:,.0f} | "
            f"V/F={ratio:.2f}x | "
            f"Buy/Sell={buy_sell:.2f}x | "
            f"DEX={snapshot.get('dex_id', 'unknown')}"
        )


# ============================================================
# DIAGNOSTIC SCORE
# ============================================================

def score_token(
    snapshot: Dict[str, Any],
) -> int:

    score = 0

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

    buy_sell = safe_float(
        snapshot.get(
            "buy_sell_ratio"
        ),
        0.0,
    )

    liquidity = snapshot.get(
        "liquidity"
    )

    age = snapshot.get(
        "age_hours"
    )

    if flow_pct >= 25:
        score += 40
    elif flow_pct >= 15:
        score += 30
    elif flow_pct >= 8:
        score += 20

    if flow >= 25_000:
        score += 25
    elif flow >= 15_000:
        score += 18
    elif flow >= 5_000:
        score += 10
    elif flow >= 1_500:
        score += 5

    if buy_sell >= 5:
        score += 15
    elif buy_sell >= 3:
        score += 10
    elif buy_sell >= 1.5:
        score += 7
    elif buy_sell > 0:
        score += 2

    if (
        liquidity is not None
        and safe_float(
            liquidity,
            0.0,
        ) >= 15_000
    ):

        score += 10

    elif (
        liquidity is not None
        and safe_float(
            liquidity,
            0.0,
        ) >= 6_000
    ):

        score += 5

    if age is not None:

        if age <= 12:
            score += 5
        elif age <= 24:
            score += 4
        elif age <= 36:
            score += 3
        elif age <= 48:
            score += 2

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

    qualified, strength, reason, needed = (
        evaluate_snapshot(
            snapshot
        )
    )

    liquidity_fallback = (
        "EARLY_PUMPFUN_FALLBACK"
        in reason
    )

    return {
        "qualified": qualified,
        "passed": qualified,
        "reason": reason,
        "signal_strength": strength,
        "required_observations": needed,

        "liquidity_fallback": (
            liquidity_fallback
        ),

        "extended_mc": (
            safe_float(
                snapshot.get(
                    "market_cap"
                ),
                0.0,
            )
            > MAX_MC_NORMAL
        ),

        "score": score_token(
            snapshot
        ),

        "volume_flow_soft": (
            safe_float(
                snapshot.get(
                    "volume_flow_ratio"
                ),
                0.0,
            )
            >= PREFERRED_VOLUME_FLOW_RATIO
        ),

        "buy_sell_soft": (
            safe_float(
                snapshot.get(
                    "buy_sell_ratio"
                ),
                0.0,
            )
            >= PREFERRED_BUY_SELL_RATIO
        ),

        "liquidity_value": snapshot.get(
            "liquidity"
        ),
    }


# ============================================================
# PENDING CONFIRMATION
# ============================================================

def token_on_cooldown(
    address: str,
) -> bool:

    item = STATE[
        "alerts"
    ].get(
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

        "required": safe_int(
            analysis.get(
                "required_observations"
            ),
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

        "snapshot": snapshot,
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(
    method: str,
    payload: Optional[
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

    try:

        data = None

        if payload is not None:

            data = urllib.parse.urlencode(
                payload
            ).encode(
                "utf-8"
            )

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "RunnerBot/4.6",
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=20,
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

            return json.loads(
                raw
            )

    except urllib.error.HTTPError as exc:

        try:

            raw = exc.read().decode(
                "utf-8"
            )

            data = json.loads(
                raw
            )

            return data

        except Exception:

            print(
                f"[TELEGRAM HTTP ERROR] "
                f"{exc}"
            )

            return None

    except Exception as exc:

        print(
            f"[TELEGRAM ERROR] {exc}"
        )

        return None


def telegram_delete_webhook() -> None:

    if not TELEGRAM_BOT_TOKEN:
        return

    result = telegram_api(
        "deleteWebhook",
        {
            "drop_pending_updates": "false"
        },
    )

    if result and result.get(
        "ok",
        False,
    ):

        print(
            "[TELEGRAM] Webhook cleared."
        )

    else:

        print(
            "[TELEGRAM] Webhook cleanup "
            "returned no success."
        )


def telegram_send(
    chat_id: int,
    text: str,
) -> None:

    telegram_api(
        "sendMessage",
        {
            "chat_id": str(
                chat_id
            ),
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
                f"[TELEGRAM] "
                f"Broadcast error: {exc}"
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

    liquidity = snapshot.get(
        "liquidity"
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

    fallback = bool(
        analysis.get(
            "liquidity_fallback",
            False,
        )
    )

    if fallback:

        liq_text = (
            "N/A — pump.fun early fallback"
        )

    elif liquidity is not None:

        liq_text = (
            f"${safe_float(liquidity):,.0f}"
        )

    else:

        liq_text = "N/A"

    signal_strength = analysis.get(
        "signal_strength",
        "NORMAL",
    )

    required = safe_int(
        analysis.get(
            "required_observations"
        ),
        NORMAL_OBSERVATIONS,
    )

    extended = bool(
        analysis.get(
            "extended_mc",
            False,
        )
    )

    mc_mode = (
        "EXTENDED"
        if extended
        else "NORMAL"
    )

    liquidity_note = (
        "\n⚠️ Pump.fun early-stage liquidity fallback"
        if fallback
        else ""
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
        f"5m Flow Proxy: ${flow:,.0f}",
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
        f"Diagnostic Score: {analysis.get('score', 0)}/100",
        liquidity_note,
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
        lines
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

    STATE[
        "last_global_alert"
    ] = now_ts()


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

        token = STATE[
            "tokens"
        ].get(
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

            key = (
                f"outcome_{window}"
            )

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
                "timestamp": closest.get(
                    "timestamp"
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

        print(
            f"[COOLDOWN] "
            f"{snapshot['symbol']}"
        )

        return False

    pending = STATE[
        "pending"
    ].get(
        address
    )

    # --------------------------------------------------------
    # FIRST OBSERVATION
    # --------------------------------------------------------

    if pending is None:

        create_pending_ignition(
            snapshot,
            analysis,
        )

        required = safe_int(
            analysis.get(
                "required_observations"
            ),
            NORMAL_OBSERVATIONS,
        )

        strength = analysis.get(
            "signal_strength",
            "NORMAL",
        )

        print(
            f"[PENDING] "
            f"{snapshot['symbol']} "
            f"1/{required} "
            f"{strength}"
        )

        if required == 1:

            if global_on_cooldown():

                print(
                    f"[GLOBAL COOLDOWN] "
                    f"{snapshot['symbol']}"
                )

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

            STATE[
                "pending"
            ].pop(
                address,
                None,
            )

            print(
                f"[ALERT] "
                f"{strength} "
                f"{snapshot['symbol']}"
            )

            return True

        return False

    # --------------------------------------------------------
    # EXPIRY
    # --------------------------------------------------------

    first_seen = safe_int(
        pending.get(
            "first_seen"
        ),
        now_ts(),
    )

    if (
        now_ts()
        - first_seen
        > PENDING_EXPIRY_SECONDS
    ):

        print(
            f"[PENDING EXPIRED] "
            f"{snapshot['symbol']}"
        )

        STATE[
            "pending"
        ].pop(
            address,
            None,
        )

        create_pending_ignition(
            snapshot,
            analysis,
        )

        return False

    # --------------------------------------------------------
    # ADD OBSERVATION
    # --------------------------------------------------------

    confirmations = safe_int(
        pending.get(
            "confirmations"
        ),
        1,
    )

    confirmations += 1

    pending[
        "confirmations"
    ] = confirmations

    pending[
        "last_seen"
    ] = now_ts()

    old_required = safe_int(
        pending.get(
            "required"
        ),
        NORMAL_OBSERVATIONS,
    )

    current_required = safe_int(
        analysis.get(
            "required_observations"
        ),
        NORMAL_OBSERVATIONS,
    )

    required = min(
        old_required,
        current_required,
    )

    pending[
        "required"
    ] = required

    pending[
        "signal_strength"
    ] = analysis.get(
        "signal_strength",
        "NORMAL",
    )

    print(
        f"[CONFIRM] "
        f"{snapshot['symbol']} "
        f"{confirmations}/{required} "
        f"{analysis.get('signal_strength', 'NORMAL')}"
    )

    # --------------------------------------------------------
    # CONFIRMED
    # --------------------------------------------------------

    if confirmations >= required:

        if global_on_cooldown():

            print(
                f"[GLOBAL COOLDOWN] "
                f"{snapshot['symbol']}"
            )

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

        STATE[
            "pending"
        ].pop(
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


# ============================================================
# TELEGRAM STATUS
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
        f"🤖 Runner Bot "
        f"{BOT_VERSION}\n\n"

        f"Scan interval: "
        f"{SCAN_INTERVAL_SECONDS}s\n\n"

        f"1. Sanity:\n"
        f"• MC ≥ $1K\n"
        f"• |Flow/MC| ≤150%\n\n"

        f"2. Liquidity:\n"
        f"• Normal: ≥$6K\n"
        f"• Pump.fun fallback only:\n"
        f"  ≤1.5h + $8K volume + "
        f"$3K flow + 12% Flow/MC\n\n"

        f"3. Age:\n"
        f"• ≤48h\n\n"

        f"4. Market Cap — V4.6:\n"
        f"• ≥$7K\n"
        f"• ≤$90K normal\n"
        f"• ≤$140K extended\n"
        f"• Extended requires Flow/MC ≥14%\n\n"

        f"5. Core Flow:\n"
        f"• Flow ≥$1.5K\n"
        f"• Flow/MC ≥8%\n"
        f"• Volume/Flow = SOFT\n"
        f"• Buy/Sell = SOFT\n\n"

        f"6. Strength:\n"
        f"• ULTRA: ≥25% Flow/MC OR ≥$25K flow → 1\n"
        f"• STRONG: ≥15% Flow/MC OR ≥$15K flow → 2\n"
        f"• NORMAL → 3\n\n"

        f"Ranking:\n"
        f"• Flow/MC ↓\n"
        f"• Absolute Flow ↓\n"
        f"• Buy/Sell ↓\n\n"

        f"Subscribers: "
        f"{subscriber_count}\n"

        f"Tracked tokens: "
        f"{token_count}\n"

        f"Pending: "
        f"{pending_count}\n"

        f"Alerts enabled: "
        f"{ALERTS_ENABLED}"
    )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def handle_command(
    chat_id: int,
    text: str,
) -> None:

    parts = text.strip().split()

    if not parts:
        return

    command = parts[0].lower()

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
                f"🤖 Runner Bot "
                f"{BOT_VERSION} "
                f"is online.\n\n"
                f"Alerts are enabled "
                f"for this chat."
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
            (
                "🛑 Runner alerts "
                "stopped for this chat."
            ),
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

    if command == "/tracking":

        telegram_send(
            chat_id,
            tracking_text(),
        )

        return

    if command == "/scan":

        telegram_send(
            chat_id,
            "🔎 Manual scan requested.",
        )

        scan_once()

        return


# ============================================================
# TELEGRAM POLLING
# ============================================================

def telegram_poll() -> bool:

    """
    Returns:
        True  = polling succeeded
        False = polling failed/conflict
    """

    if not TELEGRAM_BOT_TOKEN:
        return False

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
        return False

    if not result.get(
        "ok",
        False,
    ):

        error_code = safe_int(
            result.get(
                "error_code"
            ),
            0,
        )

        description = result.get(
            "description",
            "",
        )

        if error_code == 409:

            print(
                "[TELEGRAM 409] Another bot "
                "instance is already polling "
                "this token."
            )

            print(
                "[TELEGRAM 409] Stop the other "
                "GitHub Actions/Replit/local "
                "instance before continuing."
            )

            time.sleep(10)

        else:

            print(
                f"[TELEGRAM API ERROR] "
                f"{error_code}: "
                f"{description}"
            )

        return False

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

        STATE[
            "offset"
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

    return True


# ============================================================
# SCAN
# ============================================================

def scan_once() -> None:

    print(
        f"\n{'=' * 75}"
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

    discovered_count = len(
        addresses
    )

    record_discovered_count(
        discovered_count
    )

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

    rejected_candidates: List[
        Tuple[
            str,
            Dict[str, Any],
        ]
    ] = []

    qualified_tokens: List[
        Tuple[
            Dict[str, Any],
            Dict[str, Any],
        ]
    ] = []

    processed_count = 0
    prefiltered_count = 0

    # --------------------------------------------------------
    # BUILD CANDIDATES
    # --------------------------------------------------------

    for address, pair in selected_pairs.items():

        snapshot = pair_to_snapshot(
            pair
        )

        # ----------------------------------------------------
        # PERFORMANCE PRE-FILTER
        #
        # These are deliberately NOT counted as decision-tree
        # rejections.
        # ----------------------------------------------------

        if not passes_pre_filter(
            snapshot
        ):

            prefiltered_count += 1

            continue

        processed_count += 1

        record_snapshot(
            snapshot
        )

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
        # EXACT DECISION TREE
        # ----------------------------------------------------

        analysis = analyze(
            snapshot
        )

        if not analysis.get(
            "qualified",
            False,
        ):

            reason = analysis.get(
                "reason",
                "UNKNOWN",
            )

            # ------------------------------------------------
            # PERSISTENT REJECTION TRACKING
            # ------------------------------------------------

            add_rejection_tracking(
                snapshot,
                reason,
            )

            rejected_candidates.append(
                (
                    reason,
                    snapshot,
                )
            )

            continue

        # ----------------------------------------------------
        # PERSISTENT QUALIFICATION TRACKING
        # ----------------------------------------------------

        add_qualification_tracking(
            snapshot,
            analysis,
        )

        qualified_tokens.append(
            (
                snapshot,
                analysis,
            )
        )

    # --------------------------------------------------------
    # PERSISTENT SCAN COUNTERS
    # --------------------------------------------------------

    record_prefiltered_count(
        prefiltered_count
    )

    record_processed_count(
        processed_count
    )

    # --------------------------------------------------------
    # RANK QUALIFIED TOKENS
    # --------------------------------------------------------

    qualified_tokens.sort(
        key=lambda item:
            ranking_key(
                item[0]
            ),
        reverse=True,
    )

    # --------------------------------------------------------
    # PROCESS QUALIFIED TOKENS
    # --------------------------------------------------------

    for rank, (
        snapshot,
        analysis,
    ) in enumerate(
        qualified_tokens,
        start=1,
    ):

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

        buy_sell = safe_float(
            snapshot.get(
                "buy_sell_ratio"
            ),
            0.0,
        )

        print(
            f"[QUALIFIED #{rank}] "
            f"{snapshot['symbol']} | "
            f"MC=${snapshot['market_cap']:,.0f} | "
            f"Flow/MC={flow_pct:.1f}% | "
            f"Flow=${flow:,.0f} | "
            f"Buy/Sell={buy_sell:.2f}x | "
            f"Strength={analysis.get('signal_strength')} | "
            f"Obs={analysis.get('required_observations')}"
        )

        if analysis.get(
            "liquidity_fallback",
            False,
        ):

            print(
                f"[PUMPFUN FALLBACK PASS] "
                f"{snapshot['symbol']} | "
                f"Age={snapshot.get('age_hours', 0):.2f}h | "
                f"Vol=${snapshot.get('volume_5m', 0):,.0f} | "
                f"Flow=${snapshot.get('flow_proxy_5m', 0):,.0f} | "
                f"Flow/MC={snapshot.get('flow_pressure_pct', 0):.1f}%"
            )

        process_confirmation(
            snapshot,
            analysis,
        )

    # --------------------------------------------------------
    # REJECTIONS
    # --------------------------------------------------------

    print_near_misses(
        rejected_candidates
    )

    # --------------------------------------------------------
    # OUTCOMES
    # --------------------------------------------------------

    update_outcomes()

    # --------------------------------------------------------
    # SCAN SUMMARY
    # --------------------------------------------------------

    record_scan_summary(
        discovered=discovered_count,
        prefiltered=prefiltered_count,
        processed=processed_count,
        rejected=len(
            rejected_candidates
        ),
        qualified=len(
            qualified_tokens
        ),
    )

    save_state()

    print(
        f"[SCAN SUMMARY] "
        f"Discovered={discovered_count} | "
        f"PreFiltered={prefiltered_count} | "
        f"Processed={processed_count} | "
        f"Qualified={len(qualified_tokens)} | "
        f"Rejected={len(rejected_candidates)}"
    )

    # --------------------------------------------------------
    # PERSISTENT TRACKING SUMMARY
    # --------------------------------------------------------

    print_tracking_summary()


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    print(
        "=" * 75
    )

    print(
        f"RUNNER BOT {BOT_VERSION}"
    )

    print(
        "=" * 75
    )

    print(
        f"Scan interval: "
        f"{SCAN_INTERVAL_SECONDS}s"
    )

    print(
        "Sanity: "
        f"MC >= ${MIN_SANITY_MC:,} | "
        f"|Flow/MC| <= {MAX_SANITY_ABS_FLOW_MC_PCT}%"
    )

    print(
        "Liquidity: "
        f">=${MIN_LIQUIDITY:,} | "
        "pump.fun fallback only"
    )

    print(
        "Age: "
        f"<= {MAX_AGE_HOURS:.0f}h"
    )

    print(
        "MC V4.6 EARLY BIAS: "
        f"${MIN_MC:,} - "
        f"${MAX_MC_NORMAL:,} normal / "
        f"${MAX_MC_EXTENDED:,} extended"
    )

    print(
        "Extended MC Flow/MC: "
        f">= {EXTENDED_MC_MIN_FLOW_PCT}%"
    )

    print(
        "Core Flow: "
        f">=${MIN_FLOW:,} | "
        f"Flow/MC >= {MIN_FLOW_MC_PCT}%"
    )

    print(
        "Volume/Flow: SOFT ONLY"
    )

    print(
        "Buy/Sell: SOFT ONLY"
    )

    print(
        "Strength: "
        "ULTRA 1 | STRONG 2 | NORMAL 3"
    )

    print(
        "Ranking: "
        "Flow/MC -> Absolute Flow -> Buy/Sell"
    )

    print(
        "Tracking: "
        "PERSISTENT"
    )

    print(
        f"Rejection history: "
        f"{MAX_REJECTION_HISTORY:,}"
    )

    print(
        "=" * 75
    )

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARNING] "
            "TELEGRAM_BOT_TOKEN is not set."
        )

    else:

        telegram_delete_webhook()

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


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
