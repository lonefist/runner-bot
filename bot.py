import json
import os
import time
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ============================================================
# RUNNER BOT V4.7.2
# GMGN-FIRST SECOND-WAVE
# ============================================================

BOT_VERSION = "V4.7.2-SECOND-WAVE-GMGN"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

STATE_FILE = "runner_state_v472.json"

SCAN_INTERVAL = 15

# ============================================================
# STRATEGY
# ============================================================

MIN_AGE_HOURS = 10
MAX_AGE_HOURS = 72

MIN_MC = 30_000
MAX_MC = 400_000

MIN_LIQUIDITY = 25_000

MIN_HOLDERS = 300

MAX_DEV_RATE = 0.04
MAX_TOP10_RATE = 0.25

MIN_VOLUME_MC_PCT = 0.05

# ============================================================
# GMGN
# ============================================================

GMGN_DISCOVERY_INTERVAL = 300
GMGN_VALIDATION_INTERVAL = 300

MAX_GMGN_CANDIDATES = 30
MAX_VALIDATIONS_PER_CYCLE = 5

# ============================================================
# HOLDER TRACKING
# ============================================================

HOLDER_WINDOW = 3 * 60 * 60

# ============================================================
# ALERTS
# ============================================================

GLOBAL_ALERT_COOLDOWN = 60
TOKEN_ALERT_COOLDOWN = 30 * 60

MAX_HISTORY = 5000

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()


# ============================================================
# TIME
# ============================================================

def now() -> int:
    return int(time.time())


def iso_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def safe_float(
    value: Any,
    default: float = 0.0
) -> float:

    try:

        if value is None:
            return default

        return float(
            str(value)
            .replace(",", "")
            .replace("$", "")
            .strip()
        )

    except Exception:
        return default


def safe_int(
    value: Any,
    default: int = 0
) -> int:

    try:
        return int(float(value))
    except Exception:
        return default


def age_hours(timestamp: int) -> float:

    if not timestamp:
        return 999999

    return max(
        0,
        (now() - timestamp) / 3600
    )


def money(value: float) -> str:

    return f"${value:,.0f}"


def percentage(value: float) -> str:

    return f"{value * 100:.2f}%"


# ============================================================
# STATE
# ============================================================

def default_state():

    return {

        "started_at": iso_now(),

        "telegram_chats": {},

        "candidates": {},

        "holder_history": {},

        "observations": {},

        "cooldowns": {},

        "last_discovery": 0,

        "stats": {

            "scans": 0,

            "gmgn_discovered": 0,

            "gmgn_validated": 0,

            "dex_verified": 0,

            "qualified": 0,

            "alerts": 0,

            "rejected": 0,

            "rejections": {},
        },

        "recent_rejections": [],

        "recent_qualified": [],

        "recent_alerts": [],
    }


def load_state():

    if not os.path.exists(
        STATE_FILE
    ):
        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

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

        print(
            "State error:",
            e
        )

        return default_state()


STATE = load_state()


def save_state():

    try:

        tmp = STATE_FILE + ".tmp"

        with open(
            tmp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                STATE,
                f,
                indent=2
            )

        os.replace(
            tmp,
            STATE_FILE
        )

    except Exception as e:

        print(
            "State save error:",
            e
        )


# ============================================================
# GMGN CLI LOCATOR
# ============================================================

def locate_gmgn():

    candidates = []

    # PATH
    path = shutil.which(
        "gmgn-cli"
    )

    if path:
        candidates.append(
            ["gmgn-cli"]
        )

    # Common npm locations
    candidates.extend([
        ["/usr/local/bin/gmgn-cli"],
        ["/usr/bin/gmgn-cli"],
        ["/root/.npm-global/bin/gmgn-cli"],
        ["/home/runner/.npm-global/bin/gmgn-cli"],
    ])

    # Check candidates
    for candidate in candidates:

        executable = candidate[0]

        if os.path.isfile(
            executable
        ) and os.access(
            executable,
            os.X_OK
        ):

            return candidate

    # npx fallback
    npx = shutil.which(
        "npx"
    )

    if npx:

        return [
            npx,
            "--yes",
            "gmgn-cli@latest"
        ]

    return None


GMGN_COMMAND = locate_gmgn()


