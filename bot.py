import json
import os
import time
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V4.7.1
# GMGN-FIRST SECOND-WAVE 5–100X TRACKER
# ============================================================

BOT_VERSION = "V4.7.1-SECOND-WAVE-GMGN"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

STATE_FILE = "runner_state_v471.json"

SCAN_INTERVAL = 15

# ------------------------------------------------------------
# SECOND-WAVE HARD STRATEGY
# ------------------------------------------------------------

MIN_AGE_HOURS = 10
MAX_AGE_HOURS = 72

MIN_MC = 30_000
MAX_MC = 400_000

MIN_LIQUIDITY = 25_000

MIN_HOLDERS = 300

MAX_DEV_RATE = 0.04
MAX_TOP10_RATE = 0.25

MIN_VOLUME_MC_PCT = 0.05

# ------------------------------------------------------------
# TRACKING
# ------------------------------------------------------------

GMGN_DISCOVERY_INTERVAL = 300       # 5 minutes
GMGN_VALIDATION_INTERVAL = 300      # 5 minutes

HOLDER_GROWTH_WINDOW = 3 * 60 * 60   # 3 hours

OBSERVATION_WINDOW = 15 * 60
PENDING_EXPIRY = 20 * 60

TOKEN_COOLDOWN = 30 * 60
GLOBAL_ALERT_COOLDOWN = 60

MAX_GMGN_CANDIDATES = 25
MAX_GMGN_VALIDATIONS_PER_CYCLE = 5

MAX_HISTORY = 5000

# ------------------------------------------------------------
# ENVIRONMENT
# ------------------------------------------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts() -> int:
    return int(time.time())


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return float(value)

        if isinstance(value, (int, float)):
            return float(value)

        return float(str(value).replace(",", "").replace("$", "").strip())
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def usd(value: float) -> str:
    return f"${value:,.0f}"


def age_hours(timestamp: int) -> float:
    if not timestamp:
        return 999999

    return max(0.0, (now_ts() - timestamp) / 3600.0)


# ============================================================
# JSON STATE
# ============================================================

def default_state() -> Dict[str, Any]:
    return {
        "started_at": iso_now(),

        "telegram_chats": {},

        "candidates": {},
        "holder_history": {},

        "observations": {},
        "pending": {},

        "cooldowns": {},

        "last_gmgn_discovery": 0,
        "last_gmgn_validation": 0,
        "last_global_alert": 0,

        "stats": {
            "total_scans": 0,
            "gmgn_discoveries": 0,
            "candidates_seen": 0,
            "gmgn_validated": 0,
            "dex_verified": 0,
            "qualified": 0,
            "alerts_sent": 0,
            "rejected": 0,

            "rejections": {},
            "qualifications": 0,
        },

        "recent_rejections": [],
        "recent_qualifications": [],
        "recent_alerts": [],
    }


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return default_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        base = default_state()

        for key, value in base.items():
            if key not in state:
                state[key] = value

        for key, value in base["stats"].items():
            if key not in state["stats"]:
                state["stats"][key] = value

        return state

    except Exception as e:
        print("State load failed:", e)
        return default_state()


STATE = load_state()


def save_state() -> None:
    try:
        tmp = STATE_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE, f, indent=2)

        os.replace(tmp, STATE_FILE)

    except Exception as e:
        print("State save failed:", e)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(method: str, payload: Optional[Dict[str, Any]] = None) -> Any:

    if not TELEGRAM_TOKEN:
        return None

    url = f"{TELEGRAM_BASE}/bot{TELEGRAM_TOKEN}/{method}"

    try:
        data = None

        if payload is not None:
            data = urllib.parse.urlencode(payload).encode()

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type":
                "application/x-www-form-urlencoded"
            }
        )

        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode())

    except Exception as e:
        print("Telegram error:", e)
        return None


def send_message(chat_id: str, text: str) -> bool:

    result = telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    )

    return bool(result and result.get("ok"))


def broadcast(text: str) -> int:

    sent = 0

    for chat_id, enabled in list(
        STATE["telegram_chats"].items()
    ):

        if not enabled:
            continue

        if send_message(chat_id, text):
            sent += 1

    return sent


# ============================================================
# GMGN COMMANDS
# ============================================================

def run_command(
    args: List[str],
    raw: bool = False,
    timeout: int = 45
) -> Optional[str]:

    command = ["gmgn-cli"] + args

    if raw:
        command.append("--raw")

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:

            print(
                "GMGN command failed:",
                result.stderr.strip()
            )

            return None

        return result.stdout.strip()

    except FileNotFoundError:

        print(
            "gmgn-cli not found."
        )

        print(
            "Run: npm install -g gmgn-cli@latest"
        )

        return None

    except Exception as e:

        print(
            "GMGN execution error:",
            e
        )

        return None


def gmgn_json(args: List[str]) -> Optional[Any]:

    output = run_command(args, raw=True)

    if not output:
        return None

    try:

        return json.loads(output)

    except Exception as e:

        print(
            "GMGN JSON parse error:",
            e
        )

        print(
            "Output:",
            output[:500]
        )

        return None


