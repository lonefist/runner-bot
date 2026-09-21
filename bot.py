import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# RUNNER BOT V3.0
# EVIDENCE RUNNER / LORE INTEGRATION
# ============================================================

BOT_VERSION = "V3.0-EVIDENCE-RUNNER"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

CHAIN = "solana"

# ------------------------------------------------------------
# MARKET LIMITS
# ------------------------------------------------------------

MIN_MC = 20_000
MAX_MC = 150_000

# Primary early-runner zone
EARLY_MIN_MC = 20_000
EARLY_MAX_MC = 80_000

MIN_LIQUIDITY = 5_000
EARLY_MIN_LIQUIDITY = 10_000

SCAN_INTERVAL_SECONDS = int(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)

DISCOVERY_INTERVAL_SECONDS = int(
    os.getenv("DISCOVERY_INTERVAL_SECONDS", "300")
)

# ------------------------------------------------------------
# RUNNER RULES
# ------------------------------------------------------------

RUNNER_SCORE = 72
IDEAL_SCORE = 85

RUNNER_BS = 1.20
IDEAL_BS = 1.50

MAX_HISTORY = 20

TRACKING_TTL_SECONDS = 8 * 60 * 60
ALERT_TRACKING_TTL_SECONDS = 48 * 60 * 60

# ------------------------------------------------------------
# EARLY NARRATIVE RULES
# ------------------------------------------------------------

EARLY_MIN_PRICE = 5.0
EARLY_MIN_BS = 1.20
EARLY_MIN_TX = 100
EARLY_MIN_VOLUME_MC = 10.0

EARLY_MIN_LORE_SCORE = 21
EARLY_MIN_EVIDENCE = 2
EARLY_MIN_SOURCE_TYPES = 2

# Normal lore runner
NORMAL_MIN_LORE_SCORE = 18
NORMAL_MIN_EVIDENCE = 2
NORMAL_MIN_SOURCE_TYPES = 2

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

HTTP_TIMEOUT = 12
LORE_HTTP_TIMEOUT = 10

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; RunnerBot/3.0; +https://dexscreener.com)"
)

# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

HEATING_ALERTS_ENABLED = (
    os.getenv(
        "HEATING_ALERTS_ENABLED",
        "true"
    ).lower() == "true"
)

# ------------------------------------------------------------
# OPTIONAL X API
# ------------------------------------------------------------

X_BEARER_TOKEN = os.getenv(
    "X_BEARER_TOKEN",
    ""
)

X_BASE = "https://api.x.com/2"

# ------------------------------------------------------------
# OPTIONAL LORE AI
#
# This is NOT required.
#
# If configured, the bot can use an OpenAI-compatible
# endpoint for a final narrative interpretation.
# The deterministic evidence engine remains the authority.
# ------------------------------------------------------------

LORE_AI_BASE_URL = os.getenv(
    "LORE_AI_BASE_URL",
    ""
)

LORE_AI_MODEL = os.getenv(
    "LORE_AI_MODEL",
    ""
)

LORE_AI_API_KEY = os.getenv(
    "LORE_AI_API_KEY",
    ""
)

LORE_AI_TIMEOUT = 20

# ------------------------------------------------------------
# DISCOVERY
# ------------------------------------------------------------

DISCOVERY_ENDPOINTS = [
    "/token-profiles/latest/v1",
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
    "/community-takeovers/latest/v1",
]

# ------------------------------------------------------------
# STATE
# ------------------------------------------------------------

SUBSCRIBERS = set()

TOKEN_HISTORY: Dict[str, List[Dict[str, Any]]] = {}

DISCOVERED_TOKENS: Dict[str, Dict[str, Any]] = {}

LAST_ALERT_STATUS: Dict[str, str] = {}

LAST_DISCOVERY = 0.0

LORE_CACHE: Dict[str, Dict[str, Any]] = {}


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts() -> int:
    return int(time.time())


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
        min(maximum, value)
    )


def truncate(
    text: str,
    maximum: int
) -> str:

    if not text:
        return ""

    text = str(text)

    if len(text) <= maximum:
        return text

    return text[:maximum] + "..."


def clean_text(
    text: Any
) -> str:

    if text is None:
        return ""

    text = str(text)

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def normalize_url(
    url: str
) -> str:

    if not url:
        return ""

    url = url.strip()

    if not url.startswith(
        ("http://", "https://")
    ):
        url = "https://" + url

    return url


def domain_from_url(
    url: str
) -> str:

    try:
        parsed = urllib.parse.urlparse(
            url
        )

        return parsed.netloc.lower()

    except Exception:
        return ""


def same_origin(
    url_a: str,
    url_b: str
) -> bool:

    a = domain_from_url(url_a)
    b = domain_from_url(url_b)

    if not a or not b:
        return False

    return a == b


# ============================================================
# HTTP
# ============================================================

def http_request(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = HTTP_TIMEOUT
) -> Optional[bytes]:

    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }

    if headers:
        request_headers.update(
            headers
        )

    request = urllib.request.Request(
        url,
        headers=request_headers
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            return response.read()

    except Exception as exc:

        print(
            f"[HTTP ERROR] {url} -> {exc}"
        )

        return None


def http_get_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = HTTP_TIMEOUT
) -> Any:

    raw = http_request(
        url,
        headers=headers,
        timeout=timeout
    )

    if raw is None:
        return None

    try:

        return json.loads(
            raw.decode(
                "utf-8",
                errors="ignore"
            )
        )

    except Exception as exc:

        print(
            f"[JSON ERROR] {url} -> {exc}"
        )

        return None


def http_get_text(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = LORE_HTTP_TIMEOUT
) -> str:

    raw = http_request(
        url,
        headers=headers,
        timeout=timeout
    )

    if raw is None:
        return ""

    return raw.decode(
        "utf-8",
        errors="ignore"
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_api(
    method: str,
    payload: Optional[Dict[str, Any]] = None
) -> Any:

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

    data = urllib.parse.urlencode(
        payload or {}
    ).encode()

    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": USER_AGENT
        }
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=HTTP_TIMEOUT
        ) as response:

            raw = response.read()

        return json.loads(
            raw.decode(
                "utf-8",
                errors="ignore"
            )
        )

    except Exception as exc:

        print(
            f"[TELEGRAM ERROR] {exc}"
        )

        return None


def send_telegram(
    chat_id: int,
    text: str
) -> bool:

    result = telegram_api(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
            "disable_web_page_preview": "true",
        }
    )

    if isinstance(result, dict):
        return bool(
            result.get("ok")
        )

    return False


def broadcast(
    text: str
) -> None:

    if not HEATING_ALERTS_ENABLED:
        return

    for chat_id in list(
        SUBSCRIBERS
    ):

        send_telegram(
            chat_id,
            text
        )