def print_gmgn_status():

    global GMGN_COMMAND

    GMGN_COMMAND = locate_gmgn()

    if GMGN_COMMAND:

        print(
            "GMGN executable:",
            " ".join(GMGN_COMMAND)
        )

    else:

        print(
            "GMGN executable not found."
        )

        print(
            "The bot will attempt npx "
            "fallback when available."
        )


# ============================================================
# GMGN COMMAND
# ============================================================

def gmgn_run(
    args: List[str],
    raw: bool = True,
    timeout: int = 60
):

    global GMGN_COMMAND

    if not GMGN_COMMAND:

        GMGN_COMMAND = locate_gmgn()

    if not GMGN_COMMAND:

        return None

    command = list(
        GMGN_COMMAND
    )

    command.extend(args)

    if raw:
        command.append(
            "--raw"
        )

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:

            print(
                "GMGN error:",
                result.stderr.strip()[:500]
            )

            return None

        output = result.stdout.strip()

        if not output:
            return None

        return output

    except Exception as e:

        print(
            "GMGN execution error:",
            e
        )

        return None


def gmgn_json(
    args: List[str]
):

    output = gmgn_run(
        args,
        raw=True
    )

    if not output:
        return None

    try:

        return json.loads(
            output
        )

    except Exception:

        # Some CLIs may put additional
        # text around JSON.
        start = output.find("{")
        end = output.rfind("}")

        if (
            start >= 0
            and end > start
        ):

            try:

                return json.loads(
                    output[
                        start:end + 1
                    ]
                )

            except Exception:
                pass

        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram(
    method: str,
    payload: Optional[
        Dict[str, Any]
    ] = None
):

    if not TELEGRAM_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_TOKEN}/{method}"
    )

    try:

        data = None

        if payload:

            data = urllib.parse.urlencode(
                payload
            ).encode()

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

            return json.loads(
                response.read().decode()
            )

    except Exception as e:

        print(
            "Telegram error:",
            e
        )

        return None


def send_message(
    chat_id: str,
    text: str
):

    result = telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview":
                "true"
        }
    )

    return bool(
        result
        and result.get("ok")
    )


def broadcast(
    text: str
):

    count = 0

    for chat_id, enabled in list(
        STATE[
            "telegram_chats"
        ].items()
    ):

        if not enabled:
            continue

        if send_message(
            chat_id,
            text
        ):

            count += 1

    return count


# ============================================================
# GMGN DISCOVERY
# ============================================================

def gmgn_discover():

    print(
        "GMGN discovery: "
        "searching second-wave tokens..."
    )

    # We intentionally use broad discovery
    # and perform the exact hard filters locally.
    #
    # This avoids depending on optional
    # server-side filter names.

    data = gmgn_json(
        [
            "market",
            "trending",

            "--chain",
            "sol",

            "--interval",
            "1h",

            "--max-created",
            "72h",

            "--min-liquidity",
            str(MIN_LIQUIDITY),

            "--order-by",
            "volume",

            "--limit",
            str(MAX_GMGN_CANDIDATES),
        ]
    )

    if data is None:
        return []

    rows = []

    if isinstance(
        data,
        list
    ):

        rows = data

    elif isinstance(
        data,
        dict
    ):

        nested = data.get(
            "data"
        )

        if isinstance(
            nested,
            list
        ):

            rows = nested

        elif isinstance(
            nested,
            dict
        ):

            for key in [
                "rank",
                "tokens",
                "list",
                "data"
            ]:

                if isinstance(
                    nested.get(key),
                    list
                ):

                    rows = nested[key]
                    break

        if not rows:

            for key in [
                "rank",
                "tokens",
                "list"
            ]:

                if isinstance(
                    data.get(key),
                    list
                ):

                    rows = data[key]
                    break

    output = []

    for row in rows:

        if not isinstance(
            row,
            dict
        ):
            continue

        address = str(
            row.get(
                "address",
                ""
            )
        ).strip()

        if not address:
            continue

        output.append(
            row
        )

    return output


# ============================================================
# TOKEN INFO
# ============================================================

def gmgn_info(
    address: str
):

    return gmgn_json(
        [
            "token",
            "info",
            "--chain",
            "sol",
            "--address",
            address
        ]
    )


