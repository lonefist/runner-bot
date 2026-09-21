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
# RUNNER BOT V1.4.2
# LOW-CAP FIRST + CONTROLLED MOMENTUM
# DECAY FILTER + CONSECUTIVE WEAKENING
# LORE / NARRATIVE EVIDENCE DIAGNOSTICS
# ============================================================

BOT_VERSION = "V1.4.2-LOW-CAP-LORE-DIAGNOSTICS"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"
X_BASE = "https://api.x.com"

STATE_FILE = "runner_v142_state.json"
HISTORY_FILE = "runner_v142_history.json"

CHAIN = "solana"


# ============================================================
# MARKET RANGE
# ============================================================

MIN_MC = 20_000

PRIMARY_MAX_MC = 80_000

MAX_MC = 150_000

MIN_LIQUIDITY = 5_000


# ============================================================
# TIMING
# ============================================================

SCAN_INTERVAL = 20

DISCOVERY_INTERVAL = 300

VALIDATION_INTERVAL = 300

TRACKING_HOURS = 8

ALERT_TRACKING_HOURS = 48

MAX_DISCOVERY_CANDIDATES = 50


# ============================================================
# MARKET SCORE
# KEEPING V1.3/V1.4 THRESHOLDS UNCHANGED
# ============================================================

WATCH_SCORE = 55

RUNNER_SCORE = 72

IDEAL_SCORE = 85

MIN_OBSERVATIONS_RUNNER = 2

MIN_OBSERVATIONS_IDEAL = 3


# ============================================================
# DECAY FILTER
# ============================================================

DECAY_MC_CUTOFF = 100_000

DECAY_BS_MIN = 1.20

DECAY_TX_MIN = 20

DECAY_LIQ_DROP_PCT = 10.0

DECAY_VOLUME_MC = 20.0


# ============================================================
# WEAKENING
# ============================================================

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

LORE_AI_TIMEOUT = 45

LORE_AI_BASE_URL = os.getenv(
    "LORE_AI_BASE_URL",
    ""
).rstrip("/")

LORE_AI_MODEL = os.getenv(
    "LORE_AI_MODEL",
    "")

LORE_AI_API_KEY = os.getenv(
    "LORE_AI_API_KEY",
    "")

X_BEARER_TOKEN = os.getenv(
    "X_BEARER_TOKEN",
    "")


# ============================================================
# HTTP
# ============================================================

HTTP_TIMEOUT = 15


# ============================================================
# UTILS
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
            return default

        return float(value)

    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default

        return int(float(value))

    except Exception:
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = html.unescape(text)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def truncate(text: str, maximum: int) -> str:
    text = text or ""

    if len(text) <= maximum:
        return text

    return text[:maximum] + "... [TRUNCATED]"


def load_json(path: str, default: Any) -> Any:
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    temporary = path + ".tmp"

    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )

    os.replace(temporary, path)


# ============================================================
# HTTP
# ============================================================

def http_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = HTTP_TIMEOUT
) -> Tuple[Optional[Any], Optional[str]]:

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

            try:
                return json.loads(raw), None

            except Exception:
                return None, "INVALID_JSON"

    except urllib.error.HTTPError as e:

        try:
            body = e.read().decode(
                "utf-8",
                errors="replace"
            )

            body = truncate(body, 500)

        except Exception:
            body = ""

        return None, f"HTTP_{e.code}:{body}"

    except urllib.error.URLError as e:
        return None, f"URL_ERROR:{e.reason}"

    except Exception as e:
        return None, f"REQUEST_ERROR:{e}"


def http_text(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = HTTP_TIMEOUT
) -> Tuple[Optional[str], Optional[str]]:

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

            return raw, None

    except urllib.error.HTTPError as e:
        return None, f"HTTP_{e.code}"

    except urllib.error.URLError as e:
        return None, f"URL_ERROR:{e.reason}"

    except Exception as e:
        return None, f"REQUEST_ERROR:{e}"


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)


def telegram_api(
    method: str,
    params: Optional[Dict[str, Any]] = None
) -> Optional[Any]:

    if not TELEGRAM_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_TOKEN}/{method}"
    )

    encoded = urllib.parse.urlencode(
        params or {}
    ).encode()

    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=HTTP_TIMEOUT
        ) as response:

            data = json.loads(
                response.read().decode()
            )

            if data.get("ok"):
                return data.get("result")

            return None

    except Exception:
        return None


def send_message(
    chat_id: int,
    text: str
) -> bool:

    result = telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True
        }
    )

    return result is not None


def poll_telegram(
    state: Dict[str, Any]
) -> None:

    if not TELEGRAM_TOKEN:
        return

    offset = safe_int(
        state.get("telegram_offset"),
        0
    )

    result = telegram_api(
        "getUpdates",
        {
            "offset": offset,
            "timeout": 1
        }
    )

    if not result:
        return

    subscribers = state.setdefault(
        "subscribers",
        []
    )

    for update in result:

        update_id = safe_int(
            update.get("update_id"),
            0
        )

        if update_id >= offset:
            state["telegram_offset"] = (
                update_id + 1
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

        text = clean_text(
            message.get(
                "text",
                ""
            )
        )

        if chat_id is None:
            continue

        if text.startswith("/start"):

            if chat_id not in subscribers:
                subscribers.append(chat_id)

            send_message(
                chat_id,
                (
                    "Runner bot is online.\n\n"
                    f"Version: {BOT_VERSION}\n"
                    "Solana low-cap runner scanner active."
                )
            )

    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# DEXSCREENER
# ============================================================

DISCOVERY_ENDPOINTS = [
    "/token-profiles/latest/v1",
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
    "/community-takeovers/latest/v1",
]


def dex_get(
    path: str
) -> Optional[Any]:

    url = DEX_BASE + path

    data, error = http_json(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "RunnerBot/1.4.2"
        }
    )

    if error:
        return None

    return data


