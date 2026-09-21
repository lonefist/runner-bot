import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V6.0
# MARKET EVOLUTION ENGINE
#
# NO LORE
# NO X / TWITTER
# NO GITHUB RESEARCH
# NO AI RESEARCH
#
# IDEA:
# Discover broadly -> observe -> build history ->
# detect improvement -> signal only when market confirms
# ============================================================

BOT_VERSION = "V6.0-MARKET-EVOLUTION"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

CHAIN = "solana"

# ------------------------------------------------------------
# MARKET RANGE
# ------------------------------------------------------------

MIN_MC = 20_000
MAX_SIGNAL_MC = 150_000

# Preferred early zone
EARLY_MAX_MC = 80_000

MIN_LIQUIDITY = 5_000

# ------------------------------------------------------------
# SIGNAL CONDITIONS
# ------------------------------------------------------------

# Current market
MIN_CURRENT_BS = 1.35
MIN_PREVIOUS_BS = 1.20

MIN_PRICE_CHANGE = 2.0
MAX_PRICE_CHANGE = 50.0

MIN_VOLUME_MC = 3.5

MIN_TX = 50

# ------------------------------------------------------------
# MOMENTUM / EVOLUTION
# ------------------------------------------------------------

MIN_OBSERVATIONS = 3

# Number of consecutive/improving observations needed
MIN_IMPROVING_STEPS = 2

# How many of the major metrics must improve
MIN_IMPROVING_METRICS = 2

# ------------------------------------------------------------
# RISK FILTERS
# ------------------------------------------------------------

MAX_DECAY_SCORE = 2
MAX_WEAKENING_SCORE = 2

# ------------------------------------------------------------
# SCANNER
# ------------------------------------------------------------

SCAN_INTERVAL = int(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)

DISCOVERY_INTERVAL = int(
    os.getenv("DISCOVERY_INTERVAL_SECONDS", "300")
)

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

HEATING_ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "true").lower()
    == "true"
)

HTTP_TIMEOUT = 15

# Keep history for this many observations.
MAX_HISTORY = 12

# Avoid repeating the same alert too often.
ALERT_COOLDOWN_SECONDS = 30 * 60

# ------------------------------------------------------------
# STORAGE
# ------------------------------------------------------------

STATE_FILE = "runner_state.json"


# ============================================================
# GLOBAL STATE
# ============================================================

STATE: Dict[str, Any] = {
    "subscribers": [],
    "tokens": {},
    "last_discovery": 0,
    "last_update_id": 0,
}


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts() -> int:
    return int(time.time())


def utc_now() -> str:
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


def fmt_money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def fmt_ratio(value: float) -> str:
    if value >= 999:
        return "∞"
    return f"{value:.2f}"


def load_state() -> None:
    global STATE

    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        if isinstance(loaded, dict):
            STATE.update(loaded)

    except Exception as e:
        print(f"[STATE] Load error: {e}")


def save_state() -> None:
    try:
        tmp = STATE_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE, f, indent=2)

        os.replace(tmp, STATE_FILE)

    except Exception as e:
        print(f"[STATE] Save error: {e}")


# ============================================================
# HTTP
# ============================================================

def http_get_json(
    url: str,
    timeout: int = HTTP_TIMEOUT
) -> Optional[Any]:

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "RunnerBot/6.0 "
                    "(market scanner)"
                ),
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=timeout
        ) as response:

            body = response.read().decode(
                "utf-8",
                errors="replace"
            )

            return json.loads(body)

    except urllib.error.HTTPError as e:

        print(
            f"[HTTP ERROR] {url} -> "
            f"HTTP {e.code}"
        )

    except Exception as e:

        print(
            f"[HTTP ERROR] {url} -> {e}"
        )

    return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(
    method: str,
    params: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:

    if not TELEGRAM_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_TOKEN}/{method}"
    )

    try:

        encoded = urllib.parse.urlencode(
            params or {}
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=encoded,
            method="POST",
            headers={
                "User-Agent": "RunnerBot/6.0"
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=HTTP_TIMEOUT
        ) as response:

            body = response.read().decode(
                "utf-8",
                errors="replace"
            )

            return json.loads(body)

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] "
            f"{method}: {e}"
        )

        return None