def gmgn_config_check() -> bool:

    output = run_command(
        ["config", "--check"],
        raw=False
    )

    if output is None:
        return False

    print("GMGN config check:")
    print(output)

    return True


# ============================================================
# GMGN DISCOVERY
# ============================================================

def gmgn_discover() -> List[Dict[str, Any]]:

    print(
        "GMGN discovery: second-wave candidates..."
    )

    data = gmgn_json(
        [
            "market",
            "trending",

            "--chain",
            "sol",

            "--interval",
            "1h",

            "--min-created",
            "10h",

            "--max-created",
            "72h",

            "--min-marketcap",
            str(MIN_MC),

            "--max-marketcap",
            str(MAX_MC),

            "--min-liquidity",
            str(MIN_LIQUIDITY),

            "--min-holder-count",
            str(MIN_HOLDERS),

            "--max-top10-holder-rate",
            str(MAX_TOP10_RATE),

            "--max-dev-team-hold-rate",
            str(MAX_DEV_RATE),

            "--order-by",
            "volume",

            "--limit",
            str(MAX_GMGN_CANDIDATES),
        ]
    )

    if not data:
        return []

    rank = []

    if isinstance(data, dict):

        nested = data.get("data")

        if isinstance(nested, dict):
            rank = nested.get("rank", [])

        elif isinstance(nested, list):
            rank = nested

        elif isinstance(data.get("rank"), list):
            rank = data["rank"]

    elif isinstance(data, list):

        rank = data

    if not isinstance(rank, list):
        return []

    results = []

    for item in rank:

        if not isinstance(item, dict):
            continue

        address = str(
            item.get("address", "")
        ).strip()

        if not address:
            continue

        results.append(item)

    return results


# ============================================================
# GMGN TOKEN INFO
# ============================================================

def gmgn_token_info(
    address: str
) -> Optional[Dict[str, Any]]:

    data = gmgn_json(
        [
            "token",
            "info",
            "--chain",
            "sol",
            "--address",
            address,
        ]
    )

    if not isinstance(data, dict):
        return None

    return data


# ============================================================
# GMGN SECURITY
# ============================================================

def gmgn_security(
    address: str
) -> Optional[Dict[str, Any]]:

    data = gmgn_json(
        [
            "token",
            "security",
            "--chain",
            "sol",
            "--address",
            address,
        ]
    )

    if not isinstance(data, dict):
        return None

    return data


# ============================================================
# FIELD EXTRACTION
# ============================================================

