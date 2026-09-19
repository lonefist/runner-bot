import json
import logging
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


# ============================================================
# RUNNER ENGINE v3
# Persistent history + structure/acceleration detection
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s runner_bot: %(message)s",
)
LOGGER = logging.getLogger("runner_bot")


# ============================================================
# CONFIG
# ============================================================

DEX_BASE_URL = "https://api.dexscreener.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

ALERTS_ENABLED = (
    os.getenv("HEATING_ALERTS_ENABLED", "false").lower()
    == "true"
)

SCAN_INTERVAL_SECONDS = float(
    os.getenv("DEX_SCAN_INTERVAL_SECONDS", "15")
)

MAX_TOKENS_PER_SCAN = int(
    os.getenv("DEX_MAX_TOKENS_PER_SCAN", "200")
)

MIN_MC = float(
    os.getenv("RUNNER_MIN_MARKET_CAP_USD", "20000")
)

MAX_MC = float(
    os.getenv("RUNNER_MAX_MARKET_CAP_USD", "200000")
)

MIN_LIQUIDITY = float(
    os.getenv("RUNNER_MIN_LIQUIDITY_USD", "10000")
)

MIN_VOLUME_5M = float(
    os.getenv("RUNNER_MIN_VOLUME_5M_USD", "5000")
)

ALERT_COOLDOWN_SECONDS = int(
    os.getenv("HEATING_ALERT_COOLDOWN_SECONDS", "3600")
)

# Persistent state file.
STATE_FILE = "runner_state.json"

# Keep enough observations to study short-term structure,
# while preventing the file from growing forever.
MAX_HISTORY_PER_TOKEN = 80

# Minimum observations before structure analysis.
MIN_OBSERVATIONS = 4


# ============================================================
# HTTP
# ============================================================

def http_get_json(url: str):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "runner-bot/3.0"
        },
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class Snapshot:
    timestamp: float
    address: str
    symbol: str

    market_cap: float
    liquidity: float

    volume_5m: float
    buys_5m: int
    sells_5m: int

    price: float

    price_change_5m: float
    price_change_1h: float

    pair_created_at: Optional[int]

    @property
    def transactions(self):
        return self.buys_5m + self.sells_5m


# ============================================================
# HISTORY
# ============================================================

class TokenHistory:

    def __init__(self, address: str):
        self.address = address
        self.snapshots: List[Snapshot] = []

    def add(self, snapshot: Snapshot):

        self.snapshots.append(snapshot)

        if len(self.snapshots) > MAX_HISTORY_PER_TOKEN:
            self.snapshots = self.snapshots[
                -MAX_HISTORY_PER_TOKEN:
            ]

    def latest(self) -> Optional[Snapshot]:

        if not self.snapshots:
            return None

        return self.snapshots[-1]

    def previous(self) -> Optional[Snapshot]:

        if len(self.snapshots) < 2:
            return None

        return self.snapshots[-2]

    def observations(self):
        return len(self.snapshots)

    def market_cap_change(self):

        if len(self.snapshots) < 2:
            return None

        previous = self.previous()
        latest = self.latest()

        if not previous or previous.market_cap <= 0:
            return None

        return (
            (latest.market_cap - previous.market_cap)
            / previous.market_cap
        ) * 100

    def liquidity_change(self):

        if len(self.snapshots) < 2:
            return None

        previous = self.previous()
        latest = self.latest()

        if not previous or previous.liquidity <= 0:
            return None

        return (
            (latest.liquidity - previous.liquidity)
            / previous.liquidity
        ) * 100

    def volume_acceleration(self):

        if len(self.snapshots) < 3:
            return None

        previous = self.snapshots[-2]
        latest = self.snapshots[-1]

        if previous.volume_5m <= 0:
            return None

        return latest.volume_5m / previous.volume_5m

    def transaction_acceleration(self):

        if len(self.snapshots) < 3:
            return None

        previous = self.snapshots[-2]
        latest = self.snapshots[-1]

        if previous.transactions <= 0:
            return None

        return latest.transactions / previous.transactions

    def recent_high(self, lookback=8):

        if not self.snapshots:
            return None

        data = self.snapshots[-lookback:]

        return max(
            x.market_cap for x in data
        )

    def recent_low(self, lookback=8):

        if not self.snapshots:
            return None

        data = self.snapshots[-lookback:]

        return min(
            x.market_cap for x in data
        )

    def range_percent(self, lookback=8):

        high = self.recent_high(lookback)
        low = self.recent_low(lookback)

        if high is None or low is None or low <= 0:
            return None

        return ((high - low) / low) * 100

    def token_age_hours(self):

        latest = self.latest()

        if not latest or not latest.pair_created_at:
            return None

        age_ms = (
            int(time.time() * 1000)
            - latest.pair_created_at
        )

        return max(
            0,
            age_ms / 1000 / 60 / 60
        )


