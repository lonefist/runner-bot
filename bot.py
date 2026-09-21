import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
import re
import html
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V1.5.1
# EARLY NARRATIVE + EVIDENCE VERIFIED
# ============================================================

BOT_VERSION = "V1.5.1-EARLY-NARRATIVE-EVIDENCE"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"
X_BASE = "https://api.x.com"

STATE_FILE = "runner_v151_state.json"
HISTORY_FILE = "runner_v151_history.json"

CHAIN = "solana"

# ============================================================
# MARKET RANGE
# ============================================================

MIN_MC = 20_000
PRIMARY_MAX_MC = 80_000
MAX_MC = 150_000
MIN_LIQUIDITY = 5_000

SCAN_INTERVAL = 20
DISCOVERY_INTERVAL = 300
VALIDATION_INTERVAL = 300

TRACKING_HOURS = 8
ALERT_TRACKING_HOURS = 48

MAX_DISCOVERY_CANDIDATES = 50

# ============================================================
# NORMAL MARKET MODEL
# ============================================================

WATCH_SCORE = 55
RUNNER_SCORE = 72
IDEAL_SCORE = 85

MIN_OBSERVATIONS_RUNNER = 2
MIN_OBSERVATIONS_IDEAL = 3

# ============================================================
# EARLY NARRATIVE ROUTE
# ============================================================

EARLY_NARRATIVE_ENABLED = True

EARLY_NARRATIVE_MAX_MC = 80_000
EARLY_NARRATIVE_MIN_PRICE = 5.0
EARLY_NARRATIVE_MIN_BS = 1.20
EARLY_NARRATIVE_MIN_TX = 100
EARLY_NARRATIVE_MIN_VOL_MC = 10.0
EARLY_NARRATIVE_MIN_LIQUIDITY = 10_000

EARLY_NARRATIVE_MIN_LORE_SCORE = 21
EARLY_NARRATIVE_MIN_EVIDENCE = 2

EARLY_NARRATIVE_REQUIRE_CONFIDENCE = "HIGH"
EARLY_NARRATIVE_REQUIRE_MOMENTUM = "HIGH"

# ============================================================
# DECAY
# ============================================================

DECAY_MC_CUTOFF = 100_000
DECAY_BS_MIN = 1.20
DECAY_TX_MIN = 20
DECAY_LIQ_DROP_PCT = 10.0
DECAY_VOLUME_MC = 20.0

WEAKENING_LOOKBACK = 2

# ============================================================
# LORE
# ============================================================

LORE_MIN_SCORE = 18
LORE_MIN_EVIDENCE = 2

LORE_CACHE_MINUTES = 30

LORE_MAX_X_POSTS = 25
LORE_MAX_X_CHARS = 18_000
LORE_MAX_WEBSITE_CHARS = 12_000
LORE_MAX_GITHUB_CHARS = 10_000

LORE_AI_TIMEOUT = 45

# ============================================================
# CREDENTIALS
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    ""
)

X_BEARER_TOKEN = os.getenv(
    "X_BEARER_TOKEN",
    ""
)

LORE_AI_BASE_URL = os.getenv(
    "LORE_AI_BASE_URL",
    ""
).rstrip("/")

LORE_AI_MODEL = os.getenv(
    "LORE_AI_MODEL",
    ""
)

LORE_AI_API_KEY = os.getenv(
    "LORE_AI_API_KEY",
    ""
)

HTTP_TIMEOUT = 15


# ============================================================
# UTILITY
# ============================================================

def now_ts() -> float:
    return time.time()


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

        return float(value)

    except Exception:

        return default


def safe_int(
    value: Any,
    default: int = 0
) -> int:

    try:

        if value is None:
            return default

        return int(float(value))

    except Exception:

        return default


def clamp(
    value: float,
    minimum: float,
    maximum: float
) -> float:

    return max(
        minimum,
        min(
            maximum,
            value
        )
    )


def clean_text(
    value: Any
) -> str:

    if value is None:
        return ""

    text = str(value)

    text = html.unescape(
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def truncate(
    text: str,
    maximum: int
) -> str:

    text = text or ""

    if len(text) <= maximum:
        return text

    return text[:maximum] + "\n...[TRUNCATED]"


def load_json(
    path: str,
    default: Any
) -> Any:

    try:

        if not os.path.exists(path):
            return default

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"[JSON LOAD ERROR] {path}: {e}"
        )

        return default


def save_json(
    path: str,
    data: Any
) -> None:

    temporary = (
        path
        + ".tmp"
    )

    try:

        with open(
            temporary,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temporary,
            path
        )

    except Exception as e:

        print(
            f"[JSON SAVE ERROR] {path}: {e}"
        )


# ============================================================
# HTTP
# ============================================================

def http_json(
    url: str,
    headers: Optional[
        Dict[str, str]
    ] = None,
    timeout: int = HTTP_TIMEOUT
) -> Tuple[
    Optional[Any],
    str
]:

    request = urllib.request.Request(
        url,
        headers=headers or {},
        method="GET"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

            if not raw:
                return None, "EMPTY_RESPONSE"

            return json.loads(raw), ""

    except urllib.error.HTTPError as e:

        try:
            body = e.read().decode(
                "utf-8",
                errors="replace"
            )
        except Exception:
            body = ""

        return (
            None,
            f"HTTP_{e.code}:{truncate(body, 300)}"
        )

    except Exception as e:

        return (
            None,
            str(e)
        )


def http_text(
    url: str,
    headers: Optional[
        Dict[str, str]
    ] = None,
    timeout: int = HTTP_TIMEOUT
) -> Tuple[
    str,
    str
]:

    request = urllib.request.Request(
        url,
        headers=headers or {},
        method="GET"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

            return raw, ""

    except urllib.error.HTTPError as e:

        return (
            "",
            f"HTTP_{e.code}"
        )

    except Exception as e:

        return (
            "",
            str(e)
        )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(
    method: str,
    payload: Optional[
        Dict[str, Any]
    ] = None
) -> Optional[Dict[str, Any]]:

    if not TELEGRAM_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_TOKEN}/"
        f"{method}"
    )

    body = None

    headers = {
        "Content-Type":
            "application/json"
    }

    if payload is not None:

        body = json.dumps(
            payload
        ).encode(
            "utf-8"
        )

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

            return json.loads(raw)

    except Exception as e:

        print(
            "[TELEGRAM ERROR] "
            + str(e)
        )

        return None


def send_message(
    chat_id: Any,
    text: str
) -> bool:

    result = telegram_request(
        "sendMessage",
        {
            "chat_id":
                chat_id,

            "text":
                text,

            "disable_web_page_preview":
                True
        }
    )

    return bool(
        result
        and result.get(
            "ok"
        )
    )


def send_alert(
    state: Dict[str, Any],
    record: Dict[str, Any],
    snapshot: Dict[str, Any],
    score: int,
    lore: Dict[str, Any],
    alert: str,
    alert_path: str
) -> None:

    subscribers = state.get(
        "subscribers",
        []
    )

    print(
        "======================================================"
    )

    print("[ALERT]")
    print(alert)

    print(
        "======================================================"
    )

    sent_count = 0

    for chat_id in subscribers:

        if send_message(
            chat_id,
            alert
        ):

            sent_count += 1

    record["alerted"] = True

    record["alert_mc"] = snapshot[
        "market_cap"
    ]

    record["alert_path"] = alert_path

    record.setdefault(
        "alerts",
        []
    ).append(
        {
            "timestamp":
                iso_now(),

            "market_cap":
                snapshot[
                    "market_cap"
                ],

            "score":
                score,

            "alert_path":
                alert_path,

            "lore_score":
                lore.get(
                    "lore_score",
                    0
                ),

            "lore_confidence":
                lore.get(
                    "confidence",
                    "LOW"
                ),

            "lore_momentum":
                lore.get(
                    "momentum",
                    "LOW"
                ),

            "subscribers_notified":
                sent_count
        }
    )

    save_json(
        STATE_FILE,
        state
    )


def poll_telegram(
    state: Dict[str, Any]
) -> None:

    if not TELEGRAM_TOKEN:
        return

    offset = safe_int(
        state.get(
            "telegram_offset",
            0
        )
    )

    result = telegram_request(
        "getUpdates",
        {
            "offset":
                offset,

            "timeout":
                1
        }
    )

    if not result:
        return

    if not result.get(
        "ok"
    ):
        return

    updates = result.get(
        "result",
        []
    )

    for update in updates:

        update_id = safe_int(
            update.get(
                "update_id",
                0
            )
        )

        state[
            "telegram_offset"
        ] = update_id + 1

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

        text = clean_text(
            message.get(
                "text",
                ""
            )
        ).lower()

        if chat_id is None:
            continue

        subscribers = state.setdefault(
            "subscribers",
            []
        )

        if text.startswith(
            "/start"
        ):

            if chat_id not in subscribers:

                subscribers.append(
                    chat_id
                )

            send_message(
                chat_id,
                "Runner bot is online.\n\n"
                "You are subscribed to runner alerts."
            )

        elif text.startswith(
            "/stop"
        ):

            if chat_id in subscribers:

                subscribers.remove(
                    chat_id
                )

            send_message(
                chat_id,
                "Runner alerts stopped."
            )

        elif text.startswith(
            "/status"
        ):

            send_message(
                chat_id,
                (
                    f"Runner Bot {BOT_VERSION}\n"
                    f"Tracking: "
                    f"{len(state.get('tracking', {}))}\n"
                    f"Subscribers: "
                    f"{len(subscribers)}"
                )
            )

    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# DEXSCREENER
# ============================================================

def dex_json(
    path: str
) -> Optional[Any]:

    url = (
        DEX_BASE
        + path
    )

    data, error = http_json(
        url,
        headers={
            "User-Agent":
                "RunnerBot/1.5.1"
        }
    )

    if error:

        print(
            f"[DEX ERROR] {path}: {error}"
        )

        return None

    return data


def discovery_addresses() -> List[str]:

    addresses = []

    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
        "/community-takeovers/latest/v1"
    ]

    for endpoint in endpoints:

        data = dex_json(
            endpoint
        )

        if not isinstance(
            data,
            list
        ):
            continue

        for item in data:

            if not isinstance(
                item,
                dict
            ):
                continue

            if clean_text(
                item.get(
                    "chainId",
                    ""
                )
            ).lower() != CHAIN:

                continue

            address = clean_text(
                item.get(
                    "tokenAddress",
                    ""
                )
            )

            if address and address not in addresses:

                addresses.append(
                    address
                )

    return addresses


