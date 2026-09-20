import json
import os
import time
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V4.7
# SECOND-WAVE GMGN VALIDATED RUNNER
#
# Discovery: DexScreener
# Validation: GMGN official CLI
#
# Strategy:
#   Age:             10h - 72h
#   MC:              $30K - $400K
#   Liquidity:       >= $25K
#   Holders:         >= 300 and growing
#   Dev holding:     <= 4%
#   Top 10:          <= 25%
#   Mint revoked:    REQUIRED
#   Freeze revoked:  REQUIRED
#   Social:          REQUIRED
#   Volume/MC:       meaningful
#   Buy pressure:    positive OR recovering
#
# Confirmation signals:
#   Smart-money accumulation
#   KOL activity
#   Holder growth
#   Volume spike
#   Recent GMGN signals
#   Bundler / insider cleanliness
#
# Observation system:
#   ULTRA  = 1 observation
#   STRONG = 2 observations
#   NORMAL = 3 observations
#
# NOTE:
#   Observation count is confirmation count.
#   It is NOT a quality score.
# ============================================================


BOT_VERSION = "V4.7-SECOND-WAVE-GMGN"


# ============================================================
# ENDPOINTS
# ============================================================

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"


# ============================================================
# FILES
# ============================================================

STATE_FILE = "runner_state_v47.json"


# ============================================================
# TIMING
# ============================================================

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "15"))

GLOBAL_ALERT_COOLDOWN = 60

TOKEN_COOLDOWN = 600

# GMGN is much more expensive than DexScreener.
# We do not query GMGN every 15 seconds for the same token.
GMGN_REFRESH_SECONDS = 300

# Keep enough history to detect a several-hour second wave.
MAX_TOKEN_HISTORY = 1000

# Holder growth observation window.
HOLDER_GROWTH_WINDOW = 3 * 60 * 60


# ============================================================
# SECOND-WAVE HARD FILTER
# ============================================================

MIN_AGE_HOURS = 10
MAX_AGE_HOURS = 72

MIN_MC = 30_000
MAX_MC = 400_000

MIN_LIQUIDITY = 25_000

# 40K is the preferred level from the strategy.
PREFERRED_LIQUIDITY = 40_000

MIN_HOLDERS = 300
PREFERRED_HOLDERS = 500

MAX_DEV_HOLDING_PCT = 4.0
PREFERRED_DEV_HOLDING_PCT = 3.0

MAX_TOP10_PCT = 25.0
PREFERRED_TOP10_PCT = 22.0


# ============================================================
# VOLUME / MC
# ============================================================

# The original strategy says:
# "Volume: meaningful relative to MC"
#
# Because no exact percentage was specified, this is configurable.
# Default implementation:
# 5m volume >= 5% of market cap.
#
# Change this one number later if backtesting shows a better level.
MIN_5M_VOLUME_MC_PCT = float(
    os.getenv("MIN_5M_VOLUME_MC_PCT", "5.0")
)


# ============================================================
# BUY PRESSURE
# ============================================================

# Positive current pressure:
# buys > sells
#
# Recovery:
# current pressure positive AND previous pressure was weaker/negative.
#
# We do not use "net inflow" because DexScreener does not provide
# actual dollar net flow.
# ============================================================


# ============================================================
# GMGN
# ============================================================

GMGN_CHAIN = "sol"

GMGN_ENABLED = os.getenv(
    "GMGN_ENABLED",
    "true"
).lower() == "true"

GMGN_TIMEOUT = int(
    os.getenv("GMGN_TIMEOUT", "30")
)

GMGN_HOLDER_LIMIT = 100


# ============================================================
# TELEGRAM
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    print("WARNING: TELEGRAM_BOT_TOKEN is not configured.")


# ============================================================
# GLOBAL STATE
# ============================================================

state: Dict[str, Any] = {
    "version": BOT_VERSION,

    "alerts_enabled": True,

    "subscribers": [],

    "total_scans": 0,
    "total_discovered": 0,
    "total_prefiltered": 0,
    "total_gmgn_checked": 0,
    "total_gmgn_failed": 0,
    "total_rejected": 0,
    "total_qualified": 0,

    "rejection_counts": {},
    "qualification_counts": {},

    "recent_rejections": [],
    "recent_qualifications": [],

    "tokens": {},

    "recent_scan_history": [],

    "last_alert_time": 0,

    "started_at": time.time(),
}


# ============================================================
# BASIC UTILITIES
# ============================================================

def now_ts() -> float:
    return time.time()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return default

        if isinstance(value, str):
            value = value.replace(",", "").replace("$", "").strip()

        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def pct(part: float, whole: float) -> float:
    if whole <= 0:
        return 0.0

    return (part / whole) * 100.0


def fmt_money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def trim_history(items: List[Any], maximum: int) -> List[Any]:
    if len(items) <= maximum:
        return items

    return items[-maximum:]


# ============================================================
# JSON STATE
# ============================================================

def load_state() -> None:
    global state

    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        if isinstance(loaded, dict):
            for key, value in loaded.items():
                state[key] = value

        print(f"Loaded state: {STATE_FILE}")

    except Exception as e:
        print(f"State load error: {e}")


def save_state() -> None:
    try:
        tmp = STATE_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(tmp, STATE_FILE)

    except Exception as e:
        print(f"State save error: {e}")


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(
    method: str,
    payload: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:

    if not BOT_TOKEN:
        return None

    url = f"{TELEGRAM_BASE}/bot{BOT_TOKEN}/{method}"

    try:
        data = None

        if payload is not None:
            data = urllib.parse.urlencode(payload).encode()

        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type":
                    "application/x-www-form-urlencoded"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:

            raw = response.read().decode()

        return json.loads(raw)

    except Exception as e:
        print(f"Telegram error [{method}]: {e}")
        return None


def send_message(
    chat_id: str,
    text: str
) -> bool:

    result = telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    )

    return bool(
        result and
        result.get("ok")
    )


def broadcast(text: str) -> None:

    if not state.get("alerts_enabled", True):
        return

    subscribers = state.get("subscribers", [])

    for chat_id in subscribers:
        send_message(
            str(chat_id),
            text
        )


# ============================================================
# DEXSCREENER HTTP
# ============================================================

def http_get_json(
    url: str,
    timeout: int = 20
) -> Optional[Any]:

    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "RunnerBot/4.7"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            raw = response.read().decode()

        return json.loads(raw)

    except urllib.error.HTTPError as e:
        print(
            f"HTTP error {e.code}: {url}"
        )

    except Exception as e:
        print(
            f"HTTP error: {e}"
        )

    return None


# ============================================================
# DEXSCREENER DISCOVERY
# ============================================================

def discover_dex_tokens() -> Dict[str, Dict[str, Any]]:

    discovered: Dict[str, Dict[str, Any]] = {}

    urls = [
        f"{DEX_BASE}/token-profiles/latest/v1",
        f"{DEX_BASE}/token-boosts/latest/v1",
    ]

    for url in urls:

        payload = http_get_json(url)

        if not isinstance(payload, list):
            continue

        for item in payload:

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

            if not address:
                continue

            discovered[address] = item

    return discovered


# ============================================================
# DEX TOKEN DATA
# ============================================================