def extract_token_metrics(
    info: Dict[str, Any],
    security: Optional[Dict[str, Any]]
) -> Dict[str, Any]:

    price_obj = info.get("price") or {}
    stat = info.get("stat") or {}
    dev = info.get("dev") or {}
    links = info.get("link") or {}
    wallet_tags = info.get(
        "wallet_tags_stat"
    ) or {}

    # --------------------------------------------------------
    # GMGN market cap
    # --------------------------------------------------------

    price = safe_float(
        price_obj.get("price")
    )

    circulating_supply = safe_float(
        info.get("circulating_supply")
    )

    market_cap = price * circulating_supply

    # Some GMGN responses may expose MC directly.
    if market_cap <= 0:
        market_cap = safe_float(
            info.get("market_cap")
        )

    # --------------------------------------------------------
    # Core
    # --------------------------------------------------------

    liquidity = safe_float(
        info.get("liquidity")
    )

    holders = safe_int(
        info.get("holder_count")
    )

    created = safe_int(
        info.get("creation_timestamp")
    )

    opened = safe_int(
        info.get("open_timestamp")
    )

    # --------------------------------------------------------
    # Holder structure
    # --------------------------------------------------------

    top10 = safe_float(
        stat.get(
            "top_10_holder_rate",
            dev.get(
                "top_10_holder_rate",
                0
            )
        )
    )

    dev_team = safe_float(
        stat.get(
            "dev_team_hold_rate"
        )
    )

    creator = safe_float(
        stat.get(
            "creator_hold_rate"
        )
    )

    # --------------------------------------------------------
    # Security
    # --------------------------------------------------------

    security = security or {}

    renounced_mint = security.get(
        "renounced_mint"
    )

    renounced_freeze = security.get(
        "renounced_freeze_account"
    )

    rug_ratio = safe_float(
        security.get("rug_ratio")
    )

    creator_status = security.get(
        "creator_token_status"
    )

    if not creator_status:
        creator_status = dev.get(
            "creator_token_status"
        )

    # --------------------------------------------------------
    # Trading
    # --------------------------------------------------------

    volume_5m = safe_float(
        price_obj.get("volume_5m")
    )

    volume_1h = safe_float(
        price_obj.get("volume_1h")
    )

    volume_6h = safe_float(
        price_obj.get("volume_6h")
    )

    buy_volume_5m = safe_float(
        price_obj.get("buy_volume_5m")
    )

    sell_volume_5m = safe_float(
        price_obj.get("sell_volume_5m")
    )

    buy_volume_1h = safe_float(
        price_obj.get("buy_volume_1h")
    )

    sell_volume_1h = safe_float(
        price_obj.get("sell_volume_1h")
    )

    buy_volume_6h = safe_float(
        price_obj.get("buy_volume_6h")
    )

    sell_volume_6h = safe_float(
        price_obj.get("sell_volume_6h")
    )

    buys_5m = safe_int(
        price_obj.get("buys_5m")
    )

    sells_5m = safe_int(
        price_obj.get("sells_5m")
    )

    # --------------------------------------------------------
    # Smart money / KOL
    # --------------------------------------------------------

    smart_wallets = safe_int(
        wallet_tags.get(
            "smart_wallets"
        )
    )

    renowned_wallets = safe_int(
        wallet_tags.get(
            "renowned_wallets"
        )
    )

    sniper_wallets = safe_int(
        wallet_tags.get(
            "sniper_wallets"
        )
    )

    bundler_wallets = safe_int(
        wallet_tags.get(
            "bundler_wallets"
        )
    )

    rat_wallets = safe_int(
        wallet_tags.get(
            "rat_trader_wallets"
        )
    )

    # --------------------------------------------------------
    # Socials
    # --------------------------------------------------------

    socials = []

    for field in [
        "twitter_username",
        "telegram",
        "website",
        "discord"
    ]:

        value = links.get(field)

        if value:
            socials.append(field)

    return {

        "address": info.get(
            "address"
        ),

        "symbol": info.get(
            "symbol",
            "UNKNOWN"
        ),

        "name": info.get(
            "name",
            ""
        ),

        "price": price,

        "market_cap": market_cap,

        "liquidity": liquidity,

        "holders": holders,

        "creation_timestamp": created,

        "open_timestamp": opened,

        "top10_rate": top10,

        "dev_team_rate": dev_team,

        "creator_rate": creator,

        "creator_status": creator_status,

        "renounced_mint": renounced_mint,

        "renounced_freeze": renounced_freeze,

        "rug_ratio": rug_ratio,

        "volume_5m": volume_5m,

        "volume_1h": volume_1h,

        "volume_6h": volume_6h,

        "buy_volume_5m": buy_volume_5m,

        "sell_volume_5m": sell_volume_5m,

        "buy_volume_1h": buy_volume_1h,

        "sell_volume_1h": sell_volume_1h,

        "buy_volume_6h": buy_volume_6h,

        "sell_volume_6h": sell_volume_6h,

        "buys_5m": buys_5m,

        "sells_5m": sells_5m,

        "smart_wallets": smart_wallets,

        "renowned_wallets": renowned_wallets,

        "sniper_wallets": sniper_wallets,

        "bundler_wallets": bundler_wallets,

        "rat_wallets": rat_wallets,

        "socials": socials,

        "twitter": links.get(
            "twitter_username"
        ),

        "telegram": links.get(
            "telegram"
        ),

        "website": links.get(
            "website"
        ),
    }


# ============================================================
# HOLDER GROWTH
# ============================================================

def update_holder_history(
    address: str,
    holders: int
) -> Dict[str, Any]:

    history = STATE["holder_history"].setdefault(
        address,
        []
    )

    current_time = now_ts()

    history.append(
        {
            "time": current_time,
            "holders": holders,
        }
    )

    cutoff = current_time - HOLDER_GROWTH_WINDOW

    history[:] = [
        x for x in history
        if safe_int(x.get("time")) >= cutoff
    ]

    if len(history) > 100:
        del history[:-100]

    save_state()

    if len(history) < 2:

        return {
            "growing": False,
            "change": 0,
            "samples": len(history),
        }

    oldest = history[0]["holders"]
    newest = history[-1]["holders"]

    return {
        "growing": newest > oldest,
        "change": newest - oldest,
        "samples": len(history),
    }


# ============================================================
# BUY PRESSURE
# ============================================================

def calculate_pressure(
    metrics: Dict[str, Any]
) -> Dict[str, Any]:

    b5 = metrics["buy_volume_5m"]
    s5 = metrics["sell_volume_5m"]

    b1 = metrics["buy_volume_1h"]
    s1 = metrics["sell_volume_1h"]

    b6 = metrics["buy_volume_6h"]
    s6 = metrics["sell_volume_6h"]

    p5 = b5 - s5
    p1 = b1 - s1
    p6 = b6 - s6

    return {
        "p5": p5,
        "p1": p1,
        "p6": p6,

        "positive_5m": p5 > 0,
        "positive_1h": p1 > 0,
        "positive_6h": p6 > 0,
    }


def pressure_recovering(
    address: str,
    metrics: Dict[str, Any]
) -> bool:

    pressure = calculate_pressure(
        metrics
    )

    # Current 5m pressure is positive.
    if pressure["positive_5m"]:
        return True

    # 1h is positive even if immediate 5m is temporarily weak.
    if pressure["positive_1h"]:
        return True

    history = STATE[
        "candidates"
    ].get(address, {}).get(
        "pressure_history",
        []
    )

    if len(history) < 2:
        return False

    previous = safe_float(
        history[-2].get("p5")
    )

    current = pressure["p5"]

    return (
        current > previous
        and current >= 0
    )