def telegram_poll(
    offset: Optional[int]
) -> Tuple[List[Dict[str, Any]], Optional[int]]:

    payload = {
        "timeout": "1",
        "allowed_updates": json.dumps(
            ["message"]
        )
    }

    if offset is not None:
        payload["offset"] = str(
            offset
        )

    result = telegram_api(
        "getUpdates",
        payload
    )

    if not isinstance(
        result,
        dict
    ):
        return [], offset

    if not result.get("ok"):
        return [], offset

    updates = result.get(
        "result",
        []
    )

    if not isinstance(
        updates,
        list
    ):
        return [], offset

    new_offset = offset

    for update in updates:

        update_id = safe_int(
            update.get(
                "update_id"
            )
        )

        if new_offset is None:
            new_offset = (
                update_id + 1
            )

        else:
            new_offset = max(
                new_offset,
                update_id + 1
            )

    return updates, new_offset


def handle_telegram_updates(
    updates: List[Dict[str, Any]]
) -> None:

    for update in updates:

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

        text = clean_text(
            message.get(
                "text",
                ""
            )
        )

        if text.startswith(
            "/start"
        ):

            SUBSCRIBERS.add(
                int(chat_id)
            )

            send_telegram(
                int(chat_id),
                (
                    "🔥 Runner Bot V3.0 is online.\n\n"
                    "Market scanner: ACTIVE\n"
                    "Evidence engine: ACTIVE\n"
                    "Lore verification: ACTIVE\n\n"
                    "Use /status for scanner status."
                )
            )

            print(
                f"[TELEGRAM] /start from {chat_id}"
            )

        elif text.startswith(
            "/status"
        ):

            send_telegram(
                int(chat_id),
                build_status_message()
            )


def build_status_message() -> str:

    return (
        "🔥 RUNNER BOT V3.0\n\n"
        f"Discovered tokens: {len(DISCOVERED_TOKENS)}\n"
        f"Tracked tokens: {len(TOKEN_HISTORY)}\n"
        f"Subscribers: {len(SUBSCRIBERS)}\n"
        f"Lore cache: {len(LORE_CACHE)}\n\n"
        "Market engine: ACTIVE\n"
        "Evidence engine: ACTIVE\n"
        "Lore engine: ACTIVE"
    )


# ============================================================
# DEXSCREENER DISCOVERY
# ============================================================

def get_discovery_candidates() -> List[str]:

    addresses = set()

    for endpoint in DISCOVERY_ENDPOINTS:

        url = (
            f"{DEX_BASE}"
            f"{endpoint}"
        )

        data = http_get_json(
            url
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

            chain_id = clean_text(
                item.get(
                    "chainId"
                )
            ).lower()

            if chain_id != CHAIN:
                continue

            address = clean_text(
                item.get(
                    "tokenAddress"
                )
            )

            if address:
                addresses.add(
                    address
                )

    return list(
        addresses
    )


# ============================================================
# TOKEN LOOKUP
# ============================================================

def get_token_pairs(
    token_address: str
) -> List[Dict[str, Any]]:

    url = (
        f"{DEX_BASE}/token/"
        f"{CHAIN}/{token_address}"
    )

    data = http_get_json(
        url
    )

    if not isinstance(
        data,
        dict
    ):
        return []

    pairs = data.get(
        "pairs"
    )

    if not isinstance(
        pairs,
        list
    ):
        return []

    return pairs


def select_best_pair(
    token_address: str
) -> Optional[Dict[str, Any]]:

    pairs = get_token_pairs(
        token_address
    )

    valid_pairs = []

    for pair in pairs:

        if not isinstance(
            pair,
            dict
        ):
            continue

        if clean_text(
            pair.get(
                "chainId"
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
                "usd"
            )
        )

        if liquidity <= 0:
            continue

        valid_pairs.append(
            pair
        )

    if not valid_pairs:
        return None

    valid_pairs.sort(
        key=lambda item:
        safe_float(
            (
                item.get(
                    "liquidity",
                    {}
                ) or {}
            ).get(
                "usd"
            )
        ),
        reverse=True
    )

    return valid_pairs[0]


# ============================================================
# MARKET SNAPSHOT
# ============================================================

def make_snapshot(
    token_address: str,
    pair: Dict[str, Any]
) -> Dict[str, Any]:

    base = pair.get(
        "baseToken",
        {}
    ) or {}

    txns = pair.get(
        "txns",
        {}
    ) or {}

    m5 = txns.get(
        "m5",
        {}
    ) or {}

    buys = safe_int(
        m5.get(
            "buys"
        )
    )

    sells = safe_int(
        m5.get(
            "sells"
        )
    )

    if sells > 0:
        bs_ratio = (
            buys / sells
        )
    elif buys > 0:
        bs_ratio = 999.0
    else:
        bs_ratio = 0.0

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
            pair.get(
                "liquidity",
                {}
            ) or {}
        ).get(
            "usd"
        )
    )

    volume = safe_float(
        (
            pair.get(
                "volume",
                {}
            ) or {}
        ).get(
            "m5"
        )
    )

    price_change = safe_float(
        (
            pair.get(
                "priceChange",
                {}
            ) or {}
        ).get(
            "m5"
        )
    )

    tx_count = (
        buys + sells
    )

    volume_mc = (
        (volume / market_cap) * 100
        if market_cap > 0
        else 0.0
    )

    liquidity_mc = (
        (liquidity / market_cap) * 100
        if market_cap > 0
        else 0.0
    )

    info = pair.get(
        "info",
        {}
    ) or {}

    websites = info.get(
        "websites",
        []
    )

    socials = info.get(
        "socials",
        []
    )

    if not isinstance(
        websites,
        list
    ):
        websites = []

    if not isinstance(
        socials,
        list
    ):
        socials = []

    return {
        "timestamp": now_ts(),
        "address": token_address,
        "symbol": clean_text(
            base.get(
                "symbol",
                "UNKNOWN"
            )
        ),
        "name": clean_text(
            base.get(
                "name",
                "Unknown"
            )
        ),
        "market_cap": market_cap,
        "liquidity": liquidity,
        "volume_5m": volume,
        "buys_5m": buys,
        "sells_5m": sells,
        "bs_ratio": bs_ratio,
        "tx_5m": tx_count,
        "price_change_5m": price_change,
        "volume_mc": volume_mc,
        "liquidity_mc": liquidity_mc,
        "pair_address": clean_text(
            pair.get(
                "pairAddress"
            )
        ),
        "dex_id": clean_text(
            pair.get(
                "dexId"
            )
        ),
        "pair_url": clean_text(
            pair.get(
                "url"
            )
        ),
        "websites": websites,
        "socials": socials,
    }


# ============================================================
# HISTORY
# ============================================================

def add_history(
    address: str,
    snapshot: Dict[str, Any]
) -> None:

    history = TOKEN_HISTORY.setdefault(
        address,
        []
    )

    history.append(
        snapshot
    )

    if len(history) > MAX_HISTORY:
        del history[
            :-MAX_HISTORY
        ]


def get_previous_snapshot(
    address: str
) -> Optional[Dict[str, Any]]:

    history = TOKEN_HISTORY.get(
        address,
        []
    )

    if len(history) < 2:
        return None

    return history[-2]


# ============================================================
# DECAY
# ============================================================