def get_dex_token_data(
    address: str
) -> Optional[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}/latest/dex/tokens/"
        f"{urllib.parse.quote(address)}"
    )

    payload = http_get_json(url)

    if not isinstance(payload, dict):
        return None

    pairs = payload.get("pairs")

    if not isinstance(pairs, list):
        return None

    sol_pairs = [
        p for p in pairs
        if isinstance(p, dict)
        and str(
            p.get("chainId", "")
        ).lower() == "solana"
    ]

    if not sol_pairs:
        return None

    def liquidity_value(pair: Dict[str, Any]) -> float:
        liquidity = pair.get("liquidity") or {}

        return safe_float(
            liquidity.get("usd"),
            0
        )

    def volume_5m(pair: Dict[str, Any]) -> float:
        volume = pair.get("volume") or {}

        return safe_float(
            volume.get("m5"),
            0
        )

    valid_liquidity = [
        p for p in sol_pairs
        if liquidity_value(p) > 0
    ]

    if valid_liquidity:
        selected = max(
            valid_liquidity,
            key=liquidity_value
        )

    else:
        selected = max(
            sol_pairs,
            key=volume_5m
        )

    txns = selected.get("txns") or {}
    m5 = txns.get("m5") or {}

    buys = safe_int(
        m5.get("buys"),
        0
    )

    sells = safe_int(
        m5.get("sells"),
        0
    )

    total_tx = buys + sells

    volume_5m_value = volume_5m(selected)

    # IMPORTANT:
    # This is a pressure proxy, NOT actual dollar net flow.
    flow_proxy = 0.0

    if total_tx > 0:
        flow_proxy = (
            volume_5m_value *
            ((buys - sells) / total_tx)
        )

    price_change = selected.get(
        "priceChange"
    ) or {}

    liquidity = (
        selected.get("liquidity")
        or {}
    )

    fdv = safe_float(
        selected.get("fdv"),
        0
    )

    market_cap = safe_float(
        selected.get("marketCap"),
        0
    )

    if market_cap <= 0:
        market_cap = fdv

    created_at = safe_int(
        selected.get("pairCreatedAt"),
        0
    )

    return {
        "address": address,

        "pair": selected.get(
            "pairAddress",
            ""
        ),

        "dex": selected.get(
            "dexId",
            ""
        ),

        "url": selected.get(
            "url",
            ""
        ),

        "symbol": selected.get(
            "baseToken",
            {}
        ).get(
            "symbol",
            "UNKNOWN"
        ),

        "name": selected.get(
            "baseToken",
            {}
        ).get(
            "name",
            "Unknown"
        ),

        "price": safe_float(
            selected.get("priceUsd"),
            0
        ),

        "market_cap": market_cap,

        "fdv": fdv,

        "liquidity": safe_float(
            liquidity.get("usd"),
            0
        ),

        "volume_5m": volume_5m_value,

        "buys_5m": buys,

        "sells_5m": sells,

        "buy_sell_ratio": (
            buys / sells
            if sells > 0
            else float(buys)
        ),

        "flow_proxy": flow_proxy,

        "price_change_5m": safe_float(
            price_change.get("m5"),
            0
        ),

        "price_change_1h": safe_float(
            price_change.get("h1"),
            0
        ),

        "price_change_6h": safe_float(
            price_change.get("h6"),
            0
        ),

        "pair_created_at": created_at,

        "raw_pair": selected,
    }


# ============================================================
# GENERIC JSON HELPERS
# ============================================================

def recursive_find(
    obj: Any,
    keys: List[str]
) -> Any:

    wanted = {
        k.lower()
        for k in keys
    }

    if isinstance(obj, dict):

        for key, value in obj.items():

            if str(key).lower() in wanted:
                return value

        for value in obj.values():

            found = recursive_find(
                value,
                keys
            )

            if found is not None:
                return found

    elif isinstance(obj, list):

        for item in obj:

            found = recursive_find(
                item,
                keys
            )

            if found is not None:
                return found

    return None


def recursive_find_number(
    obj: Any,
    keys: List[str]
) -> Optional[float]:

    value = recursive_find(
        obj,
        keys
    )

    if value is None:
        return None

    try:
        return float(value)
    except Exception:
        return None


def recursive_find_bool(
    obj: Any,
    keys: List[str]
) -> Optional[bool]:

    value = recursive_find(
        obj,
        keys
    )

    if value is None:
        return None

    if isinstance(value, bool):
        return value

    text = str(value).lower().strip()

    if text in {
        "true",
        "yes",
        "1"
    }:
        return True

    if text in {
        "false",
        "no",
        "0"
    }:
        return False

    return None


def recursive_find_string(
    obj: Any,
    keys: List[str]
) -> Optional[str]:

    value = recursive_find(
        obj,
        keys
    )

    if value is None:
        return None

    if isinstance(value, str):
        return value

    return str(value)


def find_lists(
    obj: Any
) -> List[List[Any]]:

    result: List[List[Any]] = []

    if isinstance(obj, list):
        if obj and all(
            isinstance(x, dict)
            for x in obj
        ):
            result.append(obj)

        for item in obj:
            result.extend(
                find_lists(item)
            )

    elif isinstance(obj, dict):

        for value in obj.values():
            result.extend(
                find_lists(value)
            )

    return result


def first_dict_list(
    obj: Any
) -> List[Dict[str, Any]]:

    lists = find_lists(obj)

    for candidate in lists:

        if candidate:
            return [
                x for x in candidate
                if isinstance(x, dict)
            ]

    return []


# ============================================================
# GMGN CLI
# ============================================================

def gmgn_command(
    args: List[str],
    timeout: int = GMGN_TIMEOUT
) -> Optional[Any]:

    if not GMGN_ENABLED:
        return None

    command = [
        "npx",
        "--yes",
        "gmgn-cli",
    ] + args + ["--raw"]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy()
        )

        if result.returncode != 0:

            print(
                "GMGN command failed:",
                result.stderr[-1000:]
            )

            return None

        output = result.stdout.strip()

        if not output:
            return None

        # --raw should produce JSON.
        # Find JSON if npm prints extra information.
        for line in reversed(
            output.splitlines()
        ):

            line = line.strip()

            if not line:
                continue

            try:
                return json.loads(line)
            except Exception:
                continue

        # Try the entire output.
        try:
            return json.loads(output)
        except Exception:
            return None

    except subprocess.TimeoutExpired:
        print(
            "GMGN timeout:",
            " ".join(command)
        )

    except FileNotFoundError:
        print(
            "npx was not found. "
            "Install Node.js/npm in Replit."
        )

    except Exception as e:
        print(
            f"GMGN exception: {e}"
        )

    return None


def gmgn_config_check() -> bool:

    if not GMGN_ENABLED:
        return False

    result = gmgn_command(
        ["config", "--check"]
    )

    if result is None:

        # config --check may output
        # human-readable text rather than JSON.
        # So run it directly once.
        try:

            process = subprocess.run(
                [
                    "npx",
                    "--yes",
                    "gmgn-cli",
                    "config",
                    "--check",
                ],
                capture_output=True,
                text=True,
                timeout=GMGN_TIMEOUT,
                env=os.environ.copy()
            )

            combined = (
                process.stdout +
                "\n" +
                process.stderr
            ).lower()

            if (
                "api key" in combined
                and (
                    "ok" in combined
                    or
                    "configured" in combined
                    or
                    "valid" in combined
                )
            ):
                return True

            print(
                "GMGN config check output:"
            )
            print(
                combined[-2000:]
            )

            return process.returncode == 0

        except Exception as e:
            print(
                f"GMGN config check failed: {e}"
            )

            return False

    return True