def gmgn_security(
    address: str
):

    return gmgn_json(
        [
            "token",
            "security",
            "--chain",
            "sol",
            "--address",
            address
        ]
    )


# ============================================================
# METRICS
# ============================================================

def extract_metrics(
    info: Dict[str, Any],
    security: Dict[str, Any]
):

    price_data = (
        info.get("price")
        or {}
    )

    stat = (
        info.get("stat")
        or {}
    )

    dev = (
        info.get("dev")
        or {}
    )

    links = (
        info.get("link")
        or {}
    )

    wallet_tags = (
        info.get(
            "wallet_tags_stat"
        )
        or {}
    )

    price = safe_float(
        price_data.get(
            "price"
        )
    )

    supply = safe_float(
        info.get(
            "circulating_supply"
        )
    )

    market_cap = (
        price * supply
    )

    if market_cap <= 0:

        market_cap = safe_float(
            info.get(
                "market_cap"
            )
        )

    top10 = safe_float(
        stat.get(
            "top_10_holder_rate"
        )
    )

    dev_rate = safe_float(
        stat.get(
            "dev_team_hold_rate"
        )
    )

    # Some responses may expose the
    # creator separately.
    if dev_rate == 0:

        dev_rate = safe_float(
            dev.get(
                "dev_team_hold_rate"
            )
        )

    socials = []

    for field in [
        "twitter_username",
        "telegram",
        "website",
        "discord"
    ]:

        if links.get(field):
            socials.append(
                field
            )

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

        "liquidity":
            safe_float(
                info.get(
                    "liquidity"
                )
            ),

        "holders":
            safe_int(
                info.get(
                    "holder_count"
                )
            ),

        "created":
            safe_int(
                info.get(
                    "creation_timestamp"
                )
            ),

        "top10_rate":
            top10,

        "dev_rate":
            dev_rate,

        "volume_5m":
            safe_float(
                price_data.get(
                    "volume_5m"
                )
            ),

        "volume_1h":
            safe_float(
                price_data.get(
                    "volume_1h"
                )
            ),

        "volume_6h":
            safe_float(
                price_data.get(
                    "volume_6h"
                )
            ),

        "buy_5m":
            safe_float(
                price_data.get(
                    "buy_volume_5m"
                )
            ),

        "sell_5m":
            safe_float(
                price_data.get(
                    "sell_volume_5m"
                )
            ),

        "buy_1h":
            safe_float(
                price_data.get(
                    "buy_volume_1h"
                )
            ),

        "sell_1h":
            safe_float(
                price_data.get(
                    "sell_volume_1h"
                )
            ),

        "buy_6h":
            safe_float(
                price_data.get(
                    "buy_volume_6h"
                )
            ),

        "sell_6h":
            safe_float(
                price_data.get(
                    "sell_volume_6h"
                )
            ),

        "smart_money":
            safe_int(
                wallet_tags.get(
                    "smart_wallets"
                )
            ),

        "kol":
            safe_int(
                wallet_tags.get(
                    "renowned_wallets"
                )
            ),

        "snipers":
            safe_int(
                wallet_tags.get(
                    "sniper_wallets"
                )
            ),

        "bundlers":
            safe_int(
                wallet_tags.get(
                    "bundler_wallets"
                )
            ),

        "socials":
            socials,

        "twitter":
            links.get(
                "twitter_username"
            ),

        "telegram":
            links.get(
                "telegram"
            ),

        "website":
            links.get(
                "website"
            ),

        "mint_revoked":
            security.get(
                "renounced_mint"
            ),

        "freeze_revoked":
            security.get(
                "renounced_freeze_account"
            ),

        "rug_ratio":
            safe_float(
                security.get(
                    "rug_ratio"
                )
            ),
    }


# ============================================================
# HOLDER TRACKING
# ============================================================