def extract_addresses(
    data: Any
) -> List[str]:

    addresses = []

    if isinstance(data, list):

        for item in data:

            if not isinstance(item, dict):
                continue

            address = (
                item.get("tokenAddress")
                or item.get("token_address")
                or item.get("address")
            )

            if address:
                addresses.append(
                    str(address)
                )

    elif isinstance(data, dict):

        for key in (
            "pairs",
            "tokens",
            "data",
            "results"
        ):

            value = data.get(key)

            if isinstance(value, list):
                addresses.extend(
                    extract_addresses(value)
                )

    return addresses


def get_token_pairs(
    token_address: str
) -> List[Dict[str, Any]]:

    encoded = urllib.parse.quote(
        token_address,
        safe=""
    )

    data, error = http_json(
        f"{DEX_BASE}/latest/dex/tokens/{encoded}",
        headers={
            "Accept": "application/json",
            "User-Agent": "RunnerBot/1.4.2"
        }
    )

    if error:
        return []

    if not isinstance(data, dict):
        return []

    pairs = data.get(
        "pairs",
        []
    )

    if not isinstance(pairs, list):
        return []

    return [
        p for p in pairs
        if isinstance(p, dict)
    ]


def best_solana_pair(
    token_address: str
) -> Optional[Dict[str, Any]]:

    pairs = get_token_pairs(
        token_address
    )

    solana_pairs = [
        p for p in pairs
        if p.get("chainId") == CHAIN
    ]

    if not solana_pairs:
        return None

    solana_pairs.sort(
        key=lambda p: safe_float(
            (
                p.get("liquidity") or {}
            ).get("usd"),
            0
        ),
        reverse=True
    )

    return solana_pairs[0]


def extract_socials_and_websites(
    pair: Dict[str, Any]
) -> Tuple[List[str], List[str]]:

    info = pair.get(
        "info",
        {}
    )

    if not isinstance(info, dict):
        return [], []

    socials = []
    websites = []

    raw_socials = info.get(
        "socials",
        []
    )

    if isinstance(raw_socials, list):

        for social in raw_socials:

            if not isinstance(social, dict):
                continue

            platform = clean_text(
                social.get(
                    "platform",
                    ""
                )
            ).lower()

            handle = clean_text(
                social.get(
                    "handle",
                    ""
                )
            )

            url = clean_text(
                social.get(
                    "url",
                    ""
                )
            )

            if platform == "x":

                if handle:
                    socials.append(
                        "@" + handle.lstrip("@")
                    )

                elif url:
                    socials.append(url)

            elif platform in (
                "twitter",
                "telegram",
                "discord"
            ):

                if handle:
                    socials.append(
                        f"{platform}:{handle}"
                    )

                elif url:
                    socials.append(
                        f"{platform}:{url}"
                    )

    raw_websites = info.get(
        "websites",
        []
    )

    if isinstance(raw_websites, list):

        for website in raw_websites:

            if isinstance(
                website,
                dict
            ):

                url = clean_text(
                    website.get(
                        "url",
                        ""
                    )
                )

            else:
                url = clean_text(
                    website
                )

            if url:
                websites.append(url)

    return socials, websites


def extract_x_handle(
    pair: Dict[str, Any]
) -> str:

    socials, _ = (
        extract_socials_and_websites(
            pair
        )
    )

    for item in socials:

        value = item.strip()

        if value.startswith("@"):
            return value.lstrip("@")

        if "twitter.com/" in value:
            return value.rstrip("/").split(
                "twitter.com/"
            )[-1].split("/")[0]

        if "x.com/" in value:
            return value.rstrip("/").split(
                "x.com/"
            )[-1].split("/")[0]

    return ""


def extract_website(
    pair: Dict[str, Any]
) -> str:

    _, websites = (
        extract_socials_and_websites(
            pair
        )
    )

    if websites:
        return websites[0]

    return ""


def extract_dex_description(
    pair: Dict[str, Any]
) -> str:

    info = pair.get(
        "info",
        {}
    )

    if not isinstance(info, dict):
        return ""

    for key in (
        "description",
        "tokenDescription",
        "descriptionText"
    ):

        value = clean_text(
            info.get(key, "")
        )

        if value:
            return value

    base = pair.get(
        "baseToken",
        {}
    )

    if isinstance(base, dict):

        for key in (
            "description",
            "tokenDescription"
        ):

            value = clean_text(
                base.get(key, "")
            )

            if value:
                return value

    return ""