def calculate_decay(
    current: Dict[str, Any],
    previous: Optional[Dict[str, Any]]
) -> int:

    decay = 0

    if current[
        "price_change_5m"
    ] <= 0:
        decay += 1

    if current[
        "bs_ratio"
    ] < 1.0:
        decay += 1

    if current[
        "tx_5m"
    ] < 20:
        decay += 1

    if (
        current["volume_mc"] >= 20
        and current["price_change_5m"] < 0
    ):
        decay += 1

    if previous:

        previous_liquidity = safe_float(
            previous.get(
                "liquidity"
            )
        )

        current_liquidity = safe_float(
            current.get(
                "liquidity"
            )
        )

        if (
            previous_liquidity > 0
            and current_liquidity
            < previous_liquidity * 0.90
        ):
            decay += 1

        previous_bs = safe_float(
            previous.get(
                "bs_ratio"
            )
        )

        current_bs = safe_float(
            current.get(
                "bs_ratio"
            )
        )

        if (
            previous_bs > 0
            and current_bs
            < previous_bs * 0.70
        ):
            decay += 1

    return decay


# ============================================================
# WEAKENING
# ============================================================

def calculate_weakening(
    current: Dict[str, Any],
    previous: Optional[Dict[str, Any]]
) -> int:

    if not previous:
        return 0

    weakening = 0

    if (
        current["bs_ratio"]
        < previous["bs_ratio"]
    ):
        weakening += 1

    if (
        current["price_change_5m"]
        < previous["price_change_5m"]
    ):
        weakening += 1

    if (
        current["volume_mc"]
        < previous["volume_mc"]
    ):
        weakening += 1

    if (
        current["tx_5m"]
        < previous["tx_5m"]
    ):
        weakening += 1

    if (
        current["liquidity"]
        < previous["liquidity"]
    ):
        weakening += 1

    return weakening


# ============================================================
# MARKET SCORE
# ============================================================

def market_score(
    snapshot: Dict[str, Any],
    observations: int,
    decay: int,
    weakening: int
) -> int:

    mc = snapshot[
        "market_cap"
    ]

    price = snapshot[
        "price_change_5m"
    ]

    bs = snapshot[
        "bs_ratio"
    ]

    tx = snapshot[
        "tx_5m"
    ]

    volume_mc = snapshot[
        "volume_mc"
    ]

    liquidity_mc = snapshot[
        "liquidity_mc"
    ]

    score = 0

    # --------------------------------------------------------
    # Market cap
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
    # Price
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
    # Buy / sell
    # --------------------------------------------------------

    if bs >= 2.0:
        score += 20

    elif bs >= 1.5:
        score += 17

    elif bs >= 1.2:
        score += 13

    elif bs >= 1.0:
        score += 7

    # --------------------------------------------------------
    # Transactions
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
    # Volume / MC
    # --------------------------------------------------------

    if volume_mc >= 40:
        score += 15

    elif volume_mc >= 25:
        score += 13

    elif volume_mc >= 15:
        score += 10

    elif volume_mc >= 10:
        score += 7

    elif volume_mc >= 5:
        score += 3

    # --------------------------------------------------------
    # Liquidity / MC
    # --------------------------------------------------------

    if liquidity_mc >= 40:
        score += 10

    elif liquidity_mc >= 25:
        score += 8

    elif liquidity_mc >= 15:
        score += 6

    elif liquidity_mc >= 10:
        score += 4

    # --------------------------------------------------------
    # History
    # --------------------------------------------------------

    if observations >= 3:
        score += 5

    elif observations >= 2:
        score += 2

    # --------------------------------------------------------
    # Penalties
    # --------------------------------------------------------

    score -= (
        decay * 8
    )

    score -= (
        weakening * 4
    )

    return int(
        clamp(
            score,
            0,
            100
        )
    )


# ============================================================
# EARLY MARKET FILTER
# ============================================================

def early_market_candidate(
    snapshot: Dict[str, Any]
) -> bool:

    return (
        EARLY_MIN_MC
        <= snapshot["market_cap"]
        <= EARLY_MAX_MC
        and snapshot["liquidity"]
        >= EARLY_MIN_LIQUIDITY
        and snapshot["price_change_5m"]
        >= EARLY_MIN_PRICE
        and snapshot["bs_ratio"]
        >= EARLY_MIN_BS
        and snapshot["tx_5m"]
        >= EARLY_MIN_TX
        and snapshot["volume_mc"]
        >= EARLY_MIN_VOLUME_MC
    )


# ============================================================
# LORE URL EXTRACTION
# ============================================================

def extract_urls(
    text: str
) -> List[str]:

    if not text:
        return []

    pattern = (
        r"https?://"
        r"[^\s\"'<>]+"
    )

    found = re.findall(
        pattern,
        text
    )

    clean = []

    for url in found:

        url = url.rstrip(
            ".,);]}>"
        )

        if url not in clean:
            clean.append(
                url
            )

    return clean


def classify_url(
    url: str
) -> str:

    lower = url.lower()

    if "github.com" in lower:
        return "github"

    if (
        "whitepaper" in lower
        or "docs." in lower
        or "/docs" in lower
        or ".pdf" in lower
        or "documentation" in lower
    ):
        return "docs"

    if (
        "x.com" in lower
        or "twitter.com" in lower
    ):
        return "x"

    if (
        "medium.com" in lower
        or "mirror.xyz" in lower
    ):
        return "blog"

    return "website"


def collect_project_urls(
    snapshot: Dict[str, Any]
) -> Dict[str, List[str]]:

    result = {
        "website": [],
        "github": [],
        "docs": [],
        "x": [],
        "blog": [],
    }

    for item in snapshot.get(
        "websites",
        []
    ):

        if not isinstance(
            item,
            dict
        ):
            continue

        url = normalize_url(
            clean_text(
                item.get(
                    "url"
                )
            )
        )

        if not url:
            continue

        category = classify_url(
            url
        )

        result.setdefault(
            category,
            []
        ).append(
            url
        )

    for item in snapshot.get(
        "socials",
        []
    ):

        if not isinstance(
            item,
            dict
        ):
            continue

        platform = clean_text(
            item.get(
                "platform"
            )
        ).lower()

        url = normalize_url(
            clean_text(
                item.get(
                    "url"
                )
            )
        )

        if not url:
            continue

        if platform in (
            "x",
            "twitter"
        ):
            result[
                "x"
            ].append(
                url
            )

    return result


# ============================================================
# WEBSITE RESEARCH
# ============================================================

def extract_title(
    html: str
) -> str:

    match = re.search(
        r"<title[^>]*>"
        r"(.*?)"
        r"</title>",
        html,
        flags=re.I | re.S
    )

    if not match:
        return ""

    return clean_text(
        re.sub(
            r"<[^>]+>",
            " ",
            match.group(1)
        )
    )


def html_to_text(
    html: str
) -> str:

    if not html:
        return ""

    html = re.sub(
        r"<script[^>]*>.*?</script>",
        " ",
        html,
        flags=re.I | re.S
    )

    html = re.sub(
        r"<style[^>]*>.*?</style>",
        " ",
        html,
        flags=re.I | re.S
    )

    html = re.sub(
        r"<[^>]+>",
        " ",
        html
    )

    html = re.sub(
        r"&nbsp;",
        " ",
        html,
        flags=re.I
    )

    html = re.sub(
        r"&amp;",
        "&",
        html,
        flags=re.I
    )

    return clean_text(
        html
    )