def telegram_send(
    chat_id: int,
    text: str
) -> bool:

    result = telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    )

    return bool(
        result and result.get("ok")
    )


def telegram_startup_test() -> bool:

    print("[STARTUP] Testing Telegram...")

    result = telegram_api("getMe")

    if not result or not result.get("ok"):
        print("[TELEGRAM] Connection failed")
        return False

    username = (
        result
        .get("result", {})
        .get("username", "unknown")
    )

    print(
        f"[TELEGRAM] Connected @{username}"
    )

    return True


def telegram_poll() -> None:

    if not TELEGRAM_TOKEN:
        return

    offset = STATE.get(
        "last_update_id",
        0
    )

    result = telegram_api(
        "getUpdates",
        {
            "offset": offset + 1,
            "timeout": 1,
            "allowed_updates": (
                '["message"]'
            ),
        },
    )

    if not result or not result.get("ok"):
        return

    updates = result.get(
        "result",
        []
    )

    for update in updates:

        update_id = safe_int(
            update.get("update_id")
        )

        STATE["last_update_id"] = (
            update_id
        )

        message = update.get(
            "message",
            {}
        )

        chat = message.get(
            "chat",
            {}
        )

        chat_id = chat.get("id")

        text = (
            message
            .get("text", "")
            .strip()
        )

        if not chat_id:
            continue

        if text == "/start":

            if chat_id not in STATE[
                "subscribers"
            ]:

                STATE[
                    "subscribers"
                ].append(chat_id)

            telegram_send(
                chat_id,
                (
                    "🚀 Runner Bot V6 is online.\n\n"
                    "Market Evolution Engine active.\n"
                    "No lore/X filtering.\n"
                    "The bot watches market improvement "
                    "before signalling."
                ),
            )

            print(
                f"[TELEGRAM] /start from "
                f"{chat_id}"
            )

        elif text == "/status":

            tokens = STATE.get(
                "tokens",
                {}
            )

            telegram_send(
                chat_id,
                (
                    "RUNNER BOT V6 STATUS\n\n"
                    f"Tracked tokens: {len(tokens)}\n"
                    f"Subscribers: "
                    f"{len(STATE['subscribers'])}\n"
                    f"Scanner: ACTIVE"
                ),
            )

        elif text == "/clear":

            STATE["tokens"] = {}

            telegram_send(
                chat_id,
                "Tracked-token history cleared."
            )

            save_state()

    save_state()


# ============================================================
# DEXSCREENER DISCOVERY
# ============================================================

DISCOVERY_ENDPOINTS = [
    "/token-profiles/latest/v1",
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
    "/community-takeovers/latest/v1",
]


def discover_token_addresses() -> List[str]:

    addresses = set()

    for endpoint in DISCOVERY_ENDPOINTS:

        url = DEX_BASE + endpoint

        data = http_get_json(url)

        if not isinstance(data, list):
            continue

        for item in data:

            if not isinstance(item, dict):
                continue

            if item.get("chainId") != CHAIN:
                continue

            address = item.get(
                "tokenAddress"
            )

            if address:
                addresses.add(address)

    return list(addresses)


# ============================================================
# DEX TOKEN LOOKUP
# ============================================================

def get_token_pairs(
    addresses: List[str]
) -> List[Dict[str, Any]]:

    all_pairs = []

    # DexScreener supports batches.
    batch_size = 30

    for i in range(
        0,
        len(addresses),
        batch_size
    ):

        batch = addresses[
            i:i + batch_size
        ]

        encoded = ",".join(batch)

        url = (
            f"{DEX_BASE}/latest/dex/tokens/"
            f"{urllib.parse.quote(encoded)}"
        )

        data = http_get_json(url)

        if not isinstance(data, dict):
            continue

        pairs = data.get(
            "pairs",
            []
        )

        if isinstance(pairs, list):

            for pair in pairs:

                if (
                    isinstance(pair, dict)
                    and pair.get("chainId")
                    == CHAIN
                ):
                    all_pairs.append(pair)

    return all_pairs