def make_snapshot(
    pair: Dict[str, Any]
) -> Optional[Dict[str, Any]]:

    if not pair:
        return None

    base = pair.get(
        "baseToken",
        {}
    )

    if not isinstance(base, dict):
        base = {}

    token_address = clean_text(
        base.get(
            "address",
            ""
        )
    )

    if not token_address:
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

    market_cap = safe_float(
        pair.get(
            "marketCap"
        )
    )

    if market_cap <= 0:
        market_cap = safe_float(
            pair.get(
                "fdv"
            )
        )

    liquidity = safe_float(
        (
            pair.get("liquidity") or {}
        ).get(
            "usd"
        )
    )

    volume = safe_float(
        (
            pair.get("volume") or {}
        ).get(
            "m5"
        )
    )

    txns = (
        pair.get("txns") or {}
    ).get(
        "m5"
    ) or {}

    buys = safe_int(
        txns.get(
            "buys"
        )
    )

    sells = safe_int(
        txns.get(
            "sells"
        )
    )

    total_tx = buys + sells

    if sells > 0:
        bs_ratio = buys / sells
    elif buys > 0:
        bs_ratio = 999.0
    else:
        bs_ratio = 0.0

    price_change = safe_float(
        (
            pair.get("priceChange") or {}
        ).get(
            "m5"
        )
    )

    volume_mc = (
        volume / market_cap * 100
        if market_cap > 0
        else 0.0
    )

    liquidity_mc = (
        liquidity / market_cap * 100
        if market_cap > 0
        else 0.0
    )

    pair_created = safe_int(
        pair.get(
            "pairCreatedAt"
        )
    )

    if pair_created > 0:

        age_hours = max(
            0,
            (
                time.time() * 1000
                - pair_created
            ) / 3_600_000
        )

    else:
        age_hours = 0.0

    x_handle = extract_x_handle(
        pair
    )

    website = extract_website(
        pair
    )

    dex_description = (
        extract_dex_description(
            pair
        )
    )

    return {
        "address": token_address,
        "symbol": symbol,
        "name": name,
        "market_cap": market_cap,
        "liquidity": liquidity,
        "volume_5m": volume,
        "buys_5m": buys,
        "sells_5m": sells,
        "tx_5m": total_tx,
        "bs_ratio": bs_ratio,
        "price_change_5m": price_change,
        "volume_mc_pct": volume_mc,
        "liquidity_mc_pct": liquidity_mc,
        "pair_age_hours": age_hours,
        "pair_address": pair.get(
            "pairAddress",
            ""
        ),
        "dex_id": pair.get(
            "dexId",
            ""
        ),
        "url": pair.get(
            "url",
            ""
        ),
        "x_handle": x_handle,
        "website": website,
        "dex_description": dex_description,
    }


# ============================================================
# DISCOVERY
# ============================================================

def discover_candidates() -> List[Dict[str, Any]]:

    addresses = []

    for endpoint in DISCOVERY_ENDPOINTS:

        data = dex_get(endpoint)

        addresses.extend(
            extract_addresses(data)
        )

    unique = list(
        dict.fromkeys(addresses)
    )

    print(
        f"[DISCOVERY] Raw unique tokens: "
        f"{len(unique)}"
    )

    candidates = []

    for address in unique:

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

        mc = snapshot["market_cap"]

        liq = snapshot["liquidity"]

        if mc < MIN_MC:
            continue

        if mc > MAX_MC:
            continue

        if liq < MIN_LIQUIDITY:
            continue

        candidates.append(
            snapshot
        )

    def sort_key(
        item: Dict[str, Any]
    ):
        mc = item["market_cap"]

        primary = (
            0
            if mc <= PRIMARY_MAX_MC
            else 1
        )

        return (
            primary,
            -item["volume_mc_pct"],
            -item["bs_ratio"],
            -item["price_change_5m"],
            -item["liquidity"]
        )

    candidates.sort(
        key=sort_key
    )

    candidates = candidates[
        :MAX_DISCOVERY_CANDIDATES
    ]

    primary = sum(
        1
        for x in candidates
        if x["market_cap"] <= PRIMARY_MAX_MC
    )

    secondary = len(candidates) - primary

    print(
        f"[DISCOVERY] Candidates: "
        f"{len(candidates)}"
    )

    print(
        f"[PRIMARY] {primary}"
    )

    print(
        f"[SECONDARY] {secondary}"
    )

    return candidates


# ============================================================
# MARKET SCORING
# ============================================================

def directional_score(
    s: Dict[str, Any]
) -> float:

    bs = s["bs_ratio"]

    tx = s["tx_5m"]

    price = s["price_change_5m"]

    score = 0.0

    if bs >= 3:
        score += 16
    elif bs >= 2:
        score += 14
    elif bs >= 1.5:
        score += 11
    elif bs >= 1.2:
        score += 8
    elif bs >= 1:
        score += 4
    else:
        score -= 4

    if tx >= 200:
        score += 8
    elif tx >= 100:
        score += 6
    elif tx >= 50:
        score += 4
    elif tx >= 20:
        score += 2
    else:
        score -= 4

    if price >= 10:
        score += 6
    elif price > 0:
        score += 4
    elif price <= -10:
        score -= 10
    elif price < 0:
        score -= 5

    if (
        s["volume_mc_pct"] >= 20
        and price <= -15
    ):
        score -= 8

    return score


def price_momentum_score(
    s: Dict[str, Any]
) -> float:

    price = s["price_change_5m"]

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
        and s["volume_mc_pct"] >= 15
        and s["bs_ratio"] >= 1.2
    ):
        score += 1

    if (
        price > 60
        and s["volume_mc_pct"] >= 50
    ):
        score -= 2

    return clamp(
        score,
        0,
        20
    )


def volume_quality_score(
    s: Dict[str, Any]
) -> float:

    vmc = s["volume_mc_pct"]

    price = s["price_change_5m"]

    bs = s["bs_ratio"]

    tx = s["tx_5m"]

    if vmc >= 50:
        score = 14
    elif vmc >= 30:
        score = 13
    elif vmc >= 15:
        score = 11
    elif vmc >= 7:
        score = 8
    elif vmc >= 3:
        score = 5
    elif vmc >= 1:
        score = 3
    elif vmc > 0:
        score = 1
    else:
        score = 0

    if price > 0 and bs >= 1.5:
        score += 6

    elif price > 0 and bs >= 1:
        score += 3

    if price < 0 and vmc >= 20:
        score -= 12

    elif price < 0:
        score -= 5

    if tx < 20:
        score -= 4

    return clamp(
        score,
        0,
        20
    )


