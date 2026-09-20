import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V5.1
# DEXSCREENER RECOVERY RUNNER
#
# ARCHITECTURE:
# Discovery -> Basic qualification -> Tracking -> Observation
# -> Recovery detection -> Telegram alert
#
# IMPORTANT:
# Initial discovery does NOT require recovery.
# Recovery is detected from subsequent observations.
# ============================================================

BOT_VERSION = "V5.1-DEXSCREENER-RECOVERY"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"


# ============================================================
# CONFIGURATION
# ============================================================

CHAIN = "solana"

# -------------------------
# Age
# -------------------------

MIN_AGE_HOURS = 3.0

# No maximum age.
MAX_AGE_HOURS = None


# -------------------------
# Market cap
# -------------------------

MIN_MC = 30_000
MAX_MC = 400_000


# -------------------------
# Liquidity
# -------------------------

MIN_LIQUIDITY = 25_000
PREFERRED_LIQUIDITY = 40_000


# -------------------------
# Recovery measurements
# -------------------------

# Initial tracking filter.
#
# IMPORTANT:
# This is deliberately lower than the old
# "must already be recovering" logic.
#
# A token can enter tracking before its recovery
# becomes obvious.
MIN_INITIAL_VOLUME_MC_RATIO = 0.01


# Recovery requirement.
#
# Once tracked, we look for meaningful volume.
MIN_RECOVERY_VOLUME_MC_RATIO = 0.05


# Buy/sell pressure.
MIN_BUY_SELL_RATIO = 1.20
STRONG_BUY_SELL_RATIO = 1.50


# Price behavior.
MIN_5M_PRICE_CHANGE = 0.0
MAX_5M_PRICE_CHANGE = 50.0


# Volume expansion.
#
# m5 volume / h1 volume >= 10%
# indicates that a meaningful portion of recent
# hourly volume is happening in the latest 5 minutes.
VOLUME_EXPANSION_RATIO = 0.10


# Snapshot-to-snapshot improvement.
SNAPSHOT_VOLUME_INCREASE = 0.10
SNAPSHOT_BUY_RATIO_INCREASE = 0.10


# -------------------------
# Tracking
# -------------------------

OBSERVATION_WINDOW_HOURS = 12

MAX_TRACKED_TOKENS = 150

MAX_VALIDATIONS_PER_CYCLE = 20


# -------------------------
# Timing
# -------------------------

DISCOVERY_INTERVAL_SECONDS = 300
VALIDATION_INTERVAL_SECONDS = 300

SCAN_INTERVAL_SECONDS = int(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)


# -------------------------
# Telegram
# -------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

HEATING_ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "true").lower()
    == "true"
)


# ============================================================
# STATE
# ============================================================

STATE_FILE = "runner_state.json"


DEFAULT_STATE = {
    "subscribers": [],
    "tracking": {},
    "last_discovery": 0,
    "stats": {
        "discovered": 0,
        "tracked": 0,
        "alerts": 0,
        "scans": 0,
    },
}


def load_state() -> Dict[str, Any]:

    if not os.path.exists(STATE_FILE):
        return DEFAULT_STATE.copy()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            return DEFAULT_STATE.copy()

        state.setdefault("subscribers", [])
        state.setdefault("tracking", {})
        state.setdefault("last_discovery", 0)
        state.setdefault("stats", {})

        for key in DEFAULT_STATE["stats"]:
            state["stats"].setdefault(
                key,
                DEFAULT_STATE["stats"][key]
            )

        return state

    except Exception as e:

        print(f"State load error: {e}")

        return DEFAULT_STATE.copy()


def save_state(state: Dict[str, Any]):

    temp_file = STATE_FILE + ".tmp"

    try:

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(temp_file, STATE_FILE)

    except Exception as e:

        print(f"State save error: {e}")


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_ts() -> int:
    return int(time.time())


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


def fmt_money(value: float) -> str:

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def fmt_pct(value: float) -> str:
    return f"{value:+.1f}%"


def age_hours(pair_created_at: Optional[int]) -> Optional[float]:

    if not pair_created_at:
        return None

    try:

        # DexScreener normally returns milliseconds.
        created_seconds = pair_created_at / 1000.0

        return max(
            0.0,
            (time.time() - created_seconds) / 3600.0
        )

    except Exception:
        return None


# ============================================================
# HTTP
# ============================================================