def holder_growth(
    address: str,
    holders: int
):

    history = STATE[
        "holder_history"
    ].setdefault(
        address,
        []
    )

    current = now()

    history.append(
        {
            "time": current,
            "holders": holders
        }
    )

    cutoff = (
        current
        - HOLDER_WINDOW
    )

    history[:] = [
        x for x in history
        if safe_int(
            x.get("time")
        ) >= cutoff
    ]

    if len(history) > 100:

        del history[:-100]

    if len(history) < 2:

        return {
            "growing": False,
            "change": 0,
            "samples": len(history)
        }

    oldest = safe_int(
        history[0].get(
            "holders"
        )
    )

    latest = safe_int(
        history[-1].get(
            "holders"
        )
    )

    return {
        "growing":
            latest > oldest,

        "change":
            latest - oldest,

        "samples":
            len(history)
    }


# ============================================================
# BUY PRESSURE
# ============================================================

def buy_pressure(
    metrics
):

    return {

        "p5":
            metrics["buy_5m"]
            - metrics["sell_5m"],

        "p1":
            metrics["buy_1h"]
            - metrics["sell_1h"],

        "p6":
            metrics["buy_6h"]
            - metrics["sell_6h"],
    }


def pressure_ok(
    address: str,
    metrics
):

    pressure = buy_pressure(
        metrics
    )

    # Positive current pressure
    if pressure["p5"] > 0:
        return True

    # Positive 1h pressure
    if pressure["p1"] > 0:
        return True

    # Recovering 5m pressure
    candidate = STATE[
        "candidates"
    ].get(
        address,
        {}
    )

    history = candidate.get(
        "pressure_history",
        []
    )

    if len(history) < 2:
        return False

    previous = safe_float(
        history[-2].get(
            "p5"
        )
    )

    current = pressure["p5"]

    return (
        current > previous
        and current >= 0
    )


def save_pressure(
    address: str,
    metrics
):

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

    p = buy_pressure(
        metrics
    )

    history.append(
        {
            "time": now(),
            "p5": p["p5"],
            "p1": p["p1"]
        }
    )

    cutoff = (
        now()
        - 6 * 60 * 60
    )

    history[:] = [
        x for x in history
        if safe_int(
            x.get("time")
        ) >= cutoff
    ]

    if len(history) > 100:
        del history[:-100]


# ============================================================
# DEXSCREENER
# ============================================================

def http_json(
    url: str
):

    try:

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                "RunnerBot/4.7.2"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:

            return json.loads(
                response.read().decode()
            )

    except Exception as e:

        print(
            "DEX error:",
            e
        )

        return None


def dex_verify(
    address: str
):

    url = (
        f"{DEX_BASE}/latest/dex/tokens/"
        f"{urllib.parse.quote(address)}"
    )

    data = http_json(
        url
    )

    if not isinstance(
        data,
        dict
    ):
        return None

    pairs = data.get(
        "pairs"
    ) or []

    pairs = [
        p for p in pairs
        if p.get(
            "chainId"
        ) == "solana"
    ]

    if not pairs:
        return None

    pairs.sort(
        key=lambda p:
        safe_float(
            (
                p.get(
                    "liquidity"
                )
                or {}
            ).get(
                "usd"
            )
        ),
        reverse=True
    )

    pair = pairs[0]

    return {

        "dex":
            pair.get(
                "dexId"
            ),

        "liquidity":
            safe_float(
                (
                    pair.get(
                        "liquidity"
                    )
                    or {}
                ).get(
                    "usd"
                )
            ),

        "volume_5m":
            safe_float(
                (
                    pair.get(
                        "volume"
                    )
                    or {}
                ).get(
                    "m5"
                )
            ),

        "volume_1h":
            safe_float(
                (
                    pair.get(
                        "volume"
                    )
                    or {}
                ).get(
                    "h1"
                )
            ),

        "price_change_5m":
            safe_float(
                (
                    pair.get(
                        "priceChange"
                    )
                    or {}
                ).get(
                    "m5"
                )
            ),

        "price_change_1h":
            safe_float(
                (
                    pair.get(
                        "priceChange"
                    )
                    or {}
                ).get(
                    "h1"
                )
            ),
    }


# ============================================================
# REJECTION
# ============================================================

def reject(
    address: str,
    reason: str
):

    STATE[
        "stats"
    ]["rejected"] += 1

    r = STATE[
        "stats"
    ]["rejections"]

    r[reason] = (
        r.get(
            reason,
            0
        ) + 1
    )

    STATE[
        "recent_rejections"
    ].append(
        {
            "time":
                iso_now(),

            "address":
                address,

            "reason":
                reason
        }
    )

    STATE[
        "recent_rejections"
    ] = STATE[
        "recent_rejections"
    ][-MAX_HISTORY:]