def structure_score(
    s: Dict[str, Any],
    previous: Optional[Dict[str, Any]]
) -> float:

    lmc = s["liquidity_mc_pct"]

    liquidity = s["liquidity"]

    score = 0.0

    if lmc >= 20:
        score += 10
    elif lmc >= 12:
        score += 8
    elif lmc >= 8:
        score += 6
    elif lmc >= 5:
        score += 4
    elif lmc >= 3:
        score += 2

    if liquidity >= 50_000:
        score += 3
    elif liquidity >= 20_000:
        score += 2
    elif liquidity >= 10_000:
        score += 1

    if previous:

        previous_liq = safe_float(
            previous.get(
                "liquidity"
            )
        )

        if previous_liq > 0:

            delta_pct = (
                (
                    liquidity
                    - previous_liq
                )
                / previous_liq
                * 100
            )

            if delta_pct >= 5:
                score += 2

            elif delta_pct <= -10:
                score -= 3

    return clamp(
        score,
        0,
        15
    )


def aligned_observation(
    s: Dict[str, Any]
) -> bool:

    return (
        s["bs_ratio"] >= 1.2
        and s["price_change_5m"] > 0
        and s["volume_mc_pct"] >= 1
        and s["tx_5m"] >= 20
    )


def directional_observation(
    s: Dict[str, Any]
) -> bool:

    return (
        s["bs_ratio"] >= 1.5
        and s["price_change_5m"] > 0
    )


def persistence_score(
    history: List[Dict[str, Any]],
    current: Dict[str, Any]
) -> float:

    observations = (
        history[-3:]
        if history
        else []
    )

    if len(observations) < 1:
        return 0

    observations = observations + [
        current
    ]

    observations = observations[-3:]

    if len(observations) < 2:
        return 0

    aligned_count = sum(
        1
        for x in observations
        if aligned_observation(x)
    )

    score = 0

    if len(observations) == 2:

        if aligned_count >= 2:
            score = 10

        elif aligned_count == 1:
            score = 4

    else:

        if aligned_count >= 3:
            score = 15

        elif aligned_count == 2:
            score = 10

        elif aligned_count == 1:
            score = 4

    directional_count = sum(
        1
        for x in observations
        if directional_observation(x)
    )

    if directional_count >= 3:
        score += 2

    return clamp(
        score,
        0,
        15
    )


def calculate_market_score(
    s: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
    history: List[Dict[str, Any]]
) -> Tuple[int, Dict[str, float]]:

    directional = directional_score(s)

    momentum = price_momentum_score(s)

    volume = volume_quality_score(s)

    structure = structure_score(
        s,
        previous
    )

    persistence = persistence_score(
        history,
        s
    )

    total = (
        directional
        + momentum
        + volume
        + structure
        + persistence
    )

    hard_warning = (
        s["volume_mc_pct"] >= 20
        and s["price_change_5m"] <= -15
    )

    if hard_warning:
        total = min(
            total,
            45
        )

    total = int(
        clamp(
            total,
            0,
            100
        )
    )

    return total, {
        "directional": directional,
        "momentum": momentum,
        "volume": volume,
        "structure": structure,
        "persistence": persistence
    }


# ============================================================
# DECAY FILTER
# ============================================================

def decay_warnings(
    s: Dict[str, Any],
    previous: Optional[Dict[str, Any]]
) -> List[str]:

    warnings = []

    if s["market_cap"] < DECAY_MC_CUTOFF:

        if s["price_change_5m"] <= 0:
            warnings.append(
                "PRICE_NOT_RISING"
            )

        if s["bs_ratio"] < DECAY_BS_MIN:
            warnings.append(
                "WEAK_BUY_SELL_RATIO"
            )

        if s["tx_5m"] < DECAY_TX_MIN:
            warnings.append(
                "LOW_TX"
            )

        if (
            s["volume_mc_pct"] >= DECAY_VOLUME_MC
            and s["price_change_5m"] < 0
        ):
            warnings.append(
                "HIGH_VOLUME_WITH_NEGATIVE_PRICE"
            )

        if previous:

            previous_liq = safe_float(
                previous.get(
                    "liquidity"
                )
            )

            if previous_liq > 0:

                drop_pct = (
                    (
                        previous_liq
                        - s["liquidity"]
                    )
                    / previous_liq
                    * 100
                )

                if drop_pct > DECAY_LIQ_DROP_PCT:
                    warnings.append(
                        "LIQUIDITY_DROP"
                    )

    return warnings


def observation_is_weakening(
    previous: Optional[Dict[str, Any]],
    current: Dict[str, Any]
) -> bool:

    if not previous:
        return False

    worsening = 0

    if current["bs_ratio"] < previous["bs_ratio"]:
        worsening += 1

    if current["price_change_5m"] < previous["price_change_5m"]:
        worsening += 1

    if current["volume_mc_pct"] < previous["volume_mc_pct"]:
        worsening += 1

    if current["tx_5m"] < previous["tx_5m"]:
        worsening += 1

    if current["liquidity"] < previous["liquidity"]:
        worsening += 1

    return worsening >= 3


# ============================================================
# CLASSIFICATION
# ============================================================