# ============================================================
# GMGN TOKEN INFO
# ============================================================

def gmgn_token_info(
    address: str
) -> Optional[Dict[str, Any]]:

    return gmgn_command(
        [
            "token",
            "info",
            "--chain",
            GMGN_CHAIN,
            "--address",
            address,
        ]
    )


def gmgn_token_security(
    address: str
) -> Optional[Dict[str, Any]]:

    return gmgn_command(
        [
            "token",
            "security",
            "--chain",
            GMGN_CHAIN,
            "--address",
            address,
        ]
    )


def gmgn_token_pool(
    address: str
) -> Optional[Dict[str, Any]]:

    return gmgn_command(
        [
            "token",
            "pool",
            "--chain",
            GMGN_CHAIN,
            "--address",
            address,
        ]
    )


def gmgn_token_holders(
    address: str
) -> Optional[Dict[str, Any]]:

    return gmgn_command(
        [
            "token",
            "holders",
            "--chain",
            GMGN_CHAIN,
            "--address",
            address,
            "--limit",
            str(GMGN_HOLDER_LIMIT),
        ]
    )


def gmgn_smart_money_holders(
    address: str
) -> Optional[Dict[str, Any]]:

    return gmgn_command(
        [
            "token",
            "holders",
            "--chain",
            GMGN_CHAIN,
            "--address",
            address,
            "--tag",
            "smart_degen",
            "--order-by",
            "buy_volume_cur",
            "--direction",
            "desc",
            "--limit",
            "20",
        ]
    )


def gmgn_kol_holders(
    address: str
) -> Optional[Dict[str, Any]]:

    return gmgn_command(
        [
            "token",
            "holders",
            "--chain",
            GMGN_CHAIN,
            "--address",
            address,
            "--tag",
            "renowned",
            "--order-by",
            "buy_volume_cur",
            "--direction",
            "desc",
            "--limit",
            "20",
        ]
    )


def gmgn_token_traders(
    address: str
) -> Optional[Dict[str, Any]]:

    return gmgn_command(
        [
            "token",
            "traders",
            "--chain",
            GMGN_CHAIN,
            "--address",
            address,
            "--tag",
            "renowned",
            "--order-by",
            "profit",
            "--direction",
            "desc",
            "--limit",
            "20",
        ]
    )


def gmgn_signals(
    address: str
) -> Optional[Dict[str, Any]]:

    # Signal 12 = Smart Money Buy
    # Signal 20 = KOL Buy
    #
    # We retrieve both and inspect token address
    # in the returned data.

    return gmgn_command(
        [
            "market",
            "signal",
            "--chain",
            GMGN_CHAIN,
            "--signal-type",
            "12",
            "--signal-type",
            "20",
        ]
    )


# ============================================================
# GMGN EXTRACTION
# ============================================================

def extract_holder_count(
    info: Any
) -> Optional[int]:

    value = recursive_find_number(
        info,
        [
            "holder_count",
            "holders",
            "holders_count",
            "holderCount",
        ]
    )

    if value is None:
        return None

    return int(value)


def extract_market_cap(
    info: Any
) -> Optional[float]:

    return recursive_find_number(
        info,
        [
            "market_cap",
            "marketcap",
            "marketCap",
        ]
    )


def extract_liquidity(
    info: Any
) -> Optional[float]:

    return recursive_find_number(
        info,
        [
            "liquidity",
            "liquidity_usd",
            "liquidityUsd",
        ]
    )


def extract_top10(
    security: Any
) -> Optional[float]:

    value = recursive_find_number(
        security,
        [
            "top_10_holder_rate",
            "top10_holder_rate",
            "top10HolderRate",
            "top_10_holder_percentage",
        ]
    )

    if value is None:
        return None

    # GMGN commonly represents rates as
    # decimal fractions.
    #
    # Convert 0.21 -> 21%.
    if 0 <= value <= 1:
        value *= 100

    return value


def extract_dev_holding(
    security: Any,
    info: Any
) -> Optional[float]:

    value = recursive_find_number(
        security,
        [
            "dev_team_hold_rate",
            "creator_hold_rate",
            "creator_percentage",
            "dev_hold_rate",
            "dev_holding",
            "dev_holding_rate",
        ]
    )

    if value is None:

        value = recursive_find_number(
            info,
            [
                "dev_team_hold_rate",
                "creator_hold_rate",
                "creator_percentage",
                "dev_hold_rate",
                "dev_holding",
            ]
        )

    if value is None:
        return None

    if 0 <= value <= 1:
        value *= 100

    return value


def extract_socials(
    info: Any
) -> Dict[str, str]:

    website = (
        recursive_find_string(
            info,
            [
                "website",
                "website_url",
            ]
        )
        or ""
    )

    twitter = (
        recursive_find_string(
            info,
            [
                "twitter_username",
                "twitter",
                "x",
                "twitter_url",
            ]
        )
        or ""
    )

    telegram = (
        recursive_find_string(
            info,
            [
                "telegram",
                "telegram_url",
            ]
        )
        or ""
    )

    return {
        "website": website,
        "twitter": twitter,
        "telegram": telegram,
    }


def extract_renounced_mint(
    security: Any
) -> Optional[bool]:

    return recursive_find_bool(
        security,
        [
            "renounced_mint",
            "mint_renounced",
        ]
    )


def extract_renounced_freeze(
    security: Any
) -> Optional[bool]:

    return recursive_find_bool(
        security,
        [
            "renounced_freeze_account",
            "freeze_renounced",
            "freeze_account_renounced",
        ]
    )


def extract_rug_ratio(
    security: Any
) -> Optional[float]:

    return recursive_find_number(
        security,
        [
            "rug_ratio",
            "rugRatio",
        ]
    )


def extract_insider_rate(
    security: Any
) -> Optional[float]:

    value = recursive_find_number(
        security,
        [
            "insider_rate",
            "insiderRate",
        ]
    )

    if value is not None and 0 <= value <= 1:
        value *= 100

    return value


def extract_bundler_rate(
    security: Any
) -> Optional[float]:

    value = recursive_find_number(
        security,
        [
            "bundler_rate",
            "bundlerRate",
        ]
    )

    if value is not None and 0 <= value <= 1:
        value *= 100

    return value


# ============================================================
# HOLDER LIST ANALYSIS
# ============================================================

def analyze_wallet_list(
    payload: Any
) -> Dict[str, Any]:

    rows = first_dict_list(payload)

    smart_count = 0
    kol_count = 0

    buy_total = 0.0
    sell_total = 0.0

    for row in rows:

        tags = row.get("tags")

        if isinstance(tags, list):

            tag_text = " ".join(
                str(x).lower()
                for x in tags
            )

        else:

            tag_text = str(
                row.get("tag", "")
            ).lower()

        if "smart_degen" in tag_text:
            smart_count += 1

        if "renowned" in tag_text:
            kol_count += 1

        buy_total += safe_float(
            row.get("buy_volume_cur"),
            0
        )

        sell_total += safe_float(
            row.get("sell_volume_cur"),
            0
        )

    return {
        "count": len(rows),

        "smart_count": smart_count,

        "kol_count": kol_count,

        "buy_volume": buy_total,

        "sell_volume": sell_total,

        "net_buy_volume": (
            buy_total - sell_total
        ),

        "accumulating": (
            buy_total > sell_total
            and buy_total > 0
        ),
    }