def token_pairs(
    address: str
) -> List[Dict[str, Any]]:

    data = dex_json(
        f"/token-pairs/v1/{CHAIN}/{address}"
    )

    if isinstance(
        data,
        list
    ):

        return [
            x for x in data
            if isinstance(x, dict)
        ]

    if isinstance(
        data,
        dict
    ):

        pairs = data.get(
            "pairs",
            []
        )

        if isinstance(
            pairs,
            list
        ):
            return pairs

    return []


def best_solana_pair(
    address: str
) -> Optional[Dict[str, Any]]:

    pairs = token_pairs(
        address
    )

    solana_pairs = []

    for pair in pairs:

        if clean_text(
            pair.get(
                "chainId",
                ""
            )
        ).lower() != CHAIN:

            continue

        liquidity = safe_float(
            (
                pair.get(
                    "liquidity",
                    {}
                ) or {}
            ).get(
                "usd",
                0
            )
        )

        solana_pairs.append(
            (
                liquidity,
                pair
            )
        )

    if not solana_pairs:
        return None

    solana_pairs.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return solana_pairs[0][1]


# ============================================================
# SOCIAL / WEBSITE EXTRACTION
# ============================================================

def extract_socials(
    pair: Dict[str, Any]
) -> List[str]:

    result = []

    info = pair.get(
        "info",
        {}
    )

    if not isinstance(
        info,
        dict
    ):
        return result

    socials = info.get(
        "socials",
        []
    )

    if isinstance(
        socials,
        list
    ):

        for item in socials:

            if not isinstance(
                item,
                dict
            ):
                continue

            url = clean_text(
                item.get(
                    "url",
                    ""
                )
            )

            if url:
                result.append(
                    url
                )

    return result


def extract_x_handle(
    pair: Dict[str, Any]
) -> str:

    socials = extract_socials(
        pair
    )

    candidates = []

    for url in socials:

        lower = url.lower()

        if (
            "twitter.com/"
            in lower
            or
            "x.com/"
            in lower
        ):

            candidates.append(
                url
            )

    description = clean_text(
        (
            pair.get(
                "info",
                {}
            ) or {}
        ).get(
            "description",
            ""
        )
    )

    candidates.append(
        description
    )

    for value in candidates:

        match = re.search(
            r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)",
            value,
            flags=re.I
        )

        if match:

            return match.group(
                1
            ).lstrip("@")

        match = re.search(
            r"@([A-Za-z0-9_]{2,30})",
            value
        )

        if match:

            return match.group(
                1
            )

    return ""


def extract_website(
    pair: Dict[str, Any]
) -> str:

    info = pair.get(
        "info",
        {}
    )

    if not isinstance(
        info,
        dict
    ):
        return ""

    websites = info.get(
        "websites",
        []
    )

    if isinstance(
        websites,
        list
    ):

        for item in websites:

            if isinstance(
                item,
                dict
            ):

                url = clean_text(
                    item.get(
                        "url",
                        ""
                    )
                )

                if url:
                    return url

            elif isinstance(
                item,
                str
            ):

                if item.strip():
                    return item.strip()

    return ""


def extract_dex_description(
    pair: Dict[str, Any]
) -> str:

    info = pair.get(
        "info",
        {}
    )

    if not isinstance(
        info,
        dict
    ):
        return ""

    return clean_text(
        info.get(
            "description",
            ""
        )
    )


# ============================================================
# SNAPSHOT
# ============================================================