def classify_market(
    s: Dict[str, Any],
    score: int,
    history: List[Dict[str, Any]],
    decay: List[str],
    weakening_streak: int
) -> str:

    mc = s["market_cap"]

    if mc < MIN_MC:
        return "OUT_OF_RANGE"

    if mc > MAX_MC:
        return "OUT_OF_ALERT_RANGE"

    if len(history) + 1 < MIN_OBSERVATIONS_RUNNER:
        return "WATCH"

    if len(decay) >= 2:
        return "WATCH"

    if weakening_streak >= 2:
        return "WATCH"

    if (
        score >= IDEAL_SCORE
        and len(history) + 1 >= MIN_OBSERVATIONS_IDEAL
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
            "RunnerBot/1.4.2"
    }


def x_lookup_user(
    handle: str
) -> Tuple[
    Optional[Dict[str, Any]],
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
        f"{X_BASE}/2/users/by/username/"
        f"{encoded}"
    )

    data, error = http_json(
        url,
        headers=x_headers(),
        timeout=HTTP_TIMEOUT
    )

    if error:
        return None, error

    if not isinstance(data, dict):
        return None, "INVALID_RESPONSE"

    user = data.get(
        "data"
    )

    if not isinstance(user, dict):
        return None, "ACCOUNT_NOT_FOUND"

    return user, "FOUND"


def x_recent_posts(
    username: str
) -> Tuple[
    List[Dict[str, Any]],
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
            "query": query,
            "max_results": 25,
            "tweet.fields":
                "created_at,public_metrics,text",
        }
    )

    url = (
        f"{X_BASE}/2/tweets/search/recent?"
        f"{params}"
    )

    data, error = http_json(
        url,
        headers=x_headers(),
        timeout=HTTP_TIMEOUT
    )

    if error:
        return [], error

    if not isinstance(data, dict):
        return [], "INVALID_RESPONSE"

    posts = data.get(
        "data",
        []
    )

    if not isinstance(posts, list):
        return [], "NO_POSTS"

    return posts[:LORE_MAX_X_POSTS], "FOUND"


