import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V5.2
# DEXSCREENER RECOVERY / 2-CONFIRMATION
# ============================================================

BOT_VERSION = "V5.2-DEXSCREENER-2CONFIRM"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

STATE_FILE = "runner_state.json"


# ============================================================
# ENVIRONMENT
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

SCAN_INTERVAL_SECONDS = int(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)

DISCOVERY_INTERVAL_SECONDS = int(
    os.getenv("DISCOVERY_INTERVAL_SECONDS", "300")
)

VALIDATION_INTERVAL_SECONDS = int(
    os.getenv("VALIDATION_INTERVAL_SECONDS", "300")
)

HEATING_ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "true").lower()
    == "true"
)


# ============================================================
# V5.2 STRATEGY FILTERS
# ============================================================

# AGE
MIN_AGE_HOURS = 8.0
MAX_AGE_HOURS = 72.0


# MARKET CAP
MIN_MC = 40_000
MAX_MC = 350_000


# LIQUIDITY
MIN_LIQUIDITY = 35_000
PREFERRED_LIQUIDITY = 50_000


# INITIAL TRACKING
MIN_INITIAL_VOLUME_MC_RATIO = 0.015


# RECOVERY
MIN_RECOVERY_VOLUME_MC_RATIO = 0.06

MIN_BUY_SELL_RATIO = 1.40
CURRENT_BUY_SELL_RATIO = 1.45

MIN_5M_PRICE_CHANGE = 3.0
MAX_5M_PRICE_CHANGE = 35.0


# VOLUME EXPANSION
MIN_VOLUME_EXPANSION_RATIO = 0.10


# CONFIRMATION
MIN_CONFIRMATIONS = 2


# TRACKING
TRACKING_WINDOW_HOURS = 8.0
MAX_TRACKED_TOKENS = 150
MAX_VALIDATIONS_PER_CYCLE = 20


# DISCOVERY
MAX_DISCOVERY_ADDRESSES = 100


# ============================================================
# GLOBAL STATE
# ============================================================

state: Dict[str, Any] = {
    "subscribers": [],
    "tokens": {},
    "last_update_id": None,
    "last_discovery": 0,
    "last_validation": 0,
}


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts() -> float:
    return time.time()


def utc_now_string() -> str:
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


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


def clamp_text(value: str, length: int = 80) -> str:
    value = str(value or "")
    if len(value) <= length:
        return value
    return value[:length - 3] + "..."


def format_money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def format_pct(value: float) -> str:
    return f"{value:.1f}%"


# ============================================================
# FILE STATE
# ============================================================

def load_state() -> None:
    global state

    if not os.path.exists(STATE_FILE):
        print("No existing state file. Starting fresh.")
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        if isinstance(loaded, dict):
            state.update(loaded)

        print(
            f"State loaded | "
            f"subscribers={len(state.get('subscribers', []))} | "
            f"tracked={len(state.get('tokens', {}))}"
        )

    except Exception as e:
        print(f"State load error: {e}")


def save_state() -> None:
    temp_file = STATE_FILE + ".tmp"

    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False,
            )

        os.replace(temp_file, STATE_FILE)

    except Exception as e:
        print(f"State save error: {e}")


# ============================================================
# HTTP
# ============================================================

def http_get_json(
    url: str,
    timeout: int = 15
) -> Optional[Any]:

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "RunnerBot/5.2"
                ),
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=timeout
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

            return json.loads(raw)

    except urllib.error.HTTPError as e:
        print(
            f"HTTP error {e.code}: "
            f"{url}"
        )
        return None

    except urllib.error.URLError as e:
        print(
            f"URL error: "
            f"{e}"
        )
        return None

    except Exception as e:
        print(
            f"HTTP error: "
            f"{e}"
        )
        return None


