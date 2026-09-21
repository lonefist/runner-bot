import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V1.3
# LOW-CAP FIRST + CONTROLLED MOMENTUM + DIRECTIONAL PERSISTENCE
#
# PRIMARY HUNT ZONE: $20K - $80K
# SECONDARY ZONE:    $80K - $150K
#
# IMPORTANT:
# - Low caps get discovery priority.
# - Tokens above $150K are NEVER newly alerted.
# - A token discovered at low MC can continue being tracked
#   after it leaves the alert range.
# - This allows us to observe runners that later reach $1M+,
#   while preventing late entries at $450K/$500K/etc.
# ============================================================


BOT_VERSION = "V1.3-LOW-CAP-FIRST"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

STATE_FILE = "runner_v13_state.json"
HISTORY_FILE = "runner_v13_history.json"

CHAIN = "solana"


# ============================================================
# MARKET CAP CONTROL
# ============================================================

# Absolute lower boundary.
MIN_MC = 20_000

# PRIMARY hunting zone.
# This is where we want the bot to focus most aggressively.
PRIMARY_MAX_MC = 80_000

# Absolute maximum MC for a NEW ALERT.
# Anything above this is tracked if already discovered,
# but it cannot generate a new runner alert.
MAX_MC = 150_000


# ============================================================
# LIQUIDITY
# ============================================================

MIN_LIQUIDITY = 5_000


# ============================================================
# SCANNING
# ============================================================

SCAN_INTERVAL = 20

DISCOVERY_INTERVAL = 300
VALIDATION_INTERVAL = 300

TRACKING_HOURS = 8

# Increased so low-cap candidates are not lost simply because
# the discovery feed contains many high-activity tokens.
MAX_DISCOVERY_CANDIDATES = 50


# ============================================================
# SCORING
# ============================================================

WATCH_SCORE = 55
RUNNER_SCORE = 72
IDEAL_SCORE = 85

MIN_OBSERVATIONS_RUNNER = 2
MIN_OBSERVATIONS_IDEAL = 3


# ============================================================
# OUTCOME TRACKING
# ============================================================

CONTINUATION_MC_MULTIPLE = 1.50
CONTINUATION_LIQUIDITY_MULTIPLE = 0.70

FAILURE_MC_MULTIPLE = 0.80
FAILURE_LIQUIDITY_MULTIPLE = 0.50


HTTP_TIMEOUT = 15


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def money(value: Any) -> str:
    value = safe_float(value)

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def pct(value: Any) -> str:
    return f"{safe_float(value):.1f}%"


def ratio(value: Any) -> str:
    return f"{safe_float(value):.2f}"


def load_json(path: str, default: Any) -> Any:
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    temp_path = path + ".tmp"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    os.replace(temp_path, path)


# ============================================================
# STATE
# ============================================================

def default_state() -> Dict[str, Any]:
    return {
        "subscribers": [],
        "tracking": {},
        "last_discovery": 0,
        "last_validation": 0,
        "telegram_offset": 0,
    }


state = load_json(STATE_FILE, default_state())
history = load_json(HISTORY_FILE, [])

if not isinstance(state, dict):
    state = default_state()

if not isinstance(history, list):
    history = []


def save_state() -> None:
    save_json(STATE_FILE, state)


def save_history() -> None:
    global history

    # Keep history under control.
    if len(history) > 20_000:
        history = history[-20_000:]

    save_json(HISTORY_FILE, history)


def add_history(event: Dict[str, Any]) -> None:
    history.append({
        "timestamp": now_iso(),
        **event
    })

    save_history()


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


def telegram_get(method: str, params: Optional[Dict[str, Any]] = None) -> Any:
    if not TELEGRAM_TOKEN:
        return None

    url = f"{TELEGRAM_BASE}/bot{TELEGRAM_TOKEN}/{method}"

    if params:
        url += "?" + urllib.parse.urlencode(params)

    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as response:
            raw = response.read().decode("utf-8")

        return json.loads(raw)

    except Exception as e:
        print(f"Telegram GET ERROR: {e}")
        return None


def telegram_post(method: str, payload: Dict[str, Any]) -> Any:
    if not TELEGRAM_TOKEN:
        return None

    url = f"{TELEGRAM_BASE}/bot{TELEGRAM_TOKEN}/{method}"

    data = urllib.parse.urlencode(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read().decode("utf-8")

        return json.loads(raw)

    except Exception as e:
        print(f"Telegram POST ERROR: {e}")
        return None


def send_message(chat_id: str, text: str) -> None:
    telegram_post(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
        }
    )