# ============================================================
# DEXSCREENER VERIFICATION
# ============================================================

def http_json(
    url: str
) -> Optional[Any]:

    try:

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                "RunnerBot/4.7.1"
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
            "HTTP error:",
            e
        )

        return None


def dex_verify(
    address: str
) -> Optional[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}/latest/dex/tokens/"
        f"{urllib.parse.quote(address)}"
    )

    data = http_json(url)

    if not isinstance(data, dict):
        return None

    pairs = data.get("pairs") or []

    if not pairs:
        return None

    sol_pairs = [
        p for p in pairs
        if p.get("chainId") == "solana"
    ]

    if not sol_pairs:
        return None

    def pair_liquidity(pair):

        return safe_float(
            (pair.get("liquidity") or {}).get(
                "usd"
            )
        )

    sol_pairs.sort(
        key=pair_liquidity,
        reverse=True
    )

    pair = sol_pairs[0]

    return {
        "dex": pair.get(
            "dexId"
        ),

        "pair_address": pair.get(
            "pairAddress"
        ),

        "liquidity": pair_liquidity(
            pair
        ),

        "price_usd": safe_float(
            pair.get(
                "priceUsd"
            )
        ),

        "fdv": safe_float(
            pair.get(
                "fdv"
            )
        ),

        "market_cap": safe_float(
            pair.get(
                "marketCap"
            )
        ),

        "volume_5m": safe_float(
            (pair.get("volume") or {}).get(
                "m5"
            )
        ),

        "volume_1h": safe_float(
            (pair.get("volume") or {}).get(
                "h1"
            )
        ),

        "price_change_5m": safe_float(
            (pair.get("priceChange") or {}).get(
                "m5"
            )
        ),

        "price_change_1h": safe_float(
            (pair.get("priceChange") or {}).get(
                "h1"
            )
        ),
    }


# ============================================================
# HARD VALIDATION
# ============================================================

def reject(
    address: str,
    reason: str
) -> bool:

    STATE["stats"]["rejected"] += 1

    counts = STATE[
        "stats"
    ]["rejections"]

    counts[reason] = (
        counts.get(reason, 0) + 1
    )

    STATE[
        "recent_rejections"
    ].append(
        {
            "time": iso_now(),
            "address": address,
            "reason": reason,
        }
    )

    STATE[
        "recent_rejections"
    ] = STATE[
        "recent_rejections"
    ][-MAX_HISTORY:]

    return False