def http_post_json(
    url: str,
    payload: Dict[str, Any],
    timeout: int = 15
) -> Optional[Any]:

    try:
        body = json.dumps(payload).encode(
            "utf-8"
        )

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "User-Agent": "RunnerBot/5.2",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(
            req,
            timeout=timeout
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

            return json.loads(raw)

    except Exception as e:
        print(
            f"POST error: {e}"
        )
        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_url(method: str) -> str:
    return (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/{method}"
    )


def telegram_send(
    chat_id: str,
    text: str
) -> bool:

    if not TELEGRAM_BOT_TOKEN:
        return False

    result = http_post_json(
        telegram_url("sendMessage"),
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    )

    return bool(
        result
        and result.get("ok")
    )


def broadcast(text: str) -> None:

    if not HEATING_ALERTS_ENABLED:
        return

    subscribers = state.get(
        "subscribers",
        []
    )

    for chat_id in list(subscribers):

        try:
            telegram_send(
                str(chat_id),
                text
            )

        except Exception as e:
            print(
                f"Broadcast error "
                f"{chat_id}: {e}"
            )


def telegram_get_updates(
    offset: Optional[int] = None,
    timeout: int = 5
) -> List[Dict[str, Any]]:

    if not TELEGRAM_BOT_TOKEN:
        return []

    params = {
        "timeout": timeout,
        "allowed_updates": [
            "message"
        ],
    }

    if offset is not None:
        params["offset"] = offset

    url = (
        telegram_url("getUpdates")
        + "?"
        + urllib.parse.urlencode(
            params
        )
    )

    result = http_get_json(
        url,
        timeout=timeout + 10
    )

    if not result:
        return []

    if not result.get("ok"):
        return []

    return result.get(
        "result",
        []
    )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def add_subscriber(chat_id: str) -> None:

    subscribers = state.setdefault(
        "subscribers",
        []
    )

    if chat_id not in subscribers:
        subscribers.append(chat_id)

    save_state()


def remove_subscriber(chat_id: str) -> None:

    subscribers = state.setdefault(
        "subscribers",
        []
    )

    if chat_id in subscribers:
        subscribers.remove(chat_id)

    save_state()


def command_status(
    chat_id: str
) -> None:

    tracked = state.get(
        "tokens",
        {}
    )

    message = (
        f"RUNNER BOT {BOT_VERSION}\n\n"
        f"Status: ONLINE\n"
        f"Tracked: {len(tracked)}\n"
        f"Subscribers: "
        f"{len(state.get('subscribers', []))}\n\n"
        f"Strategy:\n"
        f"Age: 8h–72h\n"
        f"MC: $40K–$350K\n"
        f"Liquidity: $35K+\n"
        f"Recovery Vol/MC: 6%+\n"
        f"B/S: 1.45+\n"
        f"5m price: +3% to +35%\n"
        f"Confirmation: 2 scans"
    )

    telegram_send(
        chat_id,
        message
    )


def command_tracking(
    chat_id: str
) -> None:

    tokens = state.get(
        "tokens",
        {}
    )

    if not tokens:
        telegram_send(
            chat_id,
            "No tokens currently being tracked."
        )
        return

    lines = [
        f"TRACKING ({len(tokens)})",
        ""
    ]

    for address, token in list(
        tokens.items()
    ):

        streak = safe_int(
            token.get(
                "confirmation_streak",
                0
            )
        )

        name = token.get(
            "symbol",
            address[:8]
        )

        mc = safe_float(
            token.get(
                "mc",
                0
            )
        )

        lines.append(
            f"{name} | "
            f"MC {format_money(mc)} | "
            f"Confirm {streak}/2"
        )

    telegram_send(
        chat_id,
        "\n".join(lines)
    )


def handle_command(
    chat_id: str,
    command: str
) -> None:

    command = command.lower().strip()

    if command.startswith("/start"):

        add_subscriber(chat_id)

        telegram_send(
            chat_id,
            (
                f"Runner Bot {BOT_VERSION} "
                f"is online.\n\n"
                f"You are now subscribed to "
                f"runner alerts."
            )
        )

        return

    if command.startswith("/stop"):

        remove_subscriber(chat_id)

        telegram_send(
            chat_id,
            "Runner alerts disabled."
        )

        return

    if command.startswith("/alerts"):

        enabled = HEATING_ALERTS_ENABLED

        telegram_send(
            chat_id,
            (
                "Alerts are "
                + (
                    "ENABLED."
                    if enabled
                    else "DISABLED."
                )
            )
        )

        return

    if command.startswith("/status"):

        command_status(chat_id)
        return

    if command.startswith("/tracking"):

        command_tracking(chat_id)
        return


def process_telegram_updates() -> None:

    offset = state.get(
        "last_update_id"
    )

    updates = telegram_get_updates(
        offset=(
            int(offset) + 1
            if offset is not None
            else None
        ),
        timeout=3
    )

    for update in updates:

        try:

            update_id = update.get(
                "update_id"
            )

            if update_id is not None:
                state[
                    "last_update_id"
                ] = update_id

            message = update.get(
                "message"
            )

            if not message:
                continue

            chat = message.get(
                "chat",
                {}
            )

            chat_id = chat.get(
                "id"
            )

            text = (
                message.get(
                    "text"
                )
                or ""
            ).strip()

            if chat_id is None:
                continue

            if text.startswith("/"):
                handle_command(
                    str(chat_id),
                    text
                )

        except Exception as e:

            print(
                f"Telegram update error: "
                f"{e}"
            )

    save_state()


# ============================================================
# DEXSCREENER
# ============================================================

def get_latest_profiles() -> List[Dict[str, Any]]:

    url = (
        DEX_BASE
        + "/token-profiles/latest/v1"
    )

    data = http_get_json(url)

    if not isinstance(data, list):
        return []

    return data


def get_latest_boosts() -> List[Dict[str, Any]]:

    url = (
        DEX_BASE
        + "/token-boosts/latest/v1"
    )

    data = http_get_json(url)

    if not isinstance(data, list):
        return []

    return data


def get_token_pairs(
    address: str
) -> List[Dict[str, Any]]:

    encoded = urllib.parse.quote(
        address,
        safe=""
    )

    url = (
        DEX_BASE
        + "/tokens/v1/solana/"
        + encoded
    )

    data = http_get_json(url)

    if not isinstance(data, list):
        return []

    return data


def choose_best_pair(
    pairs: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:

    solana_pairs = [
        p
        for p in pairs
        if str(
            p.get("chainId", "")
        ).lower()
        == "solana"
    ]

    if not solana_pairs:
        return None

    def liquidity_value(
        pair: Dict[str, Any]
    ) -> float:

        liquidity = pair.get(
            "liquidity"
        ) or {}

        return safe_float(
            liquidity.get(
                "usd"
            )
        )

    solana_pairs.sort(
        key=liquidity_value,
        reverse=True
    )

    return solana_pairs[0]


# ============================================================
# TOKEN PARSING
# ============================================================

def parse_pair(
    pair: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    if not pair:
        return None

    address = (
        pair.get(
            "baseToken",
            {}
        ).get(
            "address"
        )
    )

    symbol = (
        pair.get(
            "baseToken",
            {}
        ).get(
            "symbol"
        )
        or "UNKNOWN"
    )

    name = (
        pair.get(
            "baseToken",
            {}
        ).get(
            "name"
        )
        or symbol
    )

    if not address:
        return None

    liquidity = pair.get(
        "liquidity"
    ) or {}

    volume = pair.get(
        "volume"
    ) or {}

    price_change = pair.get(
        "priceChange"
    ) or {}

    txns = pair.get(
        "txns"
    ) or {}

    m5_txns = txns.get(
        "m5"
    ) or {}

    h1_txns = txns.get(
        "h1"
    ) or {}

    m5_buys = safe_int(
        m5_txns.get(
            "buys"
        )
    )

    m5_sells = safe_int(
        m5_txns.get(
            "sells"
        )
    )

    h1_buys = safe_int(
        h1_txns.get(
            "buys"
        )
    )

    h1_sells = safe_int(
        h1_txns.get(
            "sells"
        )
    )

    m5_volume = safe_float(
        volume.get(
            "m5"
        )
    )

    h1_volume = safe_float(
        volume.get(
            "h1"
        )
    )

    mc = safe_float(
        pair.get(
            "marketCap"
        )
    )

    if mc <= 0:
        mc = safe_float(
            pair.get(
                "fdv"
            )
        )

    liq = safe_float(
        liquidity.get(
            "usd"
        )
    )

    created_at = safe_int(
        pair.get(
            "pairCreatedAt"
        )
    )

    if created_at > 0:
        age_hours = (
            max(
                0,
                int(time.time() * 1000)
                - created_at
            )
            / 3_600_000
        )
    else:
        age_hours = 0.0

    if m5_sells > 0:
        buy_sell_ratio = (
            m5_buys
            / m5_sells
        )
    elif m5_buys > 0:
        buy_sell_ratio = float(
            m5_buys
        )
    else:
        buy_sell_ratio = 0.0

    if h1_sells > 0:
        h1_buy_sell_ratio = (
            h1_buys
            / h1_sells
        )
    elif h1_buys > 0:
        h1_buy_sell_ratio = float(
            h1_buys
        )
    else:
        h1_buy_sell_ratio = 0.0

    volume_mc_ratio = 0.0

    if mc > 0:
        volume_mc_ratio = (
            m5_volume / mc
        )

    m5_h1_ratio = 0.0

    if h1_volume > 0:
        m5_h1_ratio = (
            m5_volume / h1_volume
        )

    return {
        "address": address,
        "symbol": symbol,
        "name": name,

        "url": (
            pair.get(
                "url"
            )
            or ""
        ),

        "mc": mc,
        "liquidity": liq,
        "age_hours": age_hours,

        "m5_volume": m5_volume,
        "h1_volume": h1_volume,

        "m5_volume_mc_ratio": volume_mc_ratio,
        "m5_h1_volume_ratio": m5_h1_ratio,

        "m5_price_change": safe_float(
            price_change.get(
                "m5"
            )
        ),

        "h1_price_change": safe_float(
            price_change.get(
                "h1"
            )
        ),

        "h6_price_change": safe_float(
            price_change.get(
                "h6"
            )
        ),

        "h24_price_change": safe_float(
            price_change.get(
                "h24"
            )
        ),

        "m5_buys": m5_buys,
        "m5_sells": m5_sells,

        "h1_buys": h1_buys,
        "h1_sells": h1_sells,

        "buy_sell_ratio": buy_sell_ratio,
        "h1_buy_sell_ratio": h1_buy_sell_ratio,
    }


def get_token_data(
    address: str
) -> Optional[Dict[str, Any]]:

    pairs = get_token_pairs(
        address
    )

    pair = choose_best_pair(
        pairs
    )

    if not pair:
        return None

    return parse_pair(
        pair
    )


# ============================================================
# DISCOVERY
# ============================================================

def collect_discovery_addresses() -> List[str]:

    addresses = []

    profiles = get_latest_profiles()

    boosts = get_latest_boosts()

    for item in profiles + boosts:

        chain = str(
            item.get(
                "chainId",
                ""
            )
        ).lower()

        if chain != "solana":
            continue

        address = (
            item.get(
                "tokenAddress"
            )
            or item.get(
                "address"
            )
        )

        if not address:
            continue

        if address not in addresses:
            addresses.append(address)

        if len(addresses) >= MAX_DISCOVERY_ADDRESSES:
            break

    return addresses


def initial_filter(
    data: Dict[str, Any]
) -> Tuple[bool, List[str]]:

    reasons = []

    age = safe_float(
        data.get(
            "age_hours"
        )
    )

    mc = safe_float(
        data.get(
            "mc"
        )
    )

    liquidity = safe_float(
        data.get(
            "liquidity"
        )
    )

    volume_ratio = safe_float(
        data.get(
            "m5_volume_mc_ratio"
        )
    )

    if age < MIN_AGE_HOURS:
        reasons.append(
            f"age {age:.1f}h < {MIN_AGE_HOURS}h"
        )

    if age > MAX_AGE_HOURS:
        reasons.append(
            f"age {age:.1f}h > {MAX_AGE_HOURS}h"
        )

    if mc < MIN_MC:
        reasons.append(
            f"MC {format_money(mc)} < "
            f"{format_money(MIN_MC)}"
        )

    if mc > MAX_MC:
        reasons.append(
            f"MC {format_money(mc)} > "
            f"{format_money(MAX_MC)}"
        )

    if liquidity < MIN_LIQUIDITY:
        reasons.append(
            f"liq {format_money(liquidity)} < "
            f"{format_money(MIN_LIQUIDITY)}"
        )

    if volume_ratio < MIN_INITIAL_VOLUME_MC_RATIO:
        reasons.append(
            f"vol/MC "
            f"{volume_ratio * 100:.2f}% < "
            f"{MIN_INITIAL_VOLUME_MC_RATIO * 100:.2f}%"
        )

    return (
        len(reasons) == 0,
        reasons
    )


def discover_tokens() -> None:

    print(
        f"\n[{utc_now_string()}] "
        f"Starting discovery..."
    )

    addresses = (
        collect_discovery_addresses()
    )

    print(
        f"Discovery source addresses: "
        f"{len(addresses)}"
    )

    tokens = state.setdefault(
        "tokens",
        {}
    )

    stats = {
        "source": len(addresses),
        "invalid": 0,
        "age_low": 0,
        "age_high": 0,
        "mc_low": 0,
        "mc_high": 0,
        "liq_low": 0,
        "volume_low": 0,
        "duplicates": 0,
        "new_tracking": 0,
    }

    for address in addresses:

        if address in tokens:
            stats[
                "duplicates"
            ] += 1

            continue

        data = get_token_data(
            address
        )

        if not data:
            stats[
                "invalid"
            ] += 1

            continue

        age = data[
            "age_hours"
        ]

        mc = data[
            "mc"
        ]

        liquidity = data[
            "liquidity"
        ]

        volume_ratio = data[
            "m5_volume_mc_ratio"
        ]

        if age < MIN_AGE_HOURS:
            stats[
                "age_low"
            ] += 1

        if age > MAX_AGE_HOURS:
            stats[
                "age_high"
            ] += 1

        if mc < MIN_MC:
            stats[
                "mc_low"
            ] += 1

        if mc > MAX_MC:
            stats[
                "mc_high"
            ] += 1

        if liquidity < MIN_LIQUIDITY:
            stats[
                "liq_low"
            ] += 1

        if volume_ratio < MIN_INITIAL_VOLUME_MC_RATIO:
            stats[
                "volume_low"
            ] += 1

        passed, reasons = initial_filter(
            data
        )

        if not passed:
            continue

        if (
            len(tokens)
            >= MAX_TRACKED_TOKENS
        ):
            print(
                "Maximum tracked token "
                "capacity reached."
            )
            break

        tokens[address] = {
            "address": address,
            "symbol": data[
                "symbol"
            ],
            "name": data[
                "name"
            ],

            "tracked_at": now_ts(),

            "first_seen_age": data[
                "age_hours"
            ],

            "mc": data[
                "mc"
            ],

            "liquidity": data[
                "liquidity"
            ],

            "last_data": data,

            "previous_snapshot": None,

            "confirmation_streak": 0,

            "alerted_for_cycle": False,

            "last_validation": 0,

            "validations": 0,

            "last_alert": 0,
        }

        stats[
            "new_tracking"
        ] += 1

        print(
            f"TRACKING NEW | "
            f"{data['symbol']} | "
            f"MC {format_money(data['mc'])} | "
            f"liq {format_money(data['liquidity'])} | "
            f"age {data['age_hours']:.1f}h | "
            f"vol/MC "
            f"{data['m5_volume_mc_ratio'] * 100:.2f}%"
        )

    state[
        "last_discovery"
    ] = now_ts()

    save_state()

    print(
        "Discovery diagnostics | "
        f"source={stats['source']} | "
        f"invalid={stats['invalid']} | "
        f"age<8h={stats['age_low']} | "
        f"age>72h={stats['age_high']} | "
        f"MC<$40k={stats['mc_low']} | "
        f"MC>$350k={stats['mc_high']} | "
        f"liq<$35k={stats['liq_low']} | "
        f"volume<1.5%={stats['volume_low']} | "
        f"duplicates={stats['duplicates']} | "
        f"new_tracking={stats['new_tracking']}"
    )


# ============================================================
# RECOVERY LOGIC
# ============================================================

def volume_expanding(
    current: Dict[str, Any],
    previous: Optional[Dict[str, Any]]
) -> Tuple[bool, str]:

    # Method 1:
    # current 5m volume is at least 10% of 1h volume
    m5_h1_ratio = safe_float(
        current.get(
            "m5_h1_volume_ratio"
        )
    )

    if m5_h1_ratio >= MIN_VOLUME_EXPANSION_RATIO:
        return (
            True,
            (
                f"m5/h1 "
                f"{m5_h1_ratio * 100:.1f}%"
            )
        )

    # Method 2:
    # Current 5m volume increased at least 10%
    # compared with the previous validation snapshot.
    if previous:

        previous_volume = safe_float(
            previous.get(
                "m5_volume"
            )
        )

        current_volume = safe_float(
            current.get(
                "m5_volume"
            )
        )

        if previous_volume > 0:

            expansion = (
                current_volume
                / previous_volume
                - 1
            )

            if expansion >= MIN_VOLUME_EXPANSION_RATIO:
                return (
                    True,
                    (
                        f"5m volume "
                        f"+{expansion * 100:.1f}%"
                    )
                )

    return (
        False,
        (
            f"m5/h1 "
            f"{m5_h1_ratio * 100:.1f}%"
        )
    )


def recovery_check(
    current: Dict[str, Any],
    previous: Optional[Dict[str, Any]]
) -> Tuple[bool, Dict[str, Any]]:

    price = safe_float(
        current.get(
            "m5_price_change"
        )
    )

    bs = safe_float(
        current.get(
            "buy_sell_ratio"
        )
    )

    volume_ratio = safe_float(
        current.get(
            "m5_volume_mc_ratio"
        )
    )

    buys = safe_int(
        current.get(
            "m5_buys"
        )
    )

    sells = safe_int(
        current.get(
            "m5_sells"
        )
    )

    expanding, expansion_reason = (
        volume_expanding(
            current,
            previous
        )
    )

    price_ok = (
        price >= MIN_5M_PRICE_CHANGE
        and
        price <= MAX_5M_PRICE_CHANGE
    )

    bs_ok = (
        buys > sells
        and
        bs >= CURRENT_BUY_SELL_RATIO
    )

    volume_ok = (
        volume_ratio
        >= MIN_RECOVERY_VOLUME_MC_RATIO
    )

    previous_bs_ok = False

    if previous:

        previous_bs = safe_float(
            previous.get(
                "buy_sell_ratio"
            )
        )

        previous_bs_ok = (
            previous_bs
            >= MIN_BUY_SELL_RATIO
        )

    checks = {
        "price_ok": price_ok,
        "bs_ok": bs_ok,
        "volume_ok": volume_ok,
        "volume_expanding": expanding,
        "previous_bs_ok": previous_bs_ok,

        "price": price,
        "bs": bs,
        "volume_ratio": volume_ratio,

        "buys": buys,
        "sells": sells,

        "expansion_reason":
            expansion_reason,

        "previous_bs": (
            safe_float(
                previous.get(
                    "buy_sell_ratio"
                )
            )
            if previous
            else 0.0
        ),
    }

    # The current scan must satisfy:
    #
    # 1. price +3% to +35%
    # 2. B/S >= 1.45
    # 3. volume/MC >= 6%
    # 4. volume expansion
    #
    # For confirmation 2+, the previous
    # scan must also have B/S >= 1.40.

    base_pass = (
        price_ok
        and
        bs_ok
        and
        volume_ok
        and
        expanding
    )

    return (
        base_pass,
        checks
    )


# ============================================================
# VALIDATION LOGGING
# ============================================================

def print_validation(
    token: Dict[str, Any],
    current: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
    passed: bool,
    checks: Dict[str, Any]
) -> None:

    symbol = current.get(
        "symbol"
    ) or token.get(
        "symbol",
        "UNKNOWN"
    )

    streak = safe_int(
        token.get(
            "confirmation_streak",
            0
        )
    )

    price = checks[
        "price"
    ]

    bs = checks[
        "bs"
    ]

    vol = checks[
        "volume_ratio"
    ]

    prev_bs = checks[
        "previous_bs"
    ]

    print(
        "\n"
        f"VALIDATION | {symbol}\n"
        f"  MC: "
        f"{format_money(current['mc'])}\n"
        f"  Liquidity: "
        f"{format_money(current['liquidity'])}\n"
        f"  Age: "
        f"{current['age_hours']:.1f}h\n"
        f"  5m Price: "
        f"{price:+.1f}% "
        f"{'PASS' if checks['price_ok'] else 'FAIL'}\n"
        f"  B/S: "
        f"{bs:.2f} "
        f"{'PASS' if checks['bs_ok'] else 'FAIL'}\n"
        f"  5m Vol/MC: "
        f"{vol * 100:.2f}% "
        f"{'PASS' if checks['volume_ok'] else 'FAIL'}\n"
        f"  Volume expansion: "
        f"{checks['expansion_reason']} "
        f"{'PASS' if checks['volume_expanding'] else 'FAIL'}\n"
        f"  Previous B/S: "
        f"{prev_bs:.2f} "
        f"{'PASS' if checks['previous_bs_ok'] else 'N/A'}\n"
        f"  Current confirmation: "
        f"{'PASS' if passed else 'FAIL'}\n"
        f"  Streak: "
        f"{streak}/{MIN_CONFIRMATIONS}\n"
    )


# ============================================================
# ALERT
# ============================================================

def send_runner_alert(
    data: Dict[str, Any],
    streak: int
) -> None:

    symbol = data.get(
        "symbol",
        "UNKNOWN"
    )

    name = data.get(
        "name",
        symbol
    )

    address = data.get(
        "address",
        ""
    )

    mc = safe_float(
        data.get(
            "mc"
        )
    )

    liquidity = safe_float(
        data.get(
            "liquidity"
        )
    )

    age = safe_float(
        data.get(
            "age_hours"
        )
    )

    price = safe_float(
        data.get(
            "m5_price_change"
        )
    )

    volume = safe_float(
        data.get(
            "m5_volume"
        )
    )

    volume_ratio = safe_float(
        data.get(
            "m5_volume_mc_ratio"
        )
    )

    bs = safe_float(
        data.get(
            "buy_sell_ratio"
        )
    )

    buys = safe_int(
        data.get(
            "m5_buys"
        )
    )

    sells = safe_int(
        data.get(
            "m5_sells"
        )
    )

    url = data.get(
        "url",
        ""
    )

    message = (
        "🔥 RUNNER RECOVERY CONFIRMED\n\n"

        f"🪙 {symbol}\n"
        f"{clamp_text(name, 60)}\n\n"

        f"💰 MC: {format_money(mc)}\n"
        f"💧 Liquidity: {format_money(liquidity)}\n"
        f"⏱ Age: {age:.1f}h\n\n"

        f"📈 5m Price: +{price:.1f}%\n"
        f"📊 5m Volume: {format_money(volume)}\n"
        f"📊 Vol/MC: {volume_ratio * 100:.2f}%\n\n"

        f"🟢 Buys: {buys}\n"
        f"🔴 Sells: {sells}\n"
        f"⚖️ B/S: {bs:.2f}\n\n"

        f"✅ Confirmation: {streak}/{MIN_CONFIRMATIONS}\n"
        f"⚡ Recovery confirmed across consecutive scans\n\n"

        f"Contract:\n"
        f"{address}\n\n"

        f"DexScreener:\n"
        f"{url}\n\n"

        "⚠️ Automated signal. "
        "Not financial advice."
    )

    broadcast(
        message
    )


# ============================================================
# VALIDATE TRACKED TOKENS
# ============================================================

def validate_tokens() -> None:

    tokens = state.get(
        "tokens",
        {}
    )

    if not tokens:
        print(
            f"[{utc_now_string()}] "
            "Validation skipped | tracked=0"
        )

        state[
            "last_validation"
        ] = now_ts()

        save_state()

        return

    print(
        f"\n[{utc_now_string()}] "
        f"Starting validation | "
        f"tracked={len(tokens)}"
    )

    addresses = list(
        tokens.keys()
    )

    # Oldest validations first
    addresses.sort(
        key=lambda a:
            safe_float(
                tokens[a].get(
                    "last_validation",
                    0
                )
            )
    )

    addresses = addresses[
        :MAX_VALIDATIONS_PER_CYCLE
    ]

    alerts = 0
    removed = 0

    for address in addresses:

        token = tokens.get(
            address
        )

        if not token:
            continue

        tracked_at = safe_float(
            token.get(
                "tracked_at"
            )
        )

        age_since_tracking = (
            now_ts()
            - tracked_at
        ) / 3600

        # ----------------------------------------------------
        # Remove after 8h observation window
        # ----------------------------------------------------

        if (
            age_since_tracking
            >= TRACKING_WINDOW_HOURS
        ):

            symbol = token.get(
                "symbol",
                address[:8]
            )

            print(
                f"EXPIRED | "
                f"{symbol} | "
                f"tracked "
                f"{age_since_tracking:.1f}h"
            )

            del tokens[address]

            removed += 1

            continue

        current = get_token_data(
            address
        )

        token[
            "last_validation"
        ] = now_ts()

        token[
            "validations"
        ] = safe_int(
            token.get(
                "validations",
                0
            )
        ) + 1

        if not current:

            token[
                "confirmation_streak"
            ] = 0

            token[
                "previous_snapshot"
            ] = None

            print(
                f"VALIDATION INVALID | "
                f"{token.get('symbol', address[:8])}"
            )

            continue

        previous = token.get(
            "previous_snapshot"
        )

        passed, checks = recovery_check(
            current,
            previous
        )

        # ----------------------------------------------------
        # Confirmation logic
        # ----------------------------------------------------

        if passed:

            if (
                safe_int(
                    token.get(
                        "confirmation_streak",
                        0
                    )
                ) == 0
            ):
                token[
                    "confirmation_streak"
                ] = 1

            else:
                token[
                    "confirmation_streak"
                ] = (
                    safe_int(
                        token.get(
                            "confirmation_streak",
                            0
                        )
                    )
                    + 1
                )

        else:

            token[
                "confirmation_streak"
            ] = 0

            # A failed recovery cycle allows
            # a future fresh recovery to trigger.
            token[
                "alerted_for_cycle"
            ] = False

        streak = safe_int(
            token.get(
                "confirmation_streak",
                0
            )
        )

        # ----------------------------------------------------
        # Detailed diagnostics
        # ----------------------------------------------------

        print_validation(
            token,
            current,
            previous,
            passed,
            checks
        )

        # ----------------------------------------------------
        # Alert only after 2 consecutive scans
        # ----------------------------------------------------

        if (
            passed
            and
            streak >= MIN_CONFIRMATIONS
            and
            not token.get(
                "alerted_for_cycle",
                False
            )
        ):

            send_runner_alert(
                current,
                streak
            )

            token[
                "alerted_for_cycle"
            ] = True

            token[
                "last_alert"
            ] = now_ts()

            alerts += 1

        # ----------------------------------------------------
        # Save current snapshot for next validation
        # ----------------------------------------------------

        token[
            "previous_snapshot"
        ] = current

        token[
            "last_data"
        ] = current

        token[
            "mc"
        ] = current.get(
            "mc",
            token.get(
                "mc",
                0
            )
        )

        token[
            "liquidity"
        ] = current.get(
            "liquidity",
            token.get(
                "liquidity",
                0
            )
        )

    state[
        "last_validation"
    ] = now_ts()

    save_state()

    print(
        f"Scan complete | "
        f"tracked={len(tokens)} | "
        f"alerts={alerts} | "
        f"removed={removed}"
    )


# ============================================================
# CLEANUP
# ============================================================

def cleanup_invalid_tracking() -> None:

    tokens = state.get(
        "tokens",
        {}
    )

    if not tokens:
        return

    remove_addresses = []

    for address, token in tokens.items():

        tracked_at = safe_float(
            token.get(
                "tracked_at"
            )
        )

        if tracked_at <= 0:
            continue

        age = (
            now_ts()
            - tracked_at
        ) / 3600

        if age >= TRACKING_WINDOW_HOURS:

            remove_addresses.append(
                address
            )

    for address in remove_addresses:

        symbol = tokens[address].get(
            "symbol",
            address[:8]
        )

        print(
            f"CLEANUP | "
            f"{symbol} | observation expired"
        )

        del tokens[address]

    if remove_addresses:
        save_state()


# ============================================================
# STARTUP
# ============================================================

def print_config() -> None:

    print("\n")
    print("=" * 60)
    print(
        f"RUNNER BOT {BOT_VERSION}"
    )
    print("=" * 60)

    print(
        f"Age:              "
        f"{MIN_AGE_HOURS}h - "
        f"{MAX_AGE_HOURS}h"
    )

    print(
        f"Market cap:       "
        f"{format_money(MIN_MC)} - "
        f"{format_money(MAX_MC)}"
    )

    print(
        f"Liquidity:        "
        f"{format_money(MIN_LIQUIDITY)}+"
    )

    print(
        f"Preferred liq:    "
        f"{format_money(PREFERRED_LIQUIDITY)}+"
    )

    print(
        f"Initial Vol/MC:   "
        f"{MIN_INITIAL_VOLUME_MC_RATIO * 100:.2f}%"
    )

    print(
        f"Recovery Vol/MC:  "
        f"{MIN_RECOVERY_VOLUME_MC_RATIO * 100:.2f}%"
    )

    print(
        f"Current B/S:      "
        f"{CURRENT_BUY_SELL_RATIO:.2f}+"
    )

    print(
        f"Previous B/S:     "
        f"{MIN_BUY_SELL_RATIO:.2f}+"
    )

    print(
        f"5m Price:         "
        f"+{MIN_5M_PRICE_CHANGE:.1f}% "
        f"to +{MAX_5M_PRICE_CHANGE:.1f}%"
    )

    print(
        f"Volume expansion: "
        f"{MIN_VOLUME_EXPANSION_RATIO * 100:.0f}%+"
    )

    print(
        f"Confirmation:     "
        f"{MIN_CONFIRMATIONS} consecutive scans"
    )

    print(
        f"Tracking window:  "
        f"{TRACKING_WINDOW_HOURS}h"
    )

    print(
        f"Discovery:         "
        f"every {DISCOVERY_INTERVAL_SECONDS}s"
    )

    print(
        f"Validation:        "
        f"every {VALIDATION_INTERVAL_SECONDS}s"
    )

    print("=" * 60)
    print()


def main() -> None:

    if not TELEGRAM_BOT_TOKEN:

        print(
            "ERROR: "
            "TELEGRAM_BOT_TOKEN is missing."
        )

        return

    load_state()

    print_config()

    # Initial discovery immediately
    discover_tokens()

    state[
        "last_discovery"
    ] = now_ts()

    state[
        "last_validation"
    ] = now_ts()

    save_state()

    last_loop = 0

    print(
        "Runner Bot started."
    )

    while True:

        loop_started = now_ts()

        try:

            # ------------------------------------------------
            # Telegram
            # ------------------------------------------------

            process_telegram_updates()

            # ------------------------------------------------
            # Discovery
            # ------------------------------------------------

            if (
                now_ts()
                - safe_float(
                    state.get(
                        "last_discovery",
                        0
                    )
                )
                >= DISCOVERY_INTERVAL_SECONDS
            ):

                discover_tokens()

            # ------------------------------------------------
            # Validation
            # ------------------------------------------------

            if (
                now_ts()
                - safe_float(
                    state.get(
                        "last_validation",
                        0
                    )
                )
                >= VALIDATION_INTERVAL_SECONDS
            ):

                validate_tokens()

            # ------------------------------------------------
            # Cleanup
            # ------------------------------------------------

            cleanup_invalid_tracking()

        except KeyboardInterrupt:

            print(
                "Stopping Runner Bot..."
            )

            save_state()

            break

        except Exception as e:

            print(
                f"MAIN LOOP ERROR: {e}"
            )

            save_state()

        # ----------------------------------------------------
        # Keep loop around 15 seconds
        # ----------------------------------------------------

        elapsed = (
            now_ts()
            - loop_started
        )

        sleep_for = max(
            1,
            SCAN_INTERVAL_SECONDS
            - elapsed
        )

        time.sleep(
            sleep_for
        )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
