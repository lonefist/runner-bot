import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V5.0
# DEXSCREENER RECOVERY RUNNER
# ============================================================

BOT_VERSION = "V5.0-DEXSCREENER-RECOVERY"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

STATE_FILE = "runner_state_v50.json"

# ============================================================
# STRATEGY
# ============================================================

MIN_AGE_HOURS = 3.0
MAX_AGE_HOURS = None

MIN_MC = 30_000
MAX_MC = 400_000

MIN_LIQUIDITY = 25_000
PREFERRED_LIQUIDITY = 40_000

MIN_VOLUME_MC_RATIO = 0.05

MIN_BUY_SELL_RATIO = 1.20

# Avoid tokens that have already gone vertical in 5 minutes.
MAX_5M_PRICE_CHANGE = 50.0

# Minimum 5m price activity.
MIN_5M_PRICE_CHANGE = 0.0

# A 5m volume share above this means the current 5m period
# is unusually active relative to the preceding 1h.
VOLUME_EXPANSION_RATIO = 0.10

# Tracking
OBSERVATION_WINDOW_HOURS = 12
MAX_TRACKED_TOKENS = 150

# Discovery / validation
DISCOVERY_INTERVAL_SECONDS = 300
VALIDATION_INTERVAL_SECONDS = 300

MAX_VALIDATIONS_PER_CYCLE = 20

# Telegram
SCAN_INTERVAL_SECONDS = int(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)

ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "true").lower()
    == "true"
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


# ============================================================
# HTTP
# ============================================================

def http_get(
    url: str,
    timeout: int = 20
) -> Optional[Any]:

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "RunnerBot/5.0 "
                    "(DexScreener Recovery Scanner)"
                )
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=timeout
        ) as response:

            raw = response.read().decode("utf-8")

            if not raw:
                return None

            return json.loads(raw)

    except urllib.error.HTTPError as e:

        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = ""

        print(
            f"HTTP error {e.code}: "
            f"{body[:300]}"
        )

        return None

    except Exception as e:

        print(
            f"HTTP request error: {e}"
        )

        return None


# ============================================================
# TIME
# ============================================================

def now_ts() -> float:
    return time.time()


def age_hours(
    pair_created_at: Optional[int]
) -> Optional[float]:

    if not pair_created_at:
        return None

    try:

        # DexScreener timestamps are milliseconds.
        created = (
            pair_created_at / 1000
        )

        age = (
            now_ts() - created
        ) / 3600

        return age

    except Exception:
        return None


def fmt_age(hours: Optional[float]) -> str:

    if hours is None:
        return "?"

    if hours < 24:
        return f"{hours:.1f}h"

    return f"{hours / 24:.1f}d"


# ============================================================
# NUMBERS
# ============================================================

def num(
    value: Any,
    default: float = 0.0
) -> float:

    try:

        if value is None:
            return default

        return float(value)

    except Exception:
        return default


def money(
    value: float
) -> str:

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def pct(
    value: float
) -> str:

    return f"{value:.1f}%"


# ============================================================
# STATE
# ============================================================

def default_state() -> Dict[str, Any]:

    return {
        "subscribers": [],
        "tracked": {},
        "last_discovery": 0,
        "last_validation": 0,
        "total_scans": 0,
        "total_alerts": 0
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

        state.setdefault(
            "subscribers",
            []
        )

        state.setdefault(
            "tracked",
            {}
        )

        state.setdefault(
            "last_discovery",
            0
        )

        state.setdefault(
            "last_validation",
            0
        )

        state.setdefault(
            "total_scans",
            0
        )

        state.setdefault(
            "total_alerts",
            0
        )

        return state

    except Exception as e:

        print(
            f"State load error: {e}"
        )

        return default_state()


def save_state(
    state: Dict[str, Any]
):

    tmp = STATE_FILE + ".tmp"

    try:

        with open(
            tmp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                indent=2
            )

        os.replace(
            tmp,
            STATE_FILE
        )

    except Exception as e:

        print(
            f"State save error: {e}"
        )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_call(
    method: str,
    data: Dict[str, Any]
) -> Optional[Any]:

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

    try:

        encoded = urllib.parse.urlencode(
            data
        ).encode()

        req = urllib.request.Request(
            url,
            data=encoded,
            headers={
                "Content-Type":
                    "application/x-www-form-urlencoded"
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=20
        ) as response:

            return json.loads(
                response.read().decode()
            )

    except Exception as e:

        print(
            f"Telegram error: {e}"
        )

        return None


def send_message(
    chat_id: str,
    text: str
):

    telegram_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True
        }
    )