# ============================================================
# HOLDER GROWTH
# ============================================================

def get_token_record(
    address: str
) -> Dict[str, Any]:

    tokens = state.setdefault(
        "tokens",
        {}
    )

    record = tokens.setdefault(
        address,
        {
            "address": address,
            "created_at": now_ts(),

            "observations": 0,

            "first_seen": None,
            "last_seen": None,

            "holder_history": [],

            "flow_history": [],

            "mc_history": [],

            "volume_history": [],

            "gmgn_history": [],

            "recent_qualifications": [],

            "last_gmgn_check": 0,

            "last_alert": 0,

            "alerted": False,

            "max_mc_seen": 0,

            "min_mc_seen": None,
        }
    )

    return record


def record_holder_count(
    address: str,
    holders: int
) -> Dict[str, Any]:

    record = get_token_record(
        address
    )

    history = record.setdefault(
        "holder_history",
        []
    )

    history.append({
        "ts": now_ts(),
        "holders": holders,
    })

    record["holder_history"] = trim_history(
        history,
        MAX_TOKEN_HISTORY
    )

    cutoff = now_ts() - HOLDER_GROWTH_WINDOW

    recent = [
        x for x in record["holder_history"]
        if safe_float(x.get("ts")) >= cutoff
    ]

    if len(recent) < 2:

        return {
            "known": False,
            "growing": False,
            "change": 0,
            "previous": None,
            "current": holders,
        }

    previous = min(
        recent,
        key=lambda x: x["ts"]
    )

    change = (
        holders -
        safe_int(previous.get("holders"))
    )

    return {
        "known": True,
        "growing": change > 0,
        "change": change,
        "previous": safe_int(
            previous.get("holders")
        ),
        "current": holders,
    }


# ============================================================
# MOMENTUM HISTORY
# ============================================================

def record_market_snapshot(
    address: str,
    dex: Dict[str, Any]
) -> None:

    record = get_token_record(
        address
    )

    ts = now_ts()

    record["first_seen"] = (
        record["first_seen"]
        or ts
    )

    record["last_seen"] = ts

    record["max_mc_seen"] = max(
        safe_float(
            record.get("max_mc_seen"),
            0
        ),
        safe_float(
            dex.get("market_cap"),
            0
        )
    )

    current_mc = safe_float(
        dex.get("market_cap"),
        0
    )

    minimum_mc = record.get(
        "min_mc_seen"
    )

    if minimum_mc is None:
        record["min_mc_seen"] = current_mc

    elif current_mc > 0:
        record["min_mc_seen"] = min(
            safe_float(
                minimum_mc,
                current_mc
            ),
            current_mc
        )

    record.setdefault(
        "flow_history",
        []
    ).append({
        "ts": ts,
        "flow": safe_float(
            dex.get("flow_proxy"),
            0
        ),
        "buys": safe_int(
            dex.get("buys_5m"),
            0
        ),
        "sells": safe_int(
            dex.get("sells_5m"),
            0
        ),
        "volume": safe_float(
            dex.get("volume_5m"),
            0
        ),
    })

    record.setdefault(
        "mc_history",
        []
    ).append({
        "ts": ts,
        "mc": current_mc,
    })

    record.setdefault(
        "volume_history",
        []
    ).append({
        "ts": ts,
        "volume": safe_float(
            dex.get("volume_5m"),
            0
        ),
    })

    record["flow_history"] = trim_history(
        record["flow_history"],
        MAX_TOKEN_HISTORY
    )

    record["mc_history"] = trim_history(
        record["mc_history"],
        MAX_TOKEN_HISTORY
    )

    record["volume_history"] = trim_history(
        record["volume_history"],
        MAX_TOKEN_HISTORY
    )


def buy_pressure_status(
    address: str,
    dex: Dict[str, Any]
) -> Dict[str, Any]:

    record = get_token_record(
        address
    )

    current_flow = safe_float(
        dex.get("flow_proxy"),
        0
    )

    current_buys = safe_int(
        dex.get("buys_5m"),
        0
    )

    current_sells = safe_int(
        dex.get("sells_5m"),
        0
    )

    positive = (
        current_buys >
        current_sells
    )

    history = record.get(
        "flow_history",
        []
    )

    if not history:
        return {
            "positive": positive,
            "recovering": False,
            "previous_flow": None,
        }

    previous = history[-1]

    previous_flow = safe_float(
        previous.get("flow"),
        0
    )

    recovering = (
        current_flow > 0
        and
        current_flow > previous_flow
    )

    return {
        "positive": positive,
        "recovering": recovering,
        "previous_flow": previous_flow,
    }


def volume_spike_status(
    address: str,
    current_volume: float
) -> Dict[str, Any]:

    record = get_token_record(
        address
    )

    history = record.get(
        "volume_history",
        []
    )

    if len(history) < 10:

        return {
            "known": False,
            "spike": False,
            "ratio": 0,
        }

    recent = [
        safe_float(
            x.get("volume"),
            0
        )
        for x in history[-10:]
    ]

    baseline = (
        sum(recent) /
        len(recent)
        if recent
        else 0
    )

    if baseline <= 0:
        return {
            "known": True,
            "spike": False,
            "ratio": 0,
        }

    ratio = (
        current_volume /
        baseline
    )

    return {
        "known": True,
        "spike": ratio >= 1.5,
        "ratio": ratio,
    }


# ============================================================
# AGE
# ============================================================

def token_age_hours(
    dex: Dict[str, Any]
) -> Optional[float]:

    created = safe_float(
        dex.get("pair_created_at"),
        0
    )

    if created <= 0:
        return None

    # DexScreener pairCreatedAt is milliseconds.
    if created > 10_000_000_000:
        created /= 1000

    age = (
        now_ts() -
        created
    ) / 3600

    return age


# ============================================================
# DEX PREFILTER
# ============================================================

def dex_second_wave_prefilter(
    dex: Dict[str, Any]
) -> Tuple[bool, str, Dict[str, Any]]:

    mc = safe_float(
        dex.get("market_cap"),
        0
    )

    liquidity = safe_float(
        dex.get("liquidity"),
        0
    )

    volume = safe_float(
        dex.get("volume_5m"),
        0
    )

    age = token_age_hours(
        dex
    )

    if mc <= 0:
        return False, "MC_UNAVAILABLE", {}

    if age is None:
        return False, "AGE_UNAVAILABLE", {}

    if age < MIN_AGE_HOURS:
        return False, "AGE_BELOW_10H", {}

    if age > MAX_AGE_HOURS:
        return False, "AGE_OVER_72H", {}

    if mc < MIN_MC:
        return False, "MC_BELOW_30K", {}

    if mc > MAX_MC:
        return False, "MC_ABOVE_400K", {}

    if liquidity < MIN_LIQUIDITY:
        return False, "LIQUIDITY_BELOW_25K", {}

    volume_mc_pct = pct(
        volume,
        mc
    )

    if volume_mc_pct < MIN_5M_VOLUME_MC_PCT:
        return (
            False,
            "VOLUME_MC_BELOW_THRESHOLD",
            {
                "volume_mc_pct":
                    volume_mc_pct
            }
        )

    return True, "PASS", {
        "age_hours": age,
        "volume_mc_pct": volume_mc_pct,
    }


# ============================================================
# GMGN VALIDATION
# ============================================================