def make_snapshot(
    pair: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    if not isinstance(
        pair,
        dict
    ):
        return None

    base = pair.get(
        "baseToken",
        {}
    )

    if not isinstance(
        base,
        dict
    ):
        return None

    address = clean_text(
        base.get(
            "address",
            ""
        )
    )

    if not address:
        return None

    symbol = clean_text(
        base.get(
            "symbol",
            "UNKNOWN"
        )
    )

    name = clean_text(
        base.get(
            "name",
            symbol
        )
    )

    liquidity = safe_float(
        (
            pair.get(
                "liquidity",
                {}
            ) or {}
        ).get(
            "usd",
            0
        )
    )

    market_cap = safe_float(
        pair.get(
            "marketCap",
            0
        )
    )

    if market_cap <= 0:

        market_cap = safe_float(
            pair.get(
                "fdv",
                0
            )
        )

    volume = (
        pair.get(
            "volume",
            {}
        ) or {}
    )

    volume_5m = safe_float(
        volume.get(
            "m5",
            0
        )
    )

    txns = (
        pair.get(
            "txns",
            {}
        ) or {}
    )

    tx5 = (
        txns.get(
            "m5",
            {}
        ) or {}
    )

    buys = safe_int(
        tx5.get(
            "buys",
            0
        )
    )

    sells = safe_int(
        tx5.get(
            "sells",
            0
        )
    )

    total_tx = buys + sells

    if sells <= 0:

        bs_ratio = (
            999.0
            if buys > 0
            else 0.0
        )

    else:

        bs_ratio = (
            buys / sells
        )

    price_change = safe_float(
        (
            pair.get(
                "priceChange",
                {}
            ) or {}
        ).get(
            "m5",
            0
        )
    )

    if market_cap > 0:

        volume_mc_pct = (
            volume_5m
            / market_cap
            * 100
        )

        liquidity_mc_pct = (
            liquidity
            / market_cap
            * 100
        )

    else:

        volume_mc_pct = 0
        liquidity_mc_pct = 0

    pair_created = safe_float(
        pair.get(
            "pairCreatedAt",
            0
        )
    )

    if pair_created > 0:

        if pair_created > 10_000_000_000:

            pair_created /= 1000

        age_hours = (
            time.time()
            - pair_created
        ) / 3600

    else:

        age_hours = 0

    return {

        "address":
            address,

        "symbol":
            symbol,

        "name":
            name,

        "market_cap":
            market_cap,

        "liquidity":
            liquidity,

        "volume_5m":
            volume_5m,

        "buys_5m":
            buys,

        "sells_5m":
            sells,

        "tx_5m":
            total_tx,

        "bs_ratio":
            bs_ratio,

        "price_change_5m":
            price_change,

        "volume_mc_pct":
            volume_mc_pct,

        "liquidity_mc_pct":
            liquidity_mc_pct,

        "pair_age_hours":
            age_hours,

        "pair_address":
            clean_text(
                pair.get(
                    "pairAddress",
                    ""
                )
            ),

        "dex_id":
            clean_text(
                pair.get(
                    "dexId",
                    ""
                )
            ),

        "url":
            clean_text(
                pair.get(
                    "url",
                    ""
                )
            ),

        "x_handle":
            extract_x_handle(
                pair
            ),

        "website":
            extract_website(
                pair
            ),

        "dex_description":
            extract_dex_description(
                pair
            )
    }


# ============================================================
# DISCOVERY
# ============================================================

def discover_candidates() -> List[
    Dict[str, Any]
]:

    addresses = discovery_addresses()

    candidates = []

    seen = set()

    for address in addresses:

        if address in seen:
            continue

        seen.add(address)

        pair = best_solana_pair(
            address
        )

        if not pair:
            continue

        snapshot = make_snapshot(
            pair
        )

        if not snapshot:
            continue

        mc = snapshot[
            "market_cap"
        ]

        liquidity = snapshot[
            "liquidity"
        ]

        if mc < MIN_MC:
            continue

        if mc > MAX_MC:
            continue

        if liquidity < MIN_LIQUIDITY:
            continue

        candidates.append(
            snapshot
        )

    candidates.sort(
        key=lambda s: (
            0
            if s["market_cap"]
            <= PRIMARY_MAX_MC
            else 1,

            -s["volume_mc_pct"],

            -s["bs_ratio"],

            -s["tx_5m"]
        )
    )

    candidates = candidates[
        :MAX_DISCOVERY_CANDIDATES
    ]

    print(
        f"[DISCOVERY] Found "
        f"{len(candidates)} candidates"
    )

    for s in candidates:

        print(
            f"[DISCOVERY] "
            f"{s['symbol']} | "
            f"MC ${s['market_cap']:,.0f} | "
            f"Price {s['price_change_5m']:+.1f}% | "
            f"B/S {s['bs_ratio']:.2f} | "
            f"Vol/MC {s['volume_mc_pct']:.1f}% | "
            f"TX {s['tx_5m']}"
        )

    return candidates


# ============================================================
# MARKET SCORING
# ============================================================

def score_directional(
    s: Dict[str, Any]
) -> int:

    bs = s["bs_ratio"]

    if bs >= 2.0:
        return 20

    if bs >= 1.5:
        return 16

    if bs >= 1.2:
        return 12

    if bs >= 1.0:
        return 7

    return 0


def score_price(
    s: Dict[str, Any]
) -> int:

    price = s[
        "price_change_5m"
    ]

    if price >= 20:
        return 20

    if price >= 10:
        return 17

    if price > 0:
        return 13

    if price > -5:
        return 5

    return 0


def score_volume(
    s: Dict[str, Any]
) -> int:

    vmc = s[
        "volume_mc_pct"
    ]

    if vmc >= 20:
        return 20

    if vmc >= 10:
        return 17

    if vmc >= 5:
        return 13

    if vmc >= 2:
        return 8

    if vmc > 0:
        return 3

    return 0


def score_structure(
    s: Dict[str, Any]
) -> int:

    liquidity = s[
        "liquidity"
    ]

    if liquidity >= 30_000:
        return 20

    if liquidity >= 15_000:
        return 16

    if liquidity >= 10_000:
        return 12

    if liquidity >= 5_000:
        return 7

    return 0


def score_persistence(
    previous: Optional[
        Dict[str, Any]
    ],
    history: List[
        Dict[str, Any]
    ]
) -> int:

    if not previous:
        return 0

    points = 0

    current = history[-1] \
        if history else previous

    if safe_float(
        current.get(
            "bs_ratio"
        )
    ) >= 1.2:

        points += 3

    if safe_float(
        current.get(
            "price_change_5m"
        )
    ) > 0:

        points += 3

    if safe_float(
        current.get(
            "volume_mc_pct"
        )
    ) >= 5:

        points += 2

    if safe_int(
        current.get(
            "tx_5m"
        )
    ) >= 50:

        points += 2

    return min(
        points,
        20
    )


def calculate_market_score(
    s: Dict[str, Any],
    previous: Optional[
        Dict[str, Any]
    ],
    history: List[
        Dict[str, Any]
    ]
) -> Tuple[
    int,
    Dict[str, int]
]:

    parts = {

        "directional":
            score_directional(
                s
            ),

        "price":
            score_price(
                s
            ),

        "volume":
            score_volume(
                s
            ),

        "structure":
            score_structure(
                s
            ),

        "persistence":
            score_persistence(
                previous,
                history
            )
    }

    total = sum(
        parts.values()
    )

    return (
        int(
            clamp(
                total,
                0,
                100
            )
        ),
        parts
    )


# ============================================================
# DECAY
# ============================================================

def decay_warnings(
    current: Dict[str, Any],
    previous: Optional[
        Dict[str, Any]
    ]
) -> List[str]:

    warnings = []

    mc = current[
        "market_cap"
    ]

    if mc <= DECAY_MC_CUTOFF:

        if current[
            "price_change_5m"
        ] <= 0:

            warnings.append(
                "PRICE_NON_POSITIVE"
            )

        if current[
            "bs_ratio"
        ] < DECAY_BS_MIN:

            warnings.append(
                "B/S_WEAK"
            )

        if current[
            "tx_5m"
        ] < DECAY_TX_MIN:

            warnings.append(
                "TX_LOW"
            )

        if (
            current[
                "volume_mc_pct"
            ] >= DECAY_VOLUME_MC
            and current[
                "price_change_5m"
            ] < 0
        ):

            warnings.append(
                "HIGH_VOLUME_WITH_NEGATIVE_PRICE"
            )

    if previous:

        previous_liquidity = safe_float(
            previous.get(
                "liquidity",
                0
            )
        )

        if previous_liquidity > 0:

            drop_pct = (
                (
                    previous_liquidity
                    - current[
                        "liquidity"
                    ]
                )
                / previous_liquidity
                * 100
            )

            if drop_pct > DECAY_LIQ_DROP_PCT:

                warnings.append(
                    "LIQUIDITY_DROP"
                )

    return warnings


# ============================================================
# WEAKENING
# ============================================================

def observation_is_weakening(
    previous: Optional[
        Dict[str, Any]
    ],
    current: Dict[str, Any]
) -> bool:

    if not previous:
        return False

    worsened = 0

    if (
        current["bs_ratio"]
        < safe_float(
            previous.get(
                "bs_ratio"
            )
        )
    ):
        worsened += 1

    if (
        current["price_change_5m"]
        < safe_float(
            previous.get(
                "price_change_5m"
            )
        )
    ):
        worsened += 1

    if (
        current["volume_mc_pct"]
        < safe_float(
            previous.get(
                "volume_mc_pct"
            )
        )
    ):
        worsened += 1

    if (
        current["tx_5m"]
        < safe_int(
            previous.get(
                "tx_5m"
            )
        )
    ):
        worsened += 1

    if (
        current["liquidity"]
        < safe_float(
            previous.get(
                "liquidity"
            )
        )
    ):
        worsened += 1

    return (
        worsened >= 3
    )


# ============================================================
# CLASSIFICATION
# ============================================================

def classify_market(
    s: Dict[str, Any],
    score: int,
    history: List[
        Dict[str, Any]
    ],
    decay: List[str],
    weakening_streak: int
) -> str:

    mc = s[
        "market_cap"
    ]

    if mc < MIN_MC:

        return "OUT_OF_RANGE"

    if mc > MAX_MC:

        return "OUT_OF_ALERT_RANGE"

    if (
        len(history) + 1
        < MIN_OBSERVATIONS_RUNNER
    ):

        return "WATCH"

    if len(decay) >= 2:

        return "WATCH"

    if weakening_streak >= 2:

        return "WATCH"

    if (
        score >= IDEAL_SCORE
        and len(history) + 1
        >= MIN_OBSERVATIONS_IDEAL
        and s["bs_ratio"] >= 1.5
    ):

        return "IDEAL RUNNER"

    if (
        score >= RUNNER_SCORE
        and s["price_change_5m"] > 0
        and s["bs_ratio"] >= 1.2
    ):

        return "RUNNER"

    return "WATCH"


# ============================================================
# X / TWITTER
# ============================================================

def x_headers() -> Dict[str, str]:

    if not X_BEARER_TOKEN:
        return {}

    return {
        "Authorization":
            f"Bearer {X_BEARER_TOKEN}",

        "User-Agent":
            "RunnerBot/1.5.1"
    }


def x_lookup_user(
    handle: str
) -> Tuple[
    Optional[
        Dict[str, Any]
    ],
    str
]:

    if not X_BEARER_TOKEN:

        return None, "NOT_CONFIGURED"

    if not handle:

        return None, "NO_HANDLE"

    encoded = urllib.parse.quote(
        handle.lstrip("@"),
        safe=""
    )

    url = (
        f"{X_BASE}/2/users/"
        f"by/username/{encoded}"
    )

    data, error = http_json(
        url,
        headers=x_headers(),
        timeout=HTTP_TIMEOUT
    )

    if error:

        return None, error

    if not isinstance(
        data,
        dict
    ):

        return None, "INVALID_RESPONSE"

    user = data.get(
        "data"
    )

    if not isinstance(
        user,
        dict
    ):

        return None, "ACCOUNT_NOT_FOUND"

    return user, "FOUND"


def x_recent_posts(
    username: str
) -> Tuple[
    List[
        Dict[str, Any]
    ],
    str
]:

    if not X_BEARER_TOKEN:

        return [], "NOT_CONFIGURED"

    if not username:

        return [], "NO_HANDLE"

    query = (
        f"from:{username} "
        "-is:retweet"
    )

    params = urllib.parse.urlencode(
        {
            "query":
                query,

            "max_results":
                min(
                    LORE_MAX_X_POSTS,
                    100
                ),

            "tweet.fields":
                "created_at,public_metrics,text"
        }
    )

    url = (
        f"{X_BASE}/2/tweets/"
        f"search/recent?"
        f"{params}"
    )

    data, error = http_json(
        url,
        headers=x_headers(),
        timeout=HTTP_TIMEOUT
    )

    if error:

        return [], error

    if not isinstance(
        data,
        dict
    ):

        return [], "INVALID_RESPONSE"

    posts = data.get(
        "data",
        []
    )

    if not isinstance(
        posts,
        list
    ):

        return [], "NO_POSTS"

    return (
        posts[
            :LORE_MAX_X_POSTS
        ],
        "FOUND"
    )


def format_x_posts(
    posts: List[
        Dict[str, Any]
    ]
) -> str:

    chunks = []

    for post in posts:

        text = clean_text(
            post.get(
                "text",
                ""
            )
        )

        if text:

            chunks.append(
                text
            )

    return truncate(
        "\n".join(
            f"- {x}"
            for x in chunks
        ),
        LORE_MAX_X_CHARS
    )


# ============================================================
# WEBSITE
# ============================================================

def strip_html(
    raw: str
) -> str:

    if not raw:
        return ""

    raw = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        raw,
        flags=re.I | re.S
    )

    raw = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        raw,
        flags=re.I | re.S
    )

    raw = re.sub(
        r"<[^>]+>",
        " ",
        raw
    )

    raw = html.unescape(
        raw
    )

    raw = re.sub(
        r"\s+",
        " ",
        raw
    )

    return raw.strip()


def extract_github_urls(
    raw: str
) -> List[str]:

    matches = re.findall(
        r"https?://(?:www\.)?github\.com/"
        r"[A-Za-z0-9_.-]+/"
        r"[A-Za-z0-9_.-]+",
        raw or "",
        flags=re.I
    )

    results = []

    for match in matches:

        cleaned = (
            match
            .rstrip("/")
            .rstrip("\"'")
            .rstrip(")")
            .rstrip(".")
        )

        if cleaned not in results:

            results.append(
                cleaned
            )

    return results


def fetch_website(
    url: str
) -> Tuple[
    str,
    str,
    List[str]
]:

    if not url:

        return (
            "",
            "MISSING",
            []
        )

    if not (
        url.startswith(
            "http://"
        )
        or
        url.startswith(
            "https://"
        )
    ):

        url = (
            "https://"
            + url
        )

    raw, error = http_text(
        url,
        headers={
            "User-Agent":
                "Mozilla/5.0 RunnerBot/1.5.1"
        },
        timeout=HTTP_TIMEOUT
    )

    if error:

        return (
            "",
            error,
            []
        )

    text = strip_html(
        raw or ""
    )

    text = truncate(
        text,
        LORE_MAX_WEBSITE_CHARS
    )

    github_urls = extract_github_urls(
        raw
    )

    if not text:

        return (
            "",
            "EMPTY",
            github_urls
        )

    return (
        text,
        "FOUND",
        github_urls
    )


# ============================================================
# GITHUB / PUBLIC SOURCE
# ============================================================

def fetch_github(
    github_url: str
) -> Tuple[
    str,
    str
]:

    if not github_url:

        return "", "MISSING"

    url = github_url.rstrip("/")

    raw, error = http_text(
        url,
        headers={
            "User-Agent":
                "Mozilla/5.0 RunnerBot/1.5.1"
        },
        timeout=HTTP_TIMEOUT
    )

    if error:

        return "", error

    text = strip_html(
        raw or ""
    )

    text = truncate(
        text,
        LORE_MAX_GITHUB_CHARS
    )

    if not text:

        return "", "EMPTY"

    return (
        text,
        "FOUND"
    )


# ============================================================
# ADDITIONAL PUBLIC DOCUMENT LINKS
# ============================================================