def broadcast(
    state: Dict[str, Any],
    text: str
):

    if not ALERTS_ENABLED:
        return

    for chat_id in list(
        state.get("subscribers", [])
    ):

        send_message(
            chat_id,
            text
        )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def process_telegram_updates(
    state: Dict[str, Any]
):

    result = telegram_call(
        "getUpdates",
        {
            "timeout": 1,
            "allowed_updates":
                json.dumps(["message"])
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

    if not updates:
        return

    last_update = None

    for update in updates:

        last_update = update.get(
            "update_id"
        )

        message = update.get(
            "message",
            {}
        )

        chat = message.get(
            "chat",
            {}
        )

        chat_id = chat.get(
            "id"
        )

        text = (
            message.get(
                "text",
                ""
            )
            .strip()
            .lower()
        )

        if not chat_id:
            continue

        chat_id = str(chat_id)

        if text == "/start":

            if chat_id not in state[
                "subscribers"
            ]:

                state[
                    "subscribers"
                ].append(chat_id)

                save_state(state)

            send_message(
                chat_id,
                (
                    "Runner Bot V5.0 is online.\n\n"
                    "DexScreener-only recovery scanner.\n"
                    "Age: 3h+ with no maximum age.\n"
                    "Use /status to view the scanner."
                )
            )

        elif text == "/stop":

            if chat_id in state[
                "subscribers"
            ]:

                state[
                    "subscribers"
                ].remove(chat_id)

                save_state(state)

            send_message(
                chat_id,
                "Alerts stopped."
            )

        elif text == "/alerts":

            enabled = (
                chat_id in state[
                    "subscribers"
                ]
            )

            send_message(
                chat_id,
                (
                    "Alerts: "
                    + (
                        "ON"
                        if enabled
                        else "OFF"
                    )
                )
            )

        elif text == "/status":

            tracked = len(
                state.get(
                    "tracked",
                    {}
                )
            )

            send_message(
                chat_id,
                (
                    f"Runner Bot {BOT_VERSION}\n\n"
                    f"Tracked: {tracked}\n"
                    f"Subscribers: "
                    f"{len(state['subscribers'])}\n"
                    f"Scans: "
                    f"{state['total_scans']}\n"
                    f"Alerts: "
                    f"{state['total_alerts']}\n\n"
                    "Age: 3h+\n"
                    "MC: $30K-$400K\n"
                    "Liquidity: >= $25K\n"
                    "Volume/MC: >= 5%\n"
                    "Buy/Sell: >= 1.20"
                )
            )

        elif text == "/tracking":

            tracked = state.get(
                "tracked",
                {}
            )

            if not tracked:

                send_message(
                    chat_id,
                    "No tokens are currently tracked."
                )

                continue

            lines = [
                "TRACKING\n"
            ]

            for address, item in list(
                tracked.items()
            )[:20]:

                lines.append(
                    f"{item.get('symbol', '?')} "
                    f"| obs="
                    f"{item.get('observations', 0)} "
                    f"| MC="
                    f"{money(num(item.get('mc')))}"
                )

            send_message(
                chat_id,
                "\n".join(lines)
            )

    if last_update is not None:

        telegram_call(
            "getUpdates",
            {
                "offset":
                    last_update + 1,
                "timeout": 1
            }
        )


# ============================================================
# DEXSCREENER DISCOVERY
# ============================================================

def discover_sources() -> List[str]:

    addresses = set()

    # Latest token profiles
    profiles = http_get(
        f"{DEX_BASE}/token-profiles/latest/v1"
    )

    if isinstance(profiles, list):

        for item in profiles:

            if (
                item.get("chainId")
                == "solana"
            ):

                address = item.get(
                    "tokenAddress"
                )

                if address:
                    addresses.add(address)

    # Latest boosts
    boosts = http_get(
        f"{DEX_BASE}/token-boosts/latest/v1"
    )

    if isinstance(boosts, list):

        for item in boosts:

            if (
                item.get("chainId")
                == "solana"
            ):

                address = item.get(
                    "tokenAddress"
                )

                if address:
                    addresses.add(address)

    return list(addresses)


def fetch_token_pairs(
    address: str
) -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}/tokens/v1/solana/"
        f"{urllib.parse.quote(address)}"
    )

    data = http_get(url)

    if not isinstance(data, list):
        return []

    return [
        p for p in data
        if p.get("chainId") == "solana"
    ]