def validate_with_gmgn(
    address: str,
    dex: Dict[str, Any]
) -> Dict[str, Any]:

    record = get_token_record(
        address
    )

    current_time = now_ts()

    # Do not hit GMGN every 15 sec.
    if (
        current_time -
        safe_float(
            record.get("last_gmgn_check"),
            0
        )
        <
        GMGN_REFRESH_SECONDS
    ):

        cached = record.get(
            "last_gmgn_result"
        )

        if isinstance(cached, dict):
            return cached

    info = gmgn_token_info(
        address
    )

    security = gmgn_token_security(
        address
    )

    pool = gmgn_token_pool(
        address
    )

    if (
        info is None
        or
        security is None
    ):

        state["total_gmgn_failed"] += 1

        result = {
            "passed": False,
            "reason": "GMGN_DATA_UNAVAILABLE",
        }

        record[
            "last_gmgn_check"
        ] = current_time

        record[
            "last_gmgn_result"
        ] = result

        return result

    state[
        "total_gmgn_checked"
    ] += 1

    holders = extract_holder_count(
        info
    )

    gmgn_mc = extract_market_cap(
        info
    )

    gmgn_liquidity = extract_liquidity(
        info
    )

    top10_pct = extract_top10(
        security
    )

    dev_pct = extract_dev_holding(
        security,
        info
    )

    mint_revoked = extract_renounced_mint(
        security
    )

    freeze_revoked = extract_renounced_freeze(
        security
    )

    rug_ratio = extract_rug_ratio(
        security
    )

    insider_rate = extract_insider_rate(
        security
    )

    bundler_rate = extract_bundler_rate(
        security
    )

    socials = extract_socials(
        info
    )

    social_count = sum(
        1
        for x in socials.values()
        if x
    )

    # --------------------------------------------------------
    # HOLDER GROWTH
    # --------------------------------------------------------

    holder_growth = {
        "known": False,
        "growing": False,
        "change": 0,
    }

    if holders is not None:
        holder_growth = record_holder_count(
            address,
            holders
        )

    # --------------------------------------------------------
    # MARKET PRESSURE
    # --------------------------------------------------------

    pressure = buy_pressure_status(
        address,
        dex
    )

    # --------------------------------------------------------
    # VOLUME SPIKE
    # --------------------------------------------------------

    volume_spike = volume_spike_status(
        address,
        safe_float(
            dex.get("volume_5m"),
            0
        )
    )

    # --------------------------------------------------------
    # SMART MONEY
    # --------------------------------------------------------

    smart_payload = (
        gmgn_smart_money_holders(
            address
        )
    )

    smart_stats = analyze_wallet_list(
        smart_payload
    ) if smart_payload is not None else {
        "count": 0,
        "smart_count": 0,
        "kol_count": 0,
        "buy_volume": 0,
        "sell_volume": 0,
        "net_buy_volume": 0,
        "accumulating": False,
    }

    # --------------------------------------------------------
    # KOL
    # --------------------------------------------------------

    kol_payload = (
        gmgn_kol_holders(
            address
        )
    )

    kol_stats = analyze_wallet_list(
        kol_payload
    ) if kol_payload is not None else {
        "count": 0,
        "smart_count": 0,
        "kol_count": 0,
        "buy_volume": 0,
        "sell_volume": 0,
        "net_buy_volume": 0,
        "accumulating": False,
    }

    # --------------------------------------------------------
    # GMGN HARD FILTER
    # --------------------------------------------------------

    if holders is None:
        return gmgn_fail(
            address,
            "HOLDERS_UNAVAILABLE"
        )

    if holders < MIN_HOLDERS:
        return gmgn_fail(
            address,
            "HOLDERS_BELOW_300"
        )

    # User's rule explicitly says holders must still grow.
    # We therefore do NOT pretend that "unknown" means growing.
    if not holder_growth["known"]:
        return gmgn_fail(
            address,
            "HOLDER_GROWTH_NOT_ESTABLISHED"
        )

    if not holder_growth["growing"]:
        return gmgn_fail(
            address,
            "HOLDERS_NOT_GROWING"
        )

    if dev_pct is None:
        return gmgn_fail(
            address,
            "DEV_HOLDING_UNAVAILABLE"
        )

    if dev_pct > MAX_DEV_HOLDING_PCT:
        return gmgn_fail(
            address,
            "DEV_ABOVE_4PCT"
        )

    if top10_pct is None:
        return gmgn_fail(
            address,
            "TOP10_UNAVAILABLE"
        )

    if top10_pct > MAX_TOP10_PCT:
        return gmgn_fail(
            address,
            "TOP10_ABOVE_25PCT"
        )

    if mint_revoked is not True:
        return gmgn_fail(
            address,
            "MINT_NOT_REVOKED"
        )

    if freeze_revoked is not True:
        return gmgn_fail(
            address,
            "FREEZE_NOT_REVOKED"
        )

    if social_count < 1:
        return gmgn_fail(
            address,
            "NO_SOCIAL"
        )

    # Buy pressure must be positive OR recovering.
    if not (
        pressure["positive"]
        or
        pressure["recovering"]
    ):
        return gmgn_fail(
            address,
            "BUY_PRESSURE_NOT_POSITIVE_OR_RECOVERING"
        )

    # --------------------------------------------------------
    # CONFIRMATIONS
    # --------------------------------------------------------

    confirmations: List[str] = []

    if (
        smart_stats["accumulating"]
        and
        smart_stats["count"] > 0
    ):
        confirmations.append(
            "SMART_MONEY_ACCUMULATION"
        )

    if (
        kol_stats["accumulating"]
        and
        kol_stats["count"] > 0
    ):
        confirmations.append(
            "KOL_ACCUMULATION"
        )

    if holder_growth["growing"]:
        confirmations.append(
            "HOLDER_GROWTH"
        )

    if volume_spike["spike"]:
        confirmations.append(
            "RECENT_VOLUME_SPIKE"
        )

    if pressure["positive"]:
        confirmations.append(
            "POSITIVE_BUY_PRESSURE"
        )

    elif pressure["recovering"]:
        confirmations.append(
            "RECOVERING_BUY_PRESSURE"
        )

    if (
        top10_pct <=
        PREFERRED_TOP10_PCT
    ):
        confirmations.append(
            "CLEAN_TOP10"
        )

    if (
        dev_pct <=
        PREFERRED_DEV_HOLDING_PCT
    ):
        confirmations.append(
            "LOW_DEV_HOLDING"
        )

    if (
        liquidity_value(
            dex
        )
        >= PREFERRED_LIQUIDITY
    ):
        confirmations.append(
            "STRONG_LIQUIDITY"
        )

    # GMGN risk signals.
    warnings: List[str] = []

    if (
        rug_ratio is not None
        and
        rug_ratio > 0.30
    ):
        warnings.append(
            "HIGH_RUG_RATIO"
        )

    if (
        insider_rate is not None
        and
        insider_rate > 25
    ):
        warnings.append(
            "HIGH_INSIDER_RATE"
        )

    if (
        bundler_rate is not None
        and
        bundler_rate > 25
    ):
        warnings.append(
            "HIGH_BUNDLER_RATE"
        )

    result = {
        "passed": True,

        "holders": holders,

        "holder_growth": holder_growth,

        "gmgn_market_cap": gmgn_mc,

        "gmgn_liquidity": gmgn_liquidity,

        "dev_pct": dev_pct,

        "top10_pct": top10_pct,

        "mint_revoked": mint_revoked,

        "freeze_revoked": freeze_revoked,

        "rug_ratio": rug_ratio,

        "insider_rate": insider_rate,

        "bundler_rate": bundler_rate,

        "socials": socials,

        "social_count": social_count,

        "pressure": pressure,

        "volume_spike": volume_spike,

        "smart_money": smart_stats,

        "kol": kol_stats,

        "confirmations": confirmations,

        "warnings": warnings,

        "pool_available": pool is not None,

        "checked_at": current_time,

        "checked_at_iso": iso_now(),
    }

    record[
        "last_gmgn_check"
    ] = current_time

    record[
        "last_gmgn_result"
    ] = result

    record.setdefault(
        "gmgn_history",
        []
    ).append({
        "ts": current_time,
        "holders": holders,
        "dev_pct": dev_pct,
        "top10_pct": top10_pct,
        "confirmations": confirmations,
    })

    record["gmgn_history"] = trim_history(
        record["gmgn_history"],
        100
    )

    return result