# ============================================================
# STATE
# ============================================================

histories: Dict[str, TokenHistory] = {}

last_alerts: Dict[str, float] = {}

subscribers = set()


# ============================================================
# PERSISTENCE
# ============================================================

def save_state():

    data = {
        "histories": {},
        "last_alerts": last_alerts,
    }

    for address, history in histories.items():

        data["histories"][address] = [
            asdict(snapshot)
            for snapshot in history.snapshots
        ]

    temp_file = STATE_FILE + ".tmp"

    try:

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        os.replace(temp_file, STATE_FILE)

        LOGGER.info(
            "Persistent state saved: %s tokens",
            len(histories),
        )

    except Exception as exc:

        LOGGER.error(
            "Could not save state: %s",
            exc,
        )


def load_state():

    global histories
    global last_alerts

    if not os.path.exists(STATE_FILE):

        LOGGER.info(
            "No previous state found; starting fresh"
        )

        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        stored_histories = data.get(
            "histories",
            {}
        )

        for address, snapshots in stored_histories.items():

            history = TokenHistory(address)

            for item in snapshots:

                try:

                    history.snapshots.append(
                        Snapshot(**item)
                    )

                except Exception:
                    continue

            if history.snapshots:
                histories[address] = history

        last_alerts = data.get(
            "last_alerts",
            {}
        )

        LOGGER.info(
            "Persistent state loaded: %s tokens",
            len(histories),
        )

    except Exception as exc:

        LOGGER.error(
            "Could not load persistent state: %s",
            exc,
        )


# ============================================================
# DEX DISCOVERY
# ============================================================

def fetch_json(path):

    url = DEX_BASE_URL + path

    try:
        return http_get_json(url)

    except Exception as exc:

        LOGGER.warning(
            "DEX request failed: %s",
            exc,
        )

        return {}


def discover_pairs():

    pairs = []

    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]

    for endpoint in endpoints:

        result = fetch_json(endpoint)

        if isinstance(result, list):
            pairs.extend(result)

        elif isinstance(result, dict):

            possible = (
                result.get("pairs")
                or result.get("data")
                or []
            )

            if isinstance(possible, list):
                pairs.extend(possible)

    # Search several common Solana quote assets.
    for query in [
        "SOL",
        "USDC",
        "USDT",
        "WSOL",
    ]:

        encoded = urllib.parse.quote(query)

        result = fetch_json(
            "/latest/dex/search?q=" + encoded
        )

        if isinstance(result, dict):

            found = result.get(
                "pairs",
                []
            )

            if isinstance(found, list):
                pairs.extend(found)

    return pairs


# ============================================================
# PAIR SELECTION
# ============================================================

def choose_best_pair(pairs):

    solana_pairs = []

    for pair in pairs:

        if not isinstance(pair, dict):
            continue

        if pair.get("chainId") != "solana":
            continue

        solana_pairs.append(pair)

    if not solana_pairs:
        return None

    # Highest liquidity first.
    solana_pairs.sort(
        key=lambda p: (
            (p.get("liquidity") or {}).get(
                "usd"
            )
            or 0
        ),
        reverse=True,
    )

    return solana_pairs[0]


# ============================================================
# PAIR → SNAPSHOT
# ============================================================

