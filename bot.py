import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# RUNNER BOT V3.7.1
# ============================================================

BOT_VERSION = "v3.7.1"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

STATE_FILE = "runner_state.json"

# -----------------------------
# Runner range
# -----------------------------

MIN_MC = 20_000
MAX_MC = 200_000

MIN_LIQUIDITY = 10_000

# 5m volume is a FEATURE, not a hard gate.
REFERENCE_VOLUME_5M = 5_000

# -----------------------------
# Observation range
# -----------------------------

OBSERVE_MIN_MC = 10_000
OBSERVE_MAX_MC = 250_000

# -----------------------------
# History
# -----------------------------

MAX_STORED_TOKENS = 750
MAX_HISTORY_PER_TOKEN = 120
MIN_OBSERVATIONS = 6

# -----------------------------
# Consolidation activity gate
# -----------------------------

MIN_CONSOLIDATION_VOLUME_5M = 500
MIN_CONSOLIDATION_TX = 5
MIN_CONSOLIDATION_ACTIVE_OBS = 3

# -----------------------------
# Timing
# -----------------------------

SCAN_INTERVAL_SECONDS = float(
    os.getenv("DEX_SCAN_INTERVAL_SECONDS", "15")
)

# Discovery is much heavier than analysing cached tokens.
# Do not rediscover the entire universe every 15 seconds.
DISCOVERY_INTERVAL_SECONDS = 60

# HTTP retry/backoff
HTTP_TIMEOUT = 12
HTTP_MAX_RETRIES = 3
HTTP_BACKOFF_BASE = 2.0

# -----------------------------
# Telegram
# -----------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

HEATING_ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "false").lower()
    == "true"
)

# -----------------------------
# Discovery
# -----------------------------

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

# Keep discovery bounded.
MAX_DISCOVERY_PAIRS = 300

# -----------------------------
# Outcome tracking
# -----------------------------

OUTCOME_WINDOWS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
}

CONTINUING_MC_CHANGE = 10.0
FAILED_MC_CHANGE = -15.0


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


def safe_symbol(symbol: Any) -> str:
    if symbol is None:
        return "?"
    value = str(symbol).strip()
    return value[:32] if value else "?"


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


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
        "User-Agent": "runner-bot/3.7.1",
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

                return json.loads(raw.decode("utf-8"))

        except urllib.error.HTTPError as exc:

            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")

                if retry_after:
                    try:
                        wait = float(retry_after)
                    except Exception:
                        wait = HTTP_BACKOFF_BASE * (2 ** attempt)
                else:
                    wait = HTTP_BACKOFF_BASE * (2 ** attempt)

                wait = clamp(wait, 1.0, 30.0)

                print(
                    f"HTTP 429 rate limit | waiting {wait:.1f}s | "
                    f"attempt {attempt + 1}/{HTTP_MAX_RETRIES}"
                )

                time.sleep(wait)
                continue

            print(f"HTTP error {exc.code}: {url}")
            return None

        except Exception as exc:

            print(f"HTTP error: {exc}")

            if attempt < HTTP_MAX_RETRIES - 1:
                time.sleep(
                    HTTP_BACKOFF_BASE * (2 ** attempt)
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

histories: Dict[str, List[Dict[str, Any]]] = {}

ignition_events: List[Dict[str, Any]] = []

telegram_subscribers: List[int] = []

discovery_cache: List[Dict[str, Any]] = []

last_discovery_time = 0.0

last_update_id = 0


# ============================================================
# Persistence
# ============================================================

def load_state() -> None:
    global histories
    global ignition_events
    global telegram_subscribers

    if not os.path.exists(STATE_FILE):
        print("No persistent state found. Starting fresh.")
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        histories = data.get("histories", {}) or {}

        ignition_events = data.get("ignition_events", []) or []

        telegram_subscribers = [
            int(x)
            for x in (data.get("telegram_subscribers", []) or [])
        ]

        # Remove historical ignition events that were outside
        # the intended runner range.
        before = len(ignition_events)

        ignition_events = [
            event
            for event in ignition_events
            if MIN_MC
            <= numeric(event.get("ignition_mc"))
            <= MAX_MC
            and numeric(event.get("ignition_liquidity"))
            >= MIN_LIQUIDITY
        ]

        removed = before - len(ignition_events)

        if removed:
            print(
                f"Removed {removed} out-of-range historical "
                f"ignition event(s)"
            )

        print(
            f"Persistent state loaded: "
            f"{len(histories)} tokens | "
            f"{len(ignition_events)} ignition events"
        )

    except Exception as exc:
        print(f"State load error: {exc}")
        histories = {}
        ignition_events = []
        telegram_subscribers = []


def save_state() -> None:
    global histories

    try:
        # Keep memory bounded.
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
                ranked[:MAX_STORED_TOKENS]
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
            f"Persistent state saved: "
            f"{len(histories)} tokens | "
            f"{len(ignition_events)} ignition events"
        )

    except Exception as exc:
        print(f"State save error: {exc}")