def format_x_posts(
    posts: List[Dict[str, Any]]
) -> str:

    chunks = []

    for post in posts:

        text = clean_text(
            post.get(
                "text",
                ""
            )
        )

        if not text:
            continue

        chunks.append(
            text
        )

    combined = "\n".join(
        f"- {x}"
        for x in chunks
    )

    return truncate(
        combined,
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


def fetch_website(
    url: str
) -> Tuple[str, str]:

    if not url:
        return "", "MISSING"

    if not (
        url.startswith("http://")
        or url.startswith("https://")
    ):
        url = "https://" + url

    raw, error = http_text(
        url,
        headers={
            "User-Agent":
                "Mozilla/5.0 RunnerBot/1.4.2"
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
        LORE_MAX_WEBSITE_CHARS
    )

    if not text:
        return "", "EMPTY"

    return text, "FOUND"


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
        "handle": handle,

        "x_status": "MISSING",

        "x_posts": [],

        "x_post_count": 0,

        "x_profile": {},

        "x_posts_text": "",

        "website": website,

        "website_status": (
            "MISSING"
            if not website
            else "NOT_CHECKED"
        ),

        "website_text": "",

        "dex_description":
            dex_description,

        "dex_description_status": (
            "YES"
            if dex_description
            else "NO"
        ),

        "source_count": 0,

        "source_summary": [],

        "error": ""
    }

    # --------------------------------------------------------
    # X
    # --------------------------------------------------------

    if not handle:

        result["x_status"] = "MISSING"

    elif not X_BEARER_TOKEN:

        result["x_status"] = (
            "NOT_CONFIGURED"
        )

    else:

        user, user_status = x_lookup_user(
            handle
        )

        if user_status == "FOUND":

            result["x_status"] = "FOUND"

            result["x_profile"] = (
                user or {}
            )

            user_id = clean_text(
                user.get(
                    "username",
                    ""
                )
            )

            posts, posts_status = (
                x_recent_posts(
                    user_id or handle
                )
            )

            if posts_status == "FOUND":

                result["x_posts"] = posts

                result["x_post_count"] = (
                    len(posts)
                )

                result["x_posts_text"] = (
                    format_x_posts(posts)
                )

            elif posts_status == "NO_POSTS":

                result["x_posts"] = []

                result["x_post_count"] = 0

            else:

                result["x_posts"] = []

                result["x_post_count"] = 0

        else:

            result["x_status"] = (
                "ERROR"
            )

            result["error"] = (
                f"X_LOOKUP_FAILED:{user_status}"
            )

    # --------------------------------------------------------
    # WEBSITE
    # --------------------------------------------------------

    if website:

        website_text, website_status = (
            fetch_website(
                website
            )
        )

        result["website_text"] = (
            website_text
        )

        result["website_status"] = (
            website_status
        )

    else:

        result["website_status"] = (
            "MISSING"
        )

    # --------------------------------------------------------
    # SOURCE COUNT
    #
    # IMPORTANT:
    # X profile + X posts = ONE source type.
    # Website = ONE.
    # Dex description = ONE.
    # --------------------------------------------------------

    sources = []

    if (
        result["x_status"] == "FOUND"
        and result["x_post_count"] > 0
    ):
        sources.append("X")

    if result["website_status"] == "FOUND":
        sources.append("WEBSITE")

    if (
        result["dex_description_status"]
        == "YES"
    ):
        sources.append(
            "DEX_DESCRIPTION"
        )

    result["source_summary"] = sources

    result["source_count"] = len(
        sources
    )

    # --------------------------------------------------------
    # SOURCE ERROR
    # --------------------------------------------------------

    if result["source_count"] == 0:

        if not X_BEARER_TOKEN and not website and not dex_description:
            result["error"] = (
                "NO_INDEPENDENT_SOURCES"
            )

        elif (
            result["x_status"] == "NOT_CONFIGURED"
            and result["website_status"] == "MISSING"
            and result["dex_description_status"] == "NO"
        ):
            result["error"] = (
                "NO_INDEPENDENT_SOURCES"
            )

        else:
            result["error"] = (
                "INSUFFICIENT_EVIDENCE"
            )

    elif result["source_count"] < LORE_MIN_EVIDENCE:

        result["error"] = (
            "INSUFFICIENT_EVIDENCE"
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
            "@" + handle
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
        "  DEX DESCRIPTION: "
        + sources.get(
            "dex_description_status",
            "NO"
        )
    )

    print(
        "  INDEPENDENT SOURCES: "
        + str(
            sources.get(
                "source_count",
                0
            )
        )
    )

    summary = sources.get(
        "source_summary",
        []
    )

    print(
        "  SOURCES: "
        + (
            ", ".join(summary)
            if summary
            else "NONE"
        )
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
    Optional[Dict[str, Any]],
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
            + ", ".join(missing)
        )

    system_prompt = """
You are a strict crypto token narrative/lore verifier.

Your job is NOT to invent a story.

Only identify a narrative/lore when there is actual evidence
from the supplied sources.

A token should PASS only when the evidence shows a reasonably
clear and convincing narrative that could plausibly support
organic community attention.

Evaluate:

1. Core story
2. Narrative clarity
3. Cultural relevance
4. Memetic potential
5. Community participation
6. Consistency
7. Originality
8. Narrative momentum
9. Evidence quality

Important rules:

- Never invent lore.
- Never assume a token has a story merely because of its name.
- A generic meme name is not sufficient.
- A website alone is not automatically convincing.
- Marketing claims are evidence, but should be treated cautiously.
- X posts can show narrative development and community participation.
- Look for consistency across independent sources.
- Distinguish actual evidence from promotional claims.
- If evidence is insufficient, return UNKNOWN.
- Strong market activity does not prove lore.
- Do not make investment recommendations.

Return ONLY valid JSON.

Required format:

{
  "lore_score": 0,
  "confidence": "HIGH|MEDIUM|LOW",
  "momentum": "HIGH|MEDIUM|LOW",
  "status": "PASS|FAIL|UNKNOWN",
  "narrative": "short explanation",
  "evidence": [
    "specific evidence"
  ],
  "red_flags": [
    "specific concern"
  ]
}

Score from 0 to 25.

PASS normally requires:
- score >= 18
- at least 2 specific evidence points
- confidence HIGH or MEDIUM
- evidence from at least 2 independent source types

If those conditions cannot be supported, use UNKNOWN.
"""

    payload = {
        "model": LORE_AI_MODEL,

        "messages": [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
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
                                ),
                            "website":
                                sources.get(
                                    "website"
                                ),
                            "x_handle":
                                sources.get(
                                    "handle"
                                ),
                            "dex_description":
                                sources.get(
                                    "dex_description"
                                ),
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
                            "website_text":
                                sources.get(
                                    "website_text"
                                ),
                            "dex_description":
                                sources.get(
                                    "dex_description"
                                ),
                            "source_types":
                                sources.get(
                                    "source_summary"
                                ),
                        }
                    },
                    ensure_ascii=False
                )
            }
        ],

        "temperature": 0.1,

        "max_tokens": 700
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
                return None, "AI_REQUEST_FAILED: EMPTY_RESPONSE"

            data = json.loads(
                raw
            )

    except Exception as e:

        return (
            None,
            f"AI_REQUEST_FAILED: {e}"
        )

    try:

        choices = data.get(
            "choices",
            []
        )

        if not choices:
            return (
                None,
                "AI_REQUEST_FAILED: NO_CHOICES"
            )

        message = choices[0].get(
            "message",
            {}
        )

        content = message.get(
            "content",
            ""
        )

        content = clean_text(
            content
        )

        if not content:
            return (
                None,
                "AI_REQUEST_FAILED: EMPTY_CONTENT"
            )

        # Remove markdown fences if model added them.
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
                "AI_INVALID_JSON: NOT_OBJECT"
            )

        return parsed, ""

    except json.JSONDecodeError:

        return (
            None,
            "AI_INVALID_JSON"
        )

    except Exception as e:

        return (
            None,
            f"AI_INVALID_JSON: {e}"
        )


# ============================================================
# LORE VERIFICATION
# ============================================================

def normalize_lore_result(
    raw: Dict[str, Any],
    sources: Dict[str, Any]
) -> Dict[str, Any]:

    score = safe_int(
        raw.get(
            "lore_score",
            0
        )
    )

    score = int(
        clamp(
            score,
            0,
            25
        )
    )

    confidence = (
        clean_text(
            raw.get(
                "confidence",
                "LOW"
            )
        ).upper()
    )

    momentum = (
        clean_text(
            raw.get(
                "momentum",
                "LOW"
            )
        ).upper()
    )

    status = (
        clean_text(
            raw.get(
                "status",
                "UNKNOWN"
            )
        ).upper()
    )

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

        elif sources["source_count"] < 2:

            status = "UNKNOWN"

    return {
        "lore_score": score,

        "confidence": confidence,

        "momentum": momentum,

        "status": status,

        "narrative": narrative,

        "evidence": evidence,

        "red_flags": red_flags,

        "reason": (
            "VERIFIED"
            if status == "PASS"
            else (
                "LORE_VERIFIED_FAIL"
                if status == "FAIL"
                else "INSUFFICIENT_EVIDENCE"
            )
        ),

        "source_count":
            sources["source_count"],

        "source_summary":
            sources["source_summary"],

        "x_status":
            sources["x_status"],

        "x_post_count":
            sources["x_post_count"],

        "website_status":
            sources["website_status"],

        "dex_description_status":
            sources["dex_description_status"],

        "ai_status":
            "CONNECTED"
    }