def pair_to_snapshot(pair):

    try:

        base_token = pair.get(
            "baseToken",
            {}
        )

        address = base_token.get(
            "address"
        )

        symbol = (
            base_token.get("symbol")
            or "UNKNOWN"
        )

        if not address:
            return None

        market_cap = float(
            pair.get("marketCap")
            or pair.get("fdv")
            or 0
        )

        liquidity = float(
            (pair.get("liquidity") or {}).get(
                "usd"
            )
            or 0
        )

        volume = pair.get(
            "volume"
        ) or {}

        volume_5m = float(
            volume.get("m5")
            or 0
        )

        txns = pair.get(
            "txns"
        ) or {}

        tx5 = txns.get(
            "m5"
        ) or {}

        buys = int(
            tx5.get("buys")
            or 0
        )

        sells = int(
            tx5.get("sells")
            or 0
        )

        price = float(
            pair.get("priceUsd")
            or 0
        )

        changes = pair.get(
            "priceChange"
        ) or {}

        change_5m = float(
            changes.get("m5")
            or 0
        )

        change_1h = float(
            changes.get("h1")
            or 0
        )

        created = pair.get(
            "pairCreatedAt"
        )

        if created is not None:
            created = int(created)

        return Snapshot(
            timestamp=time.time(),
            address=address,
            symbol=symbol,
            market_cap=market_cap,
            liquidity=liquidity,
            volume_5m=volume_5m,
            buys_5m=buys,
            sells_5m=sells,
            price=price,
            price_change_5m=change_5m,
            price_change_1h=change_1h,
            pair_created_at=created,
        )

    except Exception as exc:

        LOGGER.warning(
            "Could not create snapshot: %s",
            exc,
        )

        return None


# ============================================================
# STRUCTURE ENGINE
# ============================================================

def analyze(history: TokenHistory):

    latest = history.latest()

    if not latest:
        return {
            "state": "NEW",
            "breakout": False,
            "ignition": False,
            "reasons": [],
        }

    observations = history.observations()

    if observations < MIN_OBSERVATIONS:

        return {
            "state": (
                "NEW"
                if observations == 1
                else "OBSERVING"
            ),
            "breakout": False,
            "ignition": False,
            "reasons": [],
        }

    range_pct = history.range_percent(
        lookback=8
    )

    vol_accel = (
        history.volume_acceleration()
    )

    txn_accel = (
        history.transaction_acceleration()
    )

    mc_change = (
        history.market_cap_change()
    )

    liq_change = (
        history.liquidity_change()
    )

    recent_high = history.recent_high(
        lookback=8
    )

    previous_high = None

    if len(history.snapshots) >= 3:

        previous_high = max(
            x.market_cap
            for x in history.snapshots[-8:-1]
        )

    breakout = False

    if previous_high:

        breakout = (
            latest.market_cap
            > previous_high * 1.03
        )

    # --------------------------------------------------------
    # Consolidation
    # --------------------------------------------------------

    consolidated = (
        range_pct is not None
        and range_pct <= 20
    )

    # --------------------------------------------------------
    # Activity expansion
    # --------------------------------------------------------

    activity_expanding = (
        vol_accel is not None
        and txn_accel is not None
        and vol_accel >= 1.20
        and txn_accel >= 1.15
    )

    # --------------------------------------------------------
    # Positive market structure
    # --------------------------------------------------------

    positive_move = (
        mc_change is not None
        and mc_change >= 3
    )

    liquidity_support = (
        liq_change is not None
        and liq_change >= -5
    )

    # --------------------------------------------------------
    # State
    # --------------------------------------------------------

    if (
        consolidated
        and not breakout
        and not activity_expanding
    ):

        state = "CONSOLIDATION"

    elif (
        activity_expanding
        and not breakout
    ):

        state = "EXPANSION"

    elif breakout:

        state = "STRUCTURE BREAK"

    else:

        state = "OBSERVING"

    # --------------------------------------------------------
    # Ignition
    # --------------------------------------------------------

    reasons = []

    if consolidated:
        reasons.append(
            "tight recent range"
        )

    if activity_expanding:
        reasons.append(
            "activity expanding"
        )

    if breakout:
        reasons.append(
            "structure break"
        )

    if positive_move:
        reasons.append(
            "positive MC expansion"
        )

    if liquidity_support:
        reasons.append(
            "liquidity stable"
        )

    ignition = (
        observations >= 4
        and consolidated
        and activity_expanding
        and breakout
        and positive_move
        and liquidity_support
    )

    return {
        "state": state,
        "breakout": breakout,
        "ignition": ignition,
        "reasons": reasons,
        "range_pct": range_pct,
        "vol_accel": vol_accel,
        "txn_accel": txn_accel,
        "mc_change": mc_change,
        "liq_change": liq_change,
    }