def select_best_pair(
    pairs: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:

    if not pairs:
        return None

    valid = []

    for pair in pairs:

        liquidity = num(
            pair.get(
                "liquidity",
                {}
            ).get("usd")
        )

        if liquidity <= 0:
            continue

        valid.append(
            pair
        )

    if not valid:
        return None

    valid.sort(
        key=lambda p: num(
            p.get(
                "liquidity",
                {}
            ).get("usd")
        ),
        reverse=True
    )

    return valid[0]


# ============================================================
# METRICS
# ============================================================

def extract_metrics(
    pair: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    if not pair:
        return None

    base = pair.get(
        "baseToken",
        {}
    )

    address = base.get(
        "address"
    )

    if not address:
        return None

    symbol = base.get(
        "symbol",
        "UNKNOWN"
    )

    name = base.get(
        "name",
        symbol
    )

    liquidity = num(
        pair.get(
            "liquidity",
            {}
        ).get("usd")
    )

    mc = num(
        pair.get(
            "marketCap"
        )
    )

    if mc <= 0:

        mc = num(
            pair.get(
                "fdv"
            )
        )

    volume = pair.get(
        "volume",
        {}
    )

    price_change = pair.get(
        "priceChange",
        {}
    )

    txns = pair.get(
        "txns",
        {}
    )

    m5 = txns.get(
        "m5",
        {}
    )

    h1 = txns.get(
        "h1",
        {}
    )

    volume_5m = num(
        volume.get("m5")
    )

    volume_1h = num(
        volume.get("h1")
    )

    volume_6h = num(
        volume.get("h6")
    )

    price_5m = num(
        price_change.get("m5")
    )

    price_1h = num(
        price_change.get("h1")
    )

    price_6h = num(
        price_change.get("h6")
    )

    price_24h = num(
        price_change.get("h24")
    )

    buys_5m = num(
        m5.get("buys")
    )

    sells_5m = num(
        m5.get("sells")
    )

    buys_1h = num(
        h1.get("buys")
    )

    sells_1h = num(
        h1.get("sells")
    )

    if sells_5m > 0:

        buy_sell_ratio = (
            buys_5m / sells_5m
        )

    elif buys_5m > 0:

        buy_sell_ratio = 99.0

    else:

        buy_sell_ratio = 0.0

    if volume_1h > 0:

        five_min_share = (
            volume_5m /
            volume_1h
        )

    else:

        five_min_share = 0.0

    if mc > 0:

        volume_mc_ratio = (
            volume_5m / mc
        )

    else:

        volume_mc_ratio = 0.0

    age = age_hours(
        pair.get(
            "pairCreatedAt"
        )
    )

    return {
        "address": address,
        "symbol": symbol,
        "name": name,
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
        "mc": mc,
        "liquidity": liquidity,
        "age_hours": age,
        "volume_5m": volume_5m,
        "volume_1h": volume_1h,
        "volume_6h": volume_6h,
        "price_5m": price_5m,
        "price_1h": price_1h,
        "price_6h": price_6h,
        "price_24h": price_24h,
        "buys_5m": buys_5m,
        "sells_5m": sells_5m,
        "buys_1h": buys_1h,
        "sells_1h": sells_1h,
        "buy_sell_ratio": buy_sell_ratio,
        "five_min_share": five_min_share,
        "volume_mc_ratio": volume_mc_ratio
    }


# ============================================================
# HARD FILTERS
# ============================================================

def hard_filters(
    m: Dict[str, Any]
) -> List[str]:

    failures = []

    age = m.get(
        "age_hours"
    )

    if age is None:
        failures.append(
            "age unavailable"
        )

    elif age < MIN_AGE_HOURS:
        failures.append(
            "too young"
        )

    # No maximum age.

    mc = num(
        m.get("mc")
    )

    if mc < MIN_MC:
        failures.append(
            "MC below $30K"
        )

    if mc > MAX_MC:
        failures.append(
            "MC above $400K"
        )

    liquidity = num(
        m.get("liquidity")
    )

    if liquidity < MIN_LIQUIDITY:
        failures.append(
            "liquidity below $25K"
        )

    volume_mc = num(
        m.get(
            "volume_mc_ratio"
        )
    )

    if volume_mc < MIN_VOLUME_MC_RATIO:
        failures.append(
            "5m volume/MC below 5%"
        )

    buys = num(
        m.get("buys_5m")
    )

    sells = num(
        m.get("sells_5m")
    )

    if buys <= sells:
        failures.append(
            "5m buys not greater than sells"
        )

    ratio = num(
        m.get("buy_sell_ratio")
    )

    if ratio < MIN_BUY_SELL_RATIO:
        failures.append(
            "buy/sell ratio below 1.20"
        )

    price_5m = num(
        m.get("price_5m")
    )

    if price_5m < MIN_5M_PRICE_CHANGE:
        failures.append(
            "5m price not positive"
        )

    if price_5m > MAX_5M_PRICE_CHANGE:
        failures.append(
            "5m move already too extended"
        )

    return failures


# ============================================================
# RECOVERY / MOMENTUM
# ============================================================

def recovery_signals(
    m: Dict[str, Any],
    previous: Optional[Dict[str, Any]]
) -> Dict[str, Any]:

    signals = []

    price_5m = num(
        m.get("price_5m")
    )

    price_1h = num(
        m.get("price_1h")
    )

    price_6h = num(
        m.get("price_6h")
    )

    volume_5m = num(
        m.get("volume_5m")
    )

    volume_1h = num(
        m.get("volume_1h")
    )

    ratio = num(
        m.get("buy_sell_ratio")
    )

    five_min_share = num(
        m.get("five_min_share")
    )

    # --------------------------------------------------------
    # SIGNAL 1: Short-term recovery
    # --------------------------------------------------------

    if price_5m > 0 and price_1h >= 0:

        signals.append(
            "5m + 1h price recovery"
        )

    # --------------------------------------------------------
    # SIGNAL 2: Bounce after weakness
    # --------------------------------------------------------

    if (
        price_5m > 0
        and price_6h < 0
    ):

        signals.append(
            "5m bounce after 6h weakness"
        )

    # --------------------------------------------------------
    # SIGNAL 3: Volume expansion
    # --------------------------------------------------------

    if (
        volume_1h > 0
        and five_min_share
        >= VOLUME_EXPANSION_RATIO
    ):

        signals.append(
            "5m volume expansion"
        )

    # --------------------------------------------------------
    # SIGNAL 4: Current volume increasing
    # --------------------------------------------------------

    if previous:

        previous_volume = num(
            previous.get(
                "volume_5m"
            )
        )

        if (
            previous_volume > 0
            and volume_5m
            > previous_volume * 1.10
        ):

            signals.append(
                "5m volume accelerating"
            )

        previous_ratio = num(
            previous.get(
                "buy_sell_ratio"
            )
        )

        if ratio > previous_ratio:

            signals.append(
                "buy pressure improving"
            )

    # --------------------------------------------------------
    # SIGNAL 5: Strong current buy pressure
    # --------------------------------------------------------

    if ratio >= 1.50:

        signals.append(
            "strong buy pressure"
        )

    # --------------------------------------------------------
    # Signal strength
    # --------------------------------------------------------

    return {
        "signals": signals,
        "signal_count": len(signals),
        "qualified":
            len(signals) >= 1
    }


# ============================================================
# QUALIFICATION
# ============================================================

def qualify(
    m: Dict[str, Any],
    previous: Optional[Dict[str, Any]]
) -> Dict[str, Any]:

    failures = hard_filters(m)

    if failures:

        return {
            "qualified": False,
            "reason": failures,
            "signals": []
        }

    recovery = recovery_signals(
        m,
        previous
    )

    if not recovery["qualified"]:

        return {
            "qualified": False,
            "reason": [
                "no recovery/momentum signal"
            ],
            "signals": []
        }

    return {
        "qualified": True,
        "reason": [],
        "signals": recovery[
            "signals"
        ]
    }


# ============================================================
# TRACKING
# ============================================================

def observation_label(
    count: int
) -> str:

    if count == 1:
        return "ULTRA"

    if count == 2:
        return "STRONG"

    return "NORMAL"


def add_observation(
    state: Dict[str, Any],
    m: Dict[str, Any],
    signals: List[str]
):

    address = m[
        "address"
    ]

    tracked = state[
        "tracked"
    ]

    current_time = now_ts()

    if address not in tracked:

        tracked[address] = {
            "address": address,
            "symbol": m["symbol"],
            "name": m["name"],
            "first_seen": current_time,
            "last_seen": current_time,
            "observations": 0,
            "snapshots": [],
            "signals": []
        }

    item = tracked[
        address
    ]

    item[
        "last_seen"
    ] = current_time

    item[
        "observations"
    ] += 1

    snapshot = {
        "timestamp": current_time,
        "mc": m["mc"],
        "liquidity": m["liquidity"],
        "volume_5m": m["volume_5m"],
        "volume_1h": m["volume_1h"],
        "price_5m": m["price_5m"],
        "price_1h": m["price_1h"],
        "price_6h": m["price_6h"],
        "buys_5m": m["buys_5m"],
        "sells_5m": m["sells_5m"],
        "buy_sell_ratio":
            m["buy_sell_ratio"],
        "volume_mc_ratio":
            m["volume_mc_ratio"]
    }

    item[
        "snapshots"
    ].append(snapshot)

    for signal in signals:

        if signal not in item[
            "signals"
        ]:

            item[
                "signals"
            ].append(signal)

    # Keep last 12 hours of observations.
    cutoff = (
        current_time
        - OBSERVATION_WINDOW_HOURS * 3600
    )

    item[
        "snapshots"
    ] = [
        s for s in item[
            "snapshots"
        ]
        if s["timestamp"] >= cutoff
    ]

    # Keep token data current.
    item[
        "latest"
    ] = m

    item[
        "signals"
    ] = item[
        "signals"
    ][-15:]


def get_previous_snapshot(
    state: Dict[str, Any],
    address: str
) -> Optional[Dict[str, Any]]:

    item = state.get(
        "tracked",
        {}
    ).get(address)

    if not item:
        return None

    snapshots = item.get(
        "snapshots",
        []
    )

    if not snapshots:
        return None

    return snapshots[-1]


def cleanup_tracking(
    state: Dict[str, Any]
):

    tracked = state.get(
        "tracked",
        {}
    )

    now = now_ts()

    remove = []

    for address, item in tracked.items():

        last_seen = num(
            item.get(
                "last_seen"
            )
        )

        if (
            now - last_seen
            > OBSERVATION_WINDOW_HOURS * 3600
        ):

            remove.append(
                address
            )

    for address in remove:

        tracked.pop(
            address,
            None
        )

    # Hard cap
    if len(tracked) > MAX_TRACKED_TOKENS:

        ordered = sorted(
            tracked.items(),
            key=lambda x:
                num(
                    x[1].get(
                        "last_seen"
                    )
                )
        )

        excess = (
            len(tracked)
            - MAX_TRACKED_TOKENS
        )

        for address, _ in ordered[
            :excess
        ]:

            tracked.pop(
                address,
                None
            )


# ============================================================
# ALERT
# ============================================================

def build_alert(
    m: Dict[str, Any],
    item: Dict[str, Any],
    signals: List[str]
) -> str:

    observations = int(
        item.get(
            "observations",
            1
        )
    )

    label = observation_label(
        observations
    )

    liquidity_status = (
        "PREFERRED"
        if m["liquidity"]
        >= PREFERRED_LIQUIDITY
        else "PASS"
    )

    return (
        "🚨 RUNNER BOT V5.0\n\n"

        f"🔥 {label} | "
        f"{m['symbol']}\n"

        f"{m['name']}\n\n"

        "RECOVERY SETUP\n"
        f"MC: {money(m['mc'])}\n"
        f"Liquidity: "
        f"{money(m['liquidity'])} "
        f"({liquidity_status})\n"
        f"Age: "
        f"{fmt_age(m['age_hours'])}\n\n"

        "FLOW\n"
        f"5m Volume: "
        f"{money(m['volume_5m'])}\n"
        f"Volume/MC: "
        f"{pct(m['volume_mc_ratio'] * 100)}\n"
        f"5m Buys: "
        f"{int(m['buys_5m'])}\n"
        f"5m Sells: "
        f"{int(m['sells_5m'])}\n"
        f"Buy/Sell: "
        f"{m['buy_sell_ratio']:.2f}\n\n"

        "PRICE\n"
        f"5m: "
        f"{m['price_5m']:+.1f}%\n"
        f"1h: "
        f"{m['price_1h']:+.1f}%\n"
        f"6h: "
        f"{m['price_6h']:+.1f}%\n"
        f"24h: "
        f"{m['price_24h']:+.1f}%\n\n"

        "CONFIRMATIONS\n"
        + "\n".join(
            f"✓ {signal}"
            for signal in signals
        )
        + "\n\n"

        f"Observations: "
        f"{observations}\n"

        f"Dex: {m['dex']}\n\n"

        f"Chart:\n"
        f"{m['url']}\n\n"

        f"Contract:\n"
        f"{m['address']}\n\n"

        "⚠️ Scanner signal only. "
        "Always verify the contract and "
        "liquidity before trading."
    )


# ============================================================
# DISCOVERY + VALIDATION
# ============================================================

def discover_candidates(
    state: Dict[str, Any]
):

    print(
        "DexScreener discovery: "
        "searching recovery candidates..."
    )

    addresses = discover_sources()

    print(
        f"DexScreener source tokens: "
        f"{len(addresses)}"
    )

    candidates = []

    for address in addresses:

        pairs = fetch_token_pairs(
            address
        )

        pair = select_best_pair(
            pairs
        )

        if not pair:
            continue

        m = extract_metrics(
            pair
        )

        if not m:
            continue

        failures = hard_filters(
            m
        )

        if failures:
            continue

        candidates.append(
            m
        )

        # Keep discovery from hammering
        # the API unnecessarily.
        time.sleep(0.05)

    # Highest current activity first.
    candidates.sort(
        key=lambda m: (
            m["volume_mc_ratio"],
            m["buy_sell_ratio"],
            m["liquidity"]
        ),
        reverse=True
    )

    return candidates


def validate_candidates(
    state: Dict[str, Any]
):

    tracked = state.get(
        "tracked",
        {}
    )

    if not tracked:
        return

    addresses = list(
        tracked.keys()
    )[:MAX_VALIDATIONS_PER_CYCLE]

    print(
        f"Validating {len(addresses)} "
        "tracked tokens..."
    )

    for address in addresses:

        pairs = fetch_token_pairs(
            address
        )

        pair = select_best_pair(
            pairs
        )

        if not pair:
            continue

        m = extract_metrics(
            pair
        )

        if not m:
            continue

        previous = get_previous_snapshot(
            state,
            address
        )

        result = qualify(
            m,
            previous
        )

        if result["qualified"]:

            add_observation(
                state,
                m,
                result["signals"]
            )

            item = state[
                "tracked"
            ][address]

            # Alert on every new qualifying
            # observation.
            alert = build_alert(
                m,
                item,
                result["signals"]
            )

            broadcast(
                state,
                alert
            )

            state[
                "total_alerts"
            ] += 1

            print(
                f"ALERT {m['symbol']} | "
                f"obs={item['observations']}"
            )

        else:

            # Keep a snapshot only when
            # token remains in the broad
            # tracking area.
            if (
                m["mc"] >= MIN_MC
                and m["mc"] <= MAX_MC
                and m["liquidity"]
                >= MIN_LIQUIDITY
            ):

                if previous:

                    previous_volume = num(
                        previous.get(
                            "volume_5m"
                        )
                    )

                    if (
                        m["volume_5m"]
                        > previous_volume
                    ):

                        add_observation(
                            state,
                            m,
                            []
                        )

        time.sleep(0.10)

    cleanup_tracking(
        state
    )

    save_state(
        state
    )


def add_new_candidates(
    state: Dict[str, Any],
    candidates: List[Dict[str, Any]]
):

    for m in candidates:

        address = m[
            "address"
        ]

        previous = get_previous_snapshot(
            state,
            address
        )

        result = qualify(
            m,
            previous
        )

        if not result["qualified"]:
            continue

        add_observation(
            state,
            m,
            result["signals"]
        )

        item = state[
            "tracked"
        ][address]

        alert = build_alert(
            m,
            item,
            result["signals"]
        )

        broadcast(
            state,
            alert
        )

        state[
            "total_alerts"
        ] += 1

        print(
            f"NEW ALERT {m['symbol']} | "
            f"obs={item['observations']}"
        )

    save_state(
        state
    )


# ============================================================
# MAIN
# ============================================================

def print_banner():

    print("=" * 60)
    print(
        f"RUNNER BOT {BOT_VERSION}"
    )
    print(
        "DEXSCREENER RECOVERY RUNNER"
    )
    print("=" * 60)

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
        "5m Volume/MC: >= 5%"
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

    print("=" * 60)


def main():

    print_banner()

    if not TELEGRAM_BOT_TOKEN:

        print(
            "WARNING: "
            "TELEGRAM_BOT_TOKEN is not set."
        )

    state = load_state()

    print(
        "Bot loop started."
    )

    last_discovery = num(
        state.get(
            "last_discovery"
        )
    )

    last_validation = num(
        state.get(
            "last_validation"
        )
    )

    while True:

        try:

            process_telegram_updates(
                state
            )

            state[
                "total_scans"
            ] += 1

            current = now_ts()

            # ------------------------------------------------
            # Discovery
            # ------------------------------------------------

            if (
                current - last_discovery
                >= DISCOVERY_INTERVAL_SECONDS
            ):

                candidates = (
                    discover_candidates(
                        state
                    )
                )

                print(
                    "DexScreener candidates: "
                    f"{len(candidates)}"
                )

                add_new_candidates(
                    state,
                    candidates
                )

                last_discovery = current

                state[
                    "last_discovery"
                ] = current

                save_state(
                    state
                )

            # ------------------------------------------------
            # Validation
            # ------------------------------------------------

            if (
                current - last_validation
                >= VALIDATION_INTERVAL_SECONDS
            ):

                validate_candidates(
                    state
                )

                last_validation = current

                state[
                    "last_validation"
                ] = current

                save_state(
                    state
                )

            tracked_count = len(
                state.get(
                    "tracked",
                    {}
                )
            )

            print(
                "Scan complete | "
                f"tracked={tracked_count} | "
                f"alerts="
                f"{state['total_alerts']}"
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
                f"MAIN LOOP ERROR: {e}"
            )

            save_state(
                state
            )

            time.sleep(5)


if __name__ == "__main__":
    main()