def gmgn_fail(
    address: str,
    reason: str
) -> Dict[str, Any]:

    state[
        "rejection_counts"
    ][reason] = (
        state[
            "rejection_counts"
        ].get(reason, 0)
        + 1
    )

    result = {
        "passed": False,
        "reason": reason,
        "checked_at": now_ts(),
    }

    record = get_token_record(
        address
    )

    record[
        "last_gmgn_check"
    ] = now_ts()

    record[
        "last_gmgn_result"
    ] = result

    return result


def liquidity_value(
    dex: Dict[str, Any]
) -> float:

    return safe_float(
        dex.get("liquidity"),
        0
    )


# ============================================================
# OBSERVATION SYSTEM
# ============================================================

def calculate_observation_tier(
    dex: Dict[str, Any],
    gmgn: Dict[str, Any]
) -> Tuple[str, int]:

    mc = safe_float(
        dex.get("market_cap"),
        0
    )

    flow = safe_float(
        dex.get("flow_proxy"),
        0
    )

    flow_mc = pct(
        flow,
        mc
    )

    # VERY strong momentum.
    if (
        flow_mc >= 25
        or
        flow >= 25_000
    ):
        return "ULTRA", 1

    # Strong momentum.
    if (
        flow_mc >= 15
        or
        flow >= 15_000
    ):
        return "STRONG", 2

    # Second-wave normal confirmation.
    return "NORMAL", 3


# ============================================================
# QUALIFICATION
# ============================================================

def qualify_token(
    address: str,
    dex: Dict[str, Any],
    gmgn: Dict[str, Any]
) -> Tuple[bool, str, Dict[str, Any]]:

    if not gmgn.get("passed"):
        return (
            False,
            gmgn.get(
                "reason",
                "GMGN_REJECTED"
            ),
            {}
        )

    mc = safe_float(
        dex.get("market_cap"),
        0
    )

    flow = safe_float(
        dex.get("flow_proxy"),
        0
    )

    flow_mc = pct(
        flow,
        mc
    )

    bs = safe_float(
        dex.get("buy_sell_ratio"),
        0
    )

    pressure = gmgn.get(
        "pressure",
        {}
    )

    confirmations = gmgn.get(
        "confirmations",
        []
    )

    # Positive/recovering pressure is mandatory.
    if not (
        pressure.get("positive")
        or
        pressure.get("recovering")
    ):
        return (
            False,
            "PRESSURE_FAIL",
            {}
        )

    tier, observation_target = (
        calculate_observation_tier(
            dex,
            gmgn
        )
    )

    record = get_token_record(
        address
    )

    # Every successful qualifying scan
    # becomes an observation.
    record["observations"] = (
        safe_int(
            record.get(
                "observations",
                0
            )
        )
        + 1
    )

    record.setdefault(
        "recent_qualifications",
        []
    ).append({
        "ts": now_ts(),
        "mc": mc,
        "flow": flow,
        "flow_mc_pct": flow_mc,
        "buy_sell": bs,
        "tier": tier,
        "confirmations": confirmations,
    })

    record["recent_qualifications"] = trim_history(
        record["recent_qualifications"],
        100
    )

    # We alert when the observation count
    # reaches the tier's target.
    #
    # ULTRA = 1
    # STRONG = 2
    # NORMAL = 3

    if record["observations"] < observation_target:
        return (
            False,
            "WAITING_FOR_OBSERVATIONS",
            {
                "tier": tier,
                "observations":
                    record["observations"],
                "target":
                    observation_target,
            }
        )

    return (
        True,
        "QUALIFIED",
        {
            "tier": tier,
            "observations":
                record["observations"],
            "target":
                observation_target,
            "flow_mc_pct":
                flow_mc,
            "buy_sell":
                bs,
            "confirmations":
                confirmations,
        }
    )


# ============================================================
# ALERT MESSAGE
# ============================================================

def build_alert(
    dex: Dict[str, Any],
    gmgn: Dict[str, Any],
    qualification: Dict[str, Any]
) -> str:

    symbol = dex.get(
        "symbol",
        "UNKNOWN"
    )

    name = dex.get(
        "name",
        "Unknown"
    )

    address = dex.get(
        "address",
        ""
    )

    mc = safe_float(
        dex.get("market_cap"),
        0
    )

    liquidity = safe_float(
        dex.get("liquidity"),
        0
    )

    volume = safe_float(
        dex.get("volume_5m"),
        0
    )

    flow = safe_float(
        dex.get("flow_proxy"),
        0
    )

    flow_mc = pct(
        flow,
        mc
    )

    age = token_age_hours(
        dex
    )

    bs = safe_float(
        dex.get("buy_sell_ratio"),
        0
    )

    holders = gmgn.get(
        "holders",
        0
    )

    holder_growth = gmgn.get(
        "holder_growth",
        {}
    )

    dev = gmgn.get(
        "dev_pct"
    )

    top10 = gmgn.get(
        "top10_pct"
    )

    tier = qualification.get(
        "tier",
        "NORMAL"
    )

    observations = qualification.get(
        "observations",
        0
    )

    confirmations = gmgn.get(
        "confirmations",
        []
    )

    warnings = gmgn.get(
        "warnings",
        []
    )

    socials = gmgn.get(
        "socials",
        {}
    )

    gmgn_link = (
        "https://gmgn.ai/sol/token/"
        + address
    )

    lines = [
        "🚀 SECOND-WAVE RUNNER",
        "",
        f"{symbol} — {name}",
        "",
        f"🎯 Tier: {tier}",
        (
            f"👁 Observations: "
            f"{observations}"
        ),
        "",
        f"💰 MC: {fmt_money(mc)}",
        (
            f"💧 Liquidity: "
            f"{fmt_money(liquidity)}"
        ),
        (
            f"⏱ Age: "
            f"{age:.1f}h"
            if age is not None
            else "⏱ Age: N/A"
        ),
        "",
        (
            f"👥 Holders: "
            f"{holders:,}"
        ),
        (
            f"📈 Holder growth: "
            f"+{holder_growth.get('change', 0)}"
        ),
        (
            f"👨‍💻 Dev: "
            f"{fmt_pct(dev)}"
            if dev is not None
            else "👨‍💻 Dev: N/A"
        ),
        (
            f"🐋 Top 10: "
            f"{fmt_pct(top10)}"
            if top10 is not None
            else "🐋 Top 10: N/A"
        ),
        "",
        (
            f"📊 5m Vol: "
            f"{fmt_money(volume)}"
        ),
        (
            f"📐 Vol/MC: "
            f"{pct(volume, mc):.1f}%"
        ),
        (
            f"🟢 Buy/Sell: "
            f"{bs:.2f}x"
        ),
        (
            f"🌊 Flow proxy: "
            f"{fmt_money(flow)}"
        ),
        (
            f"📊 Flow/MC: "
            f"{flow_mc:.1f}%"
        ),
        "",
        (
            "🧠 Confirmations:\n"
            +
            "\n".join(
                f"• {x}"
                for x in confirmations
            )
            if confirmations
            else
            "🧠 Confirmations:\n• None"
        ),
    ]

    if warnings:

        lines.extend([
            "",
            "⚠️ Warnings:",
            *[
                f"• {x}"
                for x in warnings
            ]
        ])

    social_names = [
        name
        for name, value in socials.items()
        if value
    ]

    lines.extend([
        "",
        (
            "🔗 Socials: "
            +
            ", ".join(social_names)
            if social_names
            else
            "🔗 Socials: Present"
        ),
        "",
        f"🧬 Contract:",
        address,
        "",
        f"🔎 GMGN:",
        gmgn_link,
        "",
        (
            "⚠️ This is a screening signal, "
            "not a guarantee of performance."
        ),
    ])

    return "\n".join(lines)


