Yes. Replace everything inside bot.py with the V2 below. Do not change your GitHub Actions workflow.

This version keeps discovery from V1 and adds persistent in-memory tracking, scoring, decay/weakening detection, and WATCH / RUNNER / IDEAL RUNNER classification.

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
# ============================================================
# RUNNER BOT V2.0
# RUNNER DETECTION ENGINE
# ============================================================
BOT_VERSION = "V2.0-RUNNER-DETECTION"
DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"
CHAIN = "solana"
MIN_MC = 20_000
MAX_MC = 150_000
MIN_LIQUIDITY = 5_000
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "15"))
DISCOVERY_INTERVAL_SECONDS = int(
    os.getenv("DISCOVERY_INTERVAL_SECONDS", "300")
)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
HEATING_ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "true").lower() == "true"
)
# ============================================================
# RUNNER THRESHOLDS
# ============================================================
RUNNER_SCORE = 72
IDEAL_SCORE = 85
RUNNER_BS = 1.20
IDEAL_BS = 1.50
MAX_HISTORY = 20
TRACKING_TTL_SECONDS = 8 * 60 * 60
# ============================================================
# STATE
# ============================================================
SUBSCRIBERS = set()
TOKEN_HISTORY: Dict[str, List[Dict[str, Any]]] = {}
LAST_DISCOVERY = 0
DISCOVERED_TOKENS: Dict[str, Dict[str, Any]] = {}
# ============================================================
# HTTP
# ============================================================
def http_get_json(url: str) -> Any:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RunnerBot/2.0"
            }
        )
        with urllib.request.urlopen(
            req,
            timeout=15
        ) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except Exception as exc:
        print(f"[HTTP ERROR] {exc}")
        return None
# ============================================================
# TELEGRAM
# ============================================================
def telegram_request(
    method: str,
    params: Optional[Dict[str, Any]] = None
) -> Any:
    if not TELEGRAM_BOT_TOKEN:
        return None
    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )
    try:
        data = urllib.parse.urlencode(
            params or {}
        ).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "RunnerBot/2.0"
            }
        )
        with urllib.request.urlopen(
            req,
            timeout=20
        ) as response:
            return json.loads(
                response.read().decode("utf-8")
            )
    except Exception as exc:
        print(
            f"[TELEGRAM ERROR] {exc}"
        )
        return None
def send_telegram_message(
    chat_id: int,
    text: str
) -> None:
    telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )
# ============================================================
# TELEGRAM POLLING
# ============================================================
def handle_telegram_updates(
    offset: Optional[int]
) -> Optional[int]:
    result = telegram_request(
        "getUpdates",
        {
            "timeout": 1,
            "offset": offset
        }
    )
    if not result:
        return offset
    if not result.get("ok"):
        return offset
    updates = result.get("result", [])
    for update in updates:
        update_id = update.get("update_id")
        if update_id is not None:
            offset = update_id + 1
        message = update.get("message")
        if not message:
            continue
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = (
            message.get("text") or ""
        ).strip()
        if not chat_id:
            continue
        if text == "/start":
            SUBSCRIBERS.add(chat_id)
            print(
                f"[TELEGRAM] /start from {chat_id}"
            )
            send_telegram_message(
                chat_id,
                "Runner Bot V2 is online.\n\n"
                "Runner detection is active."
            )
        elif text == "/status":
            send_telegram_message(
                chat_id,
                (
                    "Runner Bot V2\n"
                    f"Tracked tokens: "
                    f"{len(TOKEN_HISTORY)}\n"
                    f"Discovered tokens: "
                    f"{len(DISCOVERED_TOKENS)}"
                )
            )
    return offset
# ============================================================
# DEXSCREENER DISCOVERY
# ============================================================
DISCOVERY_ENDPOINTS = [
    "/token-profiles/latest/v1",
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
    "/community-takeovers/latest/v1",
]
def discover_candidates() -> List[Dict[str, Any]]:
    candidates = {}
    for endpoint in DISCOVERY_ENDPOINTS:
        url = DEX_BASE + endpoint
        data = http_get_json(url)
        if not isinstance(data, list):
            continue
        for item in data:
            chain_id = item.get("chainId")
            if chain_id != CHAIN:
                continue
            address = (
                item.get("tokenAddress")
                or item.get("address")
            )
            if not address:
                continue
            candidates[address] = item
    return list(candidates.values())