def extract_document_urls(
    raw: str
) -> List[str]:

    if not raw:
        return []

    patterns = [

        r"https?://[^\s\"'<>]+"
        r"(?:whitepaper|docs|documentation)"
        r"[^\s\"'<>]*",

        r"https?://[^\s\"'<>]+"
        r"\.pdf(?:[^\s\"'<>]*)?"
    ]

    results = []

    for pattern in patterns:

        matches = re.findall(
            pattern,
            raw,
            flags=re.I
        )

        for match in matches:

            cleaned = (
                match
                .rstrip(".,);]}>")
            )

            if cleaned not in results:

                results.append(
                    cleaned
                )

    return results[:5]


# ============================================================
# LORE SOURCE COLLECTION
# ============================================================

def collect_lore_sources(
    s: Dict[str, Any]
) -> Dict[str, Any]:

    handle = clean_text(
        s.get(
            "x_handle",
            ""
        )
    )

    website = clean_text(
        s.get(
            "website",
            ""
        )
    )

    dex_description = clean_text(
        s.get(
            "dex_description",
            ""
        )
    )

    result = {

        "handle":
            handle,

        "x_status":
            "MISSING",

        "x_error":
            "",

        "x_posts":
            [],

        "x_post_count":
            0,

        "x_profile":
            {},

        "x_posts_text":
            "",

        "website":
            website,

        "website_status":
            (
                "MISSING"
                if not website
                else "NOT_CHECKED"
            ),

        "website_text":
            "",

        "github_url":
            "",

        "github_status":
            "MISSING",

        "github_text":
            "",

        "document_urls":
            [],

        "dex_description":
            dex_description,

        "dex_description_status":
            (
                "YES"
                if dex_description
                else "NO"
            ),

        "source_count":
            0,

        "source_summary":
            [],

        "credible_source_count":
            0,

        "credible_source_summary":
            [],

        "error":
            ""
    }

    # ========================================================
    # X
    # ========================================================

    if not handle:

        result[
            "x_status"
        ] = "MISSING"

    elif not X_BEARER_TOKEN:

        result[
            "x_status"
        ] = "NOT_CONFIGURED"

    else:

        user, status = x_lookup_user(
            handle
        )

        if status == "FOUND":

            result[
                "x_status"
            ] = "FOUND"

            result[
                "x_profile"
            ] = user or {}

            username = clean_text(
                user.get(
                    "username",
                    ""
                )
            )

            posts, posts_status = (
                x_recent_posts(
                    username or handle
                )
            )

            if posts_status == "FOUND":

                result[
                    "x_posts"
                ] = posts

                result[
                    "x_post_count"
                ] = len(posts)

                result[
                    "x_posts_text"
                ] = format_x_posts(
                    posts
                )

            else:

                result[
                    "x_error"
                ] = (
                    "POST_LOOKUP:"
                    + posts_status
                )

        else:

            result[
                "x_status"
            ] = status

            result[
                "x_error"
            ] = (
                "X_LOOKUP_FAILED:"
                + status
            )

    # ========================================================
    # WEBSITE
    # ========================================================

    if website:

        (
            website_text,
            website_status,
            github_urls
        ) = fetch_website(
            website
        )

        result[
            "website_text"
        ] = website_text

        result[
            "website_status"
        ] = website_status

        if github_urls:

            result[
                "github_url"
            ] = github_urls[0]

        result[
            "document_urls"
        ] = extract_document_urls(
            website_text
        )

    # ========================================================
    # GITHUB
    # ========================================================

    github_url = result[
        "github_url"
    ]

    if github_url:

        (
            github_text,
            github_status
        ) = fetch_github(
            github_url
        )

        result[
            "github_text"
        ] = github_text

        result[
            "github_status"
        ] = github_status

    # ========================================================
    # CREDIBLE SOURCE TYPES
    #
    # X = 1
    # WEBSITE = 1
    # GITHUB = 1
    #
    # DEX DESCRIPTION is supporting evidence ONLY.
    # ========================================================

    credible = []

    if (
        result[
            "x_status"
        ] == "FOUND"
        and result[
            "x_post_count"
        ] > 0
    ):

        credible.append(
            "X"
        )

    if (
        result[
            "website_status"
        ] == "FOUND"
    ):

        credible.append(
            "WEBSITE"
        )

    if (
        result[
            "github_status"
        ] == "FOUND"
    ):

        credible.append(
            "GITHUB"
        )

    result[
        "credible_source_summary"
    ] = credible

    result[
        "credible_source_count"
    ] = len(
        credible
    )

    # Total source display includes Dex.
    supporting = list(
        credible
    )

    if dex_description:

        supporting.append(
            "DEX_DESCRIPTION"
        )

    result[
        "source_summary"
    ] = supporting

    result[
        "source_count"
    ] = len(
        supporting
    )

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    if (
        result[
            "credible_source_count"
        ]
        < LORE_MIN_EVIDENCE
    ):

        failures = []

        if not X_BEARER_TOKEN:

            failures.append(
                "X_API_NOT_CONFIGURED"
            )

        elif result[
            "x_status"
        ] != "FOUND":

            failures.append(
                "X:"
                + result[
                    "x_status"
                ]
            )

        elif result[
            "x_post_count"
        ] == 0:

            failures.append(
                "X_NO_POSTS"
            )

        if (
            not website
            or
            result[
                "website_status"
            ] != "FOUND"
        ):

            failures.append(
                "WEBSITE:"
                + result[
                    "website_status"
                ]
            )

        if (
            not github_url
            or
            result[
                "github_status"
            ] != "FOUND"
        ):

            failures.append(
                "GITHUB:"
                + result[
                    "github_status"
                ]
            )

        result[
            "error"
        ] = (
            "INSUFFICIENT_CREDIBLE_EVIDENCE"
            + (
                " | "
                + ", ".join(
                    failures
                )
                if failures
                else ""
            )
        )

    return result


# ============================================================
# LORE DIAGNOSTICS
# ============================================================

def print_lore_sources(
    sources: Dict[str, Any]
) -> None:

    print(
        "[LORE SOURCES]"
    )

    handle = sources.get(
        "handle"
    )

    print(
        "  X HANDLE: "
        + (
            "@"
            + handle
            if handle
            else "NONE"
        )
    )

    print(
        "  X ACCOUNT: "
        + sources.get(
            "x_status",
            "UNKNOWN"
        )
    )

    if sources.get(
        "x_error"
    ):

        print(
            "  X ERROR: "
            + sources[
                "x_error"
            ]
        )

    print(
        "  X POSTS: "
        + str(
            sources.get(
                "x_post_count",
                0
            )
        )
    )

    print(
        "  WEBSITE: "
        + sources.get(
            "website_status",
            "UNKNOWN"
        )
    )

    print(
        "  GITHUB: "
        + sources.get(
            "github_status",
            "MISSING"
        )
    )

    if sources.get(
        "github_url"
    ):

        print(
            "  GITHUB URL: "
            + sources[
                "github_url"
            ]
        )

    print(
        "  DEX DESCRIPTION: "
        + sources.get(
            "dex_description_status",
            "NO"
        )
        + " (SUPPORTING ONLY)"
    )

    print(
        "  CREDIBLE SOURCES: "
        + str(
            sources.get(
                "credible_source_count",
                0
            )
        )
        + "/"
        + str(
            LORE_MIN_EVIDENCE
        )
    )

    print(
        "  CREDIBLE TYPES: "
        + (
            ", ".join(
                sources.get(
                    "credible_source_summary",
                    []
                )
            )
            or "NONE"
        )
    )

    print(
        "  ALL SOURCES: "
        + (
            ", ".join(
                sources.get(
                    "source_summary",
                    []
                )
            )
            or "NONE"
        )
    )

    if sources.get(
        "error"
    ):

        print(
            "  SOURCE DIAGNOSTIC: "
            + sources[
                "error"
            ]
        )


# ============================================================
# LORE AI
# ============================================================

def lore_ai_configured() -> bool:

    return bool(
        LORE_AI_API_KEY
        and LORE_AI_BASE_URL
        and LORE_AI_MODEL
    )