def validate_candidate(
    metrics: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    address = metrics["address"]

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    age = age_hours(
        metrics["creation_timestamp"]
    )

    if age < MIN_AGE_HOURS:
        reject(
            address,
            "AGE_BELOW_10H"
        )
        return None

    if age > MAX_AGE_HOURS:
        reject(
            address,
            "AGE_OVER_72H"
        )
        return None

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    mc = metrics["market_cap"]

    if mc < MIN_MC:
        reject(
            address,
            "MC_BELOW_30K"
        )
        return None

    if mc > MAX_MC:
        reject(
            address,
            "MC_ABOVE_400K"
        )
        return None

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if metrics["liquidity"] < MIN_LIQUIDITY:
        reject(
            address,
            "LIQUIDITY_BELOW_25K"
        )
        return None

    # --------------------------------------------------------
    # HOLDERS
    # --------------------------------------------------------

    if metrics["holders"] < MIN_HOLDERS:
        reject(
            address,
            "HOLDERS_BELOW_300"
        )
        return None

    # --------------------------------------------------------
    # DEV
    # --------------------------------------------------------

    if metrics["dev_team_rate"] > MAX_DEV_RATE:
        reject(
            address,
            "DEV_OVER_4PCT"
        )
        return None

    # --------------------------------------------------------
    # TOP 10
    # --------------------------------------------------------

    if metrics["top10_rate"] > MAX_TOP10_RATE:
        reject(
            address,
            "TOP10_OVER_25PCT"
        )
        return None

    # --------------------------------------------------------
    # MINT
    # --------------------------------------------------------

    if metrics["renounced_mint"] is not True:
        reject(
            address,
            "MINT_NOT_REVOKED"
        )
        return None

    # --------------------------------------------------------
    # FREEZE
    # --------------------------------------------------------

    if metrics["renounced_freeze"] is not True:
        reject(
            address,
            "FREEZE_NOT_REVOKED"
        )
        return None

    # --------------------------------------------------------
    # SOCIAL PRESENCE
    # --------------------------------------------------------

    if len(metrics["socials"]) == 0:
        reject(
            address,
            "NO_SOCIAL_PRESENCE"
        )
        return None

    # --------------------------------------------------------
    # VOLUME / MC
    # --------------------------------------------------------

    if mc <= 0:
        reject(
            address,
            "INVALID_MC"
        )
        return None

    volume_mc_ratio = (
        metrics["volume_5m"] / mc
    )

    if volume_mc_ratio < MIN_VOLUME_MC_PCT:
        reject(
            address,
            "VOLUME_MC_BELOW_5PCT"
        )
        return None

    # --------------------------------------------------------
    # HOLDER GROWTH
    # --------------------------------------------------------

    growth = update_holder_history(
        address,
        metrics["holders"]
    )

    if not growth["growing"]:
        reject(
            address,
            "HOLDERS_NOT_GROWING"
        )
        return None

    # --------------------------------------------------------
    # BUY PRESSURE
    # --------------------------------------------------------

    if not pressure_recovering(
        address,
        metrics
    ):
        reject(
            address,
            "BUY_PRESSURE_NOT_POSITIVE_OR_RECOVERING"
        )
        return None

    return {
        "growth": growth,
        "volume_mc_ratio": volume_mc_ratio,
    }


# ============================================================
# PRESSURE HISTORY
# ============================================================

def store_pressure_history(
    address: str,
    metrics: Dict[str, Any]
) -> None:

    candidate = STATE[
        "candidates"
    ].setdefault(
        address,
        {}
    )

    history = candidate.setdefault(
        "pressure_history",
        []
    )

    pressure = calculate_pressure(
        metrics
    )

    history.append(
        {
            "time": now_ts(),
            "p5": pressure["p5"],
            "p1": pressure["p1"],
        }
    )

    cutoff = now_ts() - 6 * 60 * 60

    history[:] = [
        x for x in history
        if safe_int(
            x.get("time")
        ) >= cutoff
    ]

    if len(history) > 100:
        del history[:-100]


# ============================================================
# CONFIRMATIONS
# ============================================================

def confirmation_flags(
    metrics: Dict[str, Any],
    growth: Dict[str, Any]
) -> List[str]:

    flags = []

    # Smart money
    if metrics["smart_wallets"] > 0:
        flags.append(
            f"Smart Money {metrics['smart_wallets']}"
        )

    if metrics["smart_wallets"] >= 3:
        flags.append(
            "Strong Smart Money"
        )

    # KOL
    if metrics["renowned_wallets"] > 0:
        flags.append(
            f"KOL {metrics['renowned_wallets']}"
        )

    # Holder growth
    if growth["change"] > 0:
        flags.append(
            f"Holders +{growth['change']}"
        )

    # Bundlers
    if metrics["bundler_wallets"] == 0:
        flags.append(
            "No GMGN bundler-wallet signal"
        )

    # Social
    if len(metrics["socials"]) >= 2:
        flags.append(
            "Multiple socials"
        )

    # Buy pressure
    pressure = calculate_pressure(
        metrics
    )

    if pressure["positive_5m"]:
        flags.append(
            "5m buying pressure"
        )
    elif pressure["positive_1h"]:
        flags.append(
            "1h buying pressure"
        )

    # Volume
    if (
        metrics["market_cap"] > 0
        and
        metrics["volume_5m"]
        / metrics["market_cap"]
        >= 0.10
    ):
        flags.append(
            "Strong volume/MC"
        )

    return flags


# ============================================================
# OBSERVATION SYSTEM
# ============================================================

def observation_count(
    address: str
) -> int:

    return safe_int(
        STATE[
            "observations"
        ].get(address, 0)
    )


def observation_label(
    count: int
) -> str:

    if count <= 1:
        return "ULTRA"

    if count == 2:
        return "STRONG"

    return "NORMAL"


def register_observation(
    address: str,
    metrics: Dict[str, Any]
) -> int:

    current = observation_count(
        address
    )

    current += 1

    STATE[
        "observations"
    ][address] = current

    return current


# ============================================================
# ALERT
# ============================================================

def build_alert(
    metrics: Dict[str, Any],
    dex: Dict[str, Any],
    growth: Dict[str, Any],
    observations: int,
    confirmations: List[str]
) -> str:

    label = observation_label(
        observations
    )

    pressure = calculate_pressure(
        metrics
    )

    volume_mc = (
        metrics["volume_5m"]
        / metrics["market_cap"]
        if metrics["market_cap"] > 0
        else 0
    )

    text = (
        "🔥 SECOND-WAVE RUNNER\n\n"

        f"{metrics['symbol']} — {label}\n"

        f"Observation: {observations}\n"

        f"Age: {age_hours(metrics['creation_timestamp']):.1f}h\n"

        f"MC: {usd(metrics['market_cap'])}\n"

        f"Liquidity: {usd(metrics['liquidity'])}\n"

        f"Holders: {metrics['holders']}"
        f" (+{growth['change']})\n"

        f"Dev Team: {pct(metrics['dev_team_rate'])}\n"

        f"Top 10: {pct(metrics['top10_rate'])}\n\n"

        "📊 MOMENTUM\n"

        f"5m Vol: {usd(metrics['volume_5m'])}\n"

        f"5m Vol/MC: {pct(volume_mc)}\n"

        f"5m Buy Vol: {usd(metrics['buy_volume_5m'])}\n"

        f"5m Sell Vol: {usd(metrics['sell_volume_5m'])}\n"

        f"1h Buy Vol: {usd(metrics['buy_volume_1h'])}\n"

        f"1h Sell Vol: {usd(metrics['sell_volume_1h'])}\n\n"

        "🛡 GMGN SECURITY\n"

        "Mint: REVOKED\n"

        "Freeze: REVOKED\n"

        f"Rug Ratio: {metrics['rug_ratio']:.3f}\n\n"

        "👛 WALLET STRUCTURE\n"

        f"Smart Money: {metrics['smart_wallets']}\n"

        f"KOL: {metrics['renowned_wallets']}\n"

        f"Snipers: {metrics['sniper_wallets']}\n"

        f"Bundlers: {metrics['bundler_wallets']}\n"

        f"Rat Traders: {metrics['rat_wallets']}\n\n"

        "⚡ CONFIRMATIONS\n"
    )

    if confirmations:

        for item in confirmations[:8]:

            text += f"• {item}\n"

    else:

        text += "• Core second-wave conditions met\n"

    text += (
        "\n🔎 DEX VERIFICATION\n"

        f"DEX: {dex.get('dex', 'unknown')}\n"

        f"DEX Liquidity: "
        f"{usd(dex.get('liquidity', 0))}\n"

        f"DEX 5m Volume: "
        f"{usd(dex.get('volume_5m', 0))}\n"

        f"DEX 1h Change: "
        f"{dex.get('price_change_1h', 0):.2f}%\n\n"

        f"Contract:\n{metrics['address']}\n\n"

        "⚠️ Research signal only. "
        "Not a guarantee of future performance."
    )

    return text


# ============================================================
# PROCESS CANDIDATE
# ============================================================

def process_candidate(
    item: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    address = str(
        item.get("address", "")
    ).strip()

    if not address:
        return None

    # --------------------------------------------------------
    # Cooldown
    # --------------------------------------------------------

    cooldown = safe_int(
        STATE["cooldowns"].get(address)
    )

    if cooldown > now_ts():
        return None

    # --------------------------------------------------------
    # GMGN INFO
    # --------------------------------------------------------

    info = gmgn_token_info(
        address
    )

    if not info:
        reject(
            address,
            "GMGN_INFO_FAILED"
        )
        return None

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    security = gmgn_security(
        address
    )

    if not security:
        reject(
            address,
            "GMGN_SECURITY_FAILED"
        )
        return None

    metrics = extract_token_metrics(
        info,
        security
    )

    store_pressure_history(
        address,
        metrics
    )

    result = validate_candidate(
        metrics
    )

    if not result:
        return None

    # --------------------------------------------------------
    # DEX SECONDARY VERIFICATION
    # --------------------------------------------------------

    dex = dex_verify(
        address
    )

    if not dex:
        reject(
            address,
            "DEX_VERIFICATION_FAILED"
        )
        return None

    # DEX liquidity should not contradict GMGN badly.
    if dex["liquidity"] > 0:

        if dex["liquidity"] < MIN_LIQUIDITY:

            reject(
                address,
                "DEX_LIQUIDITY_BELOW_25K"
            )

            return None

    STATE[
        "stats"
    ]["dex_verified"] += 1

    # --------------------------------------------------------
    # OBSERVATION
    # --------------------------------------------------------

    observations = register_observation(
        address,
        metrics
    )

    confirmations = confirmation_flags(
        metrics,
        result["growth"]
    )

    STATE[
        "stats"
    ]["qualified"] += 1

    STATE[
        "stats"
    ]["qualifications"] += 1

    STATE[
        "recent_qualifications"
    ].append(
        {
            "time": iso_now(),
            "address": address,
            "symbol": metrics["symbol"],
            "mc": metrics["market_cap"],
            "holders": metrics["holders"],
            "observations": observations,
        }
    )

    STATE[
        "recent_qualifications"
    ] = STATE[
        "recent_qualifications"
    ][-MAX_HISTORY:]

    # --------------------------------------------------------
    # ALERT COOLDOWN
    # --------------------------------------------------------

    if now_ts() - safe_int(
        STATE["last_global_alert"]
    ) < GLOBAL_ALERT_COOLDOWN:

        return {
            "metrics": metrics,
            "dex": dex,
            "growth": result["growth"],
            "observations": observations,
            "confirmations": confirmations,
            "alert_sent": False,
        }

    token_cooldown = safe_int(
        STATE["cooldowns"].get(address)
    )

    if token_cooldown > now_ts():

        return {
            "metrics": metrics,
            "dex": dex,
            "growth": result["growth"],
            "observations": observations,
            "confirmations": confirmations,
            "alert_sent": False,
        }

    # --------------------------------------------------------
    # SEND
    # --------------------------------------------------------

    alert = build_alert(
        metrics,
        dex,
        result["growth"],
        observations,
        confirmations
    )

    sent = broadcast(
        alert
    )

    if sent > 0:

        STATE[
            "last_global_alert"
        ] = now_ts()

        STATE[
            "cooldowns"
        ][address] = (
            now_ts()
            + TOKEN_COOLDOWN
        )

        STATE[
            "stats"
        ]["alerts_sent"] += sent

        STATE[
            "recent_alerts"
        ].append(
            {
                "time": iso_now(),
                "address": address,
                "symbol": metrics["symbol"],
                "observations": observations,
                "sent": sent,
            }
        )

        STATE[
            "recent_alerts"
        ] = STATE[
            "recent_alerts"
        ][-MAX_HISTORY:]

        print(
            f"🚨 ALERT {metrics['symbol']} "
            f"| {observation_label(observations)} "
            f"| sent={sent}"
        )

    return {
        "metrics": metrics,
        "dex": dex,
        "growth": result["growth"],
        "observations": observations,
        "confirmations": confirmations,
        "alert_sent": sent > 0,
    }


# ============================================================
# COMMAND HANDLER
# ============================================================

def command_response(
    chat_id: str,
    command: str
) -> Optional[str]:

    command = command.lower().strip()

    if command.startswith("/start"):

        STATE[
            "telegram_chats"
        ][str(chat_id)] = True

        save_state()

        return (
            "🔥 Runner Bot V4.7.1 online.\n\n"

            "GMGN-first second-wave scanner.\n"

            "Age: 10h–72h\n"
            "MC: $30k–$400k\n"
            "Liquidity: >= $25k\n"
            "Holders: >=300 + growing\n"
            "Dev: <=4%\n"
            "Top 10: <=25%\n"
            "Mint + Freeze: revoked\n\n"

            "You are subscribed to alerts."
        )

    if command.startswith("/stop"):

        STATE[
            "telegram_chats"
        ][str(chat_id)] = False

        save_state()

        return (
            "Alerts stopped for this chat."
        )

    if command.startswith("/alerts"):

        enabled = STATE[
            "telegram_chats"
        ].get(
            str(chat_id),
            False
        )

        return (
            "Alerts: "
            + ("ON" if enabled else "OFF")
        )

    if command.startswith("/status"):

        stats = STATE["stats"]

        return (
            "🔥 RUNNER BOT V4.7.1\n\n"

            f"Scans: {stats['total_scans']}\n"

            f"GMGN discoveries: "
            f"{stats['gmgn_discoveries']}\n"

            f"GMGN validated: "
            f"{stats['gmgn_validated']}\n"

            f"DEX verified: "
            f"{stats['dex_verified']}\n"

            f"Qualified: "
            f"{stats['qualified']}\n"

            f"Alerts: "
            f"{stats['alerts_sent']}\n\n"

            f"Tracked tokens: "
            f"{len(STATE['candidates'])}\n"

            f"Holder histories: "
            f"{len(STATE['holder_history'])}\n"
        )

    if command.startswith("/tracking"):

        observations = STATE[
            "observations"
        ]

        if not observations:

            return (
                "No qualified second-wave tokens "
                "have been observed yet."
            )

        rows = []

        for address, count in sorted(
            observations.items(),
            key=lambda x: x[1],
            reverse=True
        )[:15]:

            candidate = STATE[
                "candidates"
            ].get(address, {})

            symbol = candidate.get(
                "symbol",
                address[:6]
            )

            rows.append(
                f"{symbol}: "
                f"{observation_label(count)} "
                f"({count})"
            )

        return (
            "📡 SECOND-WAVE TRACKING\n\n"
            + "\n".join(rows)
        )

    if command.startswith("/scan"):

        return (
            "Manual scan will run on the "
            "next scanner cycle."
        )

    return None


def poll_telegram(
    offset: Optional[int]
) -> Optional[int]:

    if not TELEGRAM_TOKEN:
        return offset

    params = {
        "timeout": 1
    }

    if offset is not None:
        params["offset"] = offset

    query = urllib.parse.urlencode(
        params
    )

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_TOKEN}/getUpdates?"
        f"{query}"
    )

    try:

        with urllib.request.urlopen(
            url,
            timeout=5
        ) as response:

            data = json.loads(
                response.read().decode()
            )

        if not data.get("ok"):
            return offset

        for update in data.get(
            "result",
            []
        ):

            offset = (
                update["update_id"] + 1
            )

            message = update.get(
                "message"
            )

            if not message:
                continue

            chat = message.get(
                "chat",
                {}
            )

            chat_id = str(
                chat.get("id")
            )

            text = message.get(
                "text",
                ""
            )

            if not text.startswith("/"):
                continue

            response_text = command_response(
                chat_id,
                text
            )

            if response_text:
                send_message(
                    chat_id,
                    response_text
                )

        return offset

    except Exception:
        return offset