# ============================================================
# TOKEN PAIRS
# ============================================================
def get_token_pairs(
    token_address: str
) -> List[Dict[str, Any]]:
    url = (
        f"{DEX_BASE}/token-pairs/"
        f"{CHAIN}/{token_address}"
    )
    data = http_get_json(url)
    if not isinstance(data, dict):
        return []
    pairs = data.get("pairs")
    if not isinstance(pairs, list):
        return []
    return pairs
def select_best_pair(
    token_address: str,
    discovery_item: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    pairs = get_token_pairs(token_address)
    solana_pairs = [
        p for p in pairs
        if p.get("chainId") == CHAIN
    ]
    if not solana_pairs:
        return None
    def liquidity_value(pair):
        liquidity = pair.get("liquidity") or {}
        value = liquidity.get("usd")
        try:
            return float(value or 0)
        except Exception:
            return 0
    solana_pairs.sort(
        key=liquidity_value,
        reverse=True
    )
    return solana_pairs[0]
# ============================================================
# SAFE NUMBER
# ============================================================
def safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0
def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0
# ============================================================
# SNAPSHOT
# ============================================================
def make_snapshot(
    pair: Dict[str, Any],
    token_address: str
) -> Optional[Dict[str, Any]]:
    base_token = pair.get("baseToken") or {}
    symbol = (
        base_token.get("symbol")
        or "UNKNOWN"
    )
    name = (
        base_token.get("name")
        or symbol
    )
    market_cap = safe_float(
        pair.get("marketCap")
    )
    if market_cap <= 0:
        market_cap = safe_float(
            pair.get("fdv")
        )
    liquidity = (
        pair.get("liquidity")
        or {}
    )
    liquidity_usd = safe_float(
        liquidity.get("usd")
    )
    volume = (
        pair.get("volume")
        or {}
    )
    volume_5m = safe_float(
        volume.get("m5")
    )
    txns = (
        pair.get("txns")
        or {}
    )
    txns_5m = (
        txns.get("m5")
        or {}
    )
    buys = safe_int(
        txns_5m.get("buys")
    )
    sells = safe_int(
        txns_5m.get("sells")
    )
    total_tx = buys + sells
    if sells > 0:
        bs_ratio = buys / sells
    elif buys > 0:
        bs_ratio = 999.0
    else:
        bs_ratio = 0.0
    price_change = (
        pair.get("priceChange")
        or {}
    )
    price_5m = safe_float(
        price_change.get("m5")
    )
    if market_cap < MIN_MC:
        return None
    if market_cap > MAX_MC:
        return None
    if liquidity_usd < MIN_LIQUIDITY:
        return None
    volume_mc = 0.0
    if market_cap > 0:
        volume_mc = (
            volume_5m / market_cap
        ) * 100
    liquidity_mc = 0.0
    if market_cap > 0:
        liquidity_mc = (
            liquidity_usd / market_cap
        ) * 100
    return {
        "timestamp": time.time(),
        "address": token_address,
        "symbol": symbol,
        "name": name,
        "market_cap": market_cap,
        "liquidity": liquidity_usd,
        "volume_5m": volume_5m,
        "buys": buys,
        "sells": sells,
        "bs_ratio": bs_ratio,
        "tx_5m": total_tx,
        "price_change_5m": price_5m,
        "volume_mc": volume_mc,
        "liquidity_mc": liquidity_mc,
        "pair_address": pair.get(
            "pairAddress"
        ),
        "dex": pair.get(
            "dexId"
        ),
        "url": pair.get(
            "url"
        )
    }
# ============================================================
# HISTORY
# ============================================================
def add_history(
    snapshot: Dict[str, Any]
) -> None:
    address = snapshot["address"]
    history = TOKEN_HISTORY.setdefault(
        address,
        []
    )
    history.append(snapshot)
    if len(history) > MAX_HISTORY:
        del history[
            :-MAX_HISTORY
        ]
def get_history(
    address: str
) -> List[Dict[str, Any]]:
    return TOKEN_HISTORY.get(
        address,
        []
    )
# ============================================================
# DECAY DETECTION
# ============================================================
def calculate_decay(
    snapshot: Dict[str, Any],
    history: List[Dict[str, Any]]
) -> int:
    decay = 0
    price = snapshot["price_change_5m"]
    bs = snapshot["bs_ratio"]
    tx = snapshot["tx_5m"]
    volume_mc = snapshot["volume_mc"]
    # Current market deterioration
    if price <= 0:
        decay += 1
    if bs < 1.0:
        decay += 1
    if tx < 20:
        decay += 1
    if (
        volume_mc >= 20
        and price < 0
    ):
        decay += 1
    # Historical deterioration
    if len(history) >= 2:
        previous = history[-2]
        if (
            snapshot["liquidity"]
            < previous["liquidity"] * 0.90
        ):
            decay += 1
        if (
            snapshot["bs_ratio"]
            < previous["bs_ratio"] * 0.70
        ):
            decay += 1
    return decay
# ============================================================
# WEAKENING DETECTION
# ============================================================
def calculate_weakening(
    snapshot: Dict[str, Any],
    history: List[Dict[str, Any]]
) -> int:
    if len(history) < 2:
        return 0
    previous = history[-2]
    worsening = 0
    # B/S worsening
    if (
        snapshot["bs_ratio"]
        < previous["bs_ratio"]
    ):
        worsening += 1
    # Price worsening
    if (
        snapshot["price_change_5m"]
        < previous["price_change_5m"]
    ):
        worsening += 1
    # Volume/MC worsening
    if (
        snapshot["volume_mc"]
        < previous["volume_mc"]
    ):
        worsening += 1
    # Transactions worsening
    if (
        snapshot["tx_5m"]
        < previous["tx_5m"]
    ):
        worsening += 1
    # Liquidity worsening
    if (
        snapshot["liquidity"]
        < previous["liquidity"]
    ):
        worsening += 1
    return worsening
# ============================================================
# RUNNER SCORE
# ============================================================
def calculate_score(
    snapshot: Dict[str, Any],
    history: List[Dict[str, Any]],
    decay: int,
    weakening: int
) -> int:
    score = 0
    mc = snapshot["market_cap"]
    price = snapshot["price_change_5m"]
    bs = snapshot["bs_ratio"]
    tx = snapshot["tx_5m"]
    volume_mc = snapshot["volume_mc"]
    liquidity_mc = snapshot["liquidity_mc"]
    # --------------------------------------------------------
    # MARKET CAP ZONE
    # --------------------------------------------------------
    if 20_000 <= mc <= 50_000:
        score += 20
    elif 50_000 < mc <= 80_000:
        score += 17
    elif 80_000 < mc <= 120_000:
        score += 12
    elif 120_000 < mc <= 150_000:
        score += 7
    # --------------------------------------------------------
    # PRICE MOMENTUM
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
    # BUY / SELL PRESSURE
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
    # LIQUIDITY / MC
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
    # HISTORY BONUS
    # --------------------------------------------------------
    observations = len(history)
    if observations >= 3:
        score += 5
    elif observations >= 2:
        score += 2
    # --------------------------------------------------------
    # PENALTIES
    # --------------------------------------------------------
    score -= decay * 8
    score -= weakening * 4
    # --------------------------------------------------------
    # LIMIT
    # --------------------------------------------------------
    score = max(
        0,
        min(100, score)
    )
    return score
# ============================================================
# CLASSIFICATION
# ============================================================
def classify_token(
    snapshot: Dict[str, Any],
    history: List[Dict[str, Any]],
    score: int,
    decay: int,
    weakening: int
) -> str:
    price = snapshot["price_change_5m"]
    bs = snapshot["bs_ratio"]
    # Serious decay automatically prevents runner status
    if decay >= 3:
        return "WATCH"
    # Strong weakening prevents promotion
    if weakening >= 3:
        return "WATCH"
    # Ideal runner
    if (
        score >= IDEAL_SCORE
        and price > 0
        and bs >= IDEAL_BS
        and len(history) >= 3
    ):
        return "IDEAL RUNNER"
    # Normal runner
    if (
        score >= RUNNER_SCORE
        and price > 0
        and bs >= RUNNER_BS
        and len(history) >= 2
    ):
        return "RUNNER"
    return "WATCH"
# ============================================================
# DISPLAY
# ============================================================
def print_candidate(
    snapshot: Dict[str, Any],
    score: int,
    classification: str,
    decay: int,
    weakening: int,
    observations: int
) -> None:
    print()
    print(
        "------------------------------------------------------------"
    )
    print(
        f"${snapshot['symbol']}"
    )
    print(
        f"MC: ${snapshot['market_cap']:,.0f}"
    )
    print(
        f"Price: "
        f"{snapshot['price_change_5m']:+.2f}%"
    )
    print(
        f"B/S: "
        f"{snapshot['bs_ratio']:.2f}"
    )
    print(
        f"TX: "
        f"{snapshot['tx_5m']}"
    )
    print(
        f"5m Volume: "
        f"${snapshot['volume_5m']:,.0f}"
    )
    print(
        f"Vol/MC: "
        f"{snapshot['volume_mc']:.1f}%"
    )
    print(
        f"Liq: "
        f"${snapshot['liquidity']:,.0f}"
    )
    print(
        f"Liq/MC: "
        f"{snapshot['liquidity_mc']:.1f}%"
    )
    print(
        f"Score: {score}/100"
    )
    print(
        f"Status: {classification}"
    )
    print(
        f"Decay: {decay}"
    )
    print(
        f"Weakening: {weakening}"
    )
    print(
        f"Observations: {observations}"
    )
    print(
        "------------------------------------------------------------"
    )
# ============================================================
# TELEGRAM ALERT
# ============================================================
def format_alert(
    snapshot: Dict[str, Any],
    score: int,
    classification: str,
    decay: int,
    weakening: int,
    observations: int
) -> str:
    return (
        f"🚀 {classification}\n\n"
        f"${snapshot['symbol']} — "
        f"{snapshot['name']}\n\n"
        f"MC: ${snapshot['market_cap']:,.0f}\n"
        f"Price 5m: "
        f"{snapshot['price_change_5m']:+.2f}%\n"
        f"B/S: {snapshot['bs_ratio']:.2f}\n"
        f"TX 5m: {snapshot['tx_5m']}\n"
        f"Vol/MC: {snapshot['volume_mc']:.1f}%\n"
        f"Liquidity: ${snapshot['liquidity']:,.0f}\n"
        f"Liq/MC: {snapshot['liquidity_mc']:.1f}%\n\n"
        f"Score: {score}/100\n"
        f"Decay: {decay}\n"
        f"Weakening: {weakening}\n"
        f"Observations: {observations}\n\n"
        f"DEX: {snapshot['dex']}\n"
        f"{snapshot['url'] or ''}"
    )
# ============================================================
# ALERT CONTROL
# ============================================================
LAST_ALERT_STATUS: Dict[str, str] = {}
def maybe_alert(
    snapshot: Dict[str, Any],
    score: int,
    classification: str,
    decay: int,
    weakening: int,
    observations: int
) -> None:
    if not HEATING_ALERTS_ENABLED:
        return
    if classification not in (
        "RUNNER",
        "IDEAL RUNNER"
    ):
        return
    address = snapshot["address"]
    previous = LAST_ALERT_STATUS.get(
        address
    )
    # Do not repeatedly spam the same status
    if previous == classification:
        return
    LAST_ALERT_STATUS[address] = classification
    message = format_alert(
        snapshot,
        score,
        classification,
        decay,
        weakening,
        observations
    )
    for chat_id in list(SUBSCRIBERS):
        send_telegram_message(
            chat_id,
            message
        )
    print(
        f"[ALERT] {classification} "
        f"${snapshot['symbol']}"
    )
# ============================================================
# PROCESS TOKEN
# ============================================================
def process_token(
    token_address: str,
    discovery_item: Optional[Dict[str, Any]] = None
) -> None:
    pair = select_best_pair(
        token_address,
        discovery_item
    )
    if not pair:
        return
    snapshot = make_snapshot(
        pair,
        token_address
    )
    if not snapshot:
        return
    history = get_history(
        token_address
    )
    decay = calculate_decay(
        snapshot,
        history
    )
    weakening = calculate_weakening(
        snapshot,
        history
    )
    # Score uses previous history,
    # then current snapshot is added.
    score = calculate_score(
        snapshot,
        history,
        decay,
        weakening
    )
    add_history(
        snapshot
    )
    updated_history = get_history(
        token_address
    )
    # Recalculate classification using
    # the new observation count.
    classification = classify_token(
        snapshot,
        updated_history,
        score,
        decay,
        weakening
    )
    print_candidate(
        snapshot,
        score,
        classification,
        decay,
        weakening,
        len(updated_history)
    )
    maybe_alert(
        snapshot,
        score,
        classification,
        decay,
        weakening,
        len(updated_history)
    )
# ============================================================
# DISCOVERY CYCLE
# ============================================================
def run_discovery() -> None:
    global DISCOVERED_TOKENS
    print()
    print(
        "[DISCOVERY] Scanning..."
    )
    candidates = discover_candidates()
    print(
        f"[DISCOVERY] Found "
        f"{len(candidates)} Solana candidates"
    )
    qualified = 0
    for item in candidates:
        address = (
            item.get("tokenAddress")
            or item.get("address")
        )
        if not address:
            continue
        pair = select_best_pair(
            address,
            item
        )
        if not pair:
            continue
        snapshot = make_snapshot(
            pair,
            address
        )
        if not snapshot:
            continue
        qualified += 1
        DISCOVERED_TOKENS[
            address
        ] = item
        # Process immediately so the
        # first observation is captured.
        process_token(
            address,
            item
        )
    print(
        f"[DISCOVERY] Qualified: "
        f"{qualified}"
    )
# ============================================================
# TRACKING CYCLE
# ============================================================
def run_tracking() -> None:
    addresses = list(
        TOKEN_HISTORY.keys()
    )
    if not addresses:
        return
    print(
        f"[TRACKING] Checking "
        f"{len(addresses)} tracked tokens..."
    )
    now = time.time()
    for address in addresses:
        history = TOKEN_HISTORY.get(
            address,
            []
        )
        if not history:
            continue
        last_time = history[-1].get(
            "timestamp",
            now
        )
        # Remove stale tokens
        if (
            now - last_time
            > TRACKING_TTL_SECONDS
        ):
            print(
                f"[TRACKING] Removing stale "
                f"{address}"
            )
            TOKEN_HISTORY.pop(
                address,
                None
            )
            LAST_ALERT_STATUS.pop(
                address,
                None
            )
            continue
        process_token(
            address,
            DISCOVERED_TOKENS.get(address)
        )
# ============================================================
# STARTUP TESTS
# ============================================================
def test_telegram() -> bool:
    if not TELEGRAM_BOT_TOKEN:
        print(
            "[STARTUP] TELEGRAM_BOT_TOKEN "
            "missing"
        )
        return False
    result = telegram_request(
        "getMe"
    )
    if not result or not result.get("ok"):
        print(
            "[TELEGRAM] Connection failed"
        )
        return False
    bot = (
        result.get("result")
        or {}
    )
    username = bot.get(
        "username",
        "unknown"
    )
    print(
        f"[TELEGRAM] Connected "
        f"@{username}"
    )
    return True
def test_dexscreener() -> bool:
    url = (
        DEX_BASE
        + "/token-profiles/latest/v1"
    )
    data = http_get_json(url)
    if not isinstance(data, list):
        print(
            "[DEXSCREENER] Connection failed"
        )
        return False
    print(
        "[DEXSCREENER] Connected"
    )
    print(
        f"[STARTUP] Initial candidates: "
        f"{len(data)}"
    )
    return True
# ============================================================
# MAIN
# ============================================================
def main() -> None:
    global LAST_DISCOVERY
    print("=" * 68)
    print(
        f"RUNNER BOT {BOT_VERSION}"
    )
    print("=" * 68)
    print(
        f"CHAIN: {CHAIN}"
    )
    print(
        f"MC RANGE: "
        f"${MIN_MC:,}-${MAX_MC:,}"
    )
    print(
        f"MIN LIQUIDITY: "
        f"${MIN_LIQUIDITY:,}"
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
        f"TELEGRAM TOKEN: "
        f"{'CONFIGURED' if TELEGRAM_BOT_TOKEN else 'MISSING'}"
    )
    print()
    print(
        "[STARTUP] Testing Telegram..."
    )
    test_telegram()
    print(
        "[STARTUP] Testing DexScreener..."
    )
    test_dexscreener()
    print()
    print(
        "[STARTUP] Scanner running."
    )
    offset = None
    while True:
        try:
            offset = handle_telegram_updates(
                offset
            )
            now = time.time()
            if (
                now - LAST_DISCOVERY
                >= DISCOVERY_INTERVAL_SECONDS
            ):
                run_discovery()
                LAST_DISCOVERY = now
            else:
                run_tracking()
            time.sleep(
                SCAN_INTERVAL_SECONDS
            )
        except KeyboardInterrupt:
            print(
                "[STOP] Bot stopped."
            )
            break
        except Exception as exc:
            print(
                f"[MAIN ERROR] {exc