def lore_ai_request(
    token: Dict[str, Any],
    sources: Dict[str, Any]
) -> Tuple[
    Optional[
        Dict[str, Any]
    ],
    str
]:

    if not lore_ai_configured():

        missing = []

        if not LORE_AI_API_KEY:
            missing.append(
                "LORE_AI_API_KEY"
            )

        if not LORE_AI_BASE_URL:
            missing.append(
                "LORE_AI_BASE_URL"
            )

        if not LORE_AI_MODEL:
            missing.append(
                "LORE_AI_MODEL"
            )

        return (
            None,
            "AI_NOT_CONFIGURED: "
            + ", ".join(
                missing
            )
        )

    system_prompt = """
You are a strict crypto token narrative/lore evidence verifier.

Your job is NOT to invent a story.

Only identify a narrative when there is actual evidence in the
supplied material.

IMPORTANT SOURCE RULES:

- X profile/posts count as ONE source type.
- Website counts as ONE source type.
- GitHub counts as ONE source type.
- DexScreener description is SUPPORTING evidence only.
- Do not count the same claim copied across sources as multiple
  independent confirmations.
- Token name alone is not evidence.
- Price appreciation is not lore evidence.
- Trading volume is not lore evidence.
- Generic meme language is weak evidence.
- Promotional claims are claims, not automatically verified facts.
- Anonymous claims receive lower confidence.
- GitHub proves that a repository exists, not automatically that
  every project claim is true.
- Do not invent missing evidence.
- If evidence is insufficient, return UNKNOWN.

Evaluate:

1. Narrative clarity
2. Cultural relevance
3. Memetic potential
4. Community participation
5. Consistency across sources
6. Originality
7. Narrative momentum
8. Evidence quality
9. Whether the story appears to be actively developing

Return ONLY valid JSON.

Format:

{
  "lore_score": 0,
  "confidence": "HIGH|MEDIUM|LOW",
  "momentum": "HIGH|MEDIUM|LOW",
  "status": "PASS|FAIL|UNKNOWN",
  "narrative": "short evidence-based explanation",
  "evidence": [
    "specific evidence"
  ],
  "red_flags": [
    "specific concern"
  ]
}

Score from 0 to 25.

A PASS normally requires:

- score >= 18
- at least 2 specific evidence points
- confidence HIGH or MEDIUM
- at least 2 independent credible source types

For an EARLY NARRATIVE candidate, the external program
will additionally require:

- score >= 21
- confidence HIGH
- momentum HIGH

Do not make an investment recommendation.
"""

    user_payload = {

        "token": {

            "name":
                token.get(
                    "name"
                ),

            "symbol":
                token.get(
                    "symbol"
                ),

            "market_cap":
                token.get(
                    "market_cap"
                )
        },

        "sources": {

            "x_profile":
                sources.get(
                    "x_profile"
                ),

            "x_posts":
                sources.get(
                    "x_posts_text"
                ),

            "website":
                sources.get(
                    "website"
                ),

            "website_text":
                sources.get(
                    "website_text"
                ),

            "github_url":
                sources.get(
                    "github_url"
                ),

            "github_text":
                sources.get(
                    "github_text"
                ),

            "document_urls":
                sources.get(
                    "document_urls"
                ),

            "dex_description":
                sources.get(
                    "dex_description"
                ),

            "credible_source_types":
                sources.get(
                    "credible_source_summary"
                )
        }
    }

    payload = {

        "model":
            LORE_AI_MODEL,

        "messages": [

            {
                "role":
                    "system",

                "content":
                    system_prompt
            },

            {
                "role":
                    "user",

                "content":
                    json.dumps(
                        user_payload,
                        ensure_ascii=False
                    )
            }
        ],

        "temperature":
            0.1,

        "max_tokens":
            800
    }

    url = (
        LORE_AI_BASE_URL
        + "/chat/completions"
    )

    body = json.dumps(
        payload,
        ensure_ascii=False
    ).encode(
        "utf-8"
    )

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization":
                f"Bearer {LORE_AI_API_KEY}",

            "Content-Type":
                "application/json",

            "Accept":
                "application/json"
        },
        method="POST"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=LORE_AI_TIMEOUT
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

            if not raw:

                return (
                    None,
                    "AI_REQUEST_FAILED:EMPTY_RESPONSE"
                )

            data = json.loads(
                raw
            )

    except Exception as e:

        return (
            None,
            "AI_REQUEST_FAILED:"
            + str(e)
        )

    try:

        choices = data.get(
            "choices",
            []
        )

        if not choices:

            return (
                None,
                "AI_REQUEST_FAILED:NO_CHOICES"
            )

        message = choices[0].get(
            "message",
            {}
        )

        content = clean_text(
            message.get(
                "content",
                ""
            )
        )

        if not content:

            return (
                None,
                "AI_REQUEST_FAILED:EMPTY_CONTENT"
            )

        content = re.sub(
            r"^```json\s*",
            "",
            content,
            flags=re.I
        )

        content = re.sub(
            r"^```\s*",
            "",
            content
        )

        content = re.sub(
            r"\s*```$",
            "",
            content
        )

        parsed = json.loads(
            content
        )

        if not isinstance(
            parsed,
            dict
        ):

            return (
                None,
                "AI_INVALID_JSON:NOT_OBJECT"
            )

        return (
            parsed,
            ""
        )

    except json.JSONDecodeError:

        return (
            None,
            "AI_INVALID_JSON"
        )

    except Exception as e:

        return (
            None,
            "AI_INVALID_JSON:"
            + str(e)
        )


# ============================================================
# LORE RESULT
# ============================================================

def normalize_lore_result(
    raw: Dict[str, Any],
    sources: Dict[str, Any]
) -> Dict[str, Any]:

    score = int(
        clamp(
            safe_int(
                raw.get(
                    "lore_score",
                    0
                )
            ),
            0,
            25
        )
    )

    confidence = clean_text(
        raw.get(
            "confidence",
            "LOW"
        )
    ).upper()

    momentum = clean_text(
        raw.get(
            "momentum",
            "LOW"
        )
    ).upper()

    status = clean_text(
        raw.get(
            "status",
            "UNKNOWN"
        )
    ).upper()

    if confidence not in (
        "HIGH",
        "MEDIUM",
        "LOW"
    ):

        confidence = "LOW"

    if momentum not in (
        "HIGH",
        "MEDIUM",
        "LOW"
    ):

        momentum = "LOW"

    if status not in (
        "PASS",
        "FAIL",
        "UNKNOWN"
    ):

        status = "UNKNOWN"

    evidence = raw.get(
        "evidence",
        []
    )

    if not isinstance(
        evidence,
        list
    ):

        evidence = []

    evidence = [
        clean_text(x)
        for x in evidence
        if clean_text(x)
    ]

    red_flags = raw.get(
        "red_flags",
        []
    )

    if not isinstance(
        red_flags,
        list
    ):

        red_flags = []

    red_flags = [
        clean_text(x)
        for x in red_flags
        if clean_text(x)
    ]

    narrative = clean_text(
        raw.get(
            "narrative",
            ""
        )
    )

    credible_count = safe_int(
        sources.get(
            "credible_source_count",
            0
        )
    )

    # --------------------------------------------------------
    # LOCAL ENFORCEMENT
    # --------------------------------------------------------

    if status == "PASS":

        if score < LORE_MIN_SCORE:

            status = "UNKNOWN"

        elif len(evidence) < 2:

            status = "UNKNOWN"

        elif confidence not in (
            "HIGH",
            "MEDIUM"
        ):

            status = "UNKNOWN"

        elif credible_count < LORE_MIN_EVIDENCE:

            status = "UNKNOWN"

    return {

        "lore_score":
            score,

        "confidence":
            confidence,

        "momentum":
            momentum,

        "status":
            status,

        "narrative":
            narrative,

        "evidence":
            evidence,

        "red_flags":
            red_flags,

        "reason":
            (
                "VERIFIED"
                if status == "PASS"
                else
                "LORE_VERIFIED_FAIL"
                if status == "FAIL"
                else
                "INSUFFICIENT_EVIDENCE"
            ),

        "source_count":
            credible_count,

        "source_summary":
            sources.get(
                "credible_source_summary",
                []
            ),

        "all_source_summary":
            sources.get(
                "source_summary",
                []
            ),

        "x_status":
            sources.get(
                "x_status",
                "MISSING"
            ),

        "x_post_count":
            sources.get(
                "x_post_count",
                0
            ),

        "website_status":
            sources.get(
                "website_status",
                "MISSING"
            ),

        "github_status":
            sources.get(
                "github_status",
                "MISSING"
            ),

        "dex_description_status":
            sources.get(
                "dex_description_status",
                "NO"
            ),

        "ai_status":
            "CONNECTED"
    }


def unknown_lore_result(
    sources: Dict[str, Any],
    reason: str
) -> Dict[str, Any]:

    return {

        "lore_score":
            0,

        "confidence":
            "LOW",

        "momentum":
            "LOW",

        "status":
            "UNKNOWN",

        "narrative":
            "",

        "evidence":
            [],

        "red_flags":
            [],

        "reason":
            reason,

        "source_count":
            sources.get(
                "credible_source_count",
                0
            ),

        "source_summary":
            sources.get(
                "credible_source_summary",
                []
            ),

        "all_source_summary":
            sources.get(
                "source_summary",
                []
            ),

        "x_status":
            sources.get(
                "x_status",
                "MISSING"
            ),

        "x_post_count":
            sources.get(
                "x_post_count",
                0
            ),

        "website_status":
            sources.get(
                "website_status",
                "MISSING"
            ),

        "github_status":
            sources.get(
                "github_status",
                "MISSING"
            ),

        "dex_description_status":
            sources.get(
                "dex_description_status",
                "NO"
            ),

        "ai_status":
            (
                "NOT_CONFIGURED"
                if reason.startswith(
                    "AI_NOT_CONFIGURED"
                )
                else
                "NOT_RUN"
            ),

        "ai_error":
            reason
    }


# ============================================================
# LORE RESEARCH
# ============================================================

def research_lore(
    token: Dict[str, Any]
) -> Dict[str, Any]:

    print(
        "======================================================"
    )

    print(
        "[LORE RESEARCH] "
        + token.get(
            "symbol",
            "UNKNOWN"
        )
    )

    print(
        "======================================================"
    )

    sources = collect_lore_sources(
        token
    )

    print_lore_sources(
        sources
    )

    if (
        sources[
            "credible_source_count"
        ]
        < LORE_MIN_EVIDENCE
    ):

        reason = (
            sources.get(
                "error"
            )
            or
            "INSUFFICIENT_CREDIBLE_EVIDENCE"
        )

        print(
            "[LORE AI]"
        )

        print(
            "STATUS: NOT RUN"
        )

        print(
            "REASON: "
            + reason
        )

        result = unknown_lore_result(
            sources,
            reason
        )

        print_lore_result(
            token,
            result
        )

        return result

    if not lore_ai_configured():

        missing = []

        if not LORE_AI_API_KEY:
            missing.append(
                "LORE_AI_API_KEY"
            )

        if not LORE_AI_BASE_URL:
            missing.append(
                "LORE_AI_BASE_URL"
            )

        if not LORE_AI_MODEL:
            missing.append(
                "LORE_AI_MODEL"
            )

        reason = (
            "AI_NOT_CONFIGURED: "
            + ", ".join(
                missing
            )
        )

        print(
            "[LORE AI]"
        )

        print(
            "STATUS: NOT CONFIGURED"
        )

        print(
            "REASON: "
            + reason
        )

        result = unknown_lore_result(
            sources,
            reason
        )

        print_lore_result(
            token,
            result
        )

        return result

    print(
        "[LORE AI]"
    )

    print(
        "STATUS: CONNECTED"
    )

    raw, error = lore_ai_request(
        token,
        sources
    )

    if error:

        print(
            "STATUS: ERROR"
        )

        print(
            "REASON: "
            + error
        )

        result = unknown_lore_result(
            sources,
            error
        )

        result[
            "ai_status"
        ] = "ERROR"

        result[
            "ai_error"
        ] = error

        print_lore_result(
            token,
            result
        )

        return result

    result = normalize_lore_result(
        raw or {},
        sources
    )

    print(
        "STATUS: "
        + result[
            "status"
        ]
    )

    print(
        "SCORE: "
        + str(
            result[
                "lore_score"
            ]
        )
        + "/25"
    )

    print(
        "CONFIDENCE: "
        + result[
            "confidence"
        ]
    )

    print(
        "MOMENTUM: "
        + result[
            "momentum"
        ]
    )

    print(
        "REASON: "
        + result[
            "reason"
        ]
    )

    print_lore_result(
        token,
        result
    )

    return result