def unknown_lore_result(
    sources: Dict[str, Any],
    reason: str
) -> Dict[str, Any]:

    return {
        "lore_score": 0,

        "confidence": "LOW",

        "momentum": "LOW",

        "status": "UNKNOWN",

        "narrative": "",

        "evidence": [],

        "red_flags": [],

        "reason": reason,

        "source_count":
            sources.get(
                "source_count",
                0
            ),

        "source_summary":
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

        "dex_description_status":
            sources.get(
                "dex_description_status",
                "NO"
            ),

        "ai_status":
            "NOT_CONFIGURED"
            if reason.startswith(
                "AI_NOT_CONFIGURED"
            )
            else "NOT_RUN",

        "ai_error":
            reason
    }


# ============================================================
# LORE RESEARCH
# ============================================================

def research_lore(
    token: Dict[str, Any]
) -> Dict[str, Any]:

    address = token.get(
        "address",
        ""
    )

    print(
        "======================================================"
    )

    print(
        f"[LORE RESEARCH] "
        f"{token.get('symbol', 'UNKNOWN')}"
    )

    print(
        "======================================================"
    )

    # --------------------------------------------------------
    # FIRST: COLLECT EVIDENCE
    # --------------------------------------------------------

    sources = collect_lore_sources(
        token
    )

    # --------------------------------------------------------
    # PRINT SOURCES BEFORE AI
    # --------------------------------------------------------

    print_lore_sources(
        sources
    )

    # --------------------------------------------------------
    # INSUFFICIENT SOURCE EVIDENCE
    # --------------------------------------------------------

    if sources["source_count"] < LORE_MIN_EVIDENCE:

        reason = (
            sources.get(
                "error"
            )
            or "INSUFFICIENT_EVIDENCE"
        )

        print(
            "[LORE AI]"
        )

        print(
            "STATUS: NOT RUN"
        )

        print(
            f"REASON: {reason}"
        )

        print(
            "------------------------------------------------------"
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

    # --------------------------------------------------------
    # AI CONFIGURATION
    # --------------------------------------------------------

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
            + ", ".join(missing)
        )

        print(
            "[LORE AI]"
        )

        print(
            "STATUS: NOT CONFIGURED"
        )

        print(
            f"REASON: {reason}"
        )

        print(
            "------------------------------------------------------"
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

    # --------------------------------------------------------
    # AI ANALYSIS
    # --------------------------------------------------------

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
            f"STATUS: ERROR"
        )

        print(
            f"REASON: {error}"
        )

        print(
            "------------------------------------------------------"
        )

        result = unknown_lore_result(
            sources,
            error
        )

        result["ai_status"] = "ERROR"

        result["ai_error"] = error

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
        + result["status"]
    )

    print(
        "SCORE: "
        + str(
            result["lore_score"]
        )
        + "/25"
    )

    print(
        "CONFIDENCE: "
        + result["confidence"]
    )

    print(
        "MOMENTUM: "
        + result["momentum"]
    )

    print(
        "REASON: "
        + result["reason"]
    )

    print(
        "------------------------------------------------------"
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
        f"[LORE RESULT] "
        f"{token.get('symbol', 'UNKNOWN')}"
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
        "Source Types: "
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
            else "NONE"
        )
    )

    print(
        "AI Status: "
        f"{result.get('ai_status', 'UNKNOWN')}"
    )

    if result.get("ai_error"):

        print(
            "AI Error: "
            + str(
                result["ai_error"]
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
) -> Optional[Dict[str, Any]]:

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
        time.time() - timestamp
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
            "address": address,

            "symbol":
                snapshot["symbol"],

            "name":
                snapshot["name"],

            "first_seen":
                iso_now(),

            "initial_mc":
                snapshot["market_cap"],

            "initial_liquidity":
                snapshot["liquidity"],

            "max_mc":
                snapshot["market_cap"],

            "min_mc":
                snapshot["market_cap"],

            "max_liquidity":
                snapshot["liquidity"],

            "snapshots": [],

            "alerts": [],

            "alerted": False,

            "alert_mc": 0,

            "classification":
                "WATCH",

            "score": 0,

            "weakening_streak": 0,

            "lore": None,

            "lore_timestamp": 0,

            "outcome": None,

            "last_seen":
                iso_now()
        }

    return tracking[address]