def extract_links_from_html(
    html: str,
    base_url: str
) -> List[str]:

    links = []

    pattern = re.compile(
        r'<a[^>]+href=["\']'
        r'([^"\']+)'
        r'["\']',
        flags=re.I
    )

    for match in pattern.finditer(
        html
    ):

        href = match.group(1).strip()

        if href.startswith(
            "#"
        ):
            continue

        try:

            full = urllib.parse.urljoin(
                base_url,
                href
            )

        except Exception:
            continue

        if full.startswith(
            ("http://", "https://")
        ):
            links.append(
                full
            )

    return list(
        dict.fromkeys(
            links
        )
    )


def research_website(
    url: str
) -> Dict[str, Any]:

    html = http_get_text(
        url
    )

    if not html:
        return {
            "url": url,
            "reachable": False,
            "title": "",
            "text": "",
            "links": [],
        }

    text = html_to_text(
        html
    )

    links = extract_links_from_html(
        html,
        url
    )

    return {
        "url": url,
        "reachable": True,
        "title": extract_title(
            html
        ),
        "text": truncate(
            text,
            30_000
        ),
        "links": links[:150],
    }


# ============================================================
# GITHUB
# ============================================================

def parse_github_url(
    url: str
) -> Optional[Tuple[str, str]]:

    try:

        parsed = urllib.parse.urlparse(
            url
        )

        if parsed.netloc.lower() not in (
            "github.com",
            "www.github.com"
        ):
            return None

        parts = [
            p
            for p in parsed.path.split("/")
            if p
        ]

        if len(parts) < 2:
            return None

        owner = parts[0]
        repo = parts[1]

        repo = repo.replace(
            ".git",
            ""
        )

        return owner, repo

    except Exception:
        return None


def research_github(
    url: str
) -> Dict[str, Any]:

    parsed = parse_github_url(
        url
    )

    if not parsed:
        return {
            "reachable": False,
            "url": url,
        }

    owner, repo = parsed

    api_url = (
        "https://api.github.com/repos/"
        f"{owner}/{repo}"
    )

    data = http_get_json(
        api_url,
        headers={
            "Accept":
            "application/vnd.github+json"
        }
    )

    if not isinstance(
        data,
        dict
    ):
        return {
            "reachable": False,
            "url": url,
            "owner": owner,
            "repo": repo,
        }

    readme_url = (
        f"https://raw.githubusercontent.com/"
        f"{owner}/{repo}/HEAD/README.md"
    )

    readme = http_get_text(
        readme_url
    )

    return {
        "reachable": True,
        "url": url,
        "owner": owner,
        "repo": repo,
        "description": clean_text(
            data.get(
                "description"
            )
        ),
        "stars": safe_int(
            data.get(
                "stargazers_count"
            )
        ),
        "forks": safe_int(
            data.get(
                "forks_count"
            )
        ),
        "created_at": clean_text(
            data.get(
                "created_at"
            )
        ),
        "updated_at": clean_text(
            data.get(
                "updated_at"
            )
        ),
        "language": clean_text(
            data.get(
                "language"
            )
        ),
        "default_branch": clean_text(
            data.get(
                "default_branch"
            )
        ),
        "readme": truncate(
            html_to_text(
                readme
            ),
            20_000
        ),
    }


# ============================================================
# X / TWITTER
# ============================================================

def extract_x_username(
    url: str
) -> str:

    try:

        parsed = urllib.parse.urlparse(
            url
        )

        parts = [
            p
            for p in parsed.path.split("/")
            if p
        ]

        if not parts:
            return ""

        username = parts[0]

        if username.lower() in (
            "home",
            "search",
            "explore",
            "i"
        ):
            return ""

        return username

    except Exception:
        return ""


def research_x(
    url: str
) -> Dict[str, Any]:

    username = extract_x_username(
        url
    )

    result = {
        "url": url,
        "username": username,
        "reachable": False,
        "profile_verified": False,
        "posts_found": 0,
        "posts": [],
    }

    if not username:
        return result

    # --------------------------------------------------------
    # Without X API:
    #
    # We record the official social link but DO NOT pretend
    # that its activity has been independently verified.
    # --------------------------------------------------------

    if not X_BEARER_TOKEN:
        return result

    headers = {
        "Authorization":
        f"Bearer {X_BEARER_TOKEN}"
    }

    user_url = (
        f"{X_BASE}/users/by/username/"
        f"{urllib.parse.quote(username)}"
        "?user.fields=created_at,description,"
        "name,username,verified"
    )

    user_data = http_get_json(
        user_url,
        headers=headers
    )

    if not isinstance(
        user_data,
        dict
    ):
        return result

    user = user_data.get(
        "data"
    )

    if not isinstance(
        user,
        dict
    ):
        return result

    result[
        "reachable"
    ] = True

    result[
        "profile_verified"
    ] = bool(
        user.get(
            "verified"
        )
    )

    result[
        "profile"
    ] = {
        "name": clean_text(
            user.get(
                "name"
            )
        ),
        "username": clean_text(
            user.get(
                "username"
            )
        ),
        "description": clean_text(
            user.get(
                "description"
            )
        ),
        "created_at": clean_text(
            user.get(
                "created_at"
            )
        ),
    }

    user_id = user.get(
        "id"
    )

    if not user_id:
        return result

    posts_url = (
        f"{X_BASE}/users/"
        f"{user_id}/tweets"
        "?max_results=10"
        "&tweet.fields=created_at,text"
    )

    posts_data = http_get_json(
        posts_url,
        headers=headers
    )

    if not isinstance(
        posts_data,
        dict
    ):
        return result

    posts = posts_data.get(
        "data",
        []
    )

    if not isinstance(
        posts,
        list
    ):
        posts = []

    for post in posts:

        if not isinstance(
            post,
            dict
        ):
            continue

        result[
            "posts"
        ].append(
            {
                "text":
                    clean_text(
                        post.get(
                            "text"
                        )
                    ),
                "created_at":
                    clean_text(
                        post.get(
                            "created_at"
                        )
                    ),
            }
        )

    result[
        "posts_found"
    ] = len(
        result["posts"]
    )

    return result


# ============================================================
# SOURCE ORIGIN
# ============================================================

def source_origin(
    url: str,
    official_website: str = ""
) -> str:

    domain = domain_from_url(
        url
    )

    if not domain:
        return "unknown"

    if official_website:

        official_domain = domain_from_url(
            official_website
        )

        if (
            official_domain
            and domain == official_domain
        ):
            return "official-website"

    if "github.com" in domain:
        return "github"

    if (
        "x.com" in domain
        or "twitter.com" in domain
    ):
        return "x"

    return domain


# ============================================================
# LORE EVIDENCE
# ============================================================

def keyword_hits(
    text: str,
    keywords: List[str]
) -> int:

    lower = text.lower()

    hits = 0

    for keyword in keywords:

        if keyword.lower() in lower:
            hits += 1

    return hits


