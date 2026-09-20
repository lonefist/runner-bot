import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V1.1
# DIRECTIONAL ACTIVITY + PERSISTENCE ENGINE
#
# IMPORTANT:
# - NO DexScreener search queries
# - Discovery uses live DexScreener feeds
# - Scores activity only when it is directionally useful
# - Huge volume + collapsing price is penalized
# - High B/S with tiny activity is penalized
# - First observation is ALWAYS WATCH
# - RUNNER / IDEAL RUNNER require persistence
# ============================================================

BOT_VERSION = "V1.1-DIRECTIONAL-PERSISTENCE"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"


# ============================================================
# FILES
# ============================================================

STATE_FILE = "runner_v11_state.json"
HISTORY_FILE = "runner_v11_history.json"


# ============================================================
# SCANNER SETTINGS
# ============================================================

CHAIN = "solana"

MIN_MC = 10_000
MAX_MC = 1_000_000

MIN_LIQUIDITY = 5_000

SCAN_INTERVAL = 20

DISCOVERY_INTERVAL = 300
VALIDATION_INTERVAL = 300

TRACKING_HOURS = 8

MAX_DISCOVERY_CANDIDATES = 30


# ============================================================
# CLASSIFICATION
# ============================================================

WATCH_SCORE = 55
RUNNER_SCORE = 72
IDEAL_SCORE = 85

MIN_OBSERVATIONS_RUNNER = 2
MIN_OBSERVATIONS_IDEAL = 3


# ============================================================
# OUTCOME RESEARCH
# ============================================================

CONTINUATION_MC_MULTIPLE = 1.50
CONTINUATION_LIQUIDITY_MULTIPLE = 0.70

FAILURE_MC_MULTIPLE = 0.80
FAILURE_LIQUIDITY_MULTIPLE = 0.50


# ============================================================
# HTTP
# ============================================================

HTTP_TIMEOUT = 15


# ============================================================
# HELPERS
# ============================================================

def now_iso() -> str:
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


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def pct_change(old: float, new: float) -> float:
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100.0


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
        json.dump(data, f, indent=2, ensure_ascii=False)

    os.replace(temp_path, path)


# ============================================================
# DEFAULT STATE
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

if not isinstance(state, dict):
    state = default_state()


# ============================================================
# HISTORY
# ============================================================

history = load_json(HISTORY_FILE, [])

if not isinstance(history, list):
    history = []


def history_add(event: Dict[str, Any]) -> None:
    global history

    event = dict(event)
    event["timestamp"] = now_iso()

    history.append(event)

    # Keep file manageable.
    if len(history) > 20_000:
        history = history[-20_000:]

    save_json(HISTORY_FILE, history)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def telegram_request(
    method: str,
    params: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:

    if not TELEGRAM_TOKEN:
        return None

    url = f"{TELEGRAM_BASE}/bot{TELEGRAM_TOKEN}/{method}"

    try:
        if params:
            data = urllib.parse.urlencode(params).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                method="POST"
            )
        else:
            req = urllib.request.Request(url)

        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)

    except Exception as e:
        print(f"Telegram error: {e}")
        return None


def send_message(chat_id: str, text: str) -> None:

    telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    )


# ============================================================
# DEXSCREENER
# ============================================================