def http_get_json(
    url: str,
    timeout: int = 15
) -> Optional[Any]:

    try:

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "RunnerBot/5.1"
                ),
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

            return json.loads(raw)

    except urllib.error.HTTPError as e:

        print(
            f"HTTP error {e.code}: {url}"
        )

        return None

    except Exception as e:

        print(
            f"HTTP request error: {e}"
        )

        return None


# ============================================================
# DEXSCREENER
# ============================================================

def dex_latest_profiles() -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}/token-profiles/latest/v1"
    )

    data = http_get_json(url)

    if not isinstance(data, list):
        return []

    return data


def dex_latest_boosts() -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}/token-boosts/latest/v1"
    )

    data = http_get_json(url)

    if not isinstance(data, list):
        return []

    return data


def dex_token_pairs(
    address: str
) -> List[Dict[str, Any]]:

    encoded = urllib.parse.quote(
        address,
        safe=""
    )

    url = (
        f"{DEX_BASE}/tokens/v1/"
        f"solana/{encoded}"
    )

    data = http_get_json(url)

    if not isinstance(data, list):
        return []

    return data


# ============================================================
# DISCOVERY
# ============================================================

def discover_addresses() -> List[str]:

    addresses = set()

    profiles = dex_latest_profiles()

    for item in profiles:

        if not isinstance(item, dict):
            continue

        chain_id = str(
            item.get("chainId", "")
        ).lower()

        address = str(
            item.get("tokenAddress", "")
        ).strip()

        if (
            chain_id == CHAIN
            and address
        ):
            addresses.add(address)


    boosts = dex_latest_boosts()

    for item in boosts:

        if not isinstance(item, dict):
            continue

        chain_id = str(
            item.get("chainId", "")
        ).lower()

        address = str(
            item.get("tokenAddress", "")
        ).strip()

        if (
            chain_id == CHAIN
            and address
        ):
            addresses.add(address)


    return list(addresses)


# ============================================================
# PAIR SELECTION
# ============================================================