# ============================================================
# SCAN
# ============================================================

def scan_cycle() -> None:

    STATE[
        "stats"
    ]["total_scans"] += 1

    current = now_ts()

    # --------------------------------------------------------
    # GMGN discovery every 5 minutes
    # --------------------------------------------------------

    if (
        current
        - safe_int(
            STATE["last_gmgn_discovery"]
        )
        >= GMGN_DISCOVERY_INTERVAL
    ):

        candidates = gmgn_discover()

        STATE[
            "last_gmgn_discovery"
        ] = current

        STATE[
            "stats"
        ]["gmgn_discoveries"] += len(
            candidates
        )

        print(
            f"GMGN Discovery: "
            f"{len(candidates)} candidates"
        )

        for item in candidates:

            address = str(
                item.get("address", "")
            ).strip()

            if not address:
                continue

            existing = STATE[
                "candidates"
            ].setdefault(
                address,
                {}
            )

            existing.update(
                {
                    "symbol": item.get(
                        "symbol",
                        existing.get(
                            "symbol",
                            "UNKNOWN"
                        )
                    ),
                    "name": item.get(
                        "name",
                        existing.get(
                            "name",
                            ""
                        )
                    ),
                    "last_discovered": current,
                }
            )

            STATE[
                "stats"
            ]["candidates_seen"] += 1

    # --------------------------------------------------------
    # Select validation queue
    # --------------------------------------------------------

    queue = []

    for address, candidate in STATE[
        "candidates"
    ].items():

        last_validated = safe_int(
            candidate.get(
                "last_validated"
            )
        )

        if (
            current - last_validated
            >= GMGN_VALIDATION_INTERVAL
        ):

            queue.append(
                address
            )

    # Newest first
    queue.sort(
        key=lambda a:
        safe_int(
            STATE["candidates"]
            .get(a, {})
            .get(
                "last_discovered"
            )
        ),
        reverse=True
    )

    queue = queue[
        :MAX_GMGN_VALIDATIONS_PER_CYCLE
    ]

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    for address in queue:

        candidate = STATE[
            "candidates"
        ].get(address)

        if not candidate:
            continue

        print(
            f"Validating "
            f"{candidate.get('symbol', address[:6])}"
        )

        result = process_candidate(
            {
                "address": address,
                "symbol": candidate.get(
                    "symbol",
                    "UNKNOWN"
                ),
            }
        )

        candidate[
            "last_validated"
        ] = current

        if result:

            metrics = result[
                "metrics"
            ]

            candidate.update(
                {
                    "symbol": metrics[
                        "symbol"
                    ],
                    "market_cap": metrics[
                        "market_cap"
                    ],
                    "holders": metrics[
                        "holders"
                    ],
                    "last_qualified": current,
                }
            )

            STATE[
                "stats"
            ]["gmgn_validated"] += 1

    save_state()