# ============================================================
# HARD FILTER
# ============================================================

def validate(
    metrics
):

    address = metrics[
        "address"
    ]

    age = age_hours(
        metrics["created"]
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

    mc = metrics[
        "market_cap"
    ]

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

    if metrics[
        "liquidity"
    ] < MIN_LIQUIDITY:

        reject(
            address,
            "LIQUIDITY_BELOW_25K"
        )

        return None

    if metrics[
        "holders"
    ] < MIN_HOLDERS:

        reject(
            address,
            "HOLDERS_BELOW_300"
        )

        return None

    if metrics[
        "dev_rate"
    ] > MAX_DEV_RATE:

        reject(
            address,
            "DEV_OVER_4PCT"
        )

        return None

    if metrics[
        "top10_rate"
    ] > MAX_TOP10_RATE:

        reject(
            address,
            "TOP10_OVER_25PCT"
        )

        return None

    if metrics[
        "mint_revoked"
    ] is not True:

        reject(
            address,
            "MINT_NOT_REVOKED"
        )

        return None

    if metrics[
        "freeze_revoked"
    ] is not True:

        reject(
            address,
            "FREEZE_NOT_REVOKED"
        )

        return None

    if not metrics[
        "socials"
    ]:

        reject(
            address,
            "NO_SOCIAL_PRESENCE"
        )

        return None

    if mc <= 0:

        reject(
            address,
            "INVALID_MC"
        )

        return None

    volume_ratio = (
        metrics["volume_5m"]
        / mc
    )

    if volume_ratio < MIN_VOLUME_MC_PCT:

        reject(
            address,
            "VOLUME_MC_BELOW_5PCT"
        )

        return None

    growth = holder_growth(
        address,
        metrics["holders"]
    )

    if not growth[
        "growing"
    ]:

        reject(
            address,
            "HOLDERS_NOT_GROWING"
        )

        return None

    if not pressure_ok(
        address,
        metrics
    ):

        reject(
            address,
            "BUY_PRESSURE_NOT_POSITIVE"
        )

        return None

    return {
        "growth":
            growth,

        "volume_ratio":
            volume_ratio
    }


# ============================================================
# OBSERVATIONS
# ============================================================

def add_observation(
    address: str
):

    current = safe_int(
        STATE[
            "observations"
        ].get(
            address
        )
    )

    current += 1

    STATE[
        "observations"
    ][address] = current

    return current


def observation_name(
    count: int
):

    if count <= 1:
        return "ULTRA"

    if count == 2:
        return "STRONG"

    return "NORMAL"


# ============================================================
# CONFIRMATIONS
# ============================================================

def confirmations(
    metrics,
    growth
):

    result = []

    if metrics[
        "smart_money"
    ] > 0:

        result.append(
            f"Smart Money: "
            f"{metrics['smart_money']}"
        )

    if metrics[
        "kol"
    ] > 0:

        result.append(
            f"KOL: "
            f"{metrics['kol']}"
        )

    if growth[
        "change"
    ] > 0:

        result.append(
            f"Holders +"
            f"{growth['change']}"
        )

    if metrics[
        "bundlers"
    ] == 0:

        result.append(
            "No bundler signal"
        )

    if len(
        metrics["socials"]
    ) >= 2:

        result.append(
            "Multiple socials"
        )

    p = buy_pressure(
        metrics
    )

    if p["p5"] > 0:

        result.append(
            "5m buy pressure"
        )

    elif p["p1"] > 0:

        result.append(
            "1h buy pressure"
        )

    if (
        metrics["market_cap"] > 0
        and
        metrics["volume_5m"]
        / metrics["market_cap"]
        >= 0.10
    ):

        result.append(
            "Strong volume/MC"
        )

    return result


# ============================================================
# ALERT
# ============================================================

def build_alert(
    metrics,
    dex,
    growth,
    obs,
    confirms
):

    label = observation_name(
        obs
    )

    volume_ratio = (
        metrics["volume_5m"]
        / metrics["market_cap"]
        if metrics["market_cap"] > 0
        else 0
    )

    text = (
        "🔥 SECOND-WAVE RUNNER\n\n"

        f"{metrics['symbol']} — "
        f"{label}\n"

        f"Observation: {obs}\n"

        f"Age: "
        f"{age_hours(metrics['created']):.1f}h\n"

        f"MC: "
        f"{money(metrics['market_cap'])}\n"

        f"Liquidity: "
        f"{money(metrics['liquidity'])}\n"

        f"Holders: "
        f"{metrics['holders']} "
        f"(+{growth['change']})\n"

        f"Dev: "
        f"{percentage(metrics['dev_rate'])}\n"

        f"Top 10: "
        f"{percentage(metrics['top10_rate'])}\n\n"

        "📊 MOMENTUM\n"

        f"5m Volume: "
        f"{money(metrics['volume_5m'])}\n"

        f"Volume/MC: "
        f"{percentage(volume_ratio)}\n"

        f"5m Buy: "
        f"{money(metrics['buy_5m'])}\n"

        f"5m Sell: "
        f"{money(metrics['sell_5m'])}\n"

        f"1h Buy: "
        f"{money(metrics['buy_1h'])}\n"

        f"1h Sell: "
        f"{money(metrics['sell_1h'])}\n\n"

        "🛡 GMGN SECURITY\n"

        "Mint: REVOKED\n"
        "Freeze: REVOKED\n\n"

        "👛 WALLET DATA\n"

        f"Smart Money: "
        f"{metrics['smart_money']}\n"

        f"KOL: "
        f"{metrics['kol']}\n"

        f"Snipers: "
        f"{metrics['snipers']}\n"

        f"Bundlers: "
        f"{metrics['bundlers']}\n\n"

        "⚡ CONFIRMATIONS\n"
    )

    if confirms:

        for item in confirms[:8]:

            text += (
                f"• {item}\n"
            )

    else:

        text += (
            "• Core second-wave "
            "conditions met\n"
        )

    text += (
        "\n🔎 DEX CHECK\n"

        f"DEX: "
        f"{dex.get('dex', 'unknown')}\n"

        f"DEX Liquidity: "
        f"{money(dex.get('liquidity', 0))}\n"

        f"DEX 5m Volume: "
        f"{money(dex.get('volume_5m', 0))}\n"

        f"DEX 1h Change: "
        f"{dex.get('price_change_1h', 0):.2f}%\n\n"

        "Contract:\n"

        f"{metrics['address']}\n\n"

        "⚠️ Research signal only."
    )

    return text


# ============================================================
# PROCESS TOKEN
# ============================================================

def process(
    address: str
):

    info = gmgn_info(
        address
    )

    if not info:

        reject(
            address,
            "GMGN_INFO_FAILED"
        )

        return None

    security = gmgn_security(
        address
    )

    if not security:

        reject(
            address,
            "GMGN_SECURITY_FAILED"
        )

        return None

    metrics = extract_metrics(
        info,
        security
    )

    save_pressure(
        address,
        metrics
    )

    validation = validate(
        metrics
    )

    if not validation:
        return None

    dex = dex_verify(
        address
    )

    if not dex:

        reject(
            address,
            "DEX_VERIFICATION_FAILED"
        )

        return None

    if (
        dex["liquidity"] > 0
        and
        dex["liquidity"]
        < MIN_LIQUIDITY
    ):

        reject(
            address,
            "DEX_LIQUIDITY_BELOW_25K"
        )

        return None

    STATE[
        "stats"
    ]["dex_verified"] += 1

    obs = add_observation(
        address
    )

    conf = confirmations(
        metrics,
        validation["growth"]
    )

    STATE[
        "stats"
    ]["qualified"] += 1

    STATE[
        "recent_qualified"
    ].append(
        {
            "time":
                iso_now(),

            "address":
                address,

            "symbol":
                metrics["symbol"],

            "mc":
                metrics["market_cap"],

            "holders":
                metrics["holders"],

            "observations":
                obs
        }
    )

    STATE[
        "recent_qualified"
    ] = STATE[
        "recent_qualified"
    ][-MAX_HISTORY:]

    candidate = STATE[
        "candidates"
    ].setdefault(
        address,
        {}
    )

    candidate.update(
        {
            "symbol":
                metrics["symbol"],

            "last_validated":
                now(),

            "last_qualified":
                now(),

            "market_cap":
                metrics["market_cap"],

            "holders":
                metrics["holders"]
        }
    )

    # --------------------------------------------------------
    # ALERT COOLDOWN
    # --------------------------------------------------------

    cooldown = safe_int(
        STATE[
            "cooldowns"
        ].get(
            address
        )
    )

    if cooldown > now():

        return {
            "metrics":
                metrics,

            "dex":
                dex,

            "growth":
                validation["growth"],

            "observations":
                obs,

            "confirmations":
                conf
        }

    alert = build_alert(
        metrics,
        dex,
        validation["growth"],
        obs,
        conf
    )

    sent = broadcast(
        alert
    )

    if sent > 0:

        STATE[
            "stats"
        ]["alerts"] += sent

        STATE[
            "cooldowns"
        ][address] = (
            now()
            + TOKEN_ALERT_COOLDOWN
        )

        STATE[
            "recent_alerts"
        ].append(
            {
                "time":
                    iso_now(),

                "address":
                    address,

                "symbol":
                    metrics["symbol"],

                "observations":
                    obs,

                "sent":
                    sent
            }
        )

        STATE[
            "recent_alerts"
        ] = STATE[
            "recent_alerts"
        ][-MAX_HISTORY:]

        print(
            "🚨 ALERT:",
            metrics["symbol"],
            observation_name(obs),
            "sent:",
            sent
        )

    return {
        "metrics":
            metrics,

        "dex":
            dex,

        "growth":
            validation["growth"],

        "observations":
            obs,

        "confirmations":
            conf
    }


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def command(
    chat_id: str,
    text: str
):

    cmd = text.lower().strip()

    if cmd.startswith(
        "/start"
    ):

        STATE[
            "telegram_chats"
        ][str(chat_id)] = True

        save_state()

        return (
            "🔥 Runner Bot V4.7.2 online.\n\n"

            "GMGN-first second-wave scanner.\n\n"

            "Age: 10h–72h\n"
            "MC: $30k–$400k\n"
            "Liquidity: >= $25k\n"
            "Holders: >=300 + growing\n"
            "Dev: <=4%\n"
            "Top 10: <=25%\n"
            "Mint + Freeze: revoked\n\n"

            "Alerts: ON"
        )

    if cmd.startswith(
        "/stop"
    ):

        STATE[
            "telegram_chats"
        ][str(chat_id)] = False

        save_state()

        return (
            "Alerts stopped."
        )

    if cmd.startswith(
        "/alerts"
    ):

        active = STATE[
            "telegram_chats"
        ].get(
            str(chat_id),
            False
        )

        return (
            "Alerts: "
            + (
                "ON"
                if active
                else
                "OFF"
            )
        )

    if cmd.startswith(
        "/status"
    ):

        s = STATE[
            "stats"
        ]

        return (
            "🔥 RUNNER BOT V4.7.2\n\n"

            f"Scans: "
            f"{s['scans']}\n"

            f"GMGN discovered: "
            f"{s['gmgn_discovered']}\n"

            f"GMGN validated: "
            f"{s['gmgn_validated']}\n"

            f"DEX verified: "
            f"{s['dex_verified']}\n"

            f"Qualified: "
            f"{s['qualified']}\n"

            f"Alerts: "
            f"{s['alerts']}\n\n"

            f"Tracked: "
            f"{len(STATE['candidates'])}\n"

            f"Holder histories: "
            f"{len(STATE['holder_history'])}"
        )

    if cmd.startswith(
        "/tracking"
    ):

        observations = STATE[
            "observations"
        ]

        if not observations:

            return (
                "No qualified tokens "
                "tracked yet."
            )

        rows = []

        for address, count in sorted(
            observations.items(),
            key=lambda x: x[1],
            reverse=True
        )[:15]:

            candidate = STATE[
                "candidates"
            ].get(
                address,
                {}
            )

            symbol = candidate.get(
                "symbol",
                address[:6]
            )

            rows.append(
                f"{symbol}: "
                f"{observation_name(count)} "
                f"({count})"
            )

        return (
            "📡 TRACKING\n\n"
            + "\n".join(rows)
        )

    return None


def telegram_poll(
    offset
):

    if not TELEGRAM_TOKEN:
        return offset

    params = {
        "timeout": 1
    }

    if offset is not None:
        params["offset"] = offset

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_TOKEN}/getUpdates?"
        f"{urllib.parse.urlencode(params)}"
    )

    try:

        with urllib.request.urlopen(
            url,
            timeout=5
        ) as response:

            data = json.loads(
                response.read().decode()
            )

        if not data.get(
            "ok"
        ):
            return offset

        for update in data.get(
            "result",
            []
        ):

            offset = (
                update[
                    "update_id"
                ] + 1
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
                chat.get(
                    "id"
                )
            )

            text = message.get(
                "text",
                ""
            )

            if not text.startswith(
                "/"
            ):
                continue

            response = command(
                chat_id,
                text
            )

            if response:

                send_message(
                    chat_id,
                    response
                )

        return offset

    except Exception:

        return offset