def poll_telegram() -> None:
    if not TELEGRAM_TOKEN:
        return

    offset = safe_int(state.get("telegram_offset"), 0)

    result = telegram_get(
        "getUpdates",
        {
            "timeout": 0,
            "offset": offset,
        }
    )

    if not result or not result.get("ok"):
        return

    updates = result.get("result", [])

    for update in updates:

        update_id = safe_int(update.get("update_id"))

        if update_id >= offset:
            state["telegram_offset"] = update_id + 1

        message = update.get("message") or {}

        chat = message.get("chat") or {}

        chat_id = chat.get("id")

        text = (message.get("text") or "").strip()

        if chat_id is None:
            continue

        chat_id_str = str(chat_id)

        if text == "/start":

            if chat_id_str not in state["subscribers"]:
                state["subscribers"].append(chat_id_str)

            send_message(
                chat_id_str,
                (
                    "RUNNER BOT ONLINE\n\n"
                    "Low-cap hunt mode active.\n"
                    f"Primary zone: {money(MIN_MC)} - {money(PRIMARY_MAX_MC)}\n"
                    f"Secondary zone: {money(PRIMARY_MAX_MC)} - {money(MAX_MC)}\n"
                    f"Alert ceiling: {money(MAX_MC)}\n\n"
                    "The bot will prioritize early low-cap momentum."
                )
            )

        elif text == "/stop":

            if chat_id_str in state["subscribers"]:
                state["subscribers"].remove(chat_id_str)

            send_message(
                chat_id_str,
                "Runner bot alerts stopped."
            )

        elif text == "/status":

            tracking_count = len(state.get("tracking", {}))

            send_message(
                chat_id_str,
                (
                    "RUNNER BOT STATUS\n\n"
                    f"Version: {BOT_VERSION}\n"
                    f"Tracking: {tracking_count}\n"
                    f"Primary zone: {money(MIN_MC)} - {money(PRIMARY_MAX_MC)}\n"
                    f"Secondary zone: {money(PRIMARY_MAX_MC)} - {money(MAX_MC)}\n"
                    f"Min liquidity: {money(MIN_LIQUIDITY)}\n"
                    f"Scan interval: {SCAN_INTERVAL}s\n"
                    f"Discovery interval: {DISCOVERY_INTERVAL}s\n"
                    f"Validation interval: {VALIDATION_INTERVAL}s"
                )
            )

        elif text == "/history":

            recent = history[-10:]

            if not recent:
                send_message(
                    chat_id_str,
                    "No history yet."
                )
                continue

            lines = ["RECENT RUNNER HISTORY\n"]

            for event in recent:

                symbol = event.get("symbol", "?")
                classification = event.get("classification", "?")
                mc = money(event.get("market_cap"))

                lines.append(
                    f"{symbol} | {classification} | MC {mc}"
                )

            send_message(
                chat_id_str,
                "\n".join(lines)
            )

    save_state()


# ============================================================
# DEXSCREENER HTTP
# ============================================================

def dex_get(path: str) -> Any:

    url = DEX_BASE + path

    try:

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RunnerBot/1.3"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=HTTP_TIMEOUT
        ) as response:

            raw = response.read().decode("utf-8")

        return json.loads(raw)

    except urllib.error.HTTPError as e:

        print(
            f"DEX HTTP ERROR {e.code}: {path}"
        )

        return None

    except Exception as e:

        print(
            f"DEX ERROR {path}: {e}"
        )

        return None


# ============================================================
# PAIR SELECTION
# ============================================================

def choose_best_pair(data: Any) -> Optional[Dict[str, Any]]:

    if not isinstance(data, list):
        return None

    pairs = []

    for pair in data:

        if not isinstance(pair, dict):
            continue

        if pair.get("chainId") != CHAIN:
            continue

        pairs.append(pair)

    if not pairs:
        return None

    pairs.sort(
        key=lambda p: safe_float(
            (p.get("liquidity") or {}).get("usd")
        ),
        reverse=True
    )

    return pairs[0]


def token_pairs(token_address: str) -> Optional[Dict[str, Any]]:

    encoded = urllib.parse.quote(
        token_address,
        safe=""
    )

    data = dex_get(
        f"/token-pairs/v1/{CHAIN}/{encoded}"
    )

    return choose_best_pair(data)


# ============================================================
# SNAPSHOT
# ============================================================