# ============================================================
# ALERT CONTROL
# ============================================================

def should_alert(
    address: str
) -> bool:

    current = now_ts()

    record = get_token_record(
        address
    )

    if (
        current -
        safe_float(
            record.get("last_alert"),
            0
        )
        <
        TOKEN_COOLDOWN
    ):
        return False

    if (
        current -
        safe_float(
            state.get(
                "last_alert_time",
                0
            )
        )
        <
        GLOBAL_ALERT_COOLDOWN
    ):
        return False

    return True


def mark_alerted(
    address: str
) -> None:

    current = now_ts()

    record = get_token_record(
        address
    )

    record["last_alert"] = current

    record["alerted"] = True

    state[
        "last_alert_time"
    ] = current


# ============================================================
# STATISTICS
# ============================================================

def record_rejection(
    address: str,
    reason: str,
    dex: Optional[Dict[str, Any]] = None
) -> None:

    state["total_rejected"] += 1

    counts = state.setdefault(
        "rejection_counts",
        {}
    )

    counts[reason] = (
        counts.get(reason, 0)
        + 1
    )

    state.setdefault(
        "recent_rejections",
        []
    ).append({
        "ts": iso_now(),
        "address": address,
        "reason": reason,
        "mc": (
            dex.get("market_cap")
            if dex
            else None
        ),
    })

    state["recent_rejections"] = trim_history(
        state["recent_rejections"],
        5000
    )


def record_qualification(
    address: str,
    dex: Dict[str, Any],
    gmgn: Dict[str, Any],
    qualification: Dict[str, Any]
) -> None:

    state[
        "total_qualified"
    ] += 1

    tier = qualification.get(
        "tier",
        "UNKNOWN"
    )

    counts = state.setdefault(
        "qualification_counts",
        {}
    )

    counts[tier] = (
        counts.get(tier, 0)
        + 1
    )

    state.setdefault(
        "recent_qualifications",
        []
    ).append({
        "ts": iso_now(),
        "address": address,
        "symbol": dex.get(
            "symbol"
        ),
        "mc": dex.get(
            "market_cap"
        ),
        "tier": tier,
        "observations":
            qualification.get(
                "observations"
            ),
        "confirmations":
            gmgn.get(
                "confirmations",
                []
            ),
    })

    state["recent_qualifications"] = (
        trim_history(
            state[
                "recent_qualifications"
            ],
            1000
        )
    )


def record_scan_summary(
    discovered: int,
    prefiltered: int,
    processed: int,
    rejected: int,
    qualified: int
) -> None:

    state[
        "total_discovered"
    ] += discovered

    state[
        "total_prefiltered"
    ] += prefiltered

    state[
        "recent_scan_history"
    ].append({
        "ts": iso_now(),
        "discovered": discovered,
        "prefiltered": prefiltered,
        "processed": processed,
        "rejected": rejected,
        "qualified": qualified,
    })

    state[
        "recent_scan_history"
    ] = trim_history(
        state[
            "recent_scan_history"
        ],
        500
    )


# ============================================================
# MAIN SCAN
# ============================================================

def scan_once() -> None:

    # FIX FROM V4.6.1:
    # Count the scan at the START, not only
    # after successful completion.
    state[
        "total_scans"
    ] += 1

    discovered = (
        discover_dex_tokens()
    )

    prefiltered = 0
    processed = 0
    rejected = 0
    qualified = 0

    print(
        f"[{iso_now()}] "
        f"Discovery: {len(discovered)} tokens"
    )

    for address in list(
        discovered.keys()
    ):

        try:

            dex = get_dex_token_data(
                address
            )

            if dex is None:
                continue

            ok, reason, prefilter_data = (
                dex_second_wave_prefilter(
                    dex
                )
            )

            if not ok:

                record_rejection(
                    address,
                    reason,
                    dex
                )

                rejected += 1

                continue

            prefiltered += 1

            record_market_snapshot(
                address,
                dex
            )

            processed += 1

            gmgn = validate_with_gmgn(
                address,
                dex
            )

            if not gmgn.get(
                "passed"
            ):

                reason = gmgn.get(
                    "reason",
                    "GMGN_REJECTED"
                )

                record_rejection(
                    address,
                    reason,
                    dex
                )

                rejected += 1

                continue

            ok, reason, qualification = (
                qualify_token(
                    address,
                    dex,
                    gmgn
                )
            )

            if not ok:

                if reason != (
                    "WAITING_FOR_OBSERVATIONS"
                ):

                    record_rejection(
                        address,
                        reason,
                        dex
                    )

                continue

            qualified += 1

            record_qualification(
                address,
                dex,
                gmgn,
                qualification
            )

            if should_alert(
                address
            ):

                message = build_alert(
                    dex,
                    gmgn,
                    qualification
                )

                broadcast(
                    message
                )

                mark_alerted(
                    address
                )

                print(
                    "🚀 ALERT:",
                    dex.get("symbol"),
                    address
                )

            else:

                print(
                    "Qualified but cooldown:",
                    dex.get("symbol")
                )

        except Exception as e:

            print(
                f"Token scan error "
                f"{address}: {e}"
            )

    record_scan_summary(
        discovered=len(
            discovered
        ),
        prefiltered=prefiltered,
        processed=processed,
        rejected=rejected,
        qualified=qualified
    )

    save_state()

    print(
        f"Scan complete | "
        f"prefilter={prefiltered} | "
        f"processed={processed} | "
        f"rejected={rejected} | "
        f"qualified={qualified}"
    )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def command_response(
    chat_id: str,
    text: str
) -> None:

    send_message(
        chat_id,
        text
    )


