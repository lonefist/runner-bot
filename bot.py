import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V4.3 - STRICT CONFIRMATION
# ============================================================

BOT_VERSION = "V4.3-STRICT-CONFIRMATION"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

# IMPORTANT:
# Keep this filename so existing V4.2 history is preserved.
STATE_FILE = "runner_state_v42.json"


# ============================================================
# CORE FILTERS
# ============================================================

MIN_MC = 20_000
MAX_MC = 300_000

MIN_LIQUIDITY = 10_000

OBSERVE_MIN_MC = 10_000
OBSERVE_MAX_MC = 350_000

MAX_PAIR_AGE_HOURS = 24

REFERENCE_VOLUME_5M = 5_000

MIN_CONSOLIDATION_VOLUME_5M = 500
MIN_CONSOLIDATION_TX_5M = 5
MIN_CONSOLIDATION_ACTIVE_OBS = 3

MAX_CONSOLIDATION_RANGE_PCT = 18.0

ACTIVITY_LOOKBACK_SECONDS = 60
ACTIVITY_EXPANSION_PCT = 20.0

MAX_STORED_TOKENS = 1500
MAX_HISTORY_PER_TOKEN = 300

MIN_OBSERVATIONS = 6

DISCOVERY_INTERVAL_SECONDS = 60
MAX_DISCOVERY_TOKENS = 500

SCAN_INTERVAL_SECONDS = int(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)

DEX_BATCH_SIZE = 25


# ============================================================
# STRICT CONFIRMATION SETTINGS
# ============================================================

# Initial ignition must pass this price-change protection.
# Example:
# +10% = allowed
# -3%  = allowed
# -5%  = borderline but allowed
# -5.1% = rejected
MAX_IGNITION_DRAWDOWN_PCT = -5.0

# Confirmation requires another valid runner setup.
CONFIRMATION_REQUIRED_SCANS = 2

# Secondary confirmation path:
# MC must increase by at least this amount from ignition,
# BUT MC growth alone is NEVER enough.
CONFIRMATION_MC_GROWTH_PCT = 5.0

# Pending ignition expires after this amount of time.
PENDING_EXPIRY_SECONDS = 5 * 60


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "true").lower()
    in ("1", "true", "yes", "on")
)


# ============================================================
# HTTP
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; RunnerBot/4.3; +https://dexscreener.com)"
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

                raw = response.read().decode("utf-8")
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

        state.setdefault("version", BOT_VERSION)
        state.setdefault("tokens", {})
        state.setdefault("alerts", {})
        state.setdefault("pending_ignitions", {})
        state.setdefault("subscribers", [])
        state.setdefault("last_discovery", 0)

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


def money(value: float) -> str:

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


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

        url = DEX_BASE + endpoint

        data = http_get_json(url)

        if not isinstance(data, list):
            continue

        for item in data:

            if not isinstance(item, dict):
                continue

            chain_id = str(
                item.get("chainId", "")
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

    result = list(found)

    return result[:MAX_DISCOVERY_TOKENS]


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
            f"{DEX_BASE}/latest/dex/tokens/"
            f"{urllib.parse.quote(joined, safe=',')}"
        )

        data = http_get_json(url)

        if not isinstance(data, dict):
            continue

        pairs = data.get("pairs", [])

        if not isinstance(pairs, list):
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
# BEST PAIR SELECTION
# ============================================================