def print_lore_result(
    token: Dict[str, Any],
    result: Dict[str, Any]
) -> None:

    print(
        "[LORE RESULT] "
        + token.get(
            "symbol",
            "UNKNOWN"
        )
    )

    print(
        "Score: "
        f"{result.get('lore_score', 0)}/25"
    )

    print(
        "Confidence: "
        f"{result.get('confidence', 'LOW')}"
    )

    print(
        "Momentum: "
        f"{result.get('momentum', 'LOW')}"
    )

    print(
        "Status: "
        f"{result.get('status', 'UNKNOWN')}"
    )

    print(
        "Credible Source Types: "
        f"{result.get('source_count', 0)}"
    )

    print(
        "Sources: "
        + (
            ", ".join(
                result.get(
                    "source_summary",
                    []
                )
            )
            if result.get(
                "source_summary"
            )
            else
            "NONE"
        )
    )

    print(
        "AI Status: "
        f"{result.get('ai_status', 'UNKNOWN')}"
    )

    if result.get(
        "ai_error"
    ):

        print(
            "AI Error: "
            + str(
                result[
                    "ai_error"
                ]
            )
        )

    print(
        "Reason: "
        + str(
            result.get(
                "reason",
                ""
            )
        )
    )

    narrative = clean_text(
        result.get(
            "narrative",
            ""
        )
    )

    if narrative:

        print(
            "Narrative: "
            + narrative
        )

    evidence = result.get(
        "evidence",
        []
    )

    if evidence:

        print(
            "Evidence:"
        )

        for item in evidence:

            print(
                "  - "
                + item
            )

    red_flags = result.get(
        "red_flags",
        []
    )

    if red_flags:

        print(
            "Red Flags:"
        )

        for item in red_flags:

            print(
                "  - "
                + item
            )

    print(
        "------------------------------------------------------"
    )


# ============================================================
# LORE CACHE
# ============================================================

def get_cached_lore(
    record: Dict[str, Any]
) -> Optional[
    Dict[str, Any]
]:

    cached = record.get(
        "lore"
    )

    if not isinstance(
        cached,
        dict
    ):
        return None

    timestamp = safe_float(
        record.get(
            "lore_timestamp",
            0
        )
    )

    if timestamp <= 0:
        return None

    age_minutes = (
        time.time()
        - timestamp
    ) / 60

    if age_minutes > LORE_CACHE_MINUTES:
        return None

    return cached


# ============================================================
# TRACKING
# ============================================================

def ensure_token_record(
    state: Dict[str, Any],
    snapshot: Dict[str, Any]
) -> Dict[str, Any]:

    address = snapshot[
        "address"
    ]

    tracking = state.setdefault(
        "tracking",
        {}
    )

    if address not in tracking:

        tracking[address] = {

            "address":
                address,

            "symbol":
                snapshot[
                    "symbol"
                ],

            "name":
                snapshot[
                    "name"
                ],

            "first_seen":
                iso_now(),

            "initial_mc":
                snapshot[
                    "market_cap"
                ],

            "initial_liquidity":
                snapshot[
                    "liquidity"
                ],

            "max_mc":
                snapshot[
                    "market_cap"
                ],

            "min_mc":
                snapshot[
                    "market_cap"
                ],

            "max_liquidity":
                snapshot[
                    "liquidity"
                ],

            "snapshots":
                [],

            "alerts":
                [],

            "alerted":
                False,

            "alert_mc":
                0,

            "alert_path":
                "",

            "classification":
                "WATCH",

            "score":
                0,

            "weakening_streak":
                0,

            "lore":
                None,

            "lore_timestamp":
                0,

            "outcome":
                None,

            "last_seen":
                iso_now()
        }

    return tracking[
        address
    ]


def update_record(
    record: Dict[str, Any],
    snapshot: Dict[str, Any]
) -> None:

    record[
        "symbol"
    ] = snapshot[
        "symbol"
    ]

    record[
        "name"
    ] = snapshot[
        "name"
    ]

    record[
        "max_mc"
    ] = max(
        safe_float(
            record.get(
                "max_mc"
            )
        ),
        snapshot[
            "market_cap"
        ]
    )

    record[
        "min_mc"
    ] = min(
        safe_float(
            record.get(
                "min_mc"
            )
        ),
        snapshot[
            "market_cap"
        ]
    )

    record[
        "max_liquidity"
    ] = max(
        safe_float(
            record.get(
                "max_liquidity"
            )
        ),
        snapshot[
            "liquidity"
        ]
    )

    record[
        "last_seen"
    ] = iso_now()

    snapshots = record.setdefault(
        "snapshots",
        []
    )

    snapshots.append(
        {

            "timestamp":
                iso_now(),

            "market_cap":
                snapshot[
                    "market_cap"
                ],

            "liquidity":
                snapshot[
                    "liquidity"
                ],

            "price_change_5m":
                snapshot[
                    "price_change_5m"
                ],

            "bs_ratio":
                snapshot[
                    "bs_ratio"
                ],

            "volume_mc_pct":
                snapshot[
                    "volume_mc_pct"
                ],

            "tx_5m":
                snapshot[
                    "tx_5m"
                ]
        }
    )

    if len(
        snapshots
    ) > 100:

        del snapshots[
            :-100
        ]


def tracking_history(
    record: Dict[str, Any]
) -> List[
    Dict[str, Any]
]:

    return record.get(
        "snapshots",
        []
    )


# ============================================================
# OUTCOME
# ============================================================