# ============================================================
# Pair extraction
# ============================================================

def pair_to_snapshot(
    pair: Dict[str, Any],
) -> Optional[TokenSnapshot]:

    try:
        chain_id = str(pair.get("chainId", "")).lower()

        if chain_id != "solana":
            return None

        base_token = pair.get("baseToken") or {}

        address = str(
            base_token.get("address", "")
        ).strip()

        if not address:
            return None

        symbol = safe_symbol(
            base_token.get("symbol")
        )

        market_cap = numeric(
            pair.get("marketCap")
        )

        if market_cap <= 0:
            market_cap = numeric(
                pair.get("fdv")
            )

        liquidity_obj = pair.get("liquidity") or {}

        liquidity = numeric(
            liquidity_obj.get("usd")
        )

        volume_obj = pair.get("volume") or {}

        volume_5m = numeric(
            volume_obj.get("m5")
        )

        volume_1h = numeric(
            volume_obj.get("h1")
        )

        txns = pair.get("txns") or {}

        tx_5m = txns.get("m5") or {}
        tx_1h = txns.get("h1") or {}

        buys_5m = int(
            numeric(tx_5m.get("buys"))
        )

        sells_5m = int(
            numeric(tx_5m.get("sells"))
        )

        buys_1h = int(
            numeric(tx_1h.get("buys"))
        )

        sells_1h = int(
            numeric(tx_1h.get("sells"))
        )

        price = numeric(
            pair.get("priceUsd")
        )

        changes = pair.get("priceChange") or {}

        price_change_5m = numeric(
            changes.get("m5")
        )

        price_change_1h = numeric(
            changes.get("h1")
        )

        pair_created_at = int(
            numeric(
                pair.get("pairCreatedAt")
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

def extract_pairs(data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not data:
        return []

    pairs = data.get("pairs")

    if not pairs:
        return []

    if not isinstance(pairs, list):
        return []

    return pairs


def discover_pairs(force: bool = False) -> List[Dict[str, Any]]:
    global discovery_cache
    global last_discovery_time

    current_time = now_ts()

    if (
        not force
        and discovery_cache
        and current_time - last_discovery_time
        < DISCOVERY_INTERVAL_SECONDS
    ):
        return discovery_cache

    discovered: Dict[str, Dict[str, Any]] = {}

    request_count = 0

    # --------------------------------
    # Latest profiles
    # --------------------------------

    endpoints = [
        f"{DEX_BASE}/token-profiles/latest/v1",
        f"{DEX_BASE}/token-boosts/latest/v1",
        f"{DEX_BASE}/token-boosts/top/v1",
    ]

    profile_addresses: List[str] = []

    for endpoint in endpoints:

        data = http_json(endpoint)
        request_count += 1

        if not data:
            continue

        items = (
            data
            if isinstance(data, list)
            else data.get("tokens", [])
        )

        if not isinstance(items, list):
            continue

        for item in items:

            if not isinstance(item, dict):
                continue

            chain_id = str(
                item.get("chainId", "")
            ).lower()

            if chain_id != "solana":
                continue

            address = str(
                item.get("tokenAddress", "")
            ).strip()

            if address:
                profile_addresses.append(address)

    # --------------------------------
    # Search endpoints
    # --------------------------------

    for term in SEARCH_TERMS:

        encoded = urllib.parse.quote(term)

        url = (
            f"{DEX_BASE}/latest/dex/search"
            f"?q={encoded}"
        )

        data = http_json(url)
        request_count += 1

        for pair in extract_pairs(data):

            if not isinstance(pair, dict):
                continue

            if str(
                pair.get("chainId", "")
            ).lower() != "solana":
                continue

            base = pair.get("baseToken") or {}

            address = str(
                base.get("address", "")
            ).strip()

            if not address:
                continue

            liquidity = numeric(
                (pair.get("liquidity") or {}).get("usd")
            )

            # Keep the highest-liquidity pair for each token.
            existing = discovered.get(address)

            if (
                existing is None
                or liquidity
                > numeric(
                    (existing.get("liquidity") or {}).get("usd")
                )
            ):
                discovered[address] = pair

        # Small pause between search requests.
        time.sleep(0.15)

    # --------------------------------
    # Profile/boost addresses
    #
    # Fetch them in batches rather than
    # one request per token.
    # --------------------------------

    unique_profile_addresses = list(
        dict.fromkeys(profile_addresses)
    )

    batch_size = 25

    for start in range(
        0,
        len(unique_profile_addresses),
        batch_size,
    ):

        batch = unique_profile_addresses[
            start:start + batch_size
        ]

        if not batch:
            continue

        joined = ",".join(batch)

        url = (
            f"{DEX_BASE}/latest/dex/tokens/"
            f"{urllib.parse.quote(joined)}"
        )

        data = http_json(url)
        request_count += 1

        for pair in extract_pairs(data):

            if not isinstance(pair, dict):
                continue

            if str(
                pair.get("chainId", "")
            ).lower() != "solana":
                continue

            base = pair.get("baseToken") or {}

            address = str(
                base.get("address", "")
            ).strip()

            if not address:
                continue

            liquidity = numeric(
                (pair.get("liquidity") or {}).get("usd")
            )

            existing = discovered.get(address)

            if (
                existing is None
                or liquidity
                > numeric(
                    (existing.get("liquidity") or {}).get("usd")
                )
            ):
                discovered[address] = pair

        time.sleep(0.2)

    pairs = list(discovered.values())

    # Highest liquidity first.
    pairs.sort(
        key=lambda pair: numeric(
            (pair.get("liquidity") or {}).get("usd")
        ),
        reverse=True,
    )

    pairs = pairs[:MAX_DISCOVERY_PAIRS]

    discovery_cache = pairs
    last_discovery_time = current_time

    print(
        f"{len(pairs)} Solana pairs discovered "
        f"| discovery HTTP requests={request_count}"
    )

    return pairs


# ============================================================
# Snapshot recording
# ============================================================

def snapshot_dict(snapshot: TokenSnapshot) -> Dict[str, Any]:
    return asdict(snapshot)


def record_snapshot(
    snapshot: TokenSnapshot,
) -> bool:

    # Only observe tokens inside our wider observation band.
    if not (
        OBSERVE_MIN_MC
        <= snapshot.market_cap
        <= OBSERVE_MAX_MC
    ):
        return False

    # IMPORTANT:
    # Do not store completely dead/stale observations.
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
        [],
    )

    history.append(
        snapshot_dict(snapshot)
    )

    if len(history) > MAX_HISTORY_PER_TOKEN:
        del history[
            :len(history) - MAX_HISTORY_PER_TOKEN
        ]

    return True


# ============================================================
# Analysis
# ============================================================

def analyze(
    snapshot: TokenSnapshot,
) -> Dict[str, Any]:

    address = snapshot.address

    history = histories.get(address, [])

    if len(history) < MIN_OBSERVATIONS:
        return {
            "state": "OBSERVING",
            "score": 0,
            "reasons": ["insufficient observations"],
            "breakout": False,
            "activity_expansion": False,
            "mc_move": 0.0,
            "liq_move": 0.0,
            "range": 0.0,
            "vol_change": 0.0,
            "tx_change": 0.0,
        }

    recent = history[-MIN_OBSERVATIONS:]

    recent_5 = history[-5:]

    # --------------------------------
    # Basic values
    # --------------------------------

    oldest = recent[0]

    mc_move = pct_change(
        numeric(oldest.get("market_cap")),
        snapshot.market_cap,
    )

    liq_move = pct_change(
        numeric(oldest.get("liquidity")),
        snapshot.liquidity,
    )

    # --------------------------------
    # Range
    # --------------------------------

    mcs = [
        numeric(x.get("market_cap"))
        for x in recent
        if numeric(x.get("market_cap")) > 0
    ]

    if mcs:
        low_mc = min(mcs)
        high_mc = max(mcs)

        if low_mc > 0:
            range_pct = (
                (high_mc - low_mc)
                / low_mc
            ) * 100.0
        else:
            range_pct = 0.0
    else:
        range_pct = 0.0

    # --------------------------------
    # Volume / transaction acceleration
    # --------------------------------

    previous = recent[-2]

    previous_volume = numeric(
        previous.get("volume_5m")
    )

    previous_tx = (
        int(
            numeric(
                previous.get("buys_5m")
            )
        )
        + int(
            numeric(
                previous.get("sells_5m")
            )
        )
    )

    current_tx = (
        snapshot.buys_5m
        + snapshot.sells_5m
    )

    if previous_volume > 0:
        vol_change = pct_change(
            previous_volume,
            snapshot.volume_5m,
        )
    else:
        vol_change = 0.0

    if previous_tx > 0:
        tx_change = pct_change(
            previous_tx,
            current_tx,
        )
    else:
        tx_change = 0.0

    # --------------------------------
    # Activity expansion
    # --------------------------------

    activity_expansion = (
        vol_change >= 20.0
        or tx_change >= 20.0
    )

    # --------------------------------
    # Local breakout
    #
    # Compare current MC against the
    # previous 5 observations.
    # --------------------------------

    prior_mcs = [
        numeric(x.get("market_cap"))
        for x in recent[:-1]
        if numeric(x.get("market_cap")) > 0
    ]

    prior_high = (
        max(prior_mcs)
        if prior_mcs
        else 0.0
    )

    breakout = (
        prior_high > 0
        and snapshot.market_cap
        > prior_high * 1.02
    )

    # --------------------------------
    # Buy pressure
    # --------------------------------

    total_tx = (
        snapshot.buys_5m
        + snapshot.sells_5m
    )

    if snapshot.sells_5m > 0:
        buy_sell_ratio = (
            snapshot.buys_5m
            / snapshot.sells_5m
        )
    elif snapshot.buys_5m > 0:
        buy_sell_ratio = float(
            snapshot.buys_5m
        )
    else:
        buy_sell_ratio = 0.0

    buy_pressure = (
        snapshot.buys_5m
        > snapshot.sells_5m
    )

    strong_buy_pressure = (
        buy_sell_ratio >= 1.5
    )

    # --------------------------------
    # Liquidity behavior
    # --------------------------------

    liquidity_stable = (
        liq_move >= -15.0
    )

    # --------------------------------
    # Score
    #
    # KEEPING THE EXISTING SCORING
    # --------------------------------

    score = 0
    reasons: List[str] = []

    # Base / consolidation
    if (
        len(recent_5) >= 5
        and range_pct <= 18.0
        and abs(mc_move) <= 18.0
    ):
        score += 2
        reasons.append("base")

    if activity_expansion:
        score += 2
        reasons.append("activity expansion")

    if mc_move >= 5.0:
        score += 2
        reasons.append("MC expansion")

    if breakout:
        score += 3
        reasons.append("breakout")

    if buy_pressure:
        score += 1
        reasons.append("buy pressure")

    if strong_buy_pressure:
        score += 2
        reasons.append("strong buy pressure")

    if liquidity_stable:
        score += 1
        reasons.append("liquidity stable")

    if snapshot.volume_5m >= REFERENCE_VOLUME_5M:
        score += 1
        reasons.append("5m volume > $5K")

    if (
        snapshot.sells_5m > snapshot.buys_5m
        and total_tx > 0
    ):
        reasons.append("sell pressure")

    # --------------------------------
    # V3.7.1 consolidation activity gate
    # --------------------------------

    def meaningful_activity(
        item: Dict[str, Any]
    ) -> bool:

        volume = numeric(
            item.get("volume_5m")
        )

        buys = int(
            numeric(
                item.get("buys_5m")
            )
        )

        sells = int(
            numeric(
                item.get("sells_5m")
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
        if meaningful_activity(item)
    ]

    consolidation = (
        len(recent_5) >= 5
        and len(meaningful_recent)
        >= MIN_CONSOLIDATION_ACTIVE_OBS
        and meaningful_activity(
            snapshot_dict(snapshot)
        )
        and range_pct <= 18.0
        and abs(mc_move) <= 18.0
    )

    # --------------------------------
    # Hard runner-range gate
    #
    # This is the important V3.7.1 fix.
    # --------------------------------

    runner_market_cap = (
        MIN_MC
        <= snapshot.market_cap
        <= MAX_MC
    )

    runner_liquidity = (
        snapshot.liquidity
        >= MIN_LIQUIDITY
    )

    # --------------------------------
    # Ignition
    #
    # DO NOT change score threshold.
    # --------------------------------

    ignition = (
        runner_market_cap
        and runner_liquidity
        and breakout
        and activity_expansion
        and score >= 7
    )

    # --------------------------------
    # State
    # --------------------------------

    if ignition:
        state = "IGNITION"

    elif breakout and runner_market_cap:
        state = "STRUCTURE BREAK"

    elif activity_expansion:
        state = "EXPANSION"

    elif consolidation:
        state = "CONSOLIDATION"

    else:
        state = "OBSERVING"

    # If the token has almost no meaningful
    # activity, never call it consolidation.
    if (
        state == "CONSOLIDATION"
        and not meaningful_activity(
            snapshot_dict(snapshot)
        )
    ):
        state = "OBSERVING"

        if "insufficient activity" not in reasons:
            reasons.append("insufficient activity")

    return {
        "state": state,
        "score": score,
        "reasons": reasons,
        "breakout": breakout,
        "activity_expansion": activity_expansion,
        "mc_move": mc_move,
        "liq_move": liq_move,
        "range": range_pct,
        "vol_change": vol_change,
        "tx_change": tx_change,
        "buy_sell_ratio": buy_sell_ratio,
        "runner_market_cap": runner_market_cap,
        "runner_liquidity": runner_liquidity,
        "consolidation": consolidation,
    }


# ============================================================
# Ignition event handling
# ============================================================

def ignition_already_exists(
    address: str,
    timestamp: float,
) -> bool:

    # Prevent repeated ignition events for the same
    # token inside a 30-minute period.
    for event in ignition_events:

        if event.get("address") != address:
            continue

        previous = numeric(
            event.get("ignition_timestamp")
        )

        if (
            previous > 0
            and timestamp - previous
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

    if snapshot.liquidity < MIN_LIQUIDITY:
        return None

    timestamp = snapshot.timestamp

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

        "address": snapshot.address,
        "symbol": snapshot.symbol,

        "ignition_timestamp": timestamp,

        "ignition_mc": snapshot.market_cap,
        "ignition_liquidity": snapshot.liquidity,
        "ignition_volume_5m": snapshot.volume_5m,

        "ignition_buys_5m": snapshot.buys_5m,
        "ignition_sells_5m": snapshot.sells_5m,

        "ignition_score": analysis["score"],

        "outcomes": {},
    }

    ignition_events.append(event)

    print(
        f"🔥 IGNITION DETECTED | "
        f"{snapshot.symbol} | "
        f"CA={shorten_address(snapshot.address)} | "
        f"MC={format_usd(snapshot.market_cap)} | "
        f"5mVol={format_usd(snapshot.volume_5m)} | "
        f"buys/sells="
        f"{snapshot.buys_5m}/"
        f"{snapshot.sells_5m} | "
        f"score={analysis['score']}"
    )

    return event


# ============================================================
# Outcome tracking
# ============================================================

def classify_outcome(
    ignition_mc: float,
    current_mc: float,
) -> str:

    change = pct_change(
        ignition_mc,
        current_mc,
    )

    if change >= CONTINUING_MC_CHANGE:
        return "CONTINUING"

    if change <= FAILED_MC_CHANGE:
        return "FAILED"

    return "UNCLEAR"


def update_ignition_outcomes() -> None:

    current_time = now_ts()

    changed = False

    for event in ignition_events:

        address = event.get("address")

        ignition_time = numeric(
            event.get("ignition_timestamp")
        )

        ignition_mc = numeric(
            event.get("ignition_mc")
        )

        if not address or ignition_time <= 0:
            continue

        history = histories.get(
            address,
            [],
        )

        if not history:
            continue

        latest = history[-1]

        current_mc = numeric(
            latest.get("market_cap")
        )

        if current_mc <= 0:
            continue

        outcomes = event.setdefault(
            "outcomes",
            {},
        )

        for label, seconds in OUTCOME_WINDOWS.items():

            if label in outcomes:
                continue

            elapsed = (
                current_time
                - ignition_time
            )

            if elapsed < seconds:
                continue

            change = pct_change(
                ignition_mc,
                current_mc,
            )

            status = classify_outcome(
                ignition_mc,
                current_mc,
            )

            outcomes[label] = {
                "timestamp": current_time,
                "mc": current_mc,
                "mc_change_pct": change,
                "status": status,
            }

            print(
                f"📊 OUTCOME {label} | "
                f"{event.get('symbol', '?')} | "
                f"MC={format_usd(current_mc)} | "
                f"change={change:+.1f}% | "
                f"{status}"
            )

            changed = True

    if changed:
        save_state()


# ============================================================
# Telegram
# ============================================================

def telegram_request(
    method: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:

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
        url = f"{url}?{query}"

    return http_json(url)


def send_telegram_message(
    chat_id: int,
    text: str,
) -> bool:

    result = telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
        },
    )

    return bool(
        result
        and result.get("ok")
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
        f"MC: {format_usd(snapshot.market_cap)}\n"
        f"Liquidity: {format_usd(snapshot.liquidity)}\n"
        f"5m Volume: {format_usd(snapshot.volume_5m)}\n"
        f"Buys/Sells: "
        f"{snapshot.buys_5m}/"
        f"{snapshot.sells_5m}\n"
        f"Score: {analysis['score']}\n"
        f"CA: {snapshot.address}\n\n"
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
                f"Telegram alert error: {exc}"
            )


def process_telegram_updates() -> None:

    global last_update_id
    global telegram_subscribers

    if not TELEGRAM_BOT_TOKEN:
        return

    result = telegram_request(
        "getUpdates",
        {
            "offset": last_update_id + 1,
            "timeout": 1,
        },
    )

    if not result or not result.get("ok"):
        return

    updates = result.get("result", [])

    for update in updates:

        update_id = int(
            update.get("update_id", 0)
        )

        if update_id > last_update_id:
            last_update_id = update_id

        message = update.get("message") or {}

        chat = message.get("chat") or {}

        chat_id = chat.get("id")

        if chat_id is None:
            continue

        text = str(
            message.get("text", "")
        ).strip()

        if text.startswith("/start"):

            if chat_id not in telegram_subscribers:
                telegram_subscribers.append(
                    int(chat_id)
                )

                save_state()

            send_telegram_message(
                int(chat_id),
                (
                    "Runner bot is online.\n"
                    "You are subscribed to runner alerts."
                ),
            )


# ============================================================
# Main scan
# ============================================================

def scan_once() -> None:

    global discovery_cache

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

        existing = unique_tokens.get(
            snapshot.address
        )

        # Keep the highest-liquidity pair
        # for the same token.
        if (
            existing is None
            or snapshot.liquidity
            > existing.liquidity
        ):
            unique_tokens[
                snapshot.address
            ] = snapshot

    for snapshot in unique_tokens.values():

        # Record historical observation first.
        if record_snapshot(snapshot):
            histories_recorded += 1

        if not (
            MIN_MC
            <= snapshot.market_cap
            <= MAX_MC
        ):
            mc_reject += 1
            continue

        in_runner_range += 1

        if snapshot.liquidity < MIN_LIQUIDITY:
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
            [],
        )

        # Only print tokens that have enough history.
        if len(history) < MIN_OBSERVATIONS:
            continue

        reasons = ",".join(
            analysis["reasons"]
        )

        print(
            f"TRACKING {snapshot.symbol} | "
            f"CA={shorten_address(snapshot.address)} | "
            f"state={analysis['state']} | "
            f"obs={len(history)} | "
            f"MC={format_usd(snapshot.market_cap)} | "
            f"5mVol={format_usd(snapshot.volume_5m)} | "
            f"buys/sells="
            f"{snapshot.buys_5m}/"
            f"{snapshot.sells_5m} | "
            f"volChange="
            f"{analysis['vol_change']:.1f}% | "
            f"txChange="
            f"{analysis['tx_change']:.1f}% | "
            f"MCmove="
            f"{analysis['mc_move']:.1f}% | "
            f"liqMove="
            f"{analysis['liq_move']:.1f}% | "
            f"range="
            f"{analysis['range']:.1f}% | "
            f"breakout="
            f"{analysis['breakout']} | "
            f"score="
            f"{analysis['score']} | "
            f"reasons={reasons}"
        )

        if analysis["state"] == "IGNITION":

            event = create_ignition_event(
                snapshot,
                analysis,
            )

            if event:
                ignition_count += 1

                send_alert(
                    snapshot,
                    analysis,
                )

    print(
        "DEX scan complete: "
        f"{len(pairs)} Solana pairs discovered | "
        f"{len(unique_tokens)} unique tokens | "
        f"{histories_recorded} histories recorded | "
        f"{in_runner_range} in runner range | "
        f"{reached_liquidity} reached liquidity filter | "
        f"MC outside range={mc_reject} | "
        f"liquidity below minimum="
        f"{liquidity_reject} | "
        f"volume >= reference="
        f"{volume_reference_count} | "
        f"missing data={missing_data} | "
        f"ignition={ignition_count}"
    )

    update_ignition_outcomes()

    save_state()


# ============================================================
# Main
# ============================================================

def main() -> None:

    print(
        f"Runner Engine {BOT_VERSION} is online"
    )

    print(
        "Runner range: "
        f"MC={format_usd(MIN_MC)}-"
        f"{format_usd(MAX_MC)} | "
        f"minimum liquidity="
        f"{format_usd(MIN_LIQUIDITY)} | "
        "5m volume is a FEATURE, not a hard gate | "
        f"scan interval={SCAN_INTERVAL_SECONDS:.1f}s"
    )

    print(
        "Observation band: "
        f"{format_usd(OBSERVE_MIN_MC)}-"
        f"{format_usd(OBSERVE_MAX_MC)}"
    )

    print(
        "Consolidation activity gate: "
        f"5m volume >= "
        f"{format_usd(MIN_CONSOLIDATION_VOLUME_5M)} | "
        f"transactions >= "
        f"{MIN_CONSOLIDATION_TX} | "
        f"{MIN_CONSOLIDATION_ACTIVE_OBS}/5 "
        "recent observations"
    )

    print(
        "Ignition outcome tracking: "
        "+5m / +15m / +30m"
    )

    print(
        f"Telegram alerts enabled: "
        f"{HEATING_ALERTS_ENABLED}"
    )

    load_state()

    # Force initial discovery.
    discover_pairs(force=True)

    while True:

        cycle_start = now_ts()

        try:
            process_telegram_updates()
        except Exception as exc:
            print(
                f"Telegram polling error: {exc}"
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
                f"Telegram polling error: {exc}"
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

        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