def choose_best_pairs(
    pairs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:

    best: Dict[str, Dict[str, Any]] = {}

    for pair in pairs:

        base = pair.get("baseToken") or {}

        address = base.get("address")

        if not address:
            continue

        liquidity = safe_float(
            (pair.get("liquidity") or {}).get("usd")
        )

        current = best.get(address)

        if current is None:
            best[address] = pair
            continue

        current_liq = safe_float(
            (current.get("liquidity") or {}).get("usd")
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

    base = pair.get("baseToken") or {}

    address = str(
        base.get("address", "")
    )

    symbol = str(
        base.get("symbol", "?")
    )

    name = str(
        base.get("name", symbol)
    )

    liquidity = safe_float(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = pair.get("volume") or {}

    volume_5m = safe_float(
        volume.get("m5")
    )

    volume_1h = safe_float(
        volume.get("h1")
    )

    txns = pair.get("txns") or {}

    tx_5m = txns.get("m5") or {}

    buys = safe_int(
        tx_5m.get("buys")
    )

    sells = safe_int(
        tx_5m.get("sells")
    )

    total_tx = buys + sells

    buy_pct = (
        (buys / total_tx) * 100
        if total_tx > 0
        else 0.0
    )

    ratio = (
        buys / sells
        if sells > 0
        else float(buys)
    )

    price = safe_float(
        pair.get("priceUsd")
    )

    price_change = pair.get("priceChange") or {}

    change_5m = safe_float(
        price_change.get("m5")
    )

    change_1h = safe_float(
        price_change.get("h1")
    )

    fdv = safe_float(
        pair.get("fdv")
    )

    market_cap = safe_float(
        pair.get("marketCap")
    )

    if market_cap <= 0:
        market_cap = fdv

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

        "volume_5m": volume_5m,
        "volume_1h": volume_1h,

        "buys": buys,
        "sells": sells,
        "tx_5m": total_tx,

        "buy_pct": buy_pct,
        "buy_sell_ratio": ratio,

        "price": price,

        "change_5m": change_5m,
        "change_1h": change_1h,

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

        # Remove oldest token history.
        oldest_address = None
        oldest_time = float("inf")

        for addr, item in state["tokens"].items():

            h = item.get("history", [])

            if not h:
                continue

            t = safe_float(
                h[-1].get("timestamp")
            )

            if t < oldest_time:
                oldest_time = t
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
        }

    current = prices[-1]

    previous = prices[:-1]

    previous_max = max(previous)
    previous_min = min(previous)

    breakout = (
        current > previous_max
    )

    lowest = min(prices)
    highest = max(prices)

    if lowest > 0:
        range_pct = (
            (highest - lowest)
            / lowest
            * 100
        )
    else:
        range_pct = 0.0

    consolidation = (
        range_pct <= MAX_CONSOLIDATION_RANGE_PCT
    )

    return {
        "breakout": breakout,
        "consolidation": consolidation,
        "range_pct": range_pct,
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

    current_time = safe_float(
        current.get("timestamp")
    )

    target_time = (
        current_time
        - ACTIVITY_LOOKBACK_SECONDS
    )

    previous = None

    for item in reversed(history[:-1]):

        item_time = safe_float(
            item.get("timestamp")
        )

        if item_time <= target_time:

            previous = item
            break

    if previous is None:
        return False

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

    volume_growth = 0.0
    tx_growth = 0.0

    if old_volume > 0:

        volume_growth = (
            (new_volume - old_volume)
            / old_volume
            * 100
        )

    if old_tx > 0:

        tx_growth = (
            (new_tx - old_tx)
            / old_tx
            * 100
        )

    return (
        volume_growth >= ACTIVITY_EXPANSION_PCT
        or
        tx_growth >= ACTIVITY_EXPANSION_PCT
    )


# ============================================================
# SCORING
# ============================================================

def score_token(
    snapshot: Dict[str, Any],
    structure: Dict[str, Any],
    activity: bool
) -> int:

    score = 0

    mc = snapshot["mc"]
    liquidity = snapshot["liquidity"]
    volume = snapshot["volume_5m"]
    buy_pct = snapshot["buy_pct"]
    tx = snapshot["tx_5m"]
    age = snapshot["age_hours"]
    change_5m = snapshot["change_5m"]

    # MC
    if 20_000 <= mc <= 300_000:
        score += 10

    # Liquidity
    if liquidity >= 10_000:
        score += 10

    if liquidity >= 25_000:
        score += 5

    # 5m volume
    if volume >= 5_000:
        score += 10

    if volume >= 10_000:
        score += 5

    # Buy participation
    if buy_pct >= 50:
        score += 10

    if buy_pct >= 55:
        score += 5

    # Transactions
    if tx >= 20:
        score += 5

    if tx >= 50:
        score += 5

    # Age
    if age <= 6:
        score += 10

    elif age <= 12:
        score += 7

    elif age <= 24:
        score += 4

    # Breakout
    if structure["breakout"]:
        score += 15

    # Activity
    if activity:
        score += 15

    # Price weakness penalty
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

    score = score_token(
        snapshot,
        structure,
        activity
    )

    if (
        len(history) >= MIN_OBSERVATIONS
        and
        score >= 70
        and
        structure["breakout"]
        and
        activity
        and
        snapshot["change_5m"]
        > MAX_IGNITION_DRAWDOWN_PCT
    ):

        setup = "IGNITION"

    elif (
        structure["breakout"]
        and activity
    ):

        setup = "STRUCTURE BREAK"

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
        and analysis["breakout"]
        and analysis["activity"]
        and
        snapshot["change_5m"]
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

        "ignition_score": analysis["score"],
        "ignition_change_5m": snapshot["change_5m"],

        "confirmations": 1,

        "last_confirmation_time": now_ts(),

        "pair_url": snapshot["pair_url"],
    }

    print(
        f"[PENDING] {snapshot['symbol']} | "
        f"First valid ignition recorded | "
        f"Confirmation=1/2 | "
        f"MC={money(snapshot['mc'])}",
        flush=True
    )


def expire_pending_ignitions() -> None:

    current_time = now_ts()

    expired = []

    for address, pending in state[
        "pending_ignitions"
    ].items():

        created = safe_float(
            pending.get("ignition_time")
        )

        if (
            current_time - created
            > PENDING_EXPIRY_SECONDS
        ):

            expired.append(address)

    for address in expired:

        pending = state[
            "pending_ignitions"
        ].pop(address, None)

        if pending:

            print(
                f"[EXPIRED] "
                f"{pending.get('symbol', '?')} | "
                f"Ignition confirmation expired",
                flush=True
            )


def process_confirmation(
    snapshot: Dict[str, Any],
    analysis: Dict[str, Any]
) -> Optional[str]:

    address = snapshot["address"]

    # Already confirmed.
    if address in state["alerts"]:
        return None

    pending = state[
        "pending_ignitions"
    ].get(address)

    current_valid = valid_current_ignition(
        snapshot,
        analysis
    )

    # --------------------------------------------------------
    # No pending ignition.
    # --------------------------------------------------------

    if pending is None:

        if current_valid:

            create_pending_ignition(
                snapshot,
                analysis
            )

        return None

    # --------------------------------------------------------
    # Pending exists.
    # --------------------------------------------------------

    ignition_mc = safe_float(
        pending.get("ignition_mc")
    )

    current_mc = snapshot["mc"]

    mc_growth = pct_change(
        ignition_mc,
        current_mc
    )

    # The strict rule:
    #
    # We NEVER confirm from MC growth alone.
    #
    # Current scan must still have:
    # score >= 70
    # activity expansion
    # positive structure/breakout
    # price change > -5%
    #
    if current_valid:

        previous_confirmations = safe_int(
            pending.get("confirmations"),
            1
        )

        pending[
            "confirmations"
        ] = previous_confirmations + 1

        pending[
            "last_confirmation_time"
        ] = now_ts()

        confirmations = pending[
            "confirmations"
        ]

        print(
            f"[CONFIRM CHECK] "
            f"{snapshot['symbol']} | "
            f"Confirmation={confirmations}/"
            f"{CONFIRMATION_REQUIRED_SCANS} | "
            f"Current score={analysis['score']} | "
            f"Breakout=YES | "
            f"Activity=YES | "
            f"5m={snapshot['change_5m']:+.1f}%",
            flush=True
        )

        if (
            confirmations
            >= CONFIRMATION_REQUIRED_SCANS
        ):

            return confirm_runner(
                snapshot,
                analysis,
                pending,
                "TWO_CONSECUTIVE_VALID_SCANS"
            )

        return None

    # --------------------------------------------------------
    # Secondary path.
    #
    # MC can be +5% or more, but this still requires:
    # score >= 70
    # activity expansion
    # positive structure/breakout
    # price protection.
    # --------------------------------------------------------

    secondary_valid = (
        mc_growth
        >= CONFIRMATION_MC_GROWTH_PCT
        and
        analysis["score"] >= 70
        and
        analysis["activity"]
        and
        analysis["breakout"]
        and
        snapshot["change_5m"]
        > MAX_IGNITION_DRAWDOWN_PCT
    )

    if secondary_valid:

        print(
            f"[CONFIRM CHECK] "
            f"{snapshot['symbol']} | "
            f"MC growth={mc_growth:+.1f}% | "
            f"Current structure/activity confirmed",
            flush=True
        )

        return confirm_runner(
            snapshot,
            analysis,
            pending,
            "MC_GROWTH_WITH_CURRENT_CONFIRMATION"
        )

    print(
        f"[PENDING] "
        f"{snapshot['symbol']} | "
        f"Still pending | "
        f"Current setup not confirmed | "
        f"MC since ignition={mc_growth:+.1f}%",
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

        "checks": {
            "5m": None,
            "15m": None,
            "30m": None,
        },

        "pair_url": snapshot["pair_url"],
    }


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

    # Remove pending status.
    state[
        "pending_ignitions"
    ].pop(address, None)

    save_state()

    print(
        f"[CONFIRMED RUNNER] "
        f"{snapshot['symbol']} | "
        f"MC={money(current_mc)} | "
        f"MC since ignition="
        f"{mc_growth:+.1f}% | "
        f"Score={analysis['score']} | "
        f"Confirmation={confirmation_type}",
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

    return (
        "🚨 CONFIRMED RUNNER\n\n"

        f"🪙 {snapshot['symbol']}\n\n"

        f"📊 Score: {analysis['score']}/100\n"
        f"💰 MC: {money(snapshot['mc'])}\n"
        f"💧 Liquidity: {money(snapshot['liquidity'])}\n"
        f"📈 5m Volume: {money(snapshot['volume_5m'])}\n"
        f"🕐 Age: {snapshot['age_hours']:.1f}h\n\n"

        f"🟢 Buys: {snapshot['buys']}\n"
        f"🔴 Sells: {snapshot['sells']}\n"
        f"📊 Buy %: {snapshot['buy_pct']:.1f}%\n"
        f"⚖️ Ratio: {snapshot['buy_sell_ratio']:.2f}\n"
        f"📈 5m Change: {snapshot['change_5m']:+.1f}%\n\n"

        f"🚀 Breakout: "
        f"{'YES' if analysis['breakout'] else 'NO'}\n"

        f"⚡ Activity expansion: "
        f"{'YES' if analysis['activity'] else 'NO'}\n"

        f"✅ Confirmation: "
        f"{analysis['score'] >= 70 and analysis['breakout'] and analysis['activity']}\n\n"

        f"📈 MC since ignition: "
        f"{mc_growth:+.1f}%\n"

        f"👀 Observations: "
        f"{analysis['history_count']}\n\n"

        f"🔎 Confirmation type: "
        f"{confirmation_type}\n\n"

        f"{snapshot['pair_url']}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(
    method: str,
    payload: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
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

    message = update.get("message")

    if not message:
        return

    chat = message.get("chat") or {}

    chat_id = chat.get("id")

    if chat_id is None:
        return

    text = str(
        message.get("text", "")
    ).strip()

    if text.startswith("/start"):

        if chat_id not in state[
            "subscribers"
        ]:

            state[
                "subscribers"
            ].append(chat_id)

        save_state()

        send_telegram(
            str(chat_id),
            (
                "🔥 Runner Bot is online.\n\n"
                "You are subscribed to "
                "CONFIRMED RUNNER alerts."
            )
        )

    elif text.startswith("/stop"):

        state[
            "subscribers"
        ] = [
            x for x in state[
                "subscribers"
            ]
            if x != chat_id
        ]

        save_state()

        send_telegram(
            str(chat_id),
            "Alerts stopped."
        )

    elif text.startswith("/status"):

        pending = len(
            state[
                "pending_ignitions"
            ]
        )

        alerts = len(
            state[
                "alerts"
            ]
        )

        tokens = len(
            state[
                "tokens"
            ]
        )

        send_telegram(
            str(chat_id),
            (
                f"🔥 Runner Bot {BOT_VERSION}\n\n"
                f"Tracked tokens: {tokens}\n"
                f"Pending ignitions: {pending}\n"
                f"Confirmed alerts: {alerts}\n"
                f"Scan interval: "
                f"{SCAN_INTERVAL_SECONDS}s\n\n"
                f"Strict confirmation: ON\n"
                f"Max ignition drawdown: "
                f"{MAX_IGNITION_DRAWDOWN_PCT}%"
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

            lines.append(
                f"{item.get('symbol', '?')} "
                f"| MC {money(safe_float(item.get('start_mc')))} "
                f"| Score {item.get('score', 0)}"
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

            state[
                "telegram_offset"
            ] = update_id + 1

        try:

            handle_update(
                update
            )

        except Exception as exc:

            print(
                f"[TELEGRAM UPDATE ERROR] "
                f"{exc}",
                flush=True
            )

    save_state()


# ============================================================
# SCAN
# ============================================================

def scan_once() -> None:

    print(
        "=" * 60,
        flush=True
    )

    print(
        f"[SCAN] "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        flush=True
    )

    # --------------------------------------------------------
    # Discovery
    # --------------------------------------------------------

    current_time = now_ts()

    if (
        current_time
        -
        safe_float(
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
    # Get pairs
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

    # --------------------------------------------------------
    # Expire pending states
    # --------------------------------------------------------

    expire_pending_ignitions()

    # --------------------------------------------------------
    # Process pairs
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
                f"Obs={observations}/{MIN_OBSERVATIONS}",
                flush=True
            )

            # ------------------------------------------------
            # Core filters
            # ------------------------------------------------

            if snapshot["mc"] < MIN_MC:

                print(
                    f"[FILTER] "
                    f"{snapshot['symbol']}: "
                    f"MC below ${MIN_MC:,}",
                    flush=True
                )

                continue

            if snapshot["mc"] > MAX_MC:

                print(
                    f"[FILTER] "
                    f"{snapshot['symbol']}: "
                    f"MC above ${MAX_MC:,}",
                    flush=True
                )

                continue

            if snapshot["liquidity"] < MIN_LIQUIDITY:

                print(
                    f"[FILTER] "
                    f"{snapshot['symbol']}: "
                    f"liquidity below "
                    f"${MIN_LIQUIDITY:,}",
                    flush=True
                )

                continue

            if snapshot["age_hours"] > MAX_PAIR_AGE_HOURS:

                print(
                    f"[FILTER] "
                    f"{snapshot['symbol']}: "
                    f"age above "
                    f"{MAX_PAIR_AGE_HOURS}h",
                    flush=True
                )

                continue

            # ------------------------------------------------
            # Wait for history
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
            # Analyze
            # ------------------------------------------------

            analysis = analyze(
                snapshot
            )

            pending = state[
                "pending_ignitions"
            ].get(address)

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
                f"Score={analysis['score']}/100 | "
                f"State={analysis['setup']} | "
                f"MC={money(snapshot['mc'])} | "
                f"Liq={money(snapshot['liquidity'])} | "
                f"5mVol={money(snapshot['volume_5m'])}"
                f"{confirmation_text}",
                flush=True
            )

            # ------------------------------------------------
            # Confirmation engine
            # ------------------------------------------------

            message = process_confirmation(
                snapshot,
                analysis
            )

            if message:

                broadcast(
                    message
                )

            save_state()

        except Exception as exc:

            print(
                f"[TOKEN ERROR] "
                f"{exc}",
                flush=True
            )


# ============================================================
# MAIN LOOP
# ============================================================

def main() -> None:

    print(
        "=" * 60,
        flush=True
    )

    print(
        f"🔥 RUNNER BOT {BOT_VERSION}",
        flush=True
    )

    print(
        f"Scan interval: "
        f"{SCAN_INTERVAL_SECONDS}s",
        flush=True
    )

    print(
        f"MC range: "
        f"${MIN_MC:,} - ${MAX_MC:,}",
        flush=True
    )

    print(
        f"Minimum liquidity: "
        f"${MIN_LIQUIDITY:,}",
        flush=True
    )

    print(
        f"Maximum pair age: "
        f"{MAX_PAIR_AGE_HOURS}h",
        flush=True
    )

    print(
        f"Minimum observations: "
        f"{MIN_OBSERVATIONS}",
        flush=True
    )

    print(
        f"Strict confirmation: ON",
        flush=True
    )

    print(
        f"Confirmation scans: "
        f"{CONFIRMATION_REQUIRED_SCANS}",
        flush=True
    )

    print(
        f"Maximum ignition drawdown: "
        f"{MAX_IGNITION_DRAWDOWN_PCT}%",
        flush=True
    )

    print(
        "=" * 60,
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
                f"[MAIN ERROR] {exc}",
                flush=True
            )

            save_state()

            time.sleep(5)


if __name__ == "__main__":
    main()