# ============================================================
# STARTUP
# ============================================================

def main():

    print(
        "=" * 60
    )

    print(
        f"RUNNER BOT {BOT_VERSION}"
    )

    print(
        "GMGN-FIRST SECOND-WAVE 5–100X TRACKER"
    )

    print(
        "=" * 60
    )

    print(
        f"Age: {MIN_AGE_HOURS}h–"
        f"{MAX_AGE_HOURS}h"
    )

    print(
        f"MC: ${MIN_MC:,}–"
        f"${MAX_MC:,}"
    )

    print(
        f"Liquidity: >= ${MIN_LIQUIDITY:,}"
    )

    print(
        f"Holders: >= {MIN_HOLDERS} + growing"
    )

    print(
        f"Dev: <= {MAX_DEV_RATE * 100:.1f}%"
    )

    print(
        f"Top 10: <= {MAX_TOP10_RATE * 100:.1f}%"
    )

    print(
        f"Volume/MC: >= "
        f"{MIN_VOLUME_MC_PCT * 100:.1f}%"
    )

    print(
        "Mint + Freeze: revoked"
    )

    print(
        "GMGN: PRIMARY"
    )

    print(
        "DexScreener: SECONDARY"
    )

    print()

    print(
        "Checking GMGN CLI..."
    )

    gmgn_config_check()

    if not TELEGRAM_TOKEN:

        print(
            "WARNING: "
            "TELEGRAM_BOT_TOKEN is missing."
        )

    print()

    print(
        "Bot loop started."
    )

    offset = None

    last_scan = 0

    while True:

        try:

            offset = poll_telegram(
                offset
            )

            current = now_ts()

            if (
                current - last_scan
                >= SCAN_INTERVAL
            ):

                last_scan = current

                scan_cycle()

                print(
                    "Scan complete | "
                    f"tracked="
                    f"{len(STATE['candidates'])} | "
                    f"qualified="
                    f"{STATE['stats']['qualified']} | "
                    f"alerts="
                    f"{STATE['stats']['alerts_sent']}"
                )

            time.sleep(1)

        except KeyboardInterrupt:

            print(
                "Bot stopped."
            )

            save_state()

            break

        except Exception as e:

            print(
                "Main loop error:",
                e
            )

            save_state()

            time.sleep(5)


if __name__ == "__main__":
    main()