def make_snapshot(pair: Dict[str, Any]) -> Dict[str, Any]:

    base_token = pair.get("baseToken") or {}

    token_address = (
        base_token.get("address")
        or ""
    )

    symbol = (
        base_token.get("symbol")
        or "?"
    )

    name = (
        base_token.get("name")
        or symbol
    )

    market_cap = safe_float(
        pair.get("marketCap")
    )

    if market_cap <= 0:

        market_cap = safe_float(
            pair.get("fdv")
        )

    liquidity = safe_float(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume_5m = safe_float(
        (pair.get("volume") or {}).get("m5")
    )

    txns_5m = (
        pair.get("txns") or {}
    ).get("m5") or {}

    buys_5m = safe_int(
        txns_5m.get("buys")
    )

    sells_5m = safe_int(
        txns_5m.get("sells")
    )

    total_tx_5m = buys_5m + sells_5m

    if sells_5m > 0:
        bs_ratio = buys_5m / sells_5m
    elif buys_5m > 0:
        bs_ratio = float(buys_5m)
    else:
        bs_ratio = 0.0

    price_change_5m = safe_float(
        (pair.get("priceChange") or {}).get("m5")
    )

    pair_created_ms = safe_float(
        pair.get("pairCreatedAt")
    )

    pair_age_hours = 0.0

    if pair_created_ms > 0:

        created_seconds = pair_created_ms / 1000

        age_seconds = (
            time.time() - created_seconds
        )

        pair_age_hours = max(
            0.0,
            age_seconds / 3600
        )

    if market_cap > 0:

        volume_mc_pct = (
            volume_5m / market_cap
        ) * 100

        liquidity_mc_pct = (
            liquidity / market_cap
        ) * 100

    else:

        volume_mc_pct = 0.0
        liquidity_mc_pct = 0.0

    return {
        "timestamp": now_iso(),

        "token_address": token_address,

        "symbol": symbol,
        "name": name,

        "market_cap": market_cap,
        "liquidity": liquidity,

        "volume_5m": volume_5m,
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
        "transactions_5m": total_tx_5m,

        "bs_ratio": bs_ratio,

        "price_change_5m": price_change_5m,

        "pair_age_hours": pair_age_hours,

        "volume_mc_pct": volume_mc_pct,
        "liquidity_mc_pct": liquidity_mc_pct,

        "pair_address": pair.get("pairAddress"),
        "dex": pair.get("dexId"),
        "url": pair.get("url"),
    }


# ============================================================
# SNAPSHOT COMPARISON
# ============================================================

def compare_snapshots(
    previous: Optional[Dict[str, Any]],
    current: Dict[str, Any]
) -> Dict[str, float]:

    if not previous:

        return {
            "mc_delta": 0.0,
            "liquidity_delta": 0.0,
            "price_delta": 0.0,
            "volume_delta": 0.0,
            "buys_delta": 0.0,
            "sells_delta": 0.0,
            "bs_delta": 0.0,
            "volume_mc_delta": 0.0,
            "tx_delta": 0.0,
        }

    return {
        "mc_delta": (
            safe_float(current.get("market_cap"))
            - safe_float(previous.get("market_cap"))
        ),

        "liquidity_delta": (
            safe_float(current.get("liquidity"))
            - safe_float(previous.get("liquidity"))
        ),

        "price_delta": (
            safe_float(current.get("price_change_5m"))
            - safe_float(previous.get("price_change_5m"))
        ),

        "volume_delta": (
            safe_float(current.get("volume_5m"))
            - safe_float(previous.get("volume_5m"))
        ),

        "buys_delta": (
            safe_float(current.get("buys_5m"))
            - safe_float(previous.get("buys_5m"))
        ),

        "sells_delta": (
            safe_float(current.get("sells_5m"))
            - safe_float(previous.get("sells_5m"))
        ),

        "bs_delta": (
            safe_float(current.get("bs_ratio"))
            - safe_float(previous.get("bs_ratio"))
        ),

        "volume_mc_delta": (
            safe_float(current.get("volume_mc_pct"))
            - safe_float(previous.get("volume_mc_pct"))
        ),

        "tx_delta": (
            safe_float(current.get("transactions_5m"))
            - safe_float(previous.get("transactions_5m"))
        ),
    }


# ============================================================
# SCORE 1 — DIRECTIONAL BUYING
# MAX 30
# ============================================================

def score_directional_buying(
    snap: Dict[str, Any]
) -> Tuple[int, List[str]]:

    score = 0
    reasons = []

    bs = safe_float(snap.get("bs_ratio"))
    tx = safe_int(snap.get("transactions_5m"))
    price = safe_float(snap.get("price_change_5m"))
    volume_mc = safe_float(snap.get("volume_mc_pct"))

    if bs >= 3:
        score += 16
        reasons.append("Very strong B/S")

    elif bs >= 2:
        score += 14
        reasons.append("Strong B/S")

    elif bs >= 1.5:
        score += 11
        reasons.append("Positive B/S")

    elif bs >= 1.2:
        score += 8
        reasons.append("Acceptable B/S")

    elif bs >= 1:
        score += 4
        reasons.append("Balanced B/S")

    else:
        score -= 4
        reasons.append("Selling pressure")

    if tx >= 200:
        score += 8
        reasons.append("High transactions")

    elif tx >= 100:
        score += 6
        reasons.append("Strong transactions")

    elif tx >= 50:
        score += 4
        reasons.append("Good transactions")

    elif tx >= 20:
        score += 2

    else:
        score -= 4
        reasons.append("Low transactions")

    if price >= 10:
        score += 6

    elif price > 0:
        score += 4

    elif price <= -10:
        score -= 10
        reasons.append("Negative price momentum")

    elif price < 0:
        score -= 5
        reasons.append("Price weakening")

    if volume_mc >= 20 and price <= -15:
        score -= 8
        reasons.append("High volume but falling price")

    return max(0, min(30, score)), reasons


# ============================================================
# SCORE 2 — PRICE MOMENTUM
# MAX 20
# ============================================================

def score_price_momentum(
    snap: Dict[str, Any]
) -> Tuple[int, List[str]]:

    score = 0
    reasons = []

    price = safe_float(
        snap.get("price_change_5m")
    )

    volume_mc = safe_float(
        snap.get("volume_mc_pct")
    )

    bs = safe_float(
        snap.get("bs_ratio")
    )

    if price <= -30:
        score = 0

    elif price <= -15:
        score = 1

    elif price < 0:
        score = 4

    elif price == 0:
        score = 5

    elif price <= 3:
        score = 9

    elif price <= 10:
        score = 16

    elif price <= 20:
        score = 19

    elif price <= 30:
        score = 18

    elif price <= 40:
        score = 15

    elif price <= 50:
        score = 11

    elif price <= 60:
        score = 8

    else:
        score = 5

    if (
        price > 0
        and price <= 30
        and volume_mc >= 15
        and bs >= 1.2
    ):
        score += 1
        reasons.append("Healthy momentum alignment")

    if price > 60 and volume_mc >= 50:
        score -= 2
        reasons.append("Already heavily extended")

    return max(0, min(20, score)), reasons


# ============================================================
# SCORE 3 — VOLUME QUALITY
# MAX 20
# ============================================================

def score_volume_quality(
    snap: Dict[str, Any]
) -> Tuple[int, List[str]]:

    score = 0
    reasons = []

    volume_mc = safe_float(
        snap.get("volume_mc_pct")
    )

    price = safe_float(
        snap.get("price_change_5m")
    )

    bs = safe_float(
        snap.get("bs_ratio")
    )

    tx = safe_int(
        snap.get("transactions_5m")
    )

    if volume_mc >= 50:
        score = 14

    elif volume_mc >= 30:
        score = 13

    elif volume_mc >= 15:
        score = 11

    elif volume_mc >= 7:
        score = 8

    elif volume_mc >= 3:
        score = 5

    elif volume_mc >= 1:
        score = 3

    elif volume_mc > 0:
        score = 1

    if price > 0 and bs >= 1.5:
        score += 6
        reasons.append("Volume aligned with buying")

    elif price > 0 and bs >= 1:
        score += 3

    elif price == 0 and bs >= 1.5:
        reasons.append("Buying pressure building")

    elif price < 0 and volume_mc >= 20:
        score -= 12
        reasons.append("Volume with negative price")

    elif price < 0:
        score -= 5

    if tx < 20:
        score -= 4
        reasons.append("Low transaction activity")

    return max(0, min(20, score)), reasons


# ============================================================
# SCORE 4 — STRUCTURE
# MAX 15
# ============================================================

def score_structure(
    snap: Dict[str, Any],
    comparison: Dict[str, float]
) -> Tuple[int, List[str]]:

    score = 0
    reasons = []

    liquidity_mc = safe_float(
        snap.get("liquidity_mc_pct")
    )

    liquidity = safe_float(
        snap.get("liquidity")
    )

    liquidity_delta = safe_float(
        comparison.get("liquidity_delta")
    )

    if liquidity_mc >= 20:
        score += 10

    elif liquidity_mc >= 12:
        score += 8

    elif liquidity_mc >= 8:
        score += 6

    elif liquidity_mc >= 5:
        score += 4

    elif liquidity_mc >= 3:
        score += 2

    if liquidity >= 50_000:
        score += 3

    elif liquidity >= 20_000:
        score += 2

    elif liquidity >= 10_000:
        score += 1

    if liquidity_delta >= 0.05 * max(
        liquidity,
        1
    ):
        score += 2
        reasons.append("Liquidity improving")

    elif liquidity_delta < -0.10 * max(
        liquidity,
        1
    ):
        score -= 3
        reasons.append("Liquidity falling")

    return max(0, min(15, score)), reasons


# ============================================================
# SCORE 5 — PERSISTENCE
# MAX 15
# ============================================================

def score_persistence(
    snapshots: List[Dict[str, Any]]
) -> Tuple[int, List[str]]:

    if len(snapshots) < 2:
        return 0, ["No persistence yet"]

    recent = snapshots[-3:]

    aligned_count = 0
    directional_count = 0

    for snap in recent:

        bs = safe_float(
            snap.get("bs_ratio")
        )

        price = safe_float(
            snap.get("price_change_5m")
        )

        volume_mc = safe_float(
            snap.get("volume_mc_pct")
        )

        tx = safe_int(
            snap.get("transactions_5m")
        )

        aligned = (
            bs >= 1.2
            and price > 0
            and volume_mc >= 1
            and tx >= 20
        )

        directional = (
            bs >= 1.5
            and price > 0
        )

        if aligned:
            aligned_count += 1

        if directional:
            directional_count += 1

    score = 0
    reasons = []

    if len(recent) == 2:

        if aligned_count == 2:
            score = 10
            reasons.append(
                "2/2 observations aligned"
            )

        elif aligned_count == 1:
            score = 4
            reasons.append(
                "1/2 observations aligned"
            )

        else:
            score = 0

    else:

        if aligned_count == 3:
            score = 15
            reasons.append(
                "3/3 observations aligned"
            )

        elif aligned_count == 2:
            score = 10
            reasons.append(
                "2/3 observations aligned"
            )

        elif aligned_count == 1:
            score = 4
            reasons.append(
                "1/3 observations aligned"
            )

        else:
            score = 0

    if directional_count >= 3:
        score = min(15, score + 2)
        reasons.append(
            "Repeated directional buying"
        )

    return score, reasons


# ============================================================
# TOTAL SCORE
# ============================================================

def calculate_score(
    snap: Dict[str, Any],
    snapshots: List[Dict[str, Any]],
    comparison: Dict[str, float]
) -> Dict[str, Any]:

    directional_score, directional_reasons = (
        score_directional_buying(snap)
    )

    momentum_score, momentum_reasons = (
        score_price_momentum(snap)
    )

    volume_score, volume_reasons = (
        score_volume_quality(snap)
    )

    structure_score, structure_reasons = (
        score_structure(
            snap,
            comparison
        )
    )

    persistence_score, persistence_reasons = (
        score_persistence(snapshots)
    )

    total = (
        directional_score
        + momentum_score
        + volume_score
        + structure_score
        + persistence_score
    )

    hard_warning = (
        safe_float(snap.get("volume_mc_pct")) >= 20
        and safe_float(snap.get("price_change_5m")) <= -15
    )

    if hard_warning:
        total = min(total, 45)

    reasons = (
        directional_reasons
        + momentum_reasons
        + volume_reasons
        + structure_reasons
        + persistence_reasons
    )

    return {
        "total": total,

        "directional": directional_score,
        "momentum": momentum_score,
        "volume": volume_score,
        "structure": structure_score,
        "persistence": persistence_score,

        "hard_warning": hard_warning,

        "reasons": reasons,
    }


# ============================================================
# CLASSIFICATION
# ============================================================

def classify(
    snap: Dict[str, Any],
    score_data: Dict[str, Any],
    observation_count: int
) -> str:

    score = safe_float(
        score_data.get("total")
    )

    hard_warning = bool(
        score_data.get("hard_warning")
    )

    price = safe_float(
        snap.get("price_change_5m")
    )

    bs = safe_float(
        snap.get("bs_ratio")
    )

    if observation_count < MIN_OBSERVATIONS_RUNNER:
        return "WATCH"

    if hard_warning:
        return "WATCH"

    if (
        score >= RUNNER_SCORE
        and price > 0
        and bs >= 1.2
    ):

        if (
            observation_count >= MIN_OBSERVATIONS_IDEAL
            and score >= IDEAL_SCORE
            and bs >= 1.5
        ):
            return "IDEAL RUNNER"

        return "RUNNER"

    return "WATCH"


# ============================================================
# DISCOVERY PRIORITY
#
# THIS IS THE IMPORTANT LOW-CAP CORRECTION.
#
# Zone 2 = $20K-$80K
# Zone 1 = $80K-$150K
# Zone 0 = outside alert range
#
# The bot therefore does NOT simply sort by volume/MC anymore.
# ============================================================

def discovery_priority(
    snap: Dict[str, Any]
) -> Tuple[int, float, float, float, float]:

    mc = safe_float(
        snap.get("market_cap")
    )

    volume_mc = safe_float(
        snap.get("volume_mc_pct")
    )

    bs = safe_float(
        snap.get("bs_ratio")
    )

    price = safe_float(
        snap.get("price_change_5m")
    )

    liquidity = safe_float(
        snap.get("liquidity")
    )

    # --------------------------------------------------------
    # PRIMARY LOW-CAP ZONE
    # --------------------------------------------------------

    if MIN_MC <= mc <= PRIMARY_MAX_MC:
        zone = 2

    # --------------------------------------------------------
    # SECONDARY ZONE
    # --------------------------------------------------------

    elif PRIMARY_MAX_MC < mc <= MAX_MC:
        zone = 1

    # --------------------------------------------------------
    # OUTSIDE ALERT RANGE
    # --------------------------------------------------------

    else:
        zone = 0

    return (
        zone,
        volume_mc,
        bs,
        price,
        liquidity,
    )


# ============================================================
# DISCOVERY FEED
# ============================================================

DISCOVERY_ENDPOINTS = [
    "/token-profiles/latest/v1",
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
    "/community-takeovers/latest/v1",
]


def extract_token_addresses(
    data: Any
) -> List[str]:

    addresses = []

    if isinstance(data, list):

        items = data

    elif isinstance(data, dict):

        if isinstance(
            data.get("tokens"),
            list
        ):
            items = data["tokens"]

        elif isinstance(
            data.get("data"),
            list
        ):
            items = data["data"]

        else:
            items = [data]

    else:
        items = []

    for item in items:

        if not isinstance(item, dict):
            continue

        address = (
            item.get("tokenAddress")
            or item.get("token_address")
            or item.get("address")
        )

        if address:
            addresses.append(str(address))

    return addresses


def discover_feed() -> List[Dict[str, Any]]:

    addresses = []
    seen_addresses = set()

    # --------------------------------------------------------
    # COLLECT DISCOVERY ADDRESSES
    # --------------------------------------------------------

    for endpoint in DISCOVERY_ENDPOINTS:

        data = dex_get(endpoint)

        found = extract_token_addresses(
            data
        )

        for address in found:

            if address in seen_addresses:
                continue

            seen_addresses.add(address)
            addresses.append(address)

    print(
        f"DISCOVERY | Raw unique tokens: {len(addresses)}"
    )

    # --------------------------------------------------------
    # BUILD SNAPSHOTS
    # --------------------------------------------------------

    candidates = []

    for address in addresses:

        pair = token_pairs(address)

        if not pair:
            continue

        snap = make_snapshot(pair)

        mc = safe_float(
            snap.get("market_cap")
        )

        liquidity = safe_float(
            snap.get("liquidity")
        )

        # ----------------------------------------------------
        # DISCOVERY HARD RANGE
        #
        # Do not waste candidate slots on tokens outside
        # our desired alert range.
        # ----------------------------------------------------

        if mc < MIN_MC:
            continue

        if mc > MAX_MC:
            continue

        if liquidity < MIN_LIQUIDITY:
            continue

        candidates.append(snap)

    # --------------------------------------------------------
    # LOW-CAP FIRST SORT
    # --------------------------------------------------------

    candidates.sort(
        key=discovery_priority,
        reverse=True
    )

    candidates = candidates[
        :MAX_DISCOVERY_CANDIDATES
    ]

    primary_count = 0
    secondary_count = 0

    for snap in candidates:

        mc = safe_float(
            snap.get("market_cap")
        )

        if mc <= PRIMARY_MAX_MC:
            primary_count += 1

        else:
            secondary_count += 1

    print(
        "DISCOVERY | "
        f"Selected {len(candidates)} | "
        f"Primary <$80K: {primary_count} | "
        f"Secondary $80K-$150K: {secondary_count}"
    )

    return candidates


# ============================================================
# TRACKING
# ============================================================

def ensure_tracking(
    snap: Dict[str, Any]
) -> None:

    address = snap.get(
        "token_address"
    )

    if not address:
        return

    tracking = state.setdefault(
        "tracking",
        {}
    )

    if address not in tracking:

        tracking[address] = {
            "token_address": address,

            "symbol": snap.get(
                "symbol",
                "?"
            ),

            "name": snap.get(
                "name",
                "?"
            ),

            "first_seen": now_iso(),

            "initial_market_cap": safe_float(
                snap.get("market_cap")
            ),

            "initial_liquidity": safe_float(
                snap.get("liquidity")
            ),

            "max_market_cap": safe_float(
                snap.get("market_cap")
            ),

            "min_market_cap": safe_float(
                snap.get("market_cap")
            ),

            "max_liquidity": safe_float(
                snap.get("liquidity")
            ),

            "snapshots": [],

            "alerts": [],

            "last_alert_classification": None,

            "last_score": 0,

            "last_classification": "WATCH",

            "actual_alert_market_cap": None,

            "outcome": None,

            "outcome_checked": False,
        }


# ============================================================
# VALIDATION
#
# IMPORTANT:
#
# A token discovered at $30K can continue to be tracked at
# $300K or $1M.
#
# BUT:
# It cannot generate a NEW alert above $150K.
#
# This fixes the old problem where a token could be discovered
# earlier but only alert after reaching $450K.
# ============================================================

def validate_token(
    address: str,
    record: Dict[str, Any]
) -> None:

    pair = token_pairs(address)

    if not pair:
        return

    snap = make_snapshot(pair)

    current_mc = safe_float(
        snap.get("market_cap")
    )

    current_liquidity = safe_float(
        snap.get("liquidity")
    )

    snapshots = record.setdefault(
        "snapshots",
        []
    )

    previous = (
        snapshots[-1]
        if snapshots
        else None
    )

    comparison = compare_snapshots(
        previous,
        snap
    )

    # --------------------------------------------------------
    # ALWAYS UPDATE TRACKING DATA
    #
    # This is important.
    #
    # If a token runs from $30K to $1M, we still want to know
    # that it happened.
    # --------------------------------------------------------

    snapshots.append(snap)

    # Keep only the recent observations needed for scoring.
    if len(snapshots) > 20:
        del snapshots[:-20]

    record["max_market_cap"] = max(
        safe_float(
            record.get("max_market_cap")
        ),
        current_mc
    )

    old_min_mc = safe_float(
        record.get("min_market_cap")
    )

    if old_min_mc <= 0:
        record["min_market_cap"] = current_mc

    else:
        record["min_market_cap"] = min(
            old_min_mc,
            current_mc
        )

    record["max_liquidity"] = max(
        safe_float(
            record.get("max_liquidity")
        ),
        current_liquidity
    )

    # --------------------------------------------------------
    # HARD CURRENT-MC ALERT GATE
    #
    # This is the second major correction.
    #
    # A tracked token that has already reached $150K+
    # cannot suddenly generate a late runner alert.
    # --------------------------------------------------------

    if (
        current_mc < MIN_MC
        or current_mc > MAX_MC
    ):

        record["last_classification"] = (
            "OUT_OF_ALERT_RANGE"
        )

        record["last_score"] = 0

        print(
            f"TRACKING ONLY | "
            f"{snap.get('symbol', '?')} | "
            f"MC {money(current_mc)} | "
            f"Alert range "
            f"{money(MIN_MC)}-"
            f"{money(MAX_MC)}"
        )

        return

    # --------------------------------------------------------
    # SCORE ONLY INSIDE ALERT RANGE
    # --------------------------------------------------------

    score_data = calculate_score(
        snap,
        snapshots,
        comparison
    )

    observation_count = len(
        snapshots
    )

    classification = classify(
        snap,
        score_data,
        observation_count
    )

    record["last_score"] = (
        score_data["total"]
    )

    record["last_classification"] = (
        classification
    )

    # --------------------------------------------------------
    # ALERT
    # --------------------------------------------------------

    should_alert(
        address,
        record,
        snap,
        score_data,
        classification
    )


# ============================================================
# ALERT DECISION
# ============================================================

def should_alert(
    address: str,
    record: Dict[str, Any],
    snap: Dict[str, Any],
    score_data: Dict[str, Any],
    classification: str
) -> None:

    if classification not in (
        "RUNNER",
        "IDEAL RUNNER"
    ):
        return

    current_mc = safe_float(
        snap.get("market_cap")
    )

    # --------------------------------------------------------
    # FINAL SAFETY GATE
    #
    # Even if another part of the code changes later,
    # this prevents an alert above MAX_MC.
    # --------------------------------------------------------

    if (
        current_mc < MIN_MC
        or current_mc > MAX_MC
    ):
        return

    previous_alert = record.get(
        "last_alert_classification"
    )

    # Do not repeatedly alert on the exact same classification.
    if previous_alert == classification:
        return

    record["last_alert_classification"] = (
        classification
    )

    record.setdefault(
        "alerts",
        []
    ).append({
        "timestamp": now_iso(),
        "classification": classification,
        "market_cap": current_mc,
        "score": score_data["total"],
    })

    # Store the actual MC where the alert happened.
    if record.get(
        "actual_alert_market_cap"
    ) is None:

        record["actual_alert_market_cap"] = (
            current_mc
        )

    add_history({
        "event": "ALERT",

        "symbol": snap.get(
            "symbol",
            "?"
        ),

        "token_address": address,

        "market_cap": current_mc,

        "liquidity": safe_float(
            snap.get("liquidity")
        ),

        "classification": classification,

        "score": score_data["total"],
    })

    message = format_alert(
        snap,
        score_data,
        classification
    )

    for chat_id in state.get(
        "subscribers",
        []
    ):

        send_message(
            str(chat_id),
            message
        )

    save_state()


# ============================================================
# ALERT FORMAT
# ============================================================

def format_alert(
    snap: Dict[str, Any],
    score_data: Dict[str, Any],
    classification: str
) -> str:

    symbol = snap.get(
        "symbol",
        "?"
    )

    name = snap.get(
        "name",
        symbol
    )

    mc = safe_float(
        snap.get("market_cap")
    )

    liquidity = safe_float(
        snap.get("liquidity")
    )

    price = safe_float(
        snap.get("price_change_5m")
    )

    bs = safe_float(
        snap.get("bs_ratio")
    )

    volume_mc = safe_float(
        snap.get("volume_mc_pct")
    )

    tx = safe_int(
        snap.get("transactions_5m")
    )

    observations = len(
        snap.get(
            "snapshots",
            []
        )
    )

    reasons = score_data.get(
        "reasons",
        []
    )

    reason_text = "\n".join(
        f"• {reason}"
        for reason in reasons[:8]
    )

    return (
        f"🚀 {classification}\n\n"

        f"{symbol} — {name}\n\n"

        f"MC: {money(mc)}\n"
        f"Liquidity: {money(liquidity)}\n"
        f"5m Price: {price:+.1f}%\n"
        f"5m B/S: {bs:.2f}\n"
        f"5m Vol/MC: {volume_mc:.1f}%\n"
        f"5m TX: {tx}\n\n"

        f"SCORE: {score_data['total']}/100\n"
        f"Directional: {score_data['directional']}/30\n"
        f"Momentum: {score_data['momentum']}/20\n"
        f"Volume: {score_data['volume']}/20\n"
        f"Structure: {score_data['structure']}/15\n"
        f"Persistence: {score_data['persistence']}/15\n\n"

        f"Observations: {observations}\n\n"

        f"{reason_text}\n\n"

        f"Pair: {snap.get('pair_address', '?')}\n"
        f"DEX: {snap.get('dex', '?')}\n\n"

        f"CA:\n"
        f"{snap.get('token_address', '?')}"
    )


# ============================================================
# OUTCOME TRACKING
#
# Tokens that run beyond $150K remain trackable.
# This allows us to identify whether an early alert eventually
# became a major runner.
# ============================================================

def clean_old_tracking() -> None:

    tracking = state.get(
        "tracking",
        {}
    )

    now = time.time()

    remove_addresses = []

    for address, record in list(
        tracking.items()
    ):

        first_seen_text = record.get(
            "first_seen"
        )

        if not first_seen_text:
            continue

        try:
            first_seen = datetime.fromisoformat(
                first_seen_text
            ).timestamp()

        except Exception:
            continue

        age_hours = (
            now - first_seen
        ) / 3600

        if age_hours < TRACKING_HOURS:
            continue

        if record.get(
            "outcome_checked"
        ):
            remove_addresses.append(
                address
            )
            continue

        initial_mc = safe_float(
            record.get(
                "initial_market_cap"
            )
        )

        max_mc = safe_float(
            record.get(
                "max_market_cap"
            )
        )

        initial_liquidity = safe_float(
            record.get(
                "initial_liquidity"
            )
        )

        max_liquidity = safe_float(
            record.get(
                "max_liquidity"
            )
        )

        outcome = "NEUTRAL"

        if (
            initial_mc > 0
            and max_mc >= (
                initial_mc
                * CONTINUATION_MC_MULTIPLE
            )
            and (
                initial_liquidity <= 0
                or max_liquidity >= (
                    initial_liquidity
                    * CONTINUATION_LIQUIDITY_MULTIPLE
                )
            )
        ):

            outcome = "CONTINUED"

        elif (
            initial_mc > 0
            and max_mc <= (
                initial_mc
                * FAILURE_MC_MULTIPLE
            )
        ):

            outcome = "FAILED"

        record["outcome"] = outcome
        record["outcome_checked"] = True

        add_history({
            "event": "OUTCOME",

            "symbol": record.get(
                "symbol",
                "?"
            ),

            "token_address": address,

            "market_cap": max_mc,

            "classification": outcome,

            "initial_market_cap": initial_mc,

            "max_market_cap": max_mc,
        })

        remove_addresses.append(
            address
        )

    for address in remove_addresses:

        tracking.pop(
            address,
            None
        )

    if remove_addresses:
        save_state()


# ============================================================
# DISCOVERY CYCLE
# ============================================================

def run_discovery() -> None:

    print(
        "\n"
        + "=" * 68
    )

    print(
        "LOW-CAP DISCOVERY"
    )

    print(
        f"Primary hunt: "
        f"{money(MIN_MC)} - "
        f"{money(PRIMARY_MAX_MC)}"
    )

    print(
        f"Secondary: "
        f"{money(PRIMARY_MAX_MC)} - "
        f"{money(MAX_MC)}"
    )

    print(
        f"Liquidity minimum: "
        f"{money(MIN_LIQUIDITY)}"
    )

    print(
        "=" * 68
    )

    candidates = discover_feed()

    for snap in candidates:

        ensure_tracking(
            snap
        )

        print(
            f"TRACK | "
            f"{snap.get('symbol', '?')} | "
            f"MC {money(snap.get('market_cap'))} | "
            f"Vol/MC {pct(snap.get('volume_mc_pct'))} | "
            f"B/S {ratio(snap.get('bs_ratio'))}"
        )

    state["last_discovery"] = time.time()

    save_state()


# ============================================================
# VALIDATION CYCLE
# ============================================================

def run_validation() -> None:

    tracking = state.get(
        "tracking",
        {}
    )

    if not tracking:
        return

    print(
        "\n"
        + "=" * 68
    )

    print(
        f"VALIDATION | Tracking {len(tracking)} tokens"
    )

    print(
        "=" * 68
    )

    for address, record in list(
        tracking.items()
    ):

        try:

            validate_token(
                address,
                record
            )

        except Exception as e:

            print(
                f"VALIDATION ERROR | "
                f"{record.get('symbol', '?')} | "
                f"{e}"
            )

    state["last_validation"] = time.time()

    save_state()


# ============================================================
# STARTUP
# ============================================================

def print_startup() -> None:

    print(
        "=" * 68
    )

    print(
        f"RUNNER BOT {BOT_VERSION}"
    )

    print(
        "=" * 68
    )

    print(
        f"MC alert range: "
        f"{money(MIN_MC)} - {money(MAX_MC)}"
    )

    print(
        f"PRIMARY LOW-CAP HUNT: "
        f"{money(MIN_MC)} - "
        f"{money(PRIMARY_MAX_MC)}"
    )

    print(
        f"SECONDARY ZONE: "
        f"{money(PRIMARY_MAX_MC)} - "
        f"{money(MAX_MC)}"
    )

    print(
        f"Liquidity minimum: "
        f"{money(MIN_LIQUIDITY)}"
    )

    print(
        f"Discovery candidates: "
        f"{MAX_DISCOVERY_CANDIDATES}"
    )

    print(
        f"Discovery interval: "
        f"{DISCOVERY_INTERVAL}s"
    )

    print(
        f"Validation interval: "
        f"{VALIDATION_INTERVAL}s"
    )

    print(
        f"Tracking window: "
        f"{TRACKING_HOURS}h"
    )

    print(
        "=" * 68
    )


# ============================================================
# MAIN LOOP
# ============================================================

def main() -> None:

    print_startup()

    if not TELEGRAM_TOKEN:

        print(
            "WARNING: TELEGRAM_BOT_TOKEN "
            "is not set."
        )

    last_discovery_run = 0.0
    last_validation_run = 0.0

    while True:

        try:

            # ------------------------------------------------
            # TELEGRAM
            # ------------------------------------------------

            poll_telegram()

            current_time = time.time()

            # ------------------------------------------------
            # DISCOVERY
            # ------------------------------------------------

            if (
                current_time
                - last_discovery_run
                >= DISCOVERY_INTERVAL
            ):

                run_discovery()

                last_discovery_run = (
                    current_time
                )

            # ------------------------------------------------
            # VALIDATION
            # ------------------------------------------------

            if (
                current_time
                - last_validation_run
                >= VALIDATION_INTERVAL
            ):

                run_validation()

                last_validation_run = (
                    current_time
                )

                clean_old_tracking()

            # ------------------------------------------------
            # SLEEP
            # ------------------------------------------------

            time.sleep(
                SCAN_INTERVAL
            )

        except KeyboardInterrupt:

            print(
                "\nRunner bot stopped."
            )

            save_state()
            save_history()

            break

        except Exception as e:

            print(
                f"MAIN LOOP ERROR: {e}"
            )

            time.sleep(
                SCAN_INTERVAL
            )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