def build_evidence(
    snapshot: Dict[str, Any],
    websites: List[Dict[str, Any]],
    githubs: List[Dict[str, Any]],
    xs: List[Dict[str, Any]],
    discovered_links: List[str]
) -> Dict[str, Any]:

    evidence = []

    official_website = ""

    for site in websites:

        if site.get(
            "reachable"
        ):

            official_website = site.get(
                "url",
                ""
            )

            break

    # --------------------------------------------------------
    # Project identity
    # --------------------------------------------------------

    identity_score = 0

    if official_website:
        identity_score += 2

    if snapshot.get(
        "name"
    ):
        identity_score += 1

    if snapshot.get(
        "symbol"
    ):
        identity_score += 1

    if identity_score >= 3:

        evidence.append(
            {
                "type": "project_identity",
                "source_type": "website",
                "origin": "official-website",
                "description":
                    "A reachable project identity and official web presence were found."
            }
        )

    # --------------------------------------------------------
    # Technical/product narrative
    # --------------------------------------------------------

    technical_keywords = [
        "protocol",
        "infrastructure",
        "ai",
        "agent",
        "api",
        "sdk",
        "network",
        "protocol",
        "daemon",
        "firewall",
        "automation",
        "machine learning",
        "software",
        "developer",
        "open source",
    ]

    technical_hits = 0

    for site in websites:

        technical_hits += keyword_hits(
            site.get(
                "text",
                ""
            ),
            technical_keywords
        )

    for github in githubs:

        technical_hits += keyword_hits(
            (
                github.get(
                    "description",
                    ""
                )
                + " "
                + github.get(
                    "readme",
                    ""
                )
            ),
            technical_keywords
        )

    if technical_hits >= 2:

        evidence.append(
            {
                "type":
                    "technical_narrative",
                "source_type":
                    "technical-documentation",
                "origin":
                    "project-documentation",
                "description":
                    "The project's technical/product narrative appears in project documentation or repository material."
            }
        )

    # --------------------------------------------------------
    # GitHub implementation
    # --------------------------------------------------------

    usable_githubs = [
        item
        for item in githubs
        if item.get(
            "reachable"
        )
    ]

    for github in usable_githubs:

        if (
            github.get(
                "readme"
            )
            or github.get(
                "description"
            )
        ):

            evidence.append(
                {
                    "type":
                        "implementation",
                    "source_type":
                        "github",
                    "origin":
                        "github",
                    "description":
                        "A reachable GitHub repository contains project/repository material."
                }
            )

            break

    # --------------------------------------------------------
    # Developer identity
    # --------------------------------------------------------

    usable_x = [
        item
        for item in xs
        if item.get(
            "reachable"
        )
    ]

    if usable_x:

        evidence.append(
            {
                "type":
                    "developer_identity",
                "source_type":
                    "x",
                "origin":
                    "x",
                "description":
                    "The linked X identity was reachable through the configured X API."
            }
        )

    # --------------------------------------------------------
    # Documentation
    # --------------------------------------------------------

    doc_links = []

    for url in discovered_links:

        category = classify_url(
            url
        )

        if category == "docs":
            doc_links.append(
                url
            )

    if doc_links:

        evidence.append(
            {
                "type":
                    "documentation",
                "source_type":
                    "docs",
                "origin":
                    source_origin(
                        doc_links[0],
                        official_website
                    ),
                "description":
                    "Documentation or whitepaper links were found."
            }
        )

    # --------------------------------------------------------
    # Cross-linking
    # --------------------------------------------------------

    cross_links = 0

    all_website_links = []

    for site in websites:

        all_website_links.extend(
            site.get(
                "links",
                []
            )
        )

    website_has_github = any(
        "github.com" in link.lower()
        for link in all_website_links
    )

    website_has_x = any(
        (
            "x.com" in link.lower()
            or "twitter.com"
            in link.lower()
        )
        for link in all_website_links
    )

    if website_has_github:
        cross_links += 1

    if website_has_x:
        cross_links += 1

    if cross_links >= 1:

        evidence.append(
            {
                "type":
                    "cross_linked_identity",
                "source_type":
                    "cross-verification",
                "origin":
                    "project-links",
                "description":
                    "The official web presence links to external project identities."
            }
        )

    # --------------------------------------------------------
    # Token identity in GitHub
    # --------------------------------------------------------

    token_address = snapshot.get(
        "address",
        ""
    ).lower()

    token_name = snapshot.get(
        "name",
        ""
    ).lower()

    token_symbol = snapshot.get(
        "symbol",
        ""
    ).lower()

    repo_identity_match = False

    for github in usable_githubs:

        searchable = (
            github.get(
                "description",
                ""
            )
            + " "
            + github.get(
                "readme",
                ""
            )
        ).lower()

        if (
            token_address
            and token_address in searchable
        ):
            repo_identity_match = True
            break

        if (
            token_name
            and len(token_name) >= 4
            and token_name in searchable
        ):
            repo_identity_match = True
            break

        if (
            token_symbol
            and len(token_symbol) >= 4
            and token_symbol in searchable
        ):
            repo_identity_match = True
            break

    if repo_identity_match:

        evidence.append(
            {
                "type":
                    "token_project_link",
                "source_type":
                    "github",
                "origin":
                    "github",
                "description":
                    "The token/project identity appears in reachable repository material."
            }
        )

    return {
        "evidence": evidence,
        "official_website":
            official_website,
    }


# ============================================================
# LORE SCORING
# ============================================================