def select_best_pair(
    pairs: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:

    solana_pairs = []

    for pair in pairs:

        if not isinstance(pair, dict):
            continue

        if str(
            pair.get("chainId", "")
        ).lower() != CHAIN:

            continue

        solana_pairs.append(pair)


    if not solana_pairs:
        return None


    def liquidity_value(pair):

        liquidity = pair.get(
            "liquidity",
            {}
        )

        if not isinstance(liquidity, dict):
            return 0.0

        return safe_float(
            liquidity.get("usd")
        )


    solana_pairs.sort(
        key=liquidity_value,
        reverse=True
    )

    return solana_pairs[0]


# ============================================================
# PAIR PARSING
# ============================================================

def parse_pair(
    pair: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    base = pair.get(
        "baseToken",
        {}
    )

    if not isinstance(base, dict):
        return None

    token_address = str(
        base.get("address", "")
    ).strip()

    symbol = str(
        base.get("symbol", "UNKNOWN")
    )

    name = str(
        base.get("name", symbol)
    )

    if not token_address:
        return None


    market_cap = safe_float(
        pair.get("marketCap")
    )

    if market_cap <= 0:

        market_cap = safe_float(
            pair.get("fdv")
        )


    liquidity_data = pair.get(
        "liquidity",
        {}
    )

    if not isinstance(
        liquidity_data,
        dict
    ):
        liquidity_data = {}


    volume = pair.get(
        "volume",
        {}
    )

    if not isinstance(volume, dict):
        volume = {}


    price_change = pair.get(
        "priceChange",
        {}
    )

    if not isinstance(
        price_change,
        dict
    ):
        price_change = {}


    txns = pair.get(
        "txns",
        {}
    )

    if not isinstance(txns, dict):
        txns = {}


    m5_txns = txns.get(
        "m5",
        {}
    )

    if not isinstance(
        m5_txns,
        dict
    ):
        m5_txns = {}


    h1_txns = txns.get(
        "h1",
        {}
    )

    if not isinstance(
        h1_txns,
        dict
    ):
        h1_txns = {}


    buys_5m = safe_int(
        m5_txns.get("buys")
    )

    sells_5m = safe_int(
        m5_txns.get("sells")
    )


    if sells_5m > 0:

        buy_sell_ratio = (
            buys_5m / sells_5m
        )

    elif buys_5m > 0:

        buy_sell_ratio = float("inf")

    else:

        buy_sell_ratio = 0.0


    m5_volume = safe_float(
        volume.get("m5")
    )

    h1_volume = safe_float(
        volume.get("h1")
    )

    h6_volume = safe_float(
        volume.get("h6")
    )

    h24_volume = safe_float(
        volume.get("h24")
    )


    price_m5 = safe_float(
        price_change.get("m5")
    )

    price_h1 = safe_float(
        price_change.get("h1")
    )

    price_h6 = safe_float(
        price_change.get("h6")
    )

    price_h24 = safe_float(
        price_change.get("h24")
    )


    if market_cap > 0:

        volume_mc_ratio = (
            m5_volume / market_cap
        )

    else:

        volume_mc_ratio = 0.0


    if h1_volume > 0:

        volume_expansion_ratio = (
            m5_volume / h1_volume
        )

    else:

        volume_expansion_ratio = 0.0


    liquidity = safe_float(
        liquidity_data.get("usd")
    )


    created_at = pair.get(
        "pairCreatedAt"
    )

    age = age_hours(created_at)


    return {

        "address": token_address,

        "symbol": symbol,

        "name": name,

        "market_cap": market_cap,

        "liquidity": liquidity,

        "pair_address": str(
            pair.get("pairAddress", "")
        ),

        "dex": str(
            pair.get("dexId", "unknown")
        ),

        "url": str(
            pair.get("url", "")
        ),

        "pair_created_at": created_at,

        "age_hours": age,

        "m5_volume": m5_volume,

        "h1_volume": h1_volume,

        "h6_volume": h6_volume,

        "h24_volume": h24_volume,

        "volume_mc_ratio": volume_mc_ratio,

        "volume_expansion_ratio":
            volume_expansion_ratio,

        "buys_5m": buys_5m,

        "sells_5m": sells_5m,

        "buy_sell_ratio":
            buy_sell_ratio,

        "price_m5": price_m5,

        "price_h1": price_h1,

        "price_h6": price_h6,

        "price_h24": price_h24,

        "observed_at": now_ts(),
    }


# ============================================================
# BASIC DISCOVERY FILTERS
# ============================================================

def basic_qualification(
    data: Dict[str, Any]
) -> Tuple[bool, str]:

    age = data.get("age_hours")

    if age is None:
        return False, "age unavailable"

    if age < MIN_AGE_HOURS:
        return False, "age < 3h"


    if (
        MAX_AGE_HOURS is not None
        and age > MAX_AGE_HOURS
    ):
        return False, "age above maximum"


    mc = data.get(
        "market_cap",
        0
    )

    if mc < MIN_MC:
        return False, "MC below $30k"

    if mc > MAX_MC:
        return False, "MC above $400k"


    liquidity = data.get(
        "liquidity",
        0
    )

    if liquidity < MIN_LIQUIDITY:
        return False, "liquidity below $25k"


    # IMPORTANT:
    # We no longer require:
    #
    # 5m volume / MC >= 5%
    # buys > sells
    # ratio >= 1.20
    # price >= 0
    #
    # at discovery.
    #
    # Those are now observation/recovery conditions.

    volume_mc_ratio = data.get(
        "volume_mc_ratio",
        0
    )

    if (
        volume_mc_ratio
        < MIN_INITIAL_VOLUME_MC_RATIO
    ):
        return False, "initial volume too low"


    return True, "track"


# ============================================================
# RECOVERY DETECTION
# ============================================================

def detect_recovery(
    current: Dict[str, Any],
    previous: Optional[Dict[str, Any]]
) -> Tuple[bool, List[str]]:

    signals = []


    # --------------------------------------------------------
    # Signal 1:
    # 5m price is positive
    # --------------------------------------------------------

    price_m5 = current.get(
        "price_m5",
        0
    )

    if (
        price_m5 >= MIN_5M_PRICE_CHANGE
        and price_m5 <= MAX_5M_PRICE_CHANGE
    ):

        signals.append(
            "5m price recovery"
        )


    # --------------------------------------------------------
    # Signal 2:
    # 5m price positive + 1h positive
    # --------------------------------------------------------

    if (
        current.get("price_m5", 0) > 0
        and current.get("price_h1", 0) > 0
    ):

        signals.append(
            "5m+1h positive momentum"
        )


    # --------------------------------------------------------
    # Signal 3:
    # Bounce after 6h weakness
    # --------------------------------------------------------

    if (
        current.get("price_m5", 0) > 0
        and current.get("price_h6", 0) < 0
    ):

        signals.append(
            "bounce after 6h weakness"
        )


    # --------------------------------------------------------
    # Signal 4:
    # Volume / MC >= 5%
    # --------------------------------------------------------

    if (
        current.get(
            "volume_mc_ratio",
            0
        )
        >= MIN_RECOVERY_VOLUME_MC_RATIO
    ):

        signals.append(
            "strong 5m volume"
        )


    # --------------------------------------------------------
    # Signal 5:
    # m5 volume is at least 10% of h1 volume
    # --------------------------------------------------------

    if (
        current.get(
            "volume_expansion_ratio",
            0
        )
        >= VOLUME_EXPANSION_RATIO
    ):

        signals.append(
            "volume expansion"
        )


    # --------------------------------------------------------
    # Signal 6:
    # Buy pressure
    # --------------------------------------------------------

    ratio = current.get(
        "buy_sell_ratio",
        0
    )

    if ratio >= MIN_BUY_SELL_RATIO:

        signals.append(
            "buy pressure"
        )


    # --------------------------------------------------------
    # Signal 7:
    # Strong buy pressure
    # --------------------------------------------------------

    if ratio >= STRONG_BUY_SELL_RATIO:

        signals.append(
            "strong buy pressure"
        )


    # --------------------------------------------------------
    # Signal 8:
    # Volume accelerating compared with previous snapshot
    # --------------------------------------------------------

    if previous:

        previous_volume = previous.get(
            "m5_volume",
            0
        )

        current_volume = current.get(
            "m5_volume",
            0
        )

        if (
            previous_volume > 0
            and current_volume
            >= previous_volume
            * (1 + SNAPSHOT_VOLUME_INCREASE)
        ):

            signals.append(
                "5m volume accelerating"
            )


    # --------------------------------------------------------
    # Signal 9:
    # Buy ratio improving
    # --------------------------------------------------------

    if previous:

        previous_ratio = previous.get(
            "buy_sell_ratio",
            0
        )

        current_ratio = current.get(
            "buy_sell_ratio",
            0
        )

        if (
            previous_ratio > 0
            and current_ratio
            >= previous_ratio
            * (1 + SNAPSHOT_BUY_RATIO_INCREASE)
        ):

            signals.append(
                "buy pressure improving"
            )


    # --------------------------------------------------------
    # HARD RECOVERY CONDITIONS
    #
    # We need the token to currently have:
    #
    # 1. positive buys/sells pressure
    # 2. positive 5m price
    #
    # AND at least one additional recovery/volume signal.
    # --------------------------------------------------------

    positive_price = (
        current.get("price_m5", 0)
        >= MIN_5M_PRICE_CHANGE
        and
        current.get("price_m5", 0)
        <= MAX_5M_PRICE_CHANGE
    )


    buy_pressure = (
        current.get(
            "buys_5m",
            0
        )
        >
        current.get(
            "sells_5m",
            0
        )
        and
        current.get(
            "buy_sell_ratio",
            0
        )
        >= MIN_BUY_SELL_RATIO
    )


    volume_condition = (
        current.get(
            "volume_mc_ratio",
            0
        )
        >= MIN_RECOVERY_VOLUME_MC_RATIO
        or
        current.get(
            "volume_expansion_ratio",
            0
        )
        >= VOLUME_EXPANSION_RATIO
    )


    if (
        positive_price
        and buy_pressure
        and volume_condition
    ):

        return True, signals


    return False, signals


# ============================================================
# OBSERVATION MANAGEMENT
# ============================================================

def make_snapshot(
    data: Dict[str, Any]
) -> Dict[str, Any]:

    return {

        "timestamp":
            data.get(
                "observed_at",
                now_ts()
            ),

        "market_cap":
            data.get(
                "market_cap",
                0
            ),

        "liquidity":
            data.get(
                "liquidity",
                0
            ),

        "m5_volume":
            data.get(
                "m5_volume",
                0
            ),

        "h1_volume":
            data.get(
                "h1_volume",
                0
            ),

        "volume_mc_ratio":
            data.get(
                "volume_mc_ratio",
                0
            ),

        "volume_expansion_ratio":
            data.get(
                "volume_expansion_ratio",
                0
            ),

        "buys_5m":
            data.get(
                "buys_5m",
                0
            ),

        "sells_5m":
            data.get(
                "sells_5m",
                0
            ),

        "buy_sell_ratio":
            data.get(
                "buy_sell_ratio",
                0
            ),

        "price_m5":
            data.get(
                "price_m5",
                0
            ),

        "price_h1":
            data.get(
                "price_h1",
                0
            ),

        "price_h6":
            data.get(
                "price_h6",
                0
            ),
    }


def get_observation_label(
    count: int
) -> str:

    if count <= 1:
        return "ULTRA"

    if count == 2:
        return "STRONG"

    return "NORMAL"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(
    method: str,
    payload: Dict[str, Any]
) -> Optional[Any]:

    if not TELEGRAM_BOT_TOKEN:
        print(
            "TELEGRAM_BOT_TOKEN missing."
        )
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

    encoded = urllib.parse.urlencode(
        payload
    ).encode("utf-8")


    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type":
                "application/x-www-form-urlencoded",
        },
    )


    try:

        with urllib.request.urlopen(
            request,
            timeout=15
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

            return json.loads(raw)

    except Exception as e:

        print(
            f"Telegram error: {e}"
        )

        return None


def send_message(
    chat_id: str,
    text: str
):

    telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    )