# ============================================================
# PAIR SELECTION
# ============================================================

def select_best_pair(
    pairs: List[Dict[str, Any]],
    token_address: str
) -> Optional[Dict[str, Any]]:

    matching = []

    for pair in pairs:

        base = pair.get(
            "baseToken",
            {}
        )

        if (
            base.get("address")
            == token_address
        ):

            matching.append(pair)

    if not matching:
        return None

    matching.sort(
        key=lambda x: safe_float(
            x.get("liquidity", {})
             .get("usd")
        ),
        reverse=True,
    )

    return matching[0]


# ============================================================
# SNAPSHOT
# ============================================================

def build_snapshot(
    pair: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    base = pair.get(
        "baseToken",
        {}
    )

    token_address = base.get(
        "address"
    )

    if not token_address:
        return None

    liquidity = safe_float(
        pair.get("liquidity", {})
            .get("usd")
    )

    market_cap = safe_float(
        pair.get("marketCap")
    )

    if market_cap <= 0:

        market_cap = safe_float(
            pair.get("fdv")
        )

    volume_5m = safe_float(
        pair.get("volume", {})
            .get("m5")
    )

    txns_5m = pair.get(
        "txns", {}
    ).get("m5", {})

    buys = safe_int(
        txns_5m.get("buys")
    )

    sells = safe_int(
        txns_5m.get("sells")
    )

    if sells == 0:

        bs_ratio = (
            999.0
            if buys > 0
            else 0.0
        )

    else:

        bs_ratio = (
            buys / sells
        )

    volume_mc = (
        volume_5m / market_cap * 100
        if market_cap > 0
        else 0
    )

    return {
        "timestamp": now_ts(),
        "time": utc_now(),

        "address": token_address,

        "symbol": base.get(
            "symbol",
            "UNKNOWN"
        ),

        "name": base.get(
            "name",
            "UNKNOWN"
        ),

        "market_cap": market_cap,

        "liquidity": liquidity,

        "volume_5m": volume_5m,

        "volume_mc": volume_mc,

        "buys_5m": buys,

        "sells_5m": sells,

        "bs_ratio": bs_ratio,

        "tx_5m": buys + sells,

        "price_change_5m": safe_float(
            pair.get("priceChange", {})
                .get("m5")
        ),

        "price_usd": safe_float(
            pair.get("priceUsd")
        ),

        "pair_address": pair.get(
            "pairAddress",
            ""
        ),

        "dex": pair.get(
            "dexId",
            ""
        ),

        "url": pair.get(
            "url",
            ""
        ),

        "pair_created_at": safe_int(
            pair.get("pairCreatedAt")
        ),
    }


# ============================================================
# HISTORY
# ============================================================

def get_token_state(
    address: str
) -> Dict[str, Any]:

    tokens = STATE.setdefault(
        "tokens",
        {}
    )

    if address not in tokens:

        tokens[address] = {
            "first_seen": now_ts(),
            "last_seen": now_ts(),
            "history": [],
            "alerts": [],
            "last_alert": 0,
            "discovered": True,
        }

    return tokens[address]


def add_observation(
    snapshot: Dict[str, Any]
) -> Dict[str, Any]:

    address = snapshot["address"]

    state = get_token_state(
        address
    )

    history = state.setdefault(
        "history",
        []
    )

    history.append(snapshot)

    if len(history) > MAX_HISTORY:

        del history[
            :-MAX_HISTORY
        ]

    state["last_seen"] = now_ts()

    return state


# ============================================================
# EVOLUTION ENGINE
# ============================================================

def metric_improvement(
    current: Dict[str, Any],
    previous: Dict[str, Any]
) -> Dict[str, bool]:

    return {
        "bs": (
            current["bs_ratio"]
            > previous["bs_ratio"]
        ),

        "volume_mc": (
            current["volume_mc"]
            > previous["volume_mc"]
        ),

        "transactions": (
            current["tx_5m"]
            > previous["tx_5m"]
        ),

        "price": (
            current["price_change_5m"]
            > previous["price_change_5m"]
        ),

        "liquidity": (
            current["liquidity"]
            >= previous["liquidity"]
        ),

        "market_cap": (
            current["market_cap"]
            > previous["market_cap"]
        ),
    }


def calculate_weakening(
    current: Dict[str, Any],
    previous: Dict[str, Any]
) -> int:

    score = 0

    if (
        current["bs_ratio"]
        < previous["bs_ratio"]
    ):
        score += 1

    if (
        current["price_change_5m"]
        < previous["price_change_5m"]
    ):
        score += 1

    if (
        current["volume_mc"]
        < previous["volume_mc"]
    ):
        score += 1

    if (
        current["tx_5m"]
        < previous["tx_5m"]
    ):
        score += 1

    if (
        current["liquidity"]
        < previous["liquidity"] * 0.95
    ):
        score += 1

    return score


def calculate_decay(
    current: Dict[str, Any]
) -> int:

    score = 0

    if current["price_change_5m"] <= 0:
        score += 1

    if current["bs_ratio"] < 1.0:
        score += 1

    if current["tx_5m"] < 20:
        score += 1

    if (
        current["volume_mc"] >= 20
        and current["price_change_5m"] < 0
    ):
        score += 1

    return score


def calculate_market_score(
    snapshot: Dict[str, Any],
    history: List[Dict[str, Any]]
) -> int:

    mc = snapshot["market_cap"]
    price = snapshot["price_change_5m"]
    bs = snapshot["bs_ratio"]
    tx = snapshot["tx_5m"]
    vol_mc = snapshot["volume_mc"]
    liq = snapshot["liquidity"]

    score = 0

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    if 20_000 <= mc < 50_000:
        score += 20

    elif 50_000 <= mc < 80_000:
        score += 17

    elif 80_000 <= mc < 120_000:
        score += 12

    elif 120_000 <= mc <= 150_000:
        score += 7

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    if price >= 20:
        score += 20

    elif price >= 10:
        score += 17

    elif price >= 5:
        score += 14

    elif price > 0:
        score += 8

    # --------------------------------------------------------
    # BUY / SELL
    # --------------------------------------------------------

    if bs >= 2:
        score += 20

    elif bs >= 1.5:
        score += 17

    elif bs >= 1.2:
        score += 13

    elif bs >= 1:
        score += 7

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    if tx >= 300:
        score += 15

    elif tx >= 200:
        score += 13

    elif tx >= 100:
        score += 10

    elif tx >= 50:
        score += 6

    elif tx >= 20:
        score += 3

    # --------------------------------------------------------
    # VOLUME / MC
    # --------------------------------------------------------

    if vol_mc >= 40:
        score += 15

    elif vol_mc >= 25:
        score += 13

    elif vol_mc >= 15:
        score += 10

    elif vol_mc >= 10:
        score += 7

    elif vol_mc >= 5:
        score += 3

    # --------------------------------------------------------
    # LIQUIDITY / MC
    # --------------------------------------------------------

    liq_mc = (
        liq / mc * 100
        if mc > 0
        else 0
    )

    if liq_mc >= 40:
        score += 10

    elif liq_mc >= 25:
        score += 8

    elif liq_mc >= 15:
        score += 6

    elif liq_mc >= 10:
        score += 4

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    if len(history) >= 3:
        score += 5

    elif len(history) >= 2:
        score += 2

    # --------------------------------------------------------
    # PENALTIES
    # --------------------------------------------------------

    decay = calculate_decay(
        snapshot
    )

    weakening = 0

    if len(history) >= 2:

        weakening = calculate_weakening(
            snapshot,
            history[-2]
        )

    score -= decay * 8
    score -= weakening * 4

    return int(
        clamp(score, 0, 100)
    )


# ============================================================
# IMPROVEMENT ANALYSIS
# ============================================================

def analyze_evolution(
    history: List[Dict[str, Any]]
) -> Dict[str, Any]:

    result = {
        "improving_steps": 0,
        "improving_metrics": 0,
        "current_step_improving": False,
        "trend": "UNKNOWN",
        "weakening": 0,
        "decay": 0,
    }

    if not history:
        return result

    current = history[-1]

    result["decay"] = calculate_decay(
        current
    )

    if len(history) < 2:
        result["trend"] = "NEW"
        return result

    # --------------------------------------------------------
    # Consecutive improvement
    # --------------------------------------------------------

    improving_steps = 0

    for i in range(
        len(history) - 1,
        0,
        -1
    ):

        cur = history[i]
        prev = history[i - 1]

        changes = metric_improvement(
            cur,
            prev
        )

        metric_count = sum(
            1 for value in changes.values()
            if value
        )

        if (
            metric_count
            >= MIN_IMPROVING_METRICS
        ):

            improving_steps += 1

        else:
            break

    result[
        "improving_steps"
    ] = improving_steps

    # --------------------------------------------------------
    # Current step
    # --------------------------------------------------------

    previous = history[-2]

    changes = metric_improvement(
        current,
        previous
    )

    result[
        "improving_metrics"
    ] = sum(
        1 for value in changes.values()
        if value
    )

    result[
        "current_step_improving"
    ] = (
        result["improving_metrics"]
        >= MIN_IMPROVING_METRICS
    )

    result["weakening"] = (
        calculate_weakening(
            current,
            previous
        )
    )

    # --------------------------------------------------------
    # Trend
    # --------------------------------------------------------

    if (
        result["improving_steps"]
        >= MIN_IMPROVING_STEPS
    ):

        result["trend"] = "ACCELERATING"

    elif result[
        "current_step_improving"
    ]:

        result["trend"] = "IMPROVING"

    elif result["weakening"] >= 3:

        result["trend"] = "WEAKENING"

    else:

        result["trend"] = "MIXED"

    return result


# ============================================================
# SIGNAL DECISION
# ============================================================

def qualifies_market(
    snapshot: Dict[str, Any]
) -> bool:

    return (
        MIN_MC
        <= snapshot["market_cap"]
        <= MAX_SIGNAL_MC
        and
        snapshot["liquidity"]
        >= MIN_LIQUIDITY
    )


def qualifies_early(
    snapshot: Dict[str, Any],
    evolution: Dict[str, Any],
    history: List[Dict[str, Any]]
) -> bool:

    if not qualifies_market(snapshot):
        return False

    if snapshot["market_cap"] > EARLY_MAX_MC:
        return False

    if len(history) < MIN_OBSERVATIONS:
        return False

    if snapshot["bs_ratio"] < MIN_CURRENT_BS:
        return False

    if snapshot["price_change_5m"] < MIN_PRICE_CHANGE:
        return False

    if (
        snapshot["price_change_5m"]
        > MAX_PRICE_CHANGE
    ):
        return False

    if snapshot["volume_mc"] < MIN_VOLUME_MC:
        return False

    if snapshot["tx_5m"] < MIN_TX:
        return False

    if (
        evolution["improving_steps"]
        < MIN_IMPROVING_STEPS
    ):
        return False

    if (
        evolution["improving_metrics"]
        < MIN_IMPROVING_METRICS
    ):
        return False

    if (
        evolution["weakening"]
        > MAX_WEAKENING_SCORE
    ):
        return False

    if (
        evolution["decay"]
        > MAX_DECAY_SCORE
    ):
        return False

    # Previous B/S must show that buying pressure
    # wasn't created from one isolated jump.
    previous = history[-2]

    if previous["bs_ratio"] < MIN_PREVIOUS_BS:
        return False

    return True


def qualifies_runner(
    snapshot: Dict[str, Any],
    evolution: Dict[str, Any],
    history: List[Dict[str, Any]],
    market_score: int
) -> bool:

    if not qualifies_market(snapshot):
        return False

    if len(history) < 3:
        return False

    if market_score < 72:
        return False

    if snapshot["price_change_5m"] <= 0:
        return False

    if snapshot["bs_ratio"] < 1.2:
        return False

    if evolution["decay"] > 2:
        return False

    if evolution["weakening"] > 2:
        return False

    return True


# ============================================================
# ALERT CONTROL
# ============================================================

def alert_already_sent(
    address: str,
    alert_type: str
) -> bool:

    state = get_token_state(
        address
    )

    alerts = state.get(
        "alerts",
        []
    )

    return alert_type in alerts


def can_alert(
    address: str
) -> bool:

    state = get_token_state(
        address
    )

    last_alert = safe_int(
        state.get("last_alert")
    )

    return (
        now_ts() - last_alert
        >= ALERT_COOLDOWN_SECONDS
    )


def mark_alert(
    address: str,
    alert_type: str
) -> None:

    state = get_token_state(
        address
    )

    alerts = state.setdefault(
        "alerts",
        []
    )

    if alert_type not in alerts:
        alerts.append(alert_type)

    state["last_alert"] = now_ts()


# ============================================================
# TELEGRAM ALERT
# ============================================================

def build_alert(
    snapshot: Dict[str, Any],
    evolution: Dict[str, Any],
    market_score: int,
    alert_type: str,
    history: List[Dict[str, Any]]
) -> str:

    previous = (
        history[-2]
        if len(history) >= 2
        else None
    )

    bs_previous = (
        previous["bs_ratio"]
        if previous
        else 0
    )

    vol_previous = (
        previous["volume_mc"]
        if previous
        else 0
    )

    return (
        f"🚀 {alert_type}\n\n"

        f"{snapshot['name']} "
        f"({snapshot['symbol']})\n\n"

        f"💰 MC: "
        f"{fmt_money(snapshot['market_cap'])}\n"

        f"💧 Liquidity: "
        f"{fmt_money(snapshot['liquidity'])}\n"

        f"📈 5m Price: "
        f"{snapshot['price_change_5m']:+.2f}%\n"

        f"🟢 B/S: "
        f"{fmt_ratio(snapshot['bs_ratio'])}\n"

        f"   Previous: "
        f"{fmt_ratio(bs_previous)}\n"

        f"📊 5m Vol/MC: "
        f"{snapshot['volume_mc']:.2f}%\n"

        f"   Previous: "
        f"{vol_previous:.2f}%\n"

        f"🔄 5m TX: "
        f"{snapshot['tx_5m']}\n"

        f"📈 Market Score: "
        f"{market_score}/100\n"

        f"🔥 Trend: "
        f"{evolution['trend']}\n"

        f"⬆️ Improving steps: "
        f"{evolution['improving_steps']}\n"

        f"📚 Observations: "
        f"{len(history)}\n\n"

        f"DEX: {snapshot['dex']}\n\n"

        f"CA:\n"
        f"{snapshot['address']}\n\n"

        f"Chart:\n"
        f"{snapshot['url']}"
    )


def send_alert_to_all(
    text: str
) -> None:

    subscribers = list(
        STATE.get(
            "subscribers",
            []
        )
    )

    for chat_id in subscribers:

        telegram_send(
            chat_id,
            text
        )


# ============================================================
# TOKEN PROCESSING
# ============================================================

def process_snapshot(
    snapshot: Dict[str, Any]
) -> None:

    address = snapshot[
        "address"
    ]

    state = add_observation(
        snapshot
    )

    history = state[
        "history"
    ]

    evolution = analyze_evolution(
        history
    )

    market_score = (
        calculate_market_score(
            snapshot,
            history
        )
    )

    early = qualifies_early(
        snapshot,
        evolution,
        history
    )

    runner = qualifies_runner(
        snapshot,
        evolution,
        history,
        market_score
    )

    # --------------------------------------------------------
    # Console output
    # --------------------------------------------------------

    print(
        f"[TOKEN] "
        f"{snapshot['symbol']} "
        f"| MC {fmt_money(snapshot['market_cap'])} "
        f"| LIQ {fmt_money(snapshot['liquidity'])} "
        f"| BS {fmt_ratio(snapshot['bs_ratio'])} "
        f"| VOL/MC {snapshot['volume_mc']:.2f}% "
        f"| TX {snapshot['tx_5m']} "
        f"| P {snapshot['price_change_5m']:+.2f}% "
        f"| SCORE {market_score} "
        f"| TREND {evolution['trend']} "
        f"| OBS {len(history)}"
    )

    # --------------------------------------------------------
    # EARLY MARKET EVOLUTION SIGNAL
    # --------------------------------------------------------

    if (
        early
        and not alert_already_sent(
            address,
            "EARLY"
        )
        and can_alert(address)
    ):

        print(
            f"[SIGNAL] EARLY "
            f"{snapshot['symbol']}"
        )

        alert = build_alert(
            snapshot,
            evolution,
            market_score,
            "EARLY MARKET EVOLUTION",
            history
        )

        send_alert_to_all(
            alert
        )

        mark_alert(
            address,
            "EARLY"
        )

    # --------------------------------------------------------
    # RUNNER SIGNAL
    # --------------------------------------------------------

    elif (
        runner
        and not alert_already_sent(
            address,
            "RUNNER"
        )
        and can_alert(address)
    ):

        print(
            f"[SIGNAL] RUNNER "
            f"{snapshot['symbol']}"
        )

        alert = build_alert(
            snapshot,
            evolution,
            market_score,
            "RUNNER CONFIRMED",
            history
        )

        send_alert_to_all(
            alert
        )

        mark_alert(
            address,
            "RUNNER"
        )


# ============================================================
# DISCOVERY CYCLE
# ============================================================

def discovery_cycle() -> None:

    print(
        "\n[DISCOVERY] Scanning..."
    )

    addresses = (
        discover_token_addresses()
    )

    print(
        f"[DISCOVERY] Found "
        f"{len(addresses)} Solana token addresses"
    )

    if not addresses:
        return

    pairs = get_token_pairs(
        addresses
    )

    print(
        f"[DISCOVERY] Received "
        f"{len(pairs)} Solana pairs"
    )

    # Group pairs by base token.
    grouped: Dict[
        str,
        List[Dict[str, Any]]
    ] = {}

    for pair in pairs:

        base = pair.get(
            "baseToken",
            {}
        )

        address = base.get(
            "address"
        )

        if not address:
            continue

        grouped.setdefault(
            address,
            []
        ).append(pair)

    processed = 0

    for address, token_pairs in (
        grouped.items()
    ):

        pair = select_best_pair(
            token_pairs,
            address
        )

        if not pair:
            continue

        snapshot = build_snapshot(
            pair
        )

        if not snapshot:
            continue

        # Broad intake:
        # we do NOT require the token to
        # qualify before tracking it.
        process_snapshot(
            snapshot
        )

        processed += 1

    print(
        f"[DISCOVERY] Processed "
        f"{processed} tokens"
    )

    save_state()


# ============================================================
# TRACKING CYCLE
# ============================================================

def tracked_token_addresses() -> List[str]:

    addresses = []

    for address, state in STATE.get(
        "tokens",
        {}
    ).items():

        history = state.get(
            "history",
            []
        )

        if not history:
            continue

        # Track recently active tokens.
        last_seen = safe_int(
            state.get("last_seen")
        )

        if (
            now_ts() - last_seen
            <= 6 * 60 * 60
        ):

            addresses.append(
                address
            )

    return addresses


def tracking_cycle() -> None:

    addresses = (
        tracked_token_addresses()
    )

    if not addresses:
        return

    # Same verified batch endpoint.
    pairs = get_token_pairs(
        addresses
    )

    grouped: Dict[
        str,
        List[Dict[str, Any]]
    ] = {}

    for pair in pairs:

        base = pair.get(
            "baseToken",
            {}
        )

        address = base.get(
            "address"
        )

        if address:
            grouped.setdefault(
                address,
                []
            ).append(pair)

    for address in addresses:

        token_pairs = grouped.get(
            address,
            []
        )

        if not token_pairs:
            continue

        pair = select_best_pair(
            token_pairs,
            address
        )

        if not pair:
            continue

        snapshot = build_snapshot(
            pair
        )

        if not snapshot:
            continue

        process_snapshot(
            snapshot
        )

    save_state()


# ============================================================
# CLEANUP
# ============================================================

def cleanup_old_tokens() -> None:

    tokens = STATE.get(
        "tokens",
        {}
    )

    cutoff = (
        now_ts()
        - 12 * 60 * 60
    )

    remove = []

    for address, state in tokens.items():

        last_seen = safe_int(
            state.get("last_seen")
        )

        if (
            last_seen > 0
            and last_seen < cutoff
        ):

            history = state.get(
                "history",
                []
            )

            # Keep tokens that generated
            # an alert longer.
            if state.get("alerts"):
                continue

            if len(history) < 2:
                remove.append(address)

    for address in remove:

        tokens.pop(
            address,
            None
        )


# ============================================================
# STARTUP
# ============================================================

def startup() -> None:

    print("=" * 68)
    print(
        f"RUNNER BOT {BOT_VERSION}"
    )
    print("=" * 68)

    print(
        f"CHAIN: {CHAIN}"
    )

    print(
        f"MARKET RANGE: "
        f"${MIN_MC:,} - "
        f"${MAX_SIGNAL_MC:,}"
    )

    print(
        f"EARLY RANGE: "
        f"${MIN_MC:,} - "
        f"${EARLY_MAX_MC:,}"
    )

    print(
        f"MIN LIQUIDITY: "
        f"${MIN_LIQUIDITY:,}"
    )

    print(
        f"CURRENT B/S: "
        f"{MIN_CURRENT_BS}"
    )

    print(
        f"PREVIOUS B/S: "
        f"{MIN_PREVIOUS_BS}"
    )

    print(
        f"5m PRICE: "
        f"+{MIN_PRICE_CHANGE}% "
        f"to +{MAX_PRICE_CHANGE}%"
    )

    print(
        f"VOLUME/MC: "
        f"{MIN_VOLUME_MC}%+"
    )

    print(
        f"5m TX: "
        f"{MIN_TX}+"
    )

    print(
        f"OBSERVATIONS REQUIRED: "
        f"{MIN_OBSERVATIONS}"
    )

    print(
        f"IMPROVING STEPS REQUIRED: "
        f"{MIN_IMPROVING_STEPS}"
    )

    print(
        f"IMPROVING METRICS REQUIRED: "
        f"{MIN_IMPROVING_METRICS}"
    )

    print(
        f"SCAN INTERVAL: "
        f"{SCAN_INTERVAL}s"
    )

    print(
        f"DISCOVERY INTERVAL: "
        f"{DISCOVERY_INTERVAL}s"
    )

    print(
        f"TELEGRAM TOKEN: "
        f"{'CONFIGURED' if TELEGRAM_TOKEN else 'NOT CONFIGURED'}"
    )

    print(
        "LORE ENGINE: DISABLED"
    )

    print(
        "X / TWITTER: NOT USED"
    )

    print(
        "GITHUB RESEARCH: NOT USED"
    )

    print(
        "AI RESEARCH: NOT USED"
    )

    print("=" * 68)

    telegram_startup_test()

    print(
        "[STARTUP] Testing DexScreener..."
    )

    addresses = (
        discover_token_addresses()
    )

    if addresses:
        print(
            "[DEXSCREENER] Connected"
        )
        print(
            f"[DEXSCREENER] Initial "
            f"candidates: {len(addresses)}"
        )

    else:
        print(
            "[DEXSCREENER] No candidates "
            "returned"
        )

    print(
        "[STARTUP] Scanner running."
    )


# ============================================================
# MAIN LOOP
# ============================================================

def main() -> None:

    load_state()

    startup()

    last_discovery = 0
    last_cleanup = 0

    while True:

        try:

            telegram_poll()

            current = now_ts()

            # ------------------------------------------------
            # Full discovery
            # ------------------------------------------------

            if (
                current - last_discovery
                >= DISCOVERY_INTERVAL
            ):

                discovery_cycle()

                last_discovery = current

            else:

                # ------------------------------------------------
                # Existing-token monitoring
                # ------------------------------------------------

                tracking_cycle()

            # ------------------------------------------------
            # Cleanup
            # ------------------------------------------------

            if (
                current - last_cleanup
                >= 15 * 60
            ):

                cleanup_old_tokens()

                save_state()

                last_cleanup = current

            time.sleep(
                SCAN_INTERVAL
            )

        except KeyboardInterrupt:

            print(
                "\n[STOP] Runner Bot stopped."
            )

            save_state()

            break

        except Exception as e:

            print(
                f"[MAIN ERROR] {e}"
            )

            time.sleep(5)


if __name__ == "__main__":
    main()