def calculate_lore_score(
    evidence_data: Dict[str, Any],
    websites: List[Dict[str, Any]],
    githubs: List[Dict[str, Any]],
    xs: List[Dict[str, Any]],
    snapshot: Dict[str, Any]
) -> Dict[str, Any]:

    evidence = evidence_data.get(
        "evidence",
        []
    )

    evidence_types = set()
    source_types = set()
    origins = set()

    for item in evidence:

        evidence_types.add(
            item.get(
                "type"
            )
        )

        source_types.add(
            item.get(
                "source_type"
            )
        )

        origins.add(
            item.get(
                "origin"
            )
        )

    score = 0

    # --------------------------------------------------------
    # 1. Project identity
    # --------------------------------------------------------

    if any(
        item.get(
            "type"
        ) == "project_identity"
        for item in evidence
    ):
        score += 5

    # --------------------------------------------------------
    # 2. Technical narrative
    # --------------------------------------------------------

    if any(
        item.get(
            "type"
        ) == "technical_narrative"
        for item in evidence
    ):
        score += 5

    # --------------------------------------------------------
    # 3. Implementation
    # --------------------------------------------------------

    if any(
        item.get(
            "type"
        ) == "implementation"
        for item in evidence
    ):
        score += 5

    # --------------------------------------------------------
    # 4. Developer identity
    # --------------------------------------------------------

    if any(
        item.get(
            "type"
        ) == "developer_identity"
        for item in evidence
    ):
        score += 4

    # --------------------------------------------------------
    # 5. Cross verification
    # --------------------------------------------------------

    if (
        len(
            origins
        ) >= 2
    ):
        score += 3

    if any(
        item.get(
            "type"
        ) == "cross_linked_identity"
        for item in evidence
    ):
        score += 3

    score = min(
        score,
        25
    )

    # --------------------------------------------------------
    # Confidence
    # --------------------------------------------------------

    if (
        score >= 21
        and len(source_types) >= 2
        and len(origins) >= 2
    ):
        confidence = "HIGH"

    elif (
        score >= 15
        and len(source_types) >= 2
    ):
        confidence = "MEDIUM"

    elif score >= 8:
        confidence = "LOW"

    else:
        confidence = "UNKNOWN"

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    x_activity = sum(
        item.get(
            "posts_found",
            0
        )
        for item in xs
    )

    github_activity = sum(
        1
        for item in githubs
        if item.get(
            "reachable"
        )
    )

    website_activity = sum(
        1
        for item in websites
        if item.get(
            "reachable"
        )
    )

    if (
        x_activity >= 3
        and github_activity >= 1
        and website_activity >= 1
    ):
        momentum = "HIGH"

    elif (
        x_activity >= 1
        or (
            github_activity >= 1
            and website_activity >= 1
        )
    ):
        momentum = "MEDIUM"

    elif website_activity:
        momentum = "LOW"

    else:
        momentum = "UNKNOWN"

    return {
        "score": score,
        "confidence": confidence,
        "momentum": momentum,
        "evidence_count": len(
            evidence_types
        ),
        "source_type_count": len(
            source_types
        ),
        "origin_count": len(
            origins
        ),
        "evidence": evidence,
    }


# ============================================================
# LORE RESEARCH
# ============================================================

def research_lore(
    snapshot: Dict[str, Any]
) -> Dict[str, Any]:

    address = snapshot[
        "address"
    ]

    if address in LORE_CACHE:

        cached = LORE_CACHE[
            address
        ]

        # Cache for 30 minutes
        if (
            now_ts()
            - cached.get(
                "timestamp",
                0
            )
            < 1800
        ):
            return cached

    urls = collect_project_urls(
        snapshot
    )

    website_results = []

    for url in urls[
        "website"
    ][:3]:

        result = research_website(
            url
        )

        website_results.append(
            result
        )

    # --------------------------------------------------------
    # Discover additional URLs from official website
    # --------------------------------------------------------

    discovered_links = []

    for site in website_results:

        for link in site.get(
            "links",
            []
        ):

            category = classify_url(
                link
            )

            if category in (
                "github",
                "docs",
                "x",
                "blog"
            ):
                discovered_links.append(
                    link
                )

    # --------------------------------------------------------
    # Merge DEX + website discoveries
    # --------------------------------------------------------

    github_urls = []

    for url in urls[
        "github"
    ]:

        github_urls.append(
            url
        )

    for url in discovered_links:

        if classify_url(
            url
        ) == "github":

            github_urls.append(
                url
            )

    github_urls = list(
        dict.fromkeys(
            github_urls
        )
    )

    docs_urls = []

    for url in discovered_links:

        if classify_url(
            url
        ) == "docs":

            docs_urls.append(
                url
            )

    x_urls = list(
        dict.fromkeys(
            urls["x"]
            + [
                link
                for link in discovered_links
                if classify_url(
                    link
                ) == "x"
            ]
        )
    )

    # --------------------------------------------------------
    # Research GitHub
    # --------------------------------------------------------

    github_results = []

    for url in github_urls[:3]:

        github_results.append(
            research_github(
                url
            )
        )

    # --------------------------------------------------------
    # Research X
    # --------------------------------------------------------

    x_results = []

    for url in x_urls[:2]:

        x_results.append(
            research_x(
                url
            )
        )

    # --------------------------------------------------------
    # Evidence
    # --------------------------------------------------------

    evidence_data = build_evidence(
        snapshot,
        website_results,
        github_results,
        x_results,
        discovered_links + docs_urls
    )

    lore_score = calculate_lore_score(
        evidence_data,
        website_results,
        github_results,
        x_results,
        snapshot
    )

    result = {
        "timestamp": now_ts(),
        "address": address,
        "websites": website_results,
        "githubs": github_results,
        "xs": x_results,
        "docs_urls": docs_urls,
        "discovered_links":
            discovered_links,
        "official_website":
            evidence_data.get(
                "official_website",
                ""
            ),
        "evidence":
            lore_score.get(
                "evidence",
                []
            ),
        "score":
            lore_score[
                "score"
            ],
        "confidence":
            lore_score[
                "confidence"
            ],
        "momentum":
            lore_score[
                "momentum"
            ],
        "evidence_count":
            lore_score[
                "evidence_count"
            ],
        "source_type_count":
            lore_score[
                "source_type_count"
            ],
        "origin_count":
            lore_score[
                "origin_count"
            ],
    }

    LORE_CACHE[
        address
    ] = result

    return result


# ============================================================
# LORE DECISION
# ============================================================

def lore_pass(
    lore: Dict[str, Any]
) -> bool:

    return (
        lore.get(
            "score",
            0
        )
        >= NORMAL_MIN_LORE_SCORE
        and lore.get(
            "evidence_count",
            0
        )
        >= NORMAL_MIN_EVIDENCE
        and lore.get(
            "source_type_count",
            0
        )
        >= NORMAL_MIN_SOURCE_TYPES
    )


def early_lore_pass(
    lore: Dict[str, Any]
) -> bool:

    return (
        lore.get(
            "score",
            0
        )
        >= EARLY_MIN_LORE_SCORE
        and lore.get(
            "confidence",
            "UNKNOWN"
        ) == "HIGH"
        and lore.get(
            "momentum",
            "UNKNOWN"
        ) == "HIGH"
        and lore.get(
            "evidence_count",
            0
        )
        >= EARLY_MIN_EVIDENCE
        and lore.get(
            "source_type_count",
            0
        )
        >= EARLY_MIN_SOURCE_TYPES
    )


# ============================================================
# CLASSIFICATION
# ============================================================

def classify_token(
    snapshot: Dict[str, Any],
    observations: int,
    score: int,
    decay: int,
    weakening: int,
    lore: Optional[Dict[str, Any]]
) -> str:

    if decay >= 3:
        return "WATCH"

    if weakening >= 3:
        return "WATCH"

    # --------------------------------------------------------
    # Early narrative runner
    # --------------------------------------------------------

    if (
        early_market_candidate(
            snapshot
        )
        and lore
        and early_lore_pass(
            lore
        )
    ):
        return "EARLY NARRATIVE RUNNER"

    # --------------------------------------------------------
    # Ideal normal runner
    # --------------------------------------------------------

    if (
        score >= IDEAL_SCORE
        and snapshot[
            "price_change_5m"
        ] > 0
        and snapshot[
            "bs_ratio"
        ] >= IDEAL_BS
        and observations >= 3
        and lore
        and lore_pass(
            lore
        )
    ):
        return "IDEAL RUNNER"

    # --------------------------------------------------------
    # Normal runner
    # --------------------------------------------------------

    if (
        score >= RUNNER_SCORE
        and snapshot[
            "price_change_5m"
        ] > 0
        and snapshot[
            "bs_ratio"
        ] >= RUNNER_BS
        and observations >= 2
        and lore
        and lore_pass(
            lore
        )
    ):
        return "RUNNER"

    return "WATCH"