def broadcast(
    state: Dict[str, Any],
    text: str
):

    if not HEATING_ALERTS_ENABLED:
        return

    subscribers = state.get(
        "subscribers",
        []
    )

    for chat_id in list(subscribers):

        send_message(
            str(chat_id),
            text
        )


# ============================================================
# ALERT
# ============================================================

def build_alert(
    data: Dict[str, Any],
    observation_count: int,
    signals: List[str]
) -> str:

    symbol = data.get(
        "symbol",
        "UNKNOWN"
    )

    name = data.get(
        "name",
        symbol
    )

    label = get_observation_label(
        observation_count
    )


    liquidity = data.get(
        "liquidity",
        0
    )

    liquidity_tag = (
        "PREFERRED"
        if liquidity >= PREFERRED_LIQUIDITY
        else "MINIMUM"
    )


    ratio = data.get(
        "buy_sell_ratio",
        0
    )

    ratio_text = (
        "∞"
        if ratio == float("inf")
        else f"{ratio:.2f}"
    )


    age = data.get(
        "age_hours"
    )

    age_text = (
        f"{age:.1f}h"
        if age is not None
        else "N/A"
    )


    url = data.get(
        "url",
        ""
    )

    address = data.get(
        "address",
        ""
    )


    signals_text = "\n".join(
        f"• {signal}"
        for signal in signals
    )


    return (
        "🚨 RECOVERY RUNNER\n"
        "\n"
        f"{symbol} — {name}\n"
        f"Observation: {observation_count} "
        f"({label})\n"
        "\n"
        f"💰 MC: "
        f"{fmt_money(data.get('market_cap', 0))}\n"
        f"💧 Liquidity: "
        f"{fmt_money(liquidity)} "
        f"[{liquidity_tag}]\n"
        f"⏱ Age: {age_text}\n"
        "\n"
        f"📊 5m Volume: "
        f"{fmt_money(data.get('m5_volume', 0))}\n"
        f"📈 Volume/MC: "
        f"{data.get('volume_mc_ratio', 0) * 100:.1f}%\n"
        f"⚡ 5m/H1 Volume: "
        f"{data.get('volume_expansion_ratio', 0) * 100:.1f}%\n"
        "\n"
        f"🟢 Buys: "
        f"{data.get('buys_5m', 0)}\n"
        f"🔴 Sells: "
        f"{data.get('sells_5m', 0)}\n"
        f"⚖️ B/S: {ratio_text}\n"
        "\n"
        f"5m: "
        f"{fmt_pct(data.get('price_m5', 0))}\n"
        f"1h: "
        f"{fmt_pct(data.get('price_h1', 0))}\n"
        f"6h: "
        f"{fmt_pct(data.get('price_h6', 0))}\n"
        "\n"
        "🔎 Recovery signals:\n"
        f"{signals_text}\n"
        "\n"
        f"DEX: {data.get('dex', 'unknown')}\n"
        f"Chart: {url}\n"
        "\n"
        f"Contract:\n{address}\n"
        "\n"
        "⚠️ Research before trading."
    )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def process_updates(
    state: Dict[str, Any],
    offset: Optional[int]
) -> Optional[int]:

    if not TELEGRAM_BOT_TOKEN:
        return offset


    payload = {
        "timeout": 1,
        "allowed_updates":
            json.dumps(["message"]),
    }


    if offset is not None:
        payload["offset"] = offset


    result = telegram_request(
        "getUpdates",
        payload
    )


    if not result:
        return offset


    if not result.get("ok"):
        return offset


    updates = result.get(
        "result",
        []
    )


    for update in updates:

        update_id = update.get(
            "update_id"
        )

        if update_id is not None:
            offset = update_id + 1


        message = update.get(
            "message"
        )

        if not isinstance(
            message,
            dict
        ):
            continue


        chat = message.get(
            "chat",
            {}
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


        if text.startswith("/start"):

            if chat_id not in state[
                "subscribers"
            ]:

                state[
                    "subscribers"
                ].append(chat_id)


            send_message(
                str(chat_id),
                "🟢 Runner Bot V5.1 is online.\n\n"
                "DexScreener-only recovery scanner.\n"
                "Tracking begins before recovery confirmation."
            )


        elif text.startswith("/stop"):

            state[
                "subscribers"
            ] = [
                x
                for x in state[
                    "subscribers"
                ]
                if x != chat_id
            ]


            send_message(
                str(chat_id),
                "🔴 Alerts disabled for this chat."
            )


        elif text.startswith("/alerts"):

            enabled = (
                chat_id
                in state["subscribers"]
            )

            send_message(
                str(chat_id),
                f"Alerts: "
                f"{'ON' if enabled else 'OFF'}"
            )


        elif text.startswith("/status"):

            tracking_count = len(
                state.get(
                    "tracking",
                    {}
                )
            )

            stats = state.get(
                "stats",
                {}
            )

            send_message(
                str(chat_id),
                (
                    "🤖 RUNNER BOT V5.1\n\n"
                    f"Tracked: {tracking_count}\n"
                    f"Discovered: "
                    f"{stats.get('discovered', 0)}\n"
                    f"Alerts: "
                    f"{stats.get('alerts', 0)}\n"
                    f"Scans: "
                    f"{stats.get('scans', 0)}\n\n"
                    "Source: DexScreener\n"
                    "Chain: Solana\n"
                    "Age: 3h+\n"
                    "Max age: None\n"
                    "MC: $30k-$400k\n"
                    "Liquidity: $25k+\n"
                    "Tracking window: 12h"
                )
            )


        elif text.startswith("/tracking"):

            tracking = state.get(
                "tracking",
                {}
            )

            if not tracking:

                send_message(
                    str(chat_id),
                    "No tokens currently tracked."
                )

                continue


            lines = [
                "📡 CURRENTLY TRACKED\n"
            ]


            items = list(
                tracking.items()
            )


            items.sort(
                key=lambda item:
                    item[1].get(
                        "last_seen",
                        0
                    ),
                reverse=True
            )


            for address, item in items[:20]:

                lines.append(
                    f"{item.get('symbol', 'UNKNOWN')} "
                    f"| "
                    f"MC "
                    f"{fmt_money(item.get('market_cap', 0))} "
                    f"| "
                    f"Obs "
                    f"{item.get('observations', 0)}"
                )


            send_message(
                str(chat_id),
                "\n".join(lines)
            )


    save_state(state)

    return offset


# ============================================================
# DISCOVERY DIAGNOSTICS
# ============================================================

def new_diagnostics() -> Dict[str, int]:

    return {

        "source": 0,

        "invalid_pair": 0,

        "age_unavailable": 0,

        "age_low": 0,

        "mc_low": 0,

        "mc_high": 0,

        "liquidity_low": 0,

        "volume_low": 0,

        "tracked": 0,

        "duplicates": 0,

    }


def classify_rejection(
    reason: str,
    diagnostics: Dict[str, int]
):

    if reason == "age unavailable":
        diagnostics["age_unavailable"] += 1

    elif reason == "age < 3h":
        diagnostics["age_low"] += 1

    elif reason == "MC below $30k":
        diagnostics["mc_low"] += 1

    elif reason == "MC above $400k":
        diagnostics["mc_high"] += 1

    elif reason == "liquidity below $25k":
        diagnostics["liquidity_low"] += 1

    elif reason == "initial volume too low":
        diagnostics["volume_low"] += 1


# ============================================================
# TRACKING
# ============================================================

def cleanup_tracking(
    state: Dict[str, Any]
):

    tracking = state.get(
        "tracking",
        {}
    )

    cutoff = (
        now_ts()
        -
        int(
            OBSERVATION_WINDOW_HOURS
            * 3600
        )
    )


    remove = []


    for address, item in tracking.items():

        last_seen = safe_int(
            item.get(
                "last_seen",
                0
            )
        )

        if last_seen < cutoff:

            remove.append(address)


    for address in remove:

        tracking.pop(
            address,
            None
        )


    # Keep newest tokens if limit is exceeded.

    if len(tracking) > MAX_TRACKED_TOKENS:

        sorted_items = sorted(
            tracking.items(),
            key=lambda item:
                item[1].get(
                    "last_seen",
                    0
                ),
            reverse=True
        )


        keep = dict(
            sorted_items[
                :MAX_TRACKED_TOKENS
            ]
        )

        state[
            "tracking"
        ] = keep


# ============================================================
# DISCOVERY CYCLE
# ============================================================

def discovery_cycle(
    state: Dict[str, Any]
):

    print(
        "DexScreener discovery: "
        "searching recovery candidates..."
    )


    addresses = discover_addresses()


    print(
        f"DexScreener source tokens: "
        f"{len(addresses)}"
    )


    diagnostics = new_diagnostics()

    diagnostics["source"] = len(
        addresses
    )


    candidates = 0


    for address in addresses:

        pairs = dex_token_pairs(
            address
        )


        pair = select_best_pair(
            pairs
        )


        if not pair:

            diagnostics[
                "invalid_pair"
            ] += 1

            continue


        data = parse_pair(
            pair
        )


        if not data:

            diagnostics[
                "invalid_pair"
            ] += 1

            continue


        state[
            "stats"
        ][
            "discovered"
        ] += 1


        qualified, reason = (
            basic_qualification(data)
        )


        if not qualified:

            classify_rejection(
                reason,
                diagnostics
            )

            continue


        token_address = data[
            "address"
        ]


        if token_address in state[
            "tracking"
        ]:

            diagnostics[
                "duplicates"
            ] += 1

            continue


        candidates += 1


        snapshot = make_snapshot(
            data
        )


        state[
            "tracking"
        ][token_address] = {

            "address":
                token_address,

            "symbol":
                data["symbol"],

            "name":
                data["name"],

            "market_cap":
                data["market_cap"],

            "liquidity":
                data["liquidity"],

            "pair_address":
                data["pair_address"],

            "dex":
                data["dex"],

            "url":
                data["url"],

            "first_seen":
                now_ts(),

            "last_seen":
                now_ts(),

            "observations":
                0,

            "alerts_sent":
                0,

            "snapshots": [
                snapshot
            ],
        }


        diagnostics[
            "tracked"
        ] += 1


        state[
            "stats"
        ][
            "tracked"
        ] += 1


        print(
            f"TRACKING | "
            f"{data['symbol']} | "
            f"MC={fmt_money(data['market_cap'])} | "
            f"Liq={fmt_money(data['liquidity'])} | "
            f"Age={data['age_hours']:.1f}h"
        )


    print(
        "Discovery diagnostics | "
        f"source={diagnostics['source']} "
        f"invalid={diagnostics['invalid_pair']} "
        f"age<3h={diagnostics['age_low']} "
        f"MC<30k={diagnostics['mc_low']} "
        f"MC>400k={diagnostics['mc_high']} "
        f"liq<25k={diagnostics['liquidity_low']} "
        f"volume<1%={diagnostics['volume_low']} "
        f"duplicates={diagnostics['duplicates']} "
        f"new_tracking={diagnostics['tracked']}"
    )


    print(
        f"DexScreener candidates: "
        f"{candidates}"
    )


    state[
        "last_discovery"
    ] = now_ts()


    cleanup_tracking(
        state
    )


    save_state(
        state
    )


# ============================================================
# VALIDATION CYCLE
# ============================================================

def validation_cycle(
    state: Dict[str, Any]
):

    tracking = state.get(
        "tracking",
        {}
    )


    if not tracking:
        return


    addresses = list(
        tracking.keys()
    )


    addresses.sort(
        key=lambda address:
            tracking[address].get(
                "last_seen",
                0
            )
    )


    addresses = addresses[
        :MAX_VALIDATIONS_PER_CYCLE
    ]


    for address in addresses:

        item = tracking.get(
            address
        )

        if not item:
            continue


        pairs = dex_token_pairs(
            address
        )


        pair = select_best_pair(
            pairs
        )


        if not pair:
            continue


        data = parse_pair(
            pair
        )


        if not data:
            continue


        snapshots = item.get(
            "snapshots",
            []
        )


        previous = (
            snapshots[-1]
            if snapshots
            else None
        )


        recovered, signals = (
            detect_recovery(
                data,
                previous
            )
        )


        snapshot = make_snapshot(
            data
        )


        snapshots.append(
            snapshot
        )


        # Keep only the most recent
        # 30 observations.

        item[
            "snapshots"
        ] = snapshots[-30:]


        item[
            "last_seen"
        ] = now_ts()


        item[
            "market_cap"
        ] = data[
            "market_cap"
        ]


        item[
            "liquidity"
        ] = data[
            "liquidity"
        ]


        item[
            "symbol"
        ] = data[
            "symbol"
        ]


        item[
            "name"
        ] = data[
            "name"
        ]


        item[
            "url"
        ] = data[
            "url"
        ]


        item[
            "dex"
        ] = data[
            "dex"
        ]


        # ----------------------------------------------------
        # RECOVERY ALERT
        # ----------------------------------------------------

        if recovered:

            item[
                "observations"
            ] = (
                item.get(
                    "observations",
                    0
                )
                + 1
            )


            observation_count = item[
                "observations"
            ]


            alert = build_alert(
                data,
                observation_count,
                signals
            )


            broadcast(
                state,
                alert
            )


            item[
                "alerts_sent"
            ] = (
                item.get(
                    "alerts_sent",
                    0
                )
                + 1
            )


            state[
                "stats"
            ][
                "alerts"
            ] += 1


            print(
                f"🚨 ALERT | "
                f"{data['symbol']} | "
                f"observation="
                f"{observation_count} | "
                f"signals="
                f"{', '.join(signals)}"
            )


        else:

            print(
                f"Watching | "
                f"{data['symbol']} | "
                f"MC={fmt_money(data['market_cap'])} | "
                f"5mVol="
                f"{fmt_money(data['m5_volume'])} | "
                f"B/S="
                f"{data['buy_sell_ratio']:.2f}"
                if data["buy_sell_ratio"]
                != float("inf")
                else
                f"Watching | "
                f"{data['symbol']} | "
                f"MC={fmt_money(data['market_cap'])} | "
                f"5mVol="
                f"{fmt_money(data['m5_volume'])} | "
                f"B/S=∞"
            )


    state[
        "stats"
    ][
        "scans"
    ] += 1


    cleanup_tracking(
        state
    )


    save_state(
        state
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "============================================================"
    )

    print(
        f"RUNNER BOT {BOT_VERSION}"
    )

    print(
        "DEXSCREENER RECOVERY RUNNER"
    )

    print(
        "============================================================"
    )

    print(
        "Source: DexScreener only"
    )

    print(
        "Chain: Solana"
    )

    print(
        "Age: 3h+ | No maximum"
    )

    print(
        "MC: $30,000-$400,000"
    )

    print(
        "Liquidity: >= $25,000"
    )

    print(
        "Preferred liquidity: >= $40,000"
    )

    print(
        "Initial Volume/MC: >= 1%"
    )

    print(
        "Recovery Volume/MC: >= 5%"
    )

    print(
        "Buy/Sell: >= 1.20"
    )

    print(
        "5m Price: 0% to +50%"
    )

    print(
        "Recovery + volume expansion"
    )

    print(
        "Tracking: 12h"
    )

    print(
        "GMGN: DISABLED"
    )

    print(
        "============================================================"
    )


    if not TELEGRAM_BOT_TOKEN:

        print(
            "WARNING: "
            "TELEGRAM_BOT_TOKEN is not configured."
        )


    state = load_state()

    offset = None

    last_discovery = 0

    last_validation = 0


    print(
        "Bot loop started."
    )


    while True:

        try:

            # -----------------------------------------------
            # Telegram
            # -----------------------------------------------

            offset = process_updates(
                state,
                offset
            )


            current = now_ts()


            # -----------------------------------------------
            # Discovery
            # -----------------------------------------------

            if (
                current
                - last_discovery
                >= DISCOVERY_INTERVAL_SECONDS
            ):

                discovery_cycle(
                    state
                )

                last_discovery = current


            # -----------------------------------------------
            # Validation
            # -----------------------------------------------

            if (
                current
                - last_validation
                >= VALIDATION_INTERVAL_SECONDS
            ):

                validation_cycle(
                    state
                )

                last_validation = current


            print(
                "Scan complete | "
                f"tracked="
                f"{len(state.get('tracking', {}))} | "
                f"alerts="
                f"{state.get('stats', {}).get('alerts', 0)}"
            )


            time.sleep(
                SCAN_INTERVAL_SECONDS
            )


        except KeyboardInterrupt:

            print(
                "Bot stopped."
            )

            save_state(
                state
            )

            break


        except Exception as e:

            print(
                f"Main loop error: {e}"
            )

            save_state(
                state
            )

            time.sleep(
                SCAN_INTERVAL_SECONDS
            )


if __name__ == "__main__":
    main()