def dex_get(path: str) -> Any:

    url = DEX_BASE + path

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RunnerBot/1.1"
            }
        )

        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)

    except urllib.error.HTTPError as e:
        print(f"Dex HTTP error {e.code}: {path}")
        return None

    except Exception as e:
        print(f"Dex error: {e}")
        return None


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def process_telegram_updates() -> None:

    if not TELEGRAM_TOKEN:
        return

    offset = safe_int(state.get("telegram_offset", 0))

    result = telegram_request(
        "getUpdates",
        {
            "timeout": 1,
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

        if chat_id is None:
            continue

        text = str(message.get("text", "")).strip()

        if not text:
            continue

        chat_id_str = str(chat_id)

        if text.startswith("/start"):

            if chat_id_str not in state["subscribers"]:
                state["subscribers"].append(chat_id_str)

            send_message(
                chat_id_str,
                "RUNNER BOT V1.1 is online.\n\n"
                "Directional activity + persistence engine enabled.\n"
                "No DexScreener search queries are used."
            )

        elif text.startswith("/stop"):

            if chat_id_str in state["subscribers"]:
                state["subscribers"].remove(chat_id_str)

            send_message(
                chat_id_str,
                "Runner alerts stopped for this chat."
            )

        elif text.startswith("/status"):

            tracking_count = len(state.get("tracking", {}))

            send_message(
                chat_id_str,
                f"RUNNER BOT V1.1\n\n"
                f"Tracked tokens: {tracking_count}\n"
                f"Subscribers: {len(state['subscribers'])}\n"
                f"Discovery interval: {DISCOVERY_INTERVAL}s\n"
                f"Validation interval: {VALIDATION_INTERVAL}s\n"
                f"Tracking window: {TRACKING_HOURS}h"
            )

        elif text.startswith("/history"):

            alerts = [
                x for x in history
                if x.get("event") == "alert"
            ]

            recent = alerts[-10:]

            if not recent:
                send_message(
                    chat_id_str,
                    "No runner alerts recorded yet."
                )
            else:

                lines = ["Recent alerts:\n"]

                for item in reversed(recent):

                    lines.append(
                        f"{item.get('symbol', '?')} | "
                        f"{item.get('classification', '?')} | "
                        f"{item.get('score', 0):.1f}"
                    )

                send_message(
                    chat_id_str,
                    "\n".join(lines)
                )

    save_json(STATE_FILE, state)


# ============================================================
# PAIR EXTRACTION
# ============================================================

def choose_best_pair(pairs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:

    solana_pairs = [
        p for p in pairs
        if str(p.get("chainId", "")).lower() == CHAIN
    ]

    if not solana_pairs:
        return None

    # Highest liquidity first.
    solana_pairs.sort(
        key=lambda p: safe_float(
            (p.get("liquidity") or {}).get("usd")
        ),
        reverse=True
    )

    return solana_pairs[0]


def token_pairs(token_address: str) -> List[Dict[str, Any]]:

    data = dex_get(
        f"/token-pairs/v1/{CHAIN}/{urllib.parse.quote(token_address)}"
    )

    if not data:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        pairs = data.get("pairs")

        if isinstance(pairs, list):
            return pairs

    return []


# ============================================================
# SNAPSHOT
# ============================================================

def make_snapshot(
    token_address: str,
    pair: Dict[str, Any],
    source: str = ""
) -> Dict[str, Any]:

    base = pair.get("baseToken") or {}

    symbol = str(base.get("symbol") or "?")
    name = str(base.get("name") or symbol)

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

    txns_5m = (pair.get("txns") or {}).get("m5") or {}

    buys = safe_int(
        txns_5m.get("buys")
    )

    sells = safe_int(
        txns_5m.get("sells")
    )

    total_txns = buys + sells

    if sells > 0:
        bs_ratio = buys / sells
    elif buys > 0:
        bs_ratio = float(buys)
    else:
        bs_ratio = 0.0

    price_change_5m = safe_float(
        (pair.get("priceChange") or {}).get("m5")
    )

    price_usd = safe_float(
        pair.get("priceUsd")
    )

    created_ms = safe_float(
        pair.get("pairCreatedAt")
    )

    if created_ms > 0:
        age_hours = max(
            0.0,
            (time.time() * 1000 - created_ms) / 3_600_000
        )
    else:
        age_hours = 0.0

    volume_mc_pct = (
        (volume_5m / market_cap) * 100
        if market_cap > 0
        else 0.0
    )

    liquidity_mc_pct = (
        (liquidity / market_cap) * 100
        if market_cap > 0
        else 0.0
    )

    return {
        "timestamp": now_iso(),
        "token_address": token_address,

        "symbol": symbol,
        "name": name,

        "market_cap": market_cap,
        "liquidity": liquidity,

        "price_usd": price_usd,
        "price_change_5m": price_change_5m,

        "volume_5m": volume_5m,
        "volume_mc_pct": volume_mc_pct,

        "buys_5m": buys,
        "sells_5m": sells,
        "transactions_5m": total_txns,
        "bs_ratio": bs_ratio,

        "liquidity_mc_pct": liquidity_mc_pct,

        "age_hours": age_hours,

        "pair_address": pair.get("pairAddress", ""),
        "dex_id": pair.get("dexId", ""),

        "source": source,
    }


# ============================================================
# FEATURE DIFFERENCES
# ============================================================

def compare_snapshots(
    previous: Optional[Dict[str, Any]],
    current: Dict[str, Any]
) -> Dict[str, float]:

    if not previous:
        return {
            "mc_change_pct": 0.0,
            "liquidity_change_pct": 0.0,
            "price_change_delta": 0.0,
            "volume_change_pct": 0.0,
            "buys_change_pct": 0.0,
            "sells_change_pct": 0.0,
            "bs_change": 0.0,
            "volume_mc_change": 0.0,
            "tx_change_pct": 0.0,
        }

    return {
        "mc_change_pct": pct_change(
            safe_float(previous.get("market_cap")),
            safe_float(current.get("market_cap"))
        ),

        "liquidity_change_pct": pct_change(
            safe_float(previous.get("liquidity")),
            safe_float(current.get("liquidity"))
        ),

        "price_change_delta":
            safe_float(current.get("price_change_5m"))
            - safe_float(previous.get("price_change_5m")),

        "volume_change_pct": pct_change(
            safe_float(previous.get("volume_5m")),
            safe_float(current.get("volume_5m"))
        ),

        "buys_change_pct": pct_change(
            safe_float(previous.get("buys_5m")),
            safe_float(current.get("buys_5m"))
        ),

        "sells_change_pct": pct_change(
            safe_float(previous.get("sells_5m")),
            safe_float(current.get("sells_5m"))
        ),

        "bs_change":
            safe_float(current.get("bs_ratio"))
            - safe_float(previous.get("bs_ratio")),

        "volume_mc_change":
            safe_float(current.get("volume_mc_pct"))
            - safe_float(previous.get("volume_mc_pct")),

        "tx_change_pct": pct_change(
            safe_float(previous.get("transactions_5m")),
            safe_float(current.get("transactions_5m"))
        ),
    }


# ============================================================
# SCORE COMPONENT 1
# DIRECTIONAL BUYING PRESSURE
# MAX = 30
#
# Important:
# B/S alone cannot give a huge score.
# Transaction activity matters.
# ============================================================

def score_directional_buying(
    snap: Dict[str, Any]
) -> Tuple[float, List[str]]:

    score = 0.0
    reasons = []

    bs = safe_float(snap.get("bs_ratio"))
    tx = safe_int(snap.get("transactions_5m"))
    price = safe_float(snap.get("price_change_5m"))
    volume_mc = safe_float(snap.get("volume_mc_pct"))

    # Base B/S score.
    if bs >= 3.0:
        score += 16
        reasons.append("very strong B/S")
    elif bs >= 2.0:
        score += 14
        reasons.append("strong B/S")
    elif bs >= 1.5:
        score += 11
        reasons.append("positive B/S")
    elif bs >= 1.2:
        score += 8
    elif bs >= 1.0:
        score += 4
    else:
        score += 0
        reasons.append("selling pressure")

    # Activity confirmation.
    if tx >= 200:
        score += 8
        reasons.append("high transaction activity")
    elif tx >= 100:
        score += 6
    elif tx >= 50:
        score += 4
    elif tx >= 20:
        score += 2
    else:
        # Prevent HUHCAT-style ratios from dominating.
        score -= 4
        reasons.append("very low transaction activity")

    # Positive price confirms that buying pressure is actually
    # moving price upward.
    if price >= 10:
        score += 6
        reasons.append("buying pressure moving price")
    elif price > 0:
        score += 4
    elif price <= -10:
        score -= 10
        reasons.append("buying pressure not translating upward")
    elif price < 0:
        score -= 5

    # If there is significant activity but price is deeply negative,
    # explicitly penalize distribution-like behavior.
    if volume_mc >= 20 and price <= -15:
        score -= 8
        reasons.append("high activity with falling price")

    return clamp(score, 0, 30), reasons


# ============================================================
# SCORE COMPONENT 2
# PRICE MOMENTUM
# MAX = 20
# ============================================================

def score_price_momentum(
    snap: Dict[str, Any]
) -> Tuple[float, List[str]]:

    score = 0.0
    reasons = []

    price = safe_float(snap.get("price_change_5m"))

    # Negative movement is actively penalized.
    if price <= -30:
        score = 0
        reasons.append("severe price decline")

    elif price <= -15:
        score = 1
        reasons.append("strong price decline")

    elif price < 0:
        score = 4
        reasons.append("price declining")

    elif price == 0:
        score = 5

    elif price <= 3:
        score = 9

    elif price <= 10:
        score = 15
        reasons.append("healthy upward momentum")

    elif price <= 25:
        score = 18
        reasons.append("strong upward momentum")

    elif price <= 40:
        score = 16
        reasons.append("strong but extended")

    elif price <= 60:
        score = 12
        reasons.append("very extended move")

    else:
        score = 7
        reasons.append("extreme short-term spike")

    return score, reasons


# ============================================================
# SCORE COMPONENT 3
# VOLUME QUALITY
# MAX = 20
#
# Volume is only rewarded strongly when price direction
# and buying pressure agree with it.
# ============================================================

def score_volume_quality(
    snap: Dict[str, Any]
) -> Tuple[float, List[str]]:

    score = 0.0
    reasons = []

    volume_mc = safe_float(snap.get("volume_mc_pct"))
    price = safe_float(snap.get("price_change_5m"))
    bs = safe_float(snap.get("bs_ratio"))
    tx = safe_int(snap.get("transactions_5m"))

    # First determine raw activity.
    if volume_mc >= 50:
        raw = 14
    elif volume_mc >= 30:
        raw = 13
    elif volume_mc >= 15:
        raw = 11
    elif volume_mc >= 7:
        raw = 8
    elif volume_mc >= 3:
        raw = 5
    elif volume_mc >= 1:
        raw = 3
    elif volume_mc > 0:
        raw = 1
    else:
        raw = 0

    # Quality multiplier / adjustment.
    if price > 0 and bs >= 1.5:
        score = raw + 6
        reasons.append("volume aligned with buying")

    elif price > 0 and bs >= 1.0:
        score = raw + 3
        reasons.append("volume partly aligned")

    elif price == 0 and bs >= 1.5:
        score = raw
        reasons.append("volume without price confirmation")

    elif price < 0 and volume_mc >= 20:
        # Major penalty for exactly what V1 demonstrated was bad.
        score = raw - 12
        reasons.append("high volume during price decline")

    elif price < 0:
        score = raw - 5
        reasons.append("volume during price decline")

    else:
        score = raw

    # Tiny transaction count prevents inflated ratios/activity.
    if tx < 20:
        score -= 4
        reasons.append("insufficient transaction activity")

    return clamp(score, 0, 20), reasons


# ============================================================
# SCORE COMPONENT 4
# LIQUIDITY / STRUCTURE
# MAX = 15
# ============================================================

def score_structure(
    snap: Dict[str, Any],
    changes: Dict[str, float]
) -> Tuple[float, List[str]]:

    score = 0.0
    reasons = []

    liquidity_mc = safe_float(
        snap.get("liquidity_mc_pct")
    )

    liquidity = safe_float(
        snap.get("liquidity")
    )

    liq_change = safe_float(
        changes.get("liquidity_change_pct")
    )

    if liquidity_mc >= 20:
        score += 10
        reasons.append("strong liquidity structure")

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

    if liq_change >= 5:
        score += 2
        reasons.append("liquidity increasing")

    elif liq_change < -10:
        score -= 3
        reasons.append("liquidity falling")

    return clamp(score, 0, 15), reasons


# ============================================================
# SCORE COMPONENT 5
# PERSISTENCE
# MAX = 15
#
# This looks across observations, not just one candle.
# ============================================================

def score_persistence(
    snapshots: List[Dict[str, Any]]
) -> Tuple[float, List[str]]:

    if not snapshots:
        return 0.0, []

    aligned = 0
    directional = 0

    recent = snapshots[-3:]

    for snap in recent:

        bs = safe_float(snap.get("bs_ratio"))
        price = safe_float(snap.get("price_change_5m"))
        volume_mc = safe_float(snap.get("volume_mc_pct"))
        tx = safe_int(snap.get("transactions_5m"))

        # A genuinely aligned observation:
        # buying > selling
        # price positive
        # meaningful activity
        if (
            bs >= 1.2
            and price > 0
            and volume_mc >= 1
            and tx >= 20
        ):
            aligned += 1

        if bs >= 1.5 and price > 0:
            directional += 1

    if len(recent) >= 3 and aligned >= 3:
        score = 15
        reasons = ["three consecutive aligned observations"]

    elif aligned >= 2:
        score = 11
        reasons = ["repeated directional activity"]

    elif aligned >= 1:
        score = 6
        reasons = ["one confirmed aligned observation"]

    else:
        score = 0
        reasons = ["no persistent directional confirmation"]

    # Additional bonus for consistent directional pressure.
    if directional >= 3:
        score = min(15, score + 2)

    return score, reasons


# ============================================================
# FULL SCORE
# ============================================================

def calculate_score(
    snap: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
    snapshots: List[Dict[str, Any]]
) -> Dict[str, Any]:

    changes = compare_snapshots(
        previous,
        snap
    )

    directional_score, directional_reasons = (
        score_directional_buying(snap)
    )

    price_score, price_reasons = (
        score_price_momentum(snap)
    )

    volume_score, volume_reasons = (
        score_volume_quality(snap)
    )

    structure_score, structure_reasons = (
        score_structure(
            snap,
            changes
        )
    )

    persistence_score, persistence_reasons = (
        score_persistence(snapshots)
    )

    total = (
        directional_score
        + price_score
        + volume_score
        + structure_score
        + persistence_score
    )

    # ========================================================
    # HARD QUALITY GUARD
    #
    # High activity + major price collapse should never
    # become an ideal runner.
    # ========================================================

    price = safe_float(snap.get("price_change_5m"))
    volume_mc = safe_float(snap.get("volume_mc_pct"))

    hard_warning = False

    if volume_mc >= 20 and price <= -15:
        hard_warning = True

    if hard_warning:
        total = min(total, 45)

    return {
        "total": clamp(total, 0, 100),

        "directional": directional_score,
        "price": price_score,
        "volume": volume_score,
        "structure": structure_score,
        "persistence": persistence_score,

        "changes": changes,

        "reasons": (
            directional_reasons
            + price_reasons
            + volume_reasons
            + structure_reasons
            + persistence_reasons
        ),

        "hard_warning": hard_warning,
    }


# ============================================================
# CLASSIFICATION
# ============================================================

def classify(
    score: float,
    observation_count: int,
    snap: Dict[str, Any],
    scoring: Dict[str, Any]
) -> str:

    if observation_count < MIN_OBSERVATIONS_RUNNER:
        return "WATCH"

    price = safe_float(
        snap.get("price_change_5m")
    )

    bs = safe_float(
        snap.get("bs_ratio")
    )

    volume_mc = safe_float(
        snap.get("volume_mc_pct")
    )

    # Hard rejection for distribution-like activity.
    if scoring.get("hard_warning"):
        return "WATCH"

    # Runner requires actual directional confirmation.
    if score >= RUNNER_SCORE:

        if price > 0 and bs >= 1.2:
            if (
                observation_count >= MIN_OBSERVATIONS_IDEAL
                and score >= IDEAL_SCORE
                and price > 0
                and bs >= 1.5
            ):
                return "IDEAL RUNNER"

            return "RUNNER"

    return "WATCH"


# ============================================================
# ALERT DECISION
# ============================================================

def should_alert(
    token: Dict[str, Any],
    classification: str
) -> bool:

    previous_class = token.get("last_alert_classification")

    if classification not in {
        "RUNNER",
        "IDEAL RUNNER"
    }:
        return False

    # Alert only when entering a runner state
    # or upgrading to ideal.
    if previous_class == classification:
        return False

    return True


# ============================================================
# FORMATTING
# ============================================================

def money(value: float) -> str:

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def format_alert(
    snap: Dict[str, Any],
    scoring: Dict[str, Any],
    classification: str,
    observations: int
) -> str:

    score = scoring["total"]

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

    liquidity = safe_float(
        snap.get("liquidity")
    )

    mc = safe_float(
        snap.get("market_cap")
    )

    direction = scoring["directional"]
    price_score = scoring["price"]
    volume_score = scoring["volume"]
    structure = scoring["structure"]
    persistence = scoring["persistence"]

    reasons = scoring.get("reasons", [])

    reasons_text = ", ".join(
        reasons[:6]
    )

    return (
        f"🚀 {classification}\n\n"
        f"{snap.get('symbol', '?')} | "
        f"{snap.get('name', '?')}\n\n"

        f"MC: {money(mc)}\n"
        f"Liquidity: {money(liquidity)}\n"
        f"5m Price: {price:+.2f}%\n"
        f"B/S: {bs:.2f}\n"
        f"5m Vol/MC: {volume_mc:.2f}%\n"
        f"5m Transactions: {tx}\n\n"

        f"SCORE: {score:.1f}/100\n\n"

        f"Directional: {direction:.1f}/30\n"
        f"Price: {price_score:.1f}/20\n"
        f"Volume Quality: {volume_score:.1f}/20\n"
        f"Structure: {structure:.1f}/15\n"
        f"Persistence: {persistence:.1f}/15\n\n"

        f"Observations: {observations}\n\n"

        f"Why:\n{reasons_text}\n\n"

        f"Pair: {snap.get('pair_address', '')}\n"
        f"DEX: {snap.get('dex_id', '')}\n\n"

        f"⚠️ Experimental scanner signal. "
        f"Not a guarantee of continuation."
    )


# ============================================================
# DISCOVERY
# ============================================================

def discover_feed(
    endpoint: str
) -> List[Dict[str, Any]]:

    data = dex_get(endpoint)

    if not data:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        for key in (
            "data",
            "pairs",
            "tokens",
            "profiles",
            "boosts"
        ):

            value = data.get(key)

            if isinstance(value, list):
                return value

    return []


def extract_token_address(item: Dict[str, Any]) -> str:

    for key in (
        "tokenAddress",
        "token_address",
        "address"
    ):

        value = item.get(key)

        if value:
            return str(value)

    return ""


def discovery_candidates() -> List[Dict[str, Any]]:

    print()
    print("=" * 70)
    print("V1.1 DISCOVERY — NO SEARCH QUERIES")
    print(now_iso())
    print("=" * 70)

    profiles = discover_feed(
        "/token-profiles/latest/v1"
    )

    boosts = discover_feed(
        "/token-boosts/latest/v1"
    )

    top_boosts = discover_feed(
        "/token-boosts/top/v1"
    )

    takeovers = discover_feed(
        "/community-takeovers/latest/v1"
    )

    print(f"Latest profiles: {len(profiles)}")
    print(f"Latest boosts: {len(boosts)}")
    print(f"Top boosts: {len(top_boosts)}")
    print(f"Community takeovers: {len(takeovers)}")

    all_items = (
        profiles
        + boosts
        + top_boosts
        + takeovers
    )

    addresses = []

    seen = set()

    for item in all_items:

        if not isinstance(item, dict):
            continue

        address = extract_token_address(item)

        if not address:
            continue

        if address in seen:
            continue

        seen.add(address)
        addresses.append(address)

    print(
        f"Unique Solana tokens: {len(addresses)}"
    )

    candidates = []

    for address in addresses:

        pairs = token_pairs(address)

        pair = choose_best_pair(pairs)

        if not pair:
            continue

        snap = make_snapshot(
            address,
            pair,
            source="live-feed"
        )

        mc = safe_float(
            snap["market_cap"]
        )

        liquidity = safe_float(
            snap["liquidity"]
        )

        if mc < MIN_MC:
            continue

        if mc > MAX_MC:
            continue

        if liquidity < MIN_LIQUIDITY:
            continue

        candidates.append(snap)

    # Prefer actual activity rather than arbitrary ordering.
    candidates.sort(
        key=lambda x: (
            safe_float(x.get("volume_mc_pct")),
            safe_float(x.get("transactions_5m"))
        ),
        reverse=True
    )

    candidates = candidates[
        :MAX_DISCOVERY_CANDIDATES
    ]

    print(
        f"Broad candidates: {len(candidates)}"
    )

    for i, snap in enumerate(
        candidates,
        start=1
    ):

        print(
            f"  {i:<2} "
            f"{snap.get('symbol', '?'):<12} "
            f"MC {money(snap.get('market_cap', 0)):<10} | "
            f"Liq {money(snap.get('liquidity', 0)):<10} | "
            f"5m Vol {money(snap.get('volume_5m', 0)):<10} | "
            f"Age {snap.get('age_hours', 0):.1f}h"
        )

    return candidates


# ============================================================
# TRACKING
# ============================================================

def token_tracking_record(
    snap: Dict[str, Any]
) -> Dict[str, Any]:

    return {
        "token_address": snap["token_address"],
        "symbol": snap["symbol"],
        "name": snap["name"],

        "started_at": now_iso(),

        "alerted": False,

        "last_alert_score": 0.0,
        "last_alert_classification": None,

        "snapshots": [],

        "max_market_cap": snap["market_cap"],
        "min_market_cap": snap["market_cap"],

        "max_liquidity": snap["liquidity"],
        "min_liquidity": snap["liquidity"],

        "outcome": None,
    }


def ensure_tracking(
    candidates: List[Dict[str, Any]]
) -> None:

    tracking = state.setdefault(
        "tracking",
        {}
    )

    added = 0

    for snap in candidates:

        address = snap["token_address"]

        if address in tracking:
            continue

        tracking[address] = token_tracking_record(
            snap
        )

        added += 1

        history_add(
            {
                "event": "discovery",
                "token_address": address,
                "symbol": snap["symbol"],
                "market_cap": snap["market_cap"],
                "liquidity": snap["liquidity"],
            }
        )

    if added:
        print(
            f"NEW TRACKING: {added}"
        )


# ============================================================
# VALIDATION
# ============================================================

def validate_token(
    address: str,
    token: Dict[str, Any]
) -> None:

    pairs = token_pairs(address)

    pair = choose_best_pair(pairs)

    if not pair:
        return

    snap = make_snapshot(
        address,
        pair,
        source="validation"
    )

    snapshots = token.setdefault(
        "snapshots",
        []
    )

    previous = (
        snapshots[-1]
        if snapshots
        else None
    )

    # Calculate using existing observations.
    scoring = calculate_score(
        snap,
        previous,
        snapshots + [snap]
    )

    observations = len(snapshots) + 1

    classification = classify(
        scoring["total"],
        observations,
        snap,
        scoring
    )

    # Update tracking metrics.
    token["max_market_cap"] = max(
        safe_float(token.get("max_market_cap")),
        safe_float(snap.get("market_cap"))
    )

    token["min_market_cap"] = min(
        safe_float(token.get("min_market_cap")),
        safe_float(snap.get("market_cap"))
    )

    token["max_liquidity"] = max(
        safe_float(token.get("max_liquidity")),
        safe_float(snap.get("liquidity"))
    )

    token["min_liquidity"] = min(
        safe_float(token.get("min_liquidity")),
        safe_float(snap.get("liquidity"))
    )

    snapshots.append(snap)

    # Keep recent observations in active state.
    if len(snapshots) > 50:
        token["snapshots"] = snapshots[-50:]

    token["last_score"] = scoring["total"]
    token["last_classification"] = classification

    print(
        f"VALIDATION | "
        f"{snap['symbol']} | "
        f"{classification} | "
        f"{scoring['total']:.1f}/100 | "
        f"B/S {snap['bs_ratio']:.2f} | "
        f"5m {snap['price_change_5m']:+.2f}% | "
        f"Vol/MC {snap['volume_mc_pct']:.2f}% | "
        f"Dir {scoring['directional']:.1f} | "
        f"VolQ {scoring['volume']:.1f} | "
        f"Pers {scoring['persistence']:.1f}"
    )

    history_add(
        {
            "event": "validation",

            "token_address": address,

            "symbol": snap["symbol"],

            "classification": classification,

            "score": scoring["total"],

            "directional_score":
                scoring["directional"],

            "price_score":
                scoring["price"],

            "volume_score":
                scoring["volume"],

            "structure_score":
                scoring["structure"],

            "persistence_score":
                scoring["persistence"],

            "observations": observations,

            "market_cap":
                snap["market_cap"],

            "liquidity":
                snap["liquidity"],

            "price_change_5m":
                snap["price_change_5m"],

            "volume_mc_pct":
                snap["volume_mc_pct"],

            "bs_ratio":
                snap["bs_ratio"],

            "transactions_5m":
                snap["transactions_5m"],

            "hard_warning":
                scoring["hard_warning"],
        }
    )

    # Alert if entering/upgrading runner status.
    if should_alert(
        token,
        classification
    ):

        alert_text = format_alert(
            snap,
            scoring,
            classification,
            observations
        )

        for chat_id in state.get(
            "subscribers",
            []
        ):

            send_message(
                chat_id,
                alert_text
            )

        token["alerted"] = True

        token["last_alert_score"] = (
            scoring["total"]
        )

        token["last_alert_classification"] = (
            classification
        )

        history_add(
            {
                "event": "alert",

                "token_address": address,

                "symbol": snap["symbol"],

                "classification":
                    classification,

                "score":
                    scoring["total"],

                "market_cap":
                    snap["market_cap"],

                "liquidity":
                    snap["liquidity"],

                "price_change_5m":
                    snap["price_change_5m"],

                "bs_ratio":
                    snap["bs_ratio"],

                "volume_mc_pct":
                    snap["volume_mc_pct"],
            }
        )


# ============================================================
# OUTCOME
# ============================================================

def evaluate_outcome(
    token: Dict[str, Any]
) -> Optional[str]:

    snapshots = token.get(
        "snapshots",
        []
    )

    if not snapshots:
        return None

    started_at = token.get(
        "started_at"
    )

    if not started_at:
        return None

    try:
        started = datetime.fromisoformat(
            started_at
        )

        age_hours = (
            datetime.now(timezone.utc)
            - started
        ).total_seconds() / 3600

    except Exception:
        return None

    if age_hours < TRACKING_HOURS:
        return None

    alert_mc = None
    alert_liq = None

    # Find first runner/ideal alert.
    for snap in snapshots:

        if snap.get("market_cap", 0) <= 0:
            continue

        # The first snapshot is used as research baseline
        # when no actual alert exists.
        if alert_mc is None:
            alert_mc = safe_float(
                snap.get("market_cap")
            )

            alert_liq = safe_float(
                snap.get("liquidity")
            )

    if not alert_mc or not alert_liq:
        return "UNRESOLVED"

    max_mc = safe_float(
        token.get("max_market_cap")
    )

    min_liq = safe_float(
        token.get("min_liquidity")
    )

    mc_multiple = (
        max_mc / alert_mc
        if alert_mc > 0
        else 0
    )

    liq_multiple = (
        min_liq / alert_liq
        if alert_liq > 0
        else 0
    )

    if (
        mc_multiple >= CONTINUATION_MC_MULTIPLE
        and liq_multiple >=
        CONTINUATION_LIQUIDITY_MULTIPLE
    ):
        return "CONTINUATION"

    if (
        mc_multiple < FAILURE_MC_MULTIPLE
        or liq_multiple < FAILURE_LIQUIDITY_MULTIPLE
    ):
        return "FAILURE"

    return "MIXED"


def clean_old_tracking() -> None:

    tracking = state.get(
        "tracking",
        {}
    )

    remove = []

    for address, token in tracking.items():

        if token.get("outcome"):
            continue

        outcome = evaluate_outcome(
            token
        )

        if outcome:

            token["outcome"] = outcome

            history_add(
                {
                    "event": "outcome",

                    "token_address":
                        address,

                    "symbol":
                        token.get("symbol"),

                    "outcome":
                        outcome,

                    "max_market_cap":
                        token.get("max_market_cap"),

                    "min_liquidity":
                        token.get("min_liquidity"),
                }
            )

            print(
                f"OUTCOME | "
                f"{token.get('symbol', '?')} | "
                f"{outcome}"
            )

            remove.append(address)

    for address in remove:
        del tracking[address]


# ============================================================
# VALIDATE ALL
# ============================================================

def validate_all() -> None:

    tracking = state.get(
        "tracking",
        {}
    )

    if not tracking:
        return

    print(
        f"VALIDATING {len(tracking)} candidates..."
    )

    for address in list(tracking.keys()):

        try:
            validate_token(
                address,
                tracking[address]
            )

        except Exception as e:

            print(
                f"VALIDATION ERROR | "
                f"{address} | {e}"
            )

    clean_old_tracking()

    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# MAIN LOOP
# ============================================================

def main() -> None:

    print("=" * 70)
    print("RUNNER BOT V1.1")
    print("DIRECTIONAL ACTIVITY + PERSISTENCE ENGINE")
    print("NO SEARCH QUERIES")
    print("=" * 70)

    print(
        f"MC research range: "
        f"${MIN_MC:,} - ${MAX_MC:,}"
    )

    print(
        f"Minimum liquidity: "
        f"${MIN_LIQUIDITY:,}"
    )

    print(
        f"Watch score: {WATCH_SCORE}"
    )

    print(
        f"Runner score: {RUNNER_SCORE}"
    )

    print(
        f"Ideal runner score: {IDEAL_SCORE}"
    )

    print(
        "Scoring: "
        "Directional 30 | "
        "Price 20 | "
        "Volume Quality 20 | "
        "Structure 15 | "
        "Persistence 15"
    )

    print(
        "Discovery: DexScreener live feeds"
    )

    print(
        "Search queries: NONE"
    )

    if TELEGRAM_TOKEN:
        print(
            "Telegram: connected"
        )
    else:
        print(
            "Telegram: NOT CONNECTED"
        )

    print("=" * 70)

    last_discovery_run = 0
    last_validation_run = 0

    while True:

        try:

            process_telegram_updates()

            now = time.time()

            # ------------------------------------------------
            # DISCOVERY
            # ------------------------------------------------

            if (
                now - last_discovery_run
                >= DISCOVERY_INTERVAL
            ):

                candidates = (
                    discovery_candidates()
                )

                ensure_tracking(
                    candidates
                )

                state["last_discovery"] = now

                save_json(
                    STATE_FILE,
                    state
                )

                last_discovery_run = now

            # ------------------------------------------------
            # VALIDATION
            # ------------------------------------------------

            if (
                now - last_validation_run
                >= VALIDATION_INTERVAL
            ):

                validate_all()

                state["last_validation"] = now

                save_json(
                    STATE_FILE,
                    state
                )

                last_validation_run = now

            time.sleep(
                SCAN_INTERVAL
            )

        except KeyboardInterrupt:

            print(
                "\nBot stopped."
            )

            save_json(
                STATE_FILE,
                state
            )

            break

        except Exception as e:

            print(
                f"MAIN LOOP ERROR: {e}"
            )

            time.sleep(
                SCAN_INTERVAL
            )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