# ============================================================
# ALERT MESSAGE
# ============================================================

def build_alert(
    snapshot: Dict[str, Any],
    score: int,
    decay: int,
    weakening: int,
    lore: Dict[str, Any],
    classification: str
) -> str:

    name = snapshot[
        "name"
    ]

    symbol = snapshot[
        "symbol"
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

    tx = snapshot[
        "tx_5m"
    ]

    volume_mc = snapshot[
        "volume_mc"
    ]

    lore_score = lore.get(
        "score",
        0
    )

    confidence = lore.get(
        "confidence",
        "UNKNOWN"
    )

    momentum = lore.get(
        "momentum",
        "UNKNOWN"
    )

    evidence_count = lore.get(
        "evidence_count",
        0
    )

    source_count = lore.get(
        "source_type_count",
        0
    )

    pair_url = snapshot.get(
        "pair_url",
        ""
    )

    if classification == (
        "EARLY NARRATIVE RUNNER"
    ):
        header = (
            "🔥🔥 EARLY NARRATIVE RUNNER"
        )

    elif classification == (
        "IDEAL RUNNER"
    ):
        header = (
            "🚀 IDEAL RUNNER"
        )

    else:
        header = (
            "🔥 RUNNER"
        )

    lines = [
        header,
        "",
        f"{name} (${symbol})",
        "",
        f"MC: ${mc:,.0f}",
        f"Liquidity: ${liquidity:,.0f}",
        f"5m Price: {price:+.2f}%",
        f"5m B/S: {bs:.2f}",
        f"5m TX: {tx:,}",
        f"Vol/MC: {volume_mc:.1f}%",
        "",
        f"MARKET SCORE: {score}/100",
        f"Decay: {decay}",
        f"Weakening: {weakening}",
        "",
        "🧠 LORE / EVIDENCE",
        f"Lore Score: {lore_score}/25",
        f"Confidence: {confidence}",
        f"Momentum: {momentum}",
        f"Evidence: {evidence_count}",
        f"Source Types: {source_count}",
    ]

    official = lore.get(
        "official_website",
        ""
    )

    if official:
        lines.extend(
            [
                "",
                f"Website: {official}",
            ]
        )

    if pair_url:
        lines.extend(
            [
                "",
                f"DEX: {pair_url}",
            ]
        )

    lines.extend(
        [
            "",
            f"CA: {snapshot['address']}",
            "",
            "Evidence:",
        ]
    )

    evidence = lore.get(
        "evidence",
        []
    )

    for item in evidence[:6]:

        lines.append(
            "✓ "
            + clean_text(
                item.get(
                    "description",
                    ""
                )
            )
        )

    return "\n".join(
        lines
    )


# ============================================================
# TRACKING / PROCESSING
# ============================================================

def process_token(
    token_address: str,
    pair: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:

    if pair is None:

        pair = select_best_pair(
            token_address
        )

    if not pair:
        return None

    snapshot = make_snapshot(
        token_address,
        pair
    )

    mc = snapshot[
        "market_cap"
    ]

    liquidity = snapshot[
        "liquidity"
    ]

    # --------------------------------------------------------
    # Hard market boundaries
    # --------------------------------------------------------

    if mc < MIN_MC:
        return None

    if mc > MAX_MC:

        # Continue tracking already discovered
        # tokens, but never issue a new alert.
        if token_address not in DISCOVERED_TOKENS:
            return None

    if liquidity < MIN_LIQUIDITY:
        return None

    previous = get_previous_snapshot(
        token_address
    )

    decay = calculate_decay(
        snapshot,
        previous
    )

    weakening = calculate_weakening(
        snapshot,
        previous
    )

    add_history(
        token_address,
        snapshot
    )

    observations = len(
        TOKEN_HISTORY.get(
            token_address,
            []
        )
    )

    score = market_score(
        snapshot,
        observations,
        decay,
        weakening
    )

    # --------------------------------------------------------
    # Lore is only researched when market behavior is
    # interesting enough to justify it.
    # --------------------------------------------------------

    should_research_lore = (
        early_market_candidate(
            snapshot
        )
        or (
            score >= 65
            and snapshot[
                "price_change_5m"
            ] > 0
            and snapshot[
                "bs_ratio"
            ] >= 1.10
        )
        or token_address in LORE_CACHE
    )

    lore = None

    if should_research_lore:

        print(
            "[LORE] Researching "
            f"{snapshot['name']} "
            f"({token_address})..."
        )

        lore = research_lore(
            snapshot
        )

        print(
            "[LORE] "
            f"{snapshot['name']} "
            f"score={lore['score']}/25 "
            f"confidence={lore['confidence']} "
            f"momentum={lore['momentum']} "
            f"evidence={lore['evidence_count']} "
            f"sources={lore['source_type_count']}"
        )

    classification = classify_token(
        snapshot,
        observations,
        score,
        decay,
        weakening,
        lore
    )

    DISCOVERED_TOKENS[
        token_address
    ] = {
        "address":
            token_address,
        "name":
            snapshot["name"],
        "symbol":
            snapshot["symbol"],
        "first_seen":
            DISCOVERED_TOKENS.get(
                token_address,
                {}
            ).get(
                "first_seen",
                now_ts()
            ),
        "last_seen":
            now_ts(),
        "last_market_cap":
            mc,
        "last_classification":
            classification,
        "last_score":
            score,
    }

    # --------------------------------------------------------
    # Alerts
    # --------------------------------------------------------

    previous_status = LAST_ALERT_STATUS.get(
        token_address
    )

    should_alert = (
        classification
        in (
            "EARLY NARRATIVE RUNNER",
            "IDEAL RUNNER",
            "RUNNER",
        )
        and previous_status
        != classification
        and mc <= MAX_MC
    )

    if should_alert:

        if lore is None:

            lore = research_lore(
                snapshot
            )

        message = build_alert(
            snapshot,
            score,
            decay,
            weakening,
            lore,
            classification
        )

        broadcast(
            message
        )

        LAST_ALERT_STATUS[
            token_address
        ] = classification

        print(
            "[ALERT] "
            f"{snapshot['name']} "
            f"-> {classification}"
        )

    return {
        "snapshot": snapshot,
        "score": score,
        "decay": decay,
        "weakening": weakening,
        "observations": observations,
        "classification": classification,
        "lore": lore,
    }


# ============================================================
# DISCOVERY RUN
# ============================================================

def run_discovery() -> None:

    global LAST_DISCOVERY

    print(
        "[DISCOVERY] Scanning..."
    )

    addresses = get_discovery_candidates()

    print(
        "[DISCOVERY] Found "
        f"{len(addresses)} Solana candidates"
    )

    qualified = 0
    early_candidates = 0
    lore_candidates = 0

    for address in addresses:

        try:

            pair = select_best_pair(
                address
            )

            if not pair:
                continue

            snapshot = make_snapshot(
                address,
                pair
            )

            if (
                snapshot["market_cap"]
                < MIN_MC
                or snapshot["market_cap"]
                > MAX_MC
            ):
                continue

            if (
                snapshot["liquidity"]
                < MIN_LIQUIDITY
            ):
                continue

            qualified += 1

            if early_market_candidate(
                snapshot
            ):
                early_candidates += 1

            result = process_token(
                address,
                pair=pair
            )

            if result and result.get(
                "lore"
            ):
                lore_candidates += 1

        except Exception as exc:

            print(
                "[DISCOVERY ERROR] "
                f"{address}: {exc}"
            )

    LAST_DISCOVERY = now_ts()

    print(
        "[DISCOVERY] Qualified: "
        f"{qualified}"
    )

    print(
        "[DISCOVERY] Early market "
        f"candidates: {early_candidates}"
    )

    print(
        "[DISCOVERY] Lore researched: "
        f"{lore_candidates}"
    )


# ============================================================
# TRACKING
# ============================================================

def cleanup_tracking() -> None:

    current = now_ts()

    remove = []

    for address, data in list(
        DISCOVERED_TOKENS.items()
    ):

        last_seen = safe_int(
            data.get(
                "last_seen"
            )
        )

        alerted = (
            address
            in LAST_ALERT_STATUS
        )

        ttl = (
            ALERT_TRACKING_TTL_SECONDS
            if alerted
            else TRACKING_TTL_SECONDS
        )

        if (
            current - last_seen
            > ttl
        ):
            remove.append(
                address
            )

    for address in remove:

        DISCOVERED_TOKENS.pop(
            address,
            None
        )

        TOKEN_HISTORY.pop(
            address,
            None
        )

        LAST_ALERT_STATUS.pop(
            address,
            None
        )

        LORE_CACHE.pop(
            address,
            None
        )


def run_tracking() -> None:

    addresses = list(
        DISCOVERED_TOKENS.keys()
    )

    if not addresses:
        return

    print(
        "[TRACKING] Tracking "
        f"{len(addresses)} tokens"
    )

    for address in addresses:

        try:

            result = process_token(
                address
            )

            if result:

                print(
                    "[TRACKING] "
                    f"{result['snapshot']['symbol']} "
                    f"MC=${result['snapshot']['market_cap']:,.0f} "
                    f"score={result['score']} "
                    f"status={result['classification']}"
                )

        except Exception as exc:

            print(
                "[TRACKING ERROR] "
                f"{address}: {exc}"
            )

    cleanup_tracking()


# ============================================================
# STARTUP TESTS
# ============================================================

def test_telegram() -> bool:

    print(
        "[STARTUP] Testing Telegram..."
    )

    result = telegram_api(
        "getMe"
    )

    if not isinstance(
        result,
        dict
    ):
        return False

    if not result.get(
        "ok"
    ):
        return False

    bot = result.get(
        "result",
        {}
    )

    print(
        "[TELEGRAM] Connected @"
        f"{bot.get('username', 'unknown')}"
    )

    return True


def test_dexscreener() -> bool:

    print(
        "[STARTUP] Testing DexScreener..."
    )

    url = (
        f"{DEX_BASE}"
        f"/token-profiles/latest/v1"
    )

    data = http_get_json(
        url
    )

    if not isinstance(
        data,
        list
    ):
        return False

    print(
        "[DEXSCREENER] Connected"
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    print(
        "=" * 68
    )

    print(
        f"RUNNER BOT {BOT_VERSION}"
    )

    print(
        "=" * 68
    )

    print(
        f"CHAIN: {CHAIN}"
    )

    print(
        f"MC RANGE: "
        f"${MIN_MC:,}-${MAX_MC:,}"
    )

    print(
        f"EARLY RANGE: "
        f"${EARLY_MIN_MC:,}-${EARLY_MAX_MC:,}"
    )

    print(
        f"MIN LIQUIDITY: "
        f"${MIN_LIQUIDITY:,}"
    )

    print(
        f"EARLY MIN LIQUIDITY: "
        f"${EARLY_MIN_LIQUIDITY:,}"
    )

    print(
        f"SCAN INTERVAL: "
        f"{SCAN_INTERVAL_SECONDS}s"
    )

    print(
        f"DISCOVERY INTERVAL: "
        f"{DISCOVERY_INTERVAL_SECONDS}s"
    )

    print(
        f"RUNNER SCORE: "
        f"{RUNNER_SCORE}"
    )

    print(
        f"IDEAL SCORE: "
        f"{IDEAL_SCORE}"
    )

    print(
        f"EARLY LORE SCORE: "
        f"{EARLY_MIN_LORE_SCORE}/25"
    )

    print(
        f"X API: "
        f"{'CONFIGURED' if X_BEARER_TOKEN else 'NOT CONFIGURED'}"
    )

    print(
        f"LORE AI: "
        f"{'CONFIGURED' if LORE_AI_API_KEY else 'NOT CONFIGURED'}"
    )

    print(
        "TELEGRAM TOKEN: "
        f"{'CONFIGURED' if TELEGRAM_BOT_TOKEN else 'MISSING'}"
    )

    print(
        "=" * 68
    )

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[FATAL] TELEGRAM_BOT_TOKEN missing."
        )

        return

    if not test_telegram():

        print(
            "[FATAL] Telegram test failed."
        )

        return

    if not test_dexscreener():

        print(
            "[FATAL] DexScreener test failed."
        )

        return

    initial = get_discovery_candidates()

    print(
        "[STARTUP] Initial candidates: "
        f"{len(initial)}"
    )

    print(
        "[STARTUP] Scanner running."
    )

    telegram_offset = None

    last_tracking = 0.0

    # --------------------------------------------------------
    # Initial discovery
    # --------------------------------------------------------

    try:

        run_discovery()

    except Exception as exc:

        print(
            "[INITIAL DISCOVERY ERROR] "
            f"{exc}"
        )

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    while True:

        loop_start = time.time()

        try:

            updates, telegram_offset = (
                telegram_poll(
                    telegram_offset
                )
            )

            if updates:

                handle_telegram_updates(
                    updates
                )

            # ------------------------------------------------
            # Discovery
            # ------------------------------------------------

            if (
                now_ts()
                - LAST_DISCOVERY
                >= DISCOVERY_INTERVAL_SECONDS
            ):

                run_discovery()

            # ------------------------------------------------
            # Tracking
            # ------------------------------------------------

            if (
                time.time()
                - last_tracking
                >= SCAN_INTERVAL_SECONDS
            ):

                run_tracking()

                last_tracking = (
                    time.time()
                )

        except Exception as exc:

            print(
                "[MAIN ERROR] "
                f"{exc}"
            )

        elapsed = (
            time.time()
            - loop_start
        )

        sleep_for = max(
            1,
            SCAN_INTERVAL_SECONDS
            - int(elapsed)
        )

        time.sleep(
            sleep_for
        )


if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[SHUTDOWN] Bot stopped."
        )

    except Exception as exc:

        print(
            "[FATAL ERROR] "
            f"{exc}"
        )