# ============================================================
# FORMATTING
# ============================================================

def fmt_money(value):

    if value is None:
        return "n/a"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.2f}K"

    return f"${value:.0f}"


def fmt_pct(value):

    if value is None:
        return "n/a"

    return f"{value:.2f}%"


def fmt_x(value):

    if value is None:
        return "n/a"

    return f"{value:.2f}x"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, payload):

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/"
        + method
    )

    encoded = urllib.parse.urlencode(
        payload
    ).encode()

    try:

        request = urllib.request.Request(
            url,
            data=encoded,
            headers={
                "Content-Type":
                    "application/x-www-form-urlencoded"
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=15
        ) as response:

            return json.loads(
                response.read().decode()
            )

    except Exception as exc:

        LOGGER.warning(
            "Telegram request failed: %s",
            exc,
        )

        return None


def send_message(chat_id, text):

    telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
        },
    )


def send_alert(snapshot, analysis):

    if not ALERTS_ENABLED:
        return

    now = time.time()

    last = last_alerts.get(
        snapshot.address,
        0,
    )

    if now - last < ALERT_COOLDOWN_SECONDS:
        return

    reasons = ", ".join(
        analysis.get(
            "reasons",
            []
        )
    )

    message = (
        "🔥 IGNITION DETECTED\n\n"
        f"${snapshot.symbol}\n"
        f"MC: {fmt_money(snapshot.market_cap)}\n"
        f"Liquidity: {fmt_money(snapshot.liquidity)}\n"
        f"5m Volume: {fmt_money(snapshot.volume_5m)}\n"
        f"Buys/Sells: {snapshot.buys_5m}/{snapshot.sells_5m}\n"
        f"Volume acceleration: "
        f"{fmt_x(analysis.get('vol_accel'))}\n"
        f"Transaction acceleration: "
        f"{fmt_x(analysis.get('txn_accel'))}\n"
        f"MC change: "
        f"{fmt_pct(analysis.get('mc_change'))}\n"
        f"Structure break: YES\n"
        f"Reasons: {reasons}\n\n"
        f"CA:\n{snapshot.address}\n\n"
        "⚠️ Experimental signal. "
        "Not financial advice."
    )

    for chat_id in subscribers:

        send_message(
            chat_id,
            message,
        )

    last_alerts[
        snapshot.address
    ] = now


# ============================================================
# TELEGRAM POLLING
# ============================================================

telegram_offset = None


def poll_telegram():

    global telegram_offset

    if not TELEGRAM_BOT_TOKEN:
        return

    payload = {
        "timeout": 1,
    }

    if telegram_offset is not None:

        payload["offset"] = telegram_offset

    result = telegram_request(
        "getUpdates",
        payload,
    )

    if not result:
        return

    updates = result.get(
        "result",
        []
    )

    for update in updates:

        telegram_offset = (
            update.get("update_id", 0)
            + 1
        )

        message = update.get(
            "message"
        ) or {}

        chat = message.get(
            "chat"
        ) or {}

        chat_id = chat.get(
            "id"
        )

        text = (
            message.get("text")
            or ""
        ).strip()

        if not chat_id:
            continue

        if text.startswith("/start"):

            subscribers.add(
                chat_id
            )

            send_message(
                chat_id,
                "🔥 Runner Bot is online.\n"
                "You are subscribed to runner alerts."
            )

        elif text.startswith("/stop"):

            subscribers.discard(
                chat_id
            )

            send_message(
                chat_id,
                "Runner alerts stopped."
            )


# ============================================================
# SCAN
# ============================================================