# ============================================================
# SCANNER
# ============================================================

def scan():

    STATE[
        "stats"
    ]["scans"] += 1

    current = now()

    # --------------------------------------------------------
    # Discovery
    # --------------------------------------------------------

    if (
        current
        - STATE["last_discovery"]
        >= GMGN_DISCOVERY_INTERVAL
    ):

        rows = gmgn_discover()

        STATE[
            "last_discovery"
        ] = current

        STATE[
            "stats"
        ]["gmgn_discovered"] += len(
            rows
        )

        print(
            f"GMGN Discovery: "
            f"{len(rows)} candidates"
        )

        for row in rows:

            address = str(
                row.get(
                    "address",
                    ""
                )
            ).strip()

            if not address:
                continue

            candidate = STATE[
                "candidates"
            ].setdefault(
                address,
                {}
            )

            candidate.update(
                {
                    "symbol":
                        row.get(
                            "symbol",
                            candidate.get(
                                "symbol",
                                "UNKNOWN"
                            )
                        ),

                    "name":
                        row.get(
                            "name",
                            candidate.get(
                                "name",
                                ""
                            )
                        ),

                    "last_discovered":
                        current
                }
            )

    # --------------------------------------------------------
    # Validation queue
    # --------------------------------------------------------

    queue = []

    for address, candidate in STATE[
        "candidates"
    ].items():

        last = safe_int(
            candidate.get(
                "last_validated"
            )
        )

        if (
            current - last
            >= GMGN_VALIDATION_INTERVAL
        ):

            queue.append(
                address
            )

    queue.sort(
        key=lambda address:
        safe_int(
            STATE[
                "candidates"
            ].get(
                address,
                {}
            ).get(
                "last_discovered",
                0
            )
        ),
        reverse=True
    )

    queue = queue[
        :MAX_VALIDATIONS_PER_CYCLE
    ]

    # --------------------------------------------------------
    # Process
    # --------------------------------------------------------

    for address in queue:

        candidate = STATE[
            "candidates"
        ].get(
            address,
            {}
        )

        print(
            "GMGN validating:",
            candidate.get(
                "symbol",
                address[:8]
            )
        )

        result = process(
            address
        )

        candidate[
            "last_validated"
        ] = current

        if result:

            STATE[
                "stats"
            ]["gmgn_validated"] += 1

    save_state()


# ============================================================
# MAIN
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
        f"Dev: <= "
        f"{MAX_DEV_RATE * 100:.1f}%"
    )

    print(
        f"Top 10: <= "
        f"{MAX_TOP10_RATE * 100:.1f}%"
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
        "Checking GMGN..."
    )

    print_gmgn_status()

    if not TELEGRAM_TOKEN:

        print(
            "WARNING: "
            "TELEGRAM_BOT_TOKEN is missing."
        )

    print(
        "\nBot loop started."
    )

    offset = None

    last_scan = 0

    while True:

        try:

            offset = telegram_poll(
                offset
            )

            current = now()

            if (
                current - last_scan
                >= SCAN_INTERVAL
            ):

                last_scan = current

                scan()

                print(
                    "Scan complete | "
                    f"tracked="
                    f"{len(STATE['candidates'])} | "
                    f"qualified="
                    f"{STATE['stats']['qualified']} | "
                    f"alerts="
                    f"{STATE['stats']['alerts']}"
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