def calculate_outcome(
    record: Dict[str, Any]
) -> str:

    initial_mc = safe_float(
        record.get(
            "initial_mc"
        )
    )

    max_mc = safe_float(
        record.get(
            "max_mc"
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

    if initial_mc <= 0:
        return "NEUTRAL"

    continued = (
        max_mc
        >= initial_mc * 1.50
        and (
            initial_liquidity <= 0
            or
            max_liquidity
            >= initial_liquidity * 0.70
        )
    )

    failed = (
        max_mc
        <= initial_mc * 0.80
    )

    if continued:
        return "CONTINUED"

    if failed:
        return "FAILED"

    return "NEUTRAL"


# ============================================================
# ALERT
# ============================================================

def build_alert(
    snapshot: Dict[str, Any],
    market_score: int,
    lore: Dict[str, Any],
    alert_path: str
) -> str:

    symbol = snapshot[
        "symbol"
    ]

    name = snapshot[
        "name"
    ]

    mc = snapshot[
        "market_cap"
    ]

    liquidity = snapshot[
        "liquidity"
    ]

    price = snapshot[
        "price_change_5m"
    ]

    bs = snapshot[
        "bs_ratio"
    ]

    vmc = snapshot[
        "volume_mc_pct"
    ]

    tx = snapshot[
        "tx_5m"
    ]

    lore_score = lore.get(
        "lore_score",
        0
    )

    confidence = lore.get(
        "confidence",
        "LOW"
    )

    momentum = lore.get(
        "momentum",
        "LOW"
    )

    narrative = lore.get(
        "narrative",
        ""
    )

    evidence = lore.get(
        "evidence",
        []
    )

    ca = snapshot[
        "address"
    ]

    dex_url = snapshot.get(
        "url",
        ""
    )

    lines = [

        "🔥 RUNNER ALERT",

        "",

        f"{symbol} — {name}",

        "",

        f"PATH: {alert_path}",

        "",

        f"MC: ${mc:,.0f}",

        f"Liquidity: ${liquidity:,.0f}",

        f"5m Price: {price:+.1f}%",

        f"B/S: {bs:.2f}",

        f"5m Vol/MC: {vmc:.1f}%",

        f"5m TX: {tx:,}",

        "",

        f"Market Score: "
        f"{market_score}/100",

        "",

        f"LORE SCORE: "
        f"{lore_score}/25",

        f"LORE CONFIDENCE: "
        f"{confidence}",

        f"LORE MOMENTUM: "
        f"{momentum}",

        "",

        "NARRATIVE:",

        narrative
        or "Verified narrative",

        "",

        "EVIDENCE:"
    ]

    for item in evidence[:5]:

        lines.append(
            f"• {item}"
        )

    lines.extend(
        [

            "",

            "CONTRACT:",

            ca
        ]
    )

    if dex_url:

        lines.extend(
            [

                "",

                "DEX:",

                dex_url
            ]
        )

    lines.extend(
        [

            "",

            "⚠️ Scanner signal only. "
            "Do your own research."
        ]
    )

    return "\n".join(
        lines
    )


# ============================================================
# NORMAL ALERT GATE
# ============================================================

def normal_alert_eligible(
    snapshot: Dict[str, Any],
    market_score: int,
    classification: str,
    decay: List[str],
    weakening_streak: int,
    lore: Dict[str, Any],
    record: Dict[str, Any]
) -> Tuple[
    bool,
    List[str]
]:

    reasons = []

    mc = snapshot[
        "market_cap"
    ]

    if mc < MIN_MC:
        reasons.append(
            "MC_BELOW_MIN"
        )

    if mc > MAX_MC:
        reasons.append(
            "MC_ABOVE_ALERT_RANGE"
        )

    if classification not in (
        "RUNNER",
        "IDEAL RUNNER"
    ):

        reasons.append(
            "MARKET_CLASSIFICATION_NOT_RUNNER"
        )

    if market_score < RUNNER_SCORE:
        reasons.append(
            "MARKET_SCORE_BELOW_72"
        )

    if snapshot[
        "price_change_5m"
    ] <= 0:

        reasons.append(
            "PRICE_NOT_POSITIVE"
        )

    if snapshot[
        "bs_ratio"
    ] < 1.2:

        reasons.append(
            "B/S_BELOW_1.2"
        )

    if len(decay) >= 2:

        reasons.append(
            "DECAY_FILTER"
        )

    if weakening_streak >= 2:

        reasons.append(
            "CONSECUTIVE_WEAKENING"
        )

    if lore.get(
        "status"
    ) != "PASS":

        reasons.append(
            "LORE_VERIFICATION_FAILED"
        )

    if lore.get(
        "lore_score",
        0
    ) < LORE_MIN_SCORE:

        reasons.append(
            "LORE_SCORE_BELOW_18"
        )

    if lore.get(
        "source_count",
        0
    ) < LORE_MIN_EVIDENCE:

        reasons.append(
            "INSUFFICIENT_LORE_SOURCES"
        )

    if record.get(
        "alerted",
        False
    ):

        reasons.append(
            "ALREADY_ALERTED"
        )

    return (
        len(reasons) == 0,
        reasons
    )


# ============================================================
# EARLY MARKET GATE
# ============================================================

def early_narrative_market_check(
    snapshot: Dict[str, Any],
    decay: List[str],
    weakening_streak: int
) -> Tuple[
    bool,
    List[str]
]:

    reasons = []

    mc = snapshot[
        "market_cap"
    ]

    if not (
        MIN_MC
        <= mc
        <= EARLY_NARRATIVE_MAX_MC
    ):

        reasons.append(
            "MC_NOT_IN_EARLY_ZONE"
        )

    if (
        snapshot[
            "price_change_5m"
        ]
        <= EARLY_NARRATIVE_MIN_PRICE
    ):

        reasons.append(
            "PRICE_NOT_ABOVE_+5"
        )

    if (
        snapshot[
            "bs_ratio"
        ]
        < EARLY_NARRATIVE_MIN_BS
    ):

        reasons.append(
            "B/S_BELOW_1.20"
        )

    if (
        snapshot[
            "tx_5m"
        ]
        < EARLY_NARRATIVE_MIN_TX
    ):

        reasons.append(
            "TX_BELOW_100"
        )

    if (
        snapshot[
            "volume_mc_pct"
        ]
        < EARLY_NARRATIVE_MIN_VOL_MC
    ):

        reasons.append(
            "VOL_MC_BELOW_10"
        )

    if (
        snapshot[
            "liquidity"
        ]
        < EARLY_NARRATIVE_MIN_LIQUIDITY
    ):

        reasons.append(
            "LIQUIDITY_BELOW_10K"
        )

    if len(decay) > 0:

        reasons.append(
            "DECAY_FILTER"
        )

    if weakening_streak >= 2:

        reasons.append(
            "CONSECUTIVE_WEAKENING"
        )

    return (
        len(reasons) == 0,
        reasons
    )


# ============================================================
# EARLY LORE GATE
# ============================================================

def early_narrative_lore_check(
    lore: Dict[str, Any]
) -> Tuple[
    bool,
    List[str]
]:

    reasons = []

    if lore.get(
        "status"
    ) != "PASS":

        reasons.append(
            "LORE_NOT_PASS"
        )

    if safe_int(
        lore.get(
            "lore_score",
            0
        )
    ) < EARLY_NARRATIVE_MIN_LORE_SCORE:

        reasons.append(
            "LORE_SCORE_BELOW_21"
        )

    if lore.get(
        "confidence"
    ) != EARLY_NARRATIVE_REQUIRE_CONFIDENCE:

        reasons.append(
            "LORE_CONFIDENCE_NOT_HIGH"
        )

    if lore.get(
        "momentum"
    ) != EARLY_NARRATIVE_REQUIRE_MOMENTUM:

        reasons.append(
            "LORE_MOMENTUM_NOT_HIGH"
        )

    if safe_int(
        lore.get(
            "source_count",
            0
        )
    ) < EARLY_NARRATIVE_MIN_EVIDENCE:

        reasons.append(
            "LORE_CREDIBLE_SOURCES_BELOW_2"
        )

    if len(
        lore.get(
            "evidence",
            []
        )
    ) < 2:

        reasons.append(
            "LORE_EVIDENCE_BELOW_2"
        )

    return (
        len(reasons) == 0,
        reasons
    )


# ============================================================
# VALIDATION
# ============================================================

def validate_token(
    state: Dict[str, Any],
    snapshot: Dict[str, Any]
) -> None:

    record = ensure_token_record(
        state,
        snapshot
    )

    snapshots = tracking_history(
        record
    )

    previous = (
        snapshots[-1]
        if snapshots
        else None
    )

    current_history = snapshots[
        -3:
    ]

    score, score_parts = (
        calculate_market_score(
            snapshot,
            previous,
            current_history
        )
    )

    decay = decay_warnings(
        snapshot,
        previous
    )

    if observation_is_weakening(
        previous,
        snapshot
    ):

        record[
            "weakening_streak"
        ] = (
            safe_int(
                record.get(
                    "weakening_streak",
                    0
                )
            )
            + 1
        )

    else:

        record[
            "weakening_streak"
        ] = 0

    weakening_streak = safe_int(
        record.get(
            "weakening_streak",
            0
        )
    )

    classification = classify_market(
        snapshot,
        score,
        current_history,
        decay,
        weakening_streak
    )

    record[
        "classification"
    ] = classification

    record[
        "score"
    ] = score

    update_record(
        record,
        snapshot
    )

    # ========================================================
    # MARKET PRINT
    # ========================================================

    print(
        "[TOKEN] "
        + snapshot[
            "symbol"
        ]
    )

    print(
        f"MC: ${snapshot['market_cap']/1000:.1f}K"
    )

    print(
        f"Liquidity: "
        f"${snapshot['liquidity']/1000:.1f}K"
    )

    print(
        f"5m Price: "
        f"{snapshot['price_change_5m']:.1f}%"
    )

    print(
        f"B/S: "
        f"{snapshot['bs_ratio']:.2f}"
    )

    print(
        f"5m Vol/MC: "
        f"{snapshot['volume_mc_pct']:.1f}%"
    )

    print(
        f"5m TX: "
        f"{snapshot['tx_5m']}"
    )

    print(
        f"Market Score: "
        f"{score}/100"
    )

    print(
        "Score Parts: "
        + str(score_parts)
    )

    print(
        f"Classification: "
        f"{classification}"
    )

    print(
        f"Decay warnings: "
        f"{len(decay)}"
    )

    if decay:

        print(
            "Decay reasons: "
            + ", ".join(
                decay
            )
        )

    print(
        f"Weakening streak: "
        f"{weakening_streak}"
    )

    print(
        f"Observations: "
        f"{len(current_history) + 1}"
    )

    # ========================================================
    # HARD NEW ALERT CEILING
    # ========================================================

    if (
        snapshot[
            "market_cap"
        ]
        > MAX_MC
    ):

        print(
            "[ALERT CEILING] "
            "Above $150K — no new alert."
        )

    # ========================================================
    # ALREADY ALERTED
    # ========================================================

    if record.get(
        "alerted",
        False
    ):

        print(
            "[TRACKING] Already alerted "
            f"via {record.get('alert_path', 'UNKNOWN')}"
        )

        save_json(
            STATE_FILE,
            state
        )

        return

    # ========================================================
    # NORMAL MARKET PRE-CHECK
    # ========================================================

    normal_market_precheck = (
        MIN_MC
        <= snapshot[
            "market_cap"
        ]
        <= MAX_MC

        and score >= RUNNER_SCORE

        and snapshot[
            "price_change_5m"
        ] > 0

        and snapshot[
            "bs_ratio"
        ] >= 1.2

        and len(decay) < 2

        and weakening_streak < 2

        and classification in (
            "RUNNER",
            "IDEAL RUNNER"
        )
    )

    # ========================================================
    # EARLY MARKET PRE-CHECK
    # ========================================================

    early_market_pass = False

    early_market_reasons = []

    if EARLY_NARRATIVE_ENABLED:

        (
            early_market_pass,
            early_market_reasons
        ) = early_narrative_market_check(
            snapshot,
            decay,
            weakening_streak
        )

    # ========================================================
    # NO RESEARCH
    # ========================================================

    if (
        not normal_market_precheck
        and not early_market_pass
    ):

        print(
            "[LORE] Research not triggered."
        )

        print(
            "[RESEARCH BLOCK REASONS]"
        )

        if score < RUNNER_SCORE:

            print(
                "  - NORMAL_SCORE_BELOW_72"
            )

        if not early_market_pass:

            for reason in (
                early_market_reasons
            ):

                print(
                    "  - "
                    + reason
                )

        save_json(
            STATE_FILE,
            state
        )

        return

    # ========================================================
    # PATH DIAGNOSTIC
    # ========================================================

    if normal_market_precheck:

        print(
            "[NORMAL RUNNER]"
        )

        print(
            "Market pre-check: PASS"
        )

    if early_market_pass:

        print(
            "[EARLY NARRATIVE RUNNER]"
        )

        print(
            "Early market pre-check: PASS"
        )

        print(
            f"Normal market score: {score}/100"
        )

        if score < RUNNER_SCORE:

            print(
                "Reason: "
                "early narrative route bypassed "
                "the normal 72 score requirement."
            )

    # ========================================================
    # LORE CACHE
    # ========================================================

    cached = get_cached_lore(
        record
    )

    if cached:

        print(
            "[LORE] Using cached research"
        )

        lore = cached

    else:

        lore = research_lore(
            snapshot
        )

        record[
            "lore"
        ] = lore

        record[
            "lore_timestamp"
        ] = time.time()

    # ========================================================
    # NORMAL PATH
    # ========================================================

    if normal_market_precheck:

        (
            normal_eligible,
            normal_reasons
        ) = normal_alert_eligible(
            snapshot,
            score,
            classification,
            decay,
            weakening_streak,
            lore,
            record
        )

        if normal_eligible:

            alert_path = (
                "NORMAL RUNNER"
            )

            alert = build_alert(
                snapshot,
                score,
                lore,
                alert_path
            )

            send_alert(
                state,
                record,
                snapshot,
                score,
                lore,
                alert,
                alert_path
            )

            return

        print(
            "[NORMAL PATH] NO ALERT"
        )

        for reason in normal_reasons:

            print(
                "  - "
                + reason
            )

    # ========================================================
    # EARLY NARRATIVE PATH
    # ========================================================

    if early_market_pass:

        (
            early_lore_pass,
            early_lore_reasons
        ) = early_narrative_lore_check(
            lore
        )

        print(
            "======================================================"
        )

        print(
            "[EARLY NARRATIVE CHECK]"
        )

        print(
            f"Price: "
            f"{snapshot['price_change_5m']:+.1f}%"
            + (
                "  PASS"
                if snapshot[
                    "price_change_5m"
                ] > EARLY_NARRATIVE_MIN_PRICE
                else "  FAIL"
            )
        )

        print(
            f"B/S: "
            f"{snapshot['bs_ratio']:.2f}"
            + (
                "  PASS"
                if snapshot[
                    "bs_ratio"
                ] >= EARLY_NARRATIVE_MIN_BS
                else "  FAIL"
            )
        )

        print(
            f"TX: "
            f"{snapshot['tx_5m']:,}"
            + (
                "  PASS"
                if snapshot[
                    "tx_5m"
                ] >= EARLY_NARRATIVE_MIN_TX
                else "  FAIL"
            )
        )

        print(
            f"Vol/MC: "
            f"{snapshot['volume_mc_pct']:.1f}%"
            + (
                "  PASS"
                if snapshot[
                    "volume_mc_pct"
                ] >= EARLY_NARRATIVE_MIN_VOL_MC
                else "  FAIL"
            )
        )

        print(
            f"Liquidity: "
            f"${snapshot['liquidity']:,.0f}"
            + (
                "  PASS"
                if snapshot[
                    "liquidity"
                ] >= EARLY_NARRATIVE_MIN_LIQUIDITY
                else "  FAIL"
            )
        )

        print(
            f"MC: "
            f"${snapshot['market_cap']:,.0f}"
            + (
                "  PASS"
                if (
                    MIN_MC
                    <= snapshot[
                        "market_cap"
                    ]
                    <= EARLY_NARRATIVE_MAX_MC
                )
                else "  FAIL"
            )
        )

        print(
            f"Decay: "
            f"{len(decay)}"
            + (
                "  PASS"
                if len(decay) == 0
                else "  FAIL"
            )
        )

        print(
            "Early lore gate: "
            + (
                "PASS"
                if early_lore_pass
                else "FAIL"
            )
        )

        if early_lore_reasons:

            for reason in early_lore_reasons:

                print(
                    "  - "
                    + reason
                )

        if early_lore_pass:

            alert_path = (
                "EARLY NARRATIVE RUNNER"
            )

            alert = build_alert(
                snapshot,
                score,
                lore,
                alert_path
            )

            send_alert(
                state,
                record,
                snapshot,
                score,
                lore,
                alert,
                alert_path
            )

            return

        print(
            "[EARLY NARRATIVE] NO ALERT"
        )

    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# DISCOVERY TRACKING
# ============================================================

def update_tracking_from_discovery(
    state: Dict[str, Any],
    candidates: List[
        Dict[str, Any]
    ]
) -> None:

    for snapshot in candidates:

        ensure_token_record(
            state,
            snapshot
        )

    print(
        "[DISCOVERY] Tracking now: "
        f"{len(state.get('tracking', {}))}"
    )

    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# VALIDATION CYCLE
# ============================================================

def validation_cycle(
    state: Dict[str, Any]
) -> None:

    tracking = state.get(
        "tracking",
        {}
    )

    print(
        "===================================================================="
    )

    print(
        "VALIDATION CYCLE "
        f"{datetime.now().strftime('%H:%M:%S')}"
    )

    print(
        "===================================================================="
    )

    print(
        "[VALIDATION] Tokens: "
        f"{len(tracking)}"
    )

    for address, record in list(
        tracking.items()
    ):

        try:

            pair = best_solana_pair(
                address
            )

            if not pair:
                continue

            snapshot = make_snapshot(
                pair
            )

            if not snapshot:
                continue

            validate_token(
                state,
                snapshot
            )

        except Exception as e:

            print(
                "[VALIDATION ERROR] "
                f"{address}: {e}"
            )

    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# CLEANUP
# ============================================================

def cleanup_tracking(
    state: Dict[str, Any]
) -> None:

    tracking = state.get(
        "tracking",
        {}
    )

    remove = []

    for address, record in tracking.items():

        first_seen = record.get(
            "first_seen"
        )

        try:

            dt = datetime.fromisoformat(
                first_seen
            )

            age_hours = (
                datetime.now(
                    timezone.utc
                )
                - dt
            ).total_seconds() / 3600

        except Exception:

            age_hours = 0

        alerted = record.get(
            "alerted",
            False
        )

        max_age = (
            ALERT_TRACKING_HOURS
            if alerted
            else TRACKING_HOURS
        )

        if age_hours >= max_age:

            record[
                "outcome"
            ] = calculate_outcome(
                record
            )

            history = load_json(
                HISTORY_FILE,
                []
            )

            if not isinstance(
                history,
                list
            ):

                history = []

            history.append(
                {

                    "address":
                        address,

                    "symbol":
                        record.get(
                            "symbol"
                        ),

                    "initial_mc":
                        record.get(
                            "initial_mc"
                        ),

                    "alert_mc":
                        record.get(
                            "alert_mc"
                        ),

                    "max_mc":
                        record.get(
                            "max_mc"
                        ),

                    "max_liquidity":
                        record.get(
                            "max_liquidity"
                        ),

                    "alerted":
                        alerted,

                    "alert_path":
                        record.get(
                            "alert_path",
                            ""
                        ),

                    "outcome":
                        record.get(
                            "outcome"
                        ),

                    "first_seen":
                        record.get(
                            "first_seen"
                        ),

                    "last_seen":
                        record.get(
                            "last_seen"
                        )
                }
            )

            save_json(
                HISTORY_FILE,
                history
            )

            remove.append(
                address
            )

    for address in remove:

        tracking.pop(
            address,
            None
        )

    if remove:

        save_json(
            STATE_FILE,
            state
        )


# ============================================================
# CREDENTIAL STATUS
# ============================================================

def print_credentials() -> None:

    print(
        "CREDENTIAL STATUS"
    )

    print(
        "Telegram: "
        + (
            "OK"
            if TELEGRAM_TOKEN
            else "MISSING"
        )
    )

    print(
        "X API: "
        + (
            "OK"
            if X_BEARER_TOKEN
            else "MISSING"
        )
    )

    print(
        "Lore AI: "
        + (
            "OK"
            if lore_ai_configured()
            else "MISSING"
        )
    )


# ============================================================
# HEADER
# ============================================================

def print_header() -> None:

    print(
        "===================================================================="
    )

    print(
        f"RUNNER BOT {BOT_VERSION}"
    )

    print(
        "===================================================================="
    )

    print(
        "MC: $20,000-$150,000"
    )

    print(
        "PRIMARY: $20,000-$80,000"
    )

    print(
        "SECONDARY: $80,000-$150,000"
    )

    print(
        "NEW ALERTS ABOVE $150K: NO"
    )

    print(
        "TRACKING AFTER $150K: YES"
    )

    print(
        "Normal tracking: 8h"
    )

    print(
        "Alert tracking: 48h"
    )

    print(
        "Normal Runner score: 72+"
    )

    print(
        "Early Narrative: ON"
    )

    print(
        "Early Narrative MC: $20K-$80K"
    )

    print(
        "Early Price: > +5%"
    )

    print(
        "Early B/S: >= 1.20"
    )

    print(
        "Early TX: >= 100"
    )

    print(
        "Early Vol/MC: >= 10%"
    )

    print(
        "Early Liquidity: >= $10K"
    )

    print(
        "Early Lore: >= 21/25"
    )

    print(
        "Early Lore Confidence: HIGH"
    )

    print(
        "Early Lore Momentum: HIGH"
    )

    print(
        f"Lore threshold: "
        f"{LORE_MIN_SCORE}/25"
    )

    print(
        f"Lore cache: "
        f"{LORE_CACHE_MINUTES} minutes"
    )

    print(
        "Dex description: SUPPORTING ONLY"
    )

    print(
        "Minimum credible sources: "
        f"{LORE_MIN_EVIDENCE}"
    )

    print_credentials()

    print(
        "===================================================================="
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    print_header()

    state = load_json(
        STATE_FILE,
        {
            "subscribers": [],
            "tracking": {},
            "last_discovery": 0,
            "last_validation": 0,
            "telegram_offset": 0
        }
    )

    if not isinstance(
        state,
        dict
    ):

        state = {
            "subscribers": [],
            "tracking": {},
            "last_discovery": 0,
            "last_validation": 0,
            "telegram_offset": 0
        }

    state.setdefault(
        "subscribers",
        []
    )

    state.setdefault(
        "tracking",
        {}
    )

    state.setdefault(
        "telegram_offset",
        0
    )

    print(
        "===================================================================="
    )

    print(
        "STARTED: "
        f"{iso_now()}"
    )

    print(
        "===================================================================="
    )

    last_discovery = safe_float(
        state.get(
            "last_discovery",
            0
        )
    )

    last_validation = safe_float(
        state.get(
            "last_validation",
            0
        )
    )

    while True:

        try:

            poll_telegram(
                state
            )

            current = time.time()

            # ====================================================
            # DISCOVERY
            # ====================================================

            if (
                current
                - last_discovery
                >= DISCOVERY_INTERVAL
                or not state.get(
                    "tracking"
                )
            ):

                print(
                    "===================================================================="
                )

                print(
                    "DISCOVERY CYCLE "
                    f"{datetime.now().strftime('%H:%M:%S')}"
                )

                print(
                    "===================================================================="
                )

                candidates = (
                    discover_candidates()
                )

                update_tracking_from_discovery(
                    state,
                    candidates
                )

                last_discovery = current

                state[
                    "last_discovery"
                ] = current

                save_json(
                    STATE_FILE,
                    state
                )

            # ====================================================
            # VALIDATION
            # ====================================================

            if (
                current
                - last_validation
                >= VALIDATION_INTERVAL
                or last_validation == 0
            ):

                validation_cycle(
                    state
                )

                last_validation = current

                state[
                    "last_validation"
                ] = current

                save_json(
                    STATE_FILE,
                    state
                )

            # ====================================================
            # TELEGRAM
            # ====================================================

            poll_telegram(
                state
            )

            # ====================================================
            # CLEANUP
            # ====================================================

            cleanup_tracking(
                state
            )

            time.sleep(
                SCAN_INTERVAL
            )

        except KeyboardInterrupt:

            print(
                "Stopping bot..."
            )

            save_json(
                STATE_FILE,
                state
            )

            break

        except Exception as e:

            print(
                "[MAIN ERROR] "
                f"{e}"
            )

            time.sleep(
                SCAN_INTERVAL
            )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