def scan():

    discovered = discover_pairs()

    # Group pairs by token.
    grouped = {}

    for pair in discovered:

        if not isinstance(pair, dict):
            continue

        if pair.get("chainId") != "solana":
            continue

        base = pair.get(
            "baseToken"
        ) or {}

        address = base.get(
            "address"
        )

        if not address:
            continue

        grouped.setdefault(
            address,
            []
        ).append(pair)

    addresses = list(
        grouped.keys()
    )[:MAX_TOKENS_PER_SCAN]

    evaluated = 0
    ignition_candidates = 0
    missing_liquidity = 0

    for address in addresses:

        pair = choose_best_pair(
            grouped[address]
        )

        if not pair:
            continue

        snapshot = pair_to_snapshot(
            pair
        )

        if not snapshot:
            continue

        if not (
            MIN_MC
            <= snapshot.market_cap
            <= MAX_MC
        ):
            continue

        if snapshot.liquidity < MIN_LIQUIDITY:

            missing_liquidity += 1

            continue

        if snapshot.volume_5m < MIN_VOLUME_5M:
            continue

        evaluated += 1

        if address not in histories:

            histories[address] = (
                TokenHistory(address)
            )

        history = histories[address]

        history.add(snapshot)

        result = analyze(
            history
        )

        ignition = result.get(
            "ignition",
            False
        )

        if ignition:
            ignition_candidates += 1

        age = (
            history.token_age_hours()
        )

        LOGGER.info(
            "RUNNER DATA $%s | "
            "state=%s | "
            "obs=%s | "
            "MC=%s | "
            "5mVol=%s | "
            "buys/sells=%s/%s | "
            "volAccel=%s | "
            "txnAccel=%s | "
            "MCmove=%s | "
            "liqMove=%s | "
            "range=%s | "
            "breakout=%s | "
            "age=%s",
            snapshot.symbol,
            result.get("state"),
            history.observations(),
            fmt_money(snapshot.market_cap),
            fmt_money(snapshot.volume_5m),
            snapshot.buys_5m,
            snapshot.sells_5m,
            fmt_x(
                result.get(
                    "vol_accel"
                )
            ),
            fmt_x(
                result.get(
                    "txn_accel"
                )
            ),
            fmt_pct(
                result.get(
                    "mc_change"
                )
            ),
            fmt_pct(
                result.get(
                    "liq_change"
                )
            ),
            fmt_pct(
                result.get(
                    "range_pct"
                )
            ),
            result.get(
                "breakout"
            ),
            (
                f"{age:.1f}h"
                if age is not None
                else "n/a"
            ),
        )

        if ignition:

            LOGGER.info(
                "IGNITION CANDIDATE: $%s",
                snapshot.symbol,
            )

            send_alert(
                snapshot,
                result,
            )

    LOGGER.info(
        "DEX scan complete: %s Solana pairs discovered | "
        "%s unique tokens evaluated | "
        "%s ignition candidates | "
        "missing liquidity=%s",
        len(discovered),
        evaluated,
        ignition_candidates,
        missing_liquidity,
    )

    # Save after every scan.
    save_state()


# ============================================================
# MAIN
# ============================================================

def run():

    LOGGER.info(
        "Runner Engine v3 is online"
    )

    LOGGER.info(
        "Runner range: MC=$%s-$%s | "
        "minimum liquidity=$%s | "
        "minimum 5m volume=$%s | "
        "scan interval=%ss",
        fmt_money(MIN_MC),
        fmt_money(MAX_MC),
        fmt_money(MIN_LIQUIDITY),
        fmt_money(MIN_VOLUME_5M),
        SCAN_INTERVAL_SECONDS,
    )

    LOGGER.info(
        "Telegram alerts enabled: %s",
        ALERTS_ENABLED,
    )

    load_state()

    try:

        while True:

            started = time.time()

            poll_telegram()

            scan()

            elapsed = (
                time.time()
                - started
            )

            sleep_for = max(
                1,
                SCAN_INTERVAL_SECONDS
                - elapsed,
            )

            time.sleep(
                sleep_for
            )

    except KeyboardInterrupt:

        LOGGER.info(
            "Shutdown requested"
        )

    except Exception as exc:

        LOGGER.exception(
            "Runner Engine error: %s",
            exc,
        )

    finally:

        save_state()

        LOGGER.info(
            "Runner Engine stopped"
        )


if __name__ == "__main__":
    run()