def update_record(
    record: Dict[str, Any],
    snapshot: Dict[str, Any]
) -> None:

    record["symbol"] = snapshot[
        "symbol"
    ]

    record["name"] = snapshot[
        "name"
    ]

    record["max_mc"] = max(
        safe_float(
            record.get(
                "max_mc"
            )
        ),
        snapshot["market_cap"]
    )

    record["min_mc"] = min(
        safe_float(
            record.get(
                "min_mc"
            )
        ),
        snapshot["market_cap"]
    )

    record["max_liquidity"] = max(
        safe_float(
            record.get(
                "max_liquidity"
            )
        ),
        snapshot["liquidity"]
    )

    record["last_seen"] = iso_now()

    snapshots = record.setdefault(
        "snapshots",
        []
    )

    snapshots.append(
        {
            "timestamp":
                iso_now(),

            "market_cap":
                snapshot["market_cap"],

            "liquidity":
                snapshot["liquidity"],

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

    # Keep enough history without
    # allowing the JSON file to grow forever.
    if len(snapshots) > 100:
        del snapshots[:-100]


def tracking_history(
    record: Dict[str, Any]
) -> List[Dict[str, Any]]:

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
        max_mc >= initial_mc * 1.50
        and (
            initial_liquidity <= 0
            or max_liquidity
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
    lore: Dict[str, Any]
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
        f"MC: ${mc:,.0f}",
        f"Liquidity: ${liquidity:,.0f}",
        f"5m Price: {price:+.1f}%",
        f"B/S: {bs:.2f}",
        f"5m Vol/MC: {vmc:.1f}%",
        f"5m TX: {tx:,}",
        "",
        f"Market Score: {market_score}/100",
        "",
        f"LORE SCORE: {lore_score}/25",
        f"LORE CONFIDENCE: {confidence}",
        f"LORE MOMENTUM: {momentum}",
        "",
        "NARRATIVE:",
        narrative or "Verified narrative",
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
# ALERT GATE
# ============================================================

def alert_eligible(
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
# VALIDATION
# ============================================================

def validate_token(
    state: Dict[str, Any],
    snapshot: Dict[str, Any]
) -> None:

    address = snapshot[
        "address"
    ]

    record = ensure_token_record(
        state,
        snapshot
    )

    previous = None

    snapshots = tracking_history(
        record
    )

    if snapshots:
        previous = snapshots[-1]

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
            ) + 1
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

    # --------------------------------------------------------
    # PRINT MARKET
    # --------------------------------------------------------

    print(
        "[TOKEN] "
        + snapshot["symbol"]
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
            + ", ".join(decay)
        )

    print(
        f"Weakening streak: "
        f"{weakening_streak}"
    )

    # --------------------------------------------------------
    # CHEAP MARKET PRE-CHECK
    #
    # Lore is only researched when the market is
    # sufficiently interesting.
    # --------------------------------------------------------

    market_precheck = (
        MIN_MC
        <= snapshot["market_cap"]
        <= MAX_MC
        and score >= RUNNER_SCORE
        and snapshot["price_change_5m"] > 0
        and snapshot["bs_ratio"] >= 1.2
        and len(decay) < 2
        and weakening_streak < 2
        and classification in (
            "RUNNER",
            "IDEAL RUNNER"
        )
    )

    if not market_precheck:

        print(
            "[LORE] Research not triggered "
            "(market pre-check failed)"
        )

        save_json(
            STATE_FILE,
            state
        )

        return

    # --------------------------------------------------------
    # LORE CACHE
    # --------------------------------------------------------

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

        record["lore"] = lore

        record[
            "lore_timestamp"
        ] = time.time()

    # --------------------------------------------------------
    # ALERT GATE
    # --------------------------------------------------------

    eligible, reasons = alert_eligible(
        snapshot,
        score,
        classification,
        decay,
        weakening_streak,
        lore,
        record
    )

    if not eligible:

        print(
            "[NO ALERT]"
        )

        for reason in reasons:

            print(
                "  - "
                + reason
            )

        save_json(
            STATE_FILE,
            state
        )

        return

    # --------------------------------------------------------
    # ALERT
    # --------------------------------------------------------

    alert = build_alert(
        snapshot,
        score,
        lore
    )

    subscribers = state.get(
        "subscribers",
        []
    )

    print(
        "======================================================"
    )

    print(
        "[ALERT]"
    )

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

    record[
        "alerted"
    ] = True

    record[
        "alert_mc"
    ] = snapshot[
        "market_cap"
    ]

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

            "lore_score":
                lore.get(
                    "lore_score",
                    0
                ),

            "subscribers_notified":
                sent_count
        }
    )

    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# DISCOVERY UPDATE
# ============================================================

def update_tracking_from_discovery(
    state: Dict[str, Any],
    candidates: List[Dict[str, Any]]
) -> None:

    for snapshot in candidates:

        ensure_token_record(
            state,
            snapshot
        )

    print(
        f"[DISCOVERY] Tracking now: "
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
        f"VALIDATION CYCLE "
        f"{datetime.now().strftime('%H:%M:%S')}"
    )

    print(
        "===================================================================="
    )

    print(
        f"[VALIDATION] Tokens: "
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
                f"[VALIDATION ERROR] "
                f"{address}: {e}"
            )

    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# TRACKING CLEANUP
# ============================================================

def cleanup_tracking(
    state: Dict[str, Any]
) -> None:

    tracking = state.get(
        "tracking",
        {}
    )

    now = time.time()

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
                ) - dt
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
        f"RUNNER BOT "
        f"{BOT_VERSION}"
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
        f"Lore threshold: "
        f"{LORE_MIN_SCORE}/25"
    )

    print(
        f"Lore cache: "
        f"{LORE_CACHE_MINUTES} minutes"
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
        f"STARTED: {iso_now()}"
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

            # --------------------------------------------------------
            # DISCOVERY
            # --------------------------------------------------------

            if (
                current - last_discovery
                >= DISCOVERY_INTERVAL
                or not state.get(
                    "tracking"
                )
            ):

                print(
                    "===================================================================="
                )

                print(
                    f"DISCOVERY CYCLE "
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

            # --------------------------------------------------------
            # VALIDATION
            # --------------------------------------------------------

            if (
                current - last_validation
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

            # --------------------------------------------------------
            # TELEGRAM
            # --------------------------------------------------------

            poll_telegram(
                state
            )

            # --------------------------------------------------------
            # CLEANUP
            # --------------------------------------------------------

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
                f"[MAIN ERROR] {e}"
            )

            time.sleep(
                SCAN_INTERVAL
            )


if __name__ == "__main__":
    main()