def handle_command(
    chat_id: str,
    text: str
) -> None:

    command = (
        text.strip()
        .split()[0]
        .lower()
    )

    if command.startswith(
        "/start"
    ):

        subscribers = state.setdefault(
            "subscribers",
            []
        )

        if str(chat_id) not in subscribers:
            subscribers.append(
                str(chat_id)
            )

        save_state()

        command_response(
            chat_id,
            (
                "🚀 Runner Bot V4.7 is online.\n\n"
                "Mode: SECOND-WAVE GMGN\n"
                "Age: 10h–72h\n"
                "MC: $30K–$400K\n"
                "Liquidity: ≥$25K\n"
                "Holders: ≥300 + growing\n"
                "Dev: ≤4%\n"
                "Top 10: ≤25%\n"
                "Mint: revoked\n"
                "Freeze: revoked\n"
                "GMGN validation: ON\n\n"
                "Use /status for statistics."
            )
        )

        return

    if command == "/stop":

        if str(chat_id) in state.get(
            "subscribers",
            []
        ):

            state[
                "subscribers"
            ].remove(
                str(chat_id)
            )

        save_state()

        command_response(
            chat_id,
            "🛑 Alerts stopped."
        )

        return

    if command == "/alerts":

        enabled = state.get(
            "alerts_enabled",
            True
        )

        command_response(
            chat_id,
            f"Alerts: {'ON' if enabled else 'OFF'}"
        )

        return

    if command == "/status":

        command_response(
            chat_id,
            build_status()
        )

        return

    if command == "/tracking":

        command_response(
            chat_id,
            build_tracking()
        )

        return

    if command == "/scan":

        command_response(
            chat_id,
            "🔎 Running a manual scan..."
        )

        scan_once()

        command_response(
            chat_id,
            "✅ Manual scan complete."
        )

        return

    command_response(
        chat_id,
        (
            "Commands:\n\n"
            "/start — enable alerts\n"
            "/stop — disable alerts\n"
            "/status — bot statistics\n"
            "/tracking — tracked runners\n"
            "/scan — manual scan\n"
            "/alerts — alert status"
        )
    )


def poll_telegram(
    offset: int
) -> int:

    result = telegram_api(
        "getUpdates",
        {
            "timeout": 10,
            "offset": offset,
        }
    )

    if not result or not result.get(
        "ok"
    ):
        return offset

    updates = result.get(
        "result",
        []
    )

    for update in updates:

        offset = max(
            offset,
            safe_int(
                update.get("update_id"),
                0
            ) + 1
        )

        message = update.get(
            "message"
        )

        if not isinstance(
            message,
            dict
        ):
            continue

        chat = message.get(
            "chat"
        ) or {}

        chat_id = chat.get(
            "id"
        )

        text = message.get(
            "text",
            ""
        )

        if chat_id is None:
            continue

        if text.startswith("/"):
            handle_command(
                str(chat_id),
                text
            )

    return offset


# ============================================================
# STATUS
# ============================================================

def build_status() -> str:

    uptime = (
        now_ts()
        -
        safe_float(
            state.get(
                "started_at",
                now_ts()
            )
        )
    )

    hours = uptime / 3600

    return (
        f"🤖 Runner Bot {BOT_VERSION}\n\n"
        f"⏱ Uptime: {hours:.1f}h\n"
        f"🔎 Scans: "
        f"{state.get('total_scans', 0)}\n"
        f"🧭 Discovered: "
        f"{state.get('total_discovered', 0)}\n"
        f"🎯 Prefiltered: "
        f"{state.get('total_prefiltered', 0)}\n"
        f"🧠 GMGN checked: "
        f"{state.get('total_gmgn_checked', 0)}\n"
        f"❌ GMGN failed: "
        f"{state.get('total_gmgn_failed', 0)}\n"
        f"🚫 Rejected: "
        f"{state.get('total_rejected', 0)}\n"
        f"🚀 Qualified: "
        f"{state.get('total_qualified', 0)}\n\n"
        f"📡 GMGN: "
        f"{'ON' if GMGN_ENABLED else 'OFF'}\n"
        f"🔔 Alerts: "
        f"{'ON' if state.get('alerts_enabled') else 'OFF'}\n"
        f"👥 Subscribers: "
        f"{len(state.get('subscribers', []))}"
    )


def build_tracking() -> str:

    tokens = state.get(
        "tokens",
        {}
    )

    active = []

    for address, record in tokens.items():

        observations = safe_int(
            record.get(
                "observations",
                0
            )
        )

        if observations <= 0:
            continue

        result = record.get(
            "last_gmgn_result",
            {}
        )

        if not result.get(
            "passed"
        ):
            continue

        active.append({
            "address": address,
            "observations":
                observations,
            "holders":
                result.get(
                    "holders",
                    0
                ),
            "confirmations":
                result.get(
                    "confirmations",
                    []
                ),
        })

    active.sort(
        key=lambda x:
            x["observations"],
        reverse=True
    )

    lines = [
        "📊 SECOND-WAVE TRACKING",
        "",
        f"Tracked qualifying tokens: {len(active)}",
        "",
    ]

    for item in active[:20]:

        short = (
            item["address"][:6]
            + "..."
            + item["address"][-4:]
        )

        lines.append(
            f"• {short} | "
            f"Obs {item['observations']} | "
            f"Holders {item['holders']}"
        )

    if len(active) == 0:
        lines.append(
            "No qualifying tokens currently tracked."
        )

    return "\n".join(lines)


# ============================================================
# MAIN LOOP
# ============================================================

def main() -> None:

    load_state()

    print(
        "=" * 60
    )

    print(
        f"RUNNER BOT {BOT_VERSION}"
    )

    print(
        "SECOND-WAVE GMGN VALIDATION"
    )

    print(
        "=" * 60
    )

    print(
        f"Age: {MIN_AGE_HOURS}h–{MAX_AGE_HOURS}h"
    )

    print(
        f"MC: "
        f"${MIN_MC:,}–${MAX_MC:,}"
    )

    print(
        f"Liquidity: >= "
        f"${MIN_LIQUIDITY:,}"
    )

    print(
        f"Holders: >= "
        f"{MIN_HOLDERS} + growing"
    )

    print(
        f"Dev: <= "
        f"{MAX_DEV_HOLDING_PCT}%"
    )

    print(
        f"Top 10: <= "
        f"{MAX_TOP10_PCT}%"
    )

    print(
        f"Volume/MC: >= "
        f"{MIN_5M_VOLUME_MC_PCT}%"
    )

    print(
        f"GMGN: "
        f"{'ENABLED' if GMGN_ENABLED else 'DISABLED'}"
    )

    print(
        "=" * 60
    )

    # --------------------------------------------------------
    # Verify GMGN
    # --------------------------------------------------------

    if GMGN_ENABLED:

        print(
            "Checking GMGN CLI..."
        )

        gmgn_ok = gmgn_config_check()

        if not gmgn_ok:

            print(
                "\n"
                "WARNING: GMGN config check "
                "could not be confirmed.\n"
                "The bot will still start, "
                "but GMGN candidates will not "
                "pass validation until the CLI "
                "is configured correctly.\n"
            )

        else:

            print(
                "✅ GMGN CLI configured."
            )

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    offset = 0

    print(
        "Bot loop started."
    )

    last_scan = 0

    while True:

        try:

            current = now_ts()

            # Telegram commands.
            offset = poll_telegram(
                offset
            )

            # Scanner.
            if (
                current -
                last_scan
                >=
                SCAN_INTERVAL
            ):

                scan_once()

                last_scan = current

            time.sleep(1)

        except KeyboardInterrupt:

            print(
                "Bot stopped."
            )

            save_state()

            break

        except Exception as e:

            print(
                f"Main loop error: {e}"
            )

            time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
