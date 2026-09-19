import os
import json
import time
import signal
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


# ============================================================
# RUNNER ENGINE
# Persistent history + structure/acceleration detection
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s runner_bot: %(message)s",
)

log = logging.getLogger("runner_bot")


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

ALERTS_ENABLED = (
    os.environ.get("HEATING_ALERTS_ENABLED", "false").lower()
    == "true"
)

SCAN_INTERVAL = float(
    os.environ.get("DEX_SCAN_INTERVAL_SECONDS", "15")
)

MAX_TOKENS_PER_SCAN = int(
    os.environ.get("DEX_MAX_TOKENS_PER_SCAN", "200")
)

MIN_MC = 20_000
MAX_MC = 200_000
MIN_LIQUIDITY = 10_000
MIN_VOLUME_5M = 5_000

STATE_FILE = "runner_state.json"

MAX_HISTORY = 120

DEX_BASE = "https://api.dexscreener.com"

running = True

subscribers = set()

histories: Dict[str, "TokenHistory"] = {}


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

    pair_created_at: int

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

        if len(self.snapshots) > MAX_HISTORY:
            self.snapshots = self.snapshots[-MAX_HISTORY:]

    def previous(self, steps=1) -> Optional[Snapshot]:

        if len(self.snapshots) <= steps:
            return None

        return self.snapshots[-1 - steps]

    def market_cap_change(self, steps=1):

        old = self.previous(steps)

        if not old or old.market_cap <= 0:
            return 0.0

        return (
            self.snapshots[-1].market_cap / old.market_cap - 1
        ) * 100

    def liquidity_change(self, steps=1):

        old = self.previous(steps)

        if not old or old.liquidity <= 0:
            return 0.0

        return (
            self.snapshots[-1].liquidity / old.liquidity - 1
        ) * 100

    def volume_change(self, steps=1):

        old = self.previous(steps)

        if not old or old.volume_5m <= 0:
            return 1.0

        return (
            self.snapshots[-1].volume_5m
            / old.volume_5m
        )

    def transaction_change(self, steps=1):

        old = self.previous(steps)

        if not old:
            return 1.0

        current_tx = (
            self.snapshots[-1].buys_5m
            + self.snapshots[-1].sells_5m
        )

        old_tx = (
            old.buys_5m
            + old.sells_5m
        )

        if old_tx <= 0:
            return 1.0

        return current_tx / old_tx

    def recent_high(self, lookback=20):

        data = self.snapshots[-lookback:]

        if not data:
            return 0

        return max(
            x.market_cap for x in data
        )

    def recent_low(self, lookback=20):

        data = self.snapshots[-lookback:]

        if not data:
            return 0

        return min(
            x.market_cap for x in data
        )

    def range_percent(self, lookback=20):

        high = self.recent_high(lookback)
        low = self.recent_low(lookback)

        if low <= 0:
            return 0

        return (
            (high - low) / low
        ) * 100


# ============================================================
# HTTP
# ============================================================

def get_json(url):

    try:

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RunnerBot/3.1"
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=15
        ) as response:

            return json.loads(
                response.read().decode("utf-8")
            )

    except Exception as exc:

        log.warning(
            "HTTP error: %s",
            exc
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram(method, payload=None):

    if not BOT_TOKEN:
        return None

    url = (
        "https://api.telegram.org/bot"
        + BOT_TOKEN
        + "/"
        + method
    )

    try:

        data = None

        if payload is not None:
            data = json.dumps(
                payload
            ).encode()

        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json"
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

        log.warning(
            "Telegram error: %s",
            exc
        )

        return None


def poll_telegram(offset):

    result = telegram(
        "getUpdates",
        {
            "timeout": 1,
            "offset": offset
        }
    )

    if not result or not result.get("ok"):
        return offset

    for update in result.get(
        "result",
        []
    ):

        offset = (
            update["update_id"] + 1
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

        text = message.get(
            "text",
            ""
        )

        if chat_id and text.startswith(
            "/start"
        ):

            subscribers.add(
                chat_id
            )

            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text":
                        "🔥 Runner Bot is online.\n\n"
                        "Runner detection is active.\n"
                        "Alerts are currently in testing mode."
                }
            )

    return offset


def send_alert(message):

    if not ALERTS_ENABLED:
        return

    for chat_id in list(
        subscribers
    ):

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True
            }
        )


# ============================================================
# DEX SCREENER DISCOVERY
# ============================================================
#
# IMPORTANT:
# We deliberately DO NOT use:
#
#     /tokens/{address}
#
# because that was producing the 404 errors.
#
# Instead we collect Solana pairs directly from:
#
#     token profiles
#     token boosts
#     DEX Screener search
#
# ============================================================

def discover_pairs():

    pairs = []

    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
    ]

    # --------------------------------------------------------
    # Profiles / boosts
    # --------------------------------------------------------

    for endpoint in endpoints:

        url = DEX_BASE + endpoint

        data = get_json(url)

        if not data:
            continue

        if isinstance(data, list):

            items = data

        elif isinstance(data, dict):

            items = (
                data.get("pairs")
                or data.get("tokens")
                or data.get("data")
                or []
            )

        else:

            items = []

        if not isinstance(
            items,
            list
        ):
            continue

        for item in items:

            if not isinstance(
                item,
                dict
            ):
                continue

            if item.get(
                "chainId"
            ) != "solana":

                continue

            pairs.append(item)

    # --------------------------------------------------------
    # Direct DEX Screener searches
    # --------------------------------------------------------

    searches = [
        "SOL",
        "USDC",
        "USDT",
        "WSOL",
    ]

    for query in searches:

        encoded = urllib.parse.quote(
            query
        )

        url = (
            DEX_BASE
            + "/latest/dex/search?q="
            + encoded
        )

        data = get_json(url)

        if not data:
            continue

        found = data.get(
            "pairs",
            []
        )

        if not isinstance(
            found,
            list
        ):
            continue

        for pair in found:

            if not isinstance(
                pair,
                dict
            ):
                continue

            if pair.get(
                "chainId"
            ) != "solana":

                continue

            pairs.append(pair)

    return pairs


# ============================================================
# BEST PAIR PER TOKEN
# ============================================================

def choose_best_pairs(pairs):

    best = {}

    for pair in pairs:

        if not isinstance(
            pair,
            dict
        ):
            continue

        if pair.get(
            "chainId"
        ) != "solana":

            continue

        base = pair.get(
            "baseToken",
            {}
        )

        address = base.get(
            "address"
        )

        if not address:
            continue

        liquidity_data = (
            pair.get(
                "liquidity"
            )
            or {}
        )

        liquidity = (
            liquidity_data.get(
                "usd"
            )
            or 0
        )

        try:
            liquidity = float(
                liquidity
            )

        except Exception:
            liquidity = 0

        current = best.get(
            address
        )

        if current is None:

            best[address] = pair

        else:

            current_liquidity = (
                current.get(
                    "liquidity"
                )
                or {}
            ).get(
                "usd"
            ) or 0

            try:
                current_liquidity = float(
                    current_liquidity
                )

            except Exception:
                current_liquidity = 0

            if liquidity > current_liquidity:

                best[address] = pair

    result = list(
        best.values()
    )

    return result[
        :MAX_TOKENS_PER_SCAN
    ]


# ============================================================
# PAIR → SNAPSHOT
# ============================================================

def make_snapshot(pair):

    try:

        base = pair.get(
            "baseToken",
            {}
        )

        address = base.get(
            "address"
        )

        if not address:
            return None

        symbol = (
            base.get(
                "symbol"
            )
            or "?"
        )

        market_cap = (
            pair.get(
                "marketCap"
            )
            or pair.get(
                "fdv"
            )
            or 0
        )

        liquidity = (
            pair.get(
                "liquidity"
            )
            or {}
        ).get(
            "usd"
        ) or 0

        volume = (
            pair.get(
                "volume"
            )
            or {}
        )

        volume_5m = (
            volume.get(
                "m5"
            )
            or 0
        )

        txns = (
            pair.get(
                "txns"
            )
            or {}
        )

        tx5 = (
            txns.get(
                "m5"
            )
            or {}
        )

        buys = (
            tx5.get(
                "buys"
            )
            or 0
        )

        sells = (
            tx5.get(
                "sells"
            )
            or 0
        )

        price = (
            pair.get(
                "priceUsd"
            )
            or 0
        )

        changes = (
            pair.get(
                "priceChange"
            )
            or {}
        )

        price_change_5m = (
            changes.get(
                "m5"
            )
            or 0
        )

        price_change_1h = (
            changes.get(
                "h1"
            )
            or 0
        )

        created = (
            pair.get(
                "pairCreatedAt"
            )
            or 0
        )

        market_cap = float(
            market_cap
        )

        liquidity = float(
            liquidity
        )

        volume_5m = float(
            volume_5m
        )

        price = float(
            price
        )

        buys = int(
            buys
        )

        sells = int(
            sells
        )

        price_change_5m = float(
            price_change_5m
        )

        price_change_1h = float(
            price_change_1h
        )

        created = int(
            created
        )

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
            price_change_5m=price_change_5m,
            price_change_1h=price_change_1h,
            pair_created_at=created,
        )

    except Exception as exc:

        log.warning(
            "Snapshot error: %s",
            exc
        )

        return None


# ============================================================
# RUNNER ANALYSIS
# ============================================================

def analyze(snapshot):

    address = snapshot.address
    symbol = snapshot.symbol

    history = histories.setdefault(
        address,
        TokenHistory(address)
    )

    history.add(
        snapshot
    )

    observations = len(
        history.snapshots
    )

    if observations < 4:

        return "NEW"

    # Compare with approximately
    # 45 seconds ago when scanning every 15 sec.
    #
    # This gives us a short-term change
    # without pretending that DEX Screener's
    # rolling 5m volume is a new 15-second bar.

    steps = min(
        3,
        observations - 1
    )

    mc_move = history.market_cap_change(
        steps
    )

    vol_change = history.volume_change(
        steps
    )

    txn_change = history.transaction_change(
        steps
    )

    liquidity_change = history.liquidity_change(
        steps
    )

    lookback = min(
        20,
        observations
    )

    range_pct = history.range_percent(
        lookback
    )

    recent_high = history.recent_high(
        lookback
    )

    previous_high = 0

    if observations > 1:

        previous_data = (
            history.snapshots[
                -lookback:-1
            ]
        )

        if previous_data:

            previous_high = max(
                x.market_cap
                for x in previous_data
            )

    breakout = (
        previous_high > 0
        and snapshot.market_cap
        > previous_high * 1.03
    )

    # --------------------------------------------------------
    # BUY / SELL COUNT PRESSURE
    # --------------------------------------------------------

    buys = snapshot.buys_5m
    sells = snapshot.sells_5m

    if sells > 0:

        buy_sell_ratio = (
            buys / sells
        )

    else:

        buy_sell_ratio = (
            float(buys)
            if buys > 0
            else 0
        )

    buy_pressure = (
        buys > sells
        and buy_sell_ratio >= 1.25
    )

    # --------------------------------------------------------
    # CONSOLIDATION
    # --------------------------------------------------------

    consolidated = (
        range_pct <= 20
    )

    # --------------------------------------------------------
    # ACTIVITY EXPANSION
    # --------------------------------------------------------

    activity_expanding = (
        vol_change >= 1.20
        and txn_change >= 1.15
    )

    # --------------------------------------------------------
    # POSITIVE STRUCTURE
    # --------------------------------------------------------

    positive_move = (
        mc_move >= 3
    )

    liquidity_supported = (
        liquidity_change >= -5
    )

    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    if (
        consolidated
        and not activity_expanding
        and not breakout
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
    # IGNITION
    # --------------------------------------------------------

    ignition = (
        observations >= 6
        and consolidated
        and activity_expanding
        and breakout
        and positive_move
        and liquidity_supported
        and buy_pressure
    )

    if ignition:

        log.info(
            "🔥 IGNITION candidate: %s | "
            "MC=$%.2fK | "
            "5mVol=$%.2fK | "
            "buys/sells=%s/%s | "
            "buy/sell=%.2fx | "
            "volAccel=%.2fx | "
            "txnAccel=%.2fx | "
            "MCmove=%.2f%% | "
            "liqMove=%.2f%% | "
            "range=%.2f%%",

            symbol,

            snapshot.market_cap / 1000,

            snapshot.volume_5m / 1000,

            buys,

            sells,

            buy_sell_ratio,

            vol_change,

            txn_change,

            mc_move,

            liquidity_change,

            range_pct
        )

        send_alert(
            f"🔥 IGNITION DETECTED\n\n"
            f"${symbol}\n"
            f"MC: ${snapshot.market_cap:,.0f}\n"
            f"5m Volume: ${snapshot.volume_5m:,.0f}\n"
            f"Buys/Sells: {buys}/{sells}\n"
            f"Buy/Sell ratio: {buy_sell_ratio:.2f}x\n"
            f"Volume acceleration: {vol_change:.2f}x\n"
            f"Transaction acceleration: {txn_change:.2f}x\n"
            f"MC movement: {mc_move:.2f}%\n\n"
            f"CA:\n{address}\n\n"
            f"⚠️ Experimental runner detection."
        )

        return "IGNITION"

    return state


# ============================================================
# PERSISTENCE
# ============================================================

def save_state():

    output = {}

    for address, history in histories.items():

        output[address] = {
            "snapshots": [
                asdict(snapshot)
                for snapshot in history.snapshots
            ]
        }

    temp_file = (
        STATE_FILE
        + ".tmp"
    )

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                output,
                file
            )

        os.replace(
            temp_file,
            STATE_FILE
        )

        log.info(
            "Persistent state saved: %s tokens",
            len(output)
        )

    except Exception as exc:

        log.warning(
            "Could not save state: %s",
            exc
        )


def load_state():

    global histories

    if not os.path.exists(
        STATE_FILE
    ):

        log.info(
            "No previous state found; starting fresh"
        )

        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(
                file
            )

        for address, value in data.items():

            history = TokenHistory(
                address
            )

            for raw in value.get(
                "snapshots",
                []
            ):

                try:

                    history.add(
                        Snapshot(
                            **raw
                        )
                    )

                except Exception:
                    continue

            if history.snapshots:

                histories[
                    address
                ] = history

        log.info(
            "Persistent state loaded: %s tokens",
            len(histories)
        )

    except Exception as exc:

        log.warning(
            "Could not load state: %s",
            exc
        )


# ============================================================
# SCAN
# ============================================================

def scan():

    pairs = discover_pairs()

    best_pairs = choose_best_pairs(
        pairs
    )

    discovered = len(
        pairs
    )

    unique = len(
        best_pairs
    )

    evaluated = 0
    rejected_mc = 0
    rejected_liquidity = 0
    rejected_volume = 0
    missing_data = 0
    ignition = 0

    for pair in best_pairs:

        snapshot = make_snapshot(
            pair
        )

        if snapshot is None:

            missing_data += 1
            continue

        if snapshot.market_cap <= 0:

            missing_data += 1
            continue

        # ----------------------------------------------------
        # Count token before filters
        # ----------------------------------------------------

        evaluated += 1

        # ----------------------------------------------------
        # MARKET CAP
        # ----------------------------------------------------

        if not (
            MIN_MC
            <= snapshot.market_cap
            <= MAX_MC
        ):

            rejected_mc += 1
            continue

        # ----------------------------------------------------
        # LIQUIDITY
        # ----------------------------------------------------

        if (
            snapshot.liquidity
            < MIN_LIQUIDITY
        ):

            rejected_liquidity += 1
            continue

        # ----------------------------------------------------
        # VOLUME
        # ----------------------------------------------------

        if (
            snapshot.volume_5m
            < MIN_VOLUME_5M
        ):

            rejected_volume += 1
            continue

        # ----------------------------------------------------
        # RUNNER ENGINE
        # ----------------------------------------------------

        state = analyze(
            snapshot
        )

        if state == "IGNITION":

            ignition += 1

    log.info(
        "DEX scan complete: "
        "%s Solana pairs discovered | "
        "%s unique tokens | "
        "%s reached filter | "
        "MC reject=%s | "
        "liquidity reject=%s | "
        "volume reject=%s | "
        "missing data=%s | "
        "ignition=%s",

        discovered,
        unique,
        evaluated,
        rejected_mc,
        rejected_liquidity,
        rejected_volume,
        missing_data,
        ignition
    )

    save_state()


# ============================================================
# SHUTDOWN
# ============================================================

def shutdown(
    signum=None,
    frame=None
):

    global running

    running = False

    log.info(
        "Shutdown requested"
    )


signal.signal(
    signal.SIGTERM,
    shutdown
)

signal.signal(
    signal.SIGINT,
    shutdown
)


# ============================================================
# MAIN
# ============================================================

def main():

    log.info(
        "Runner Engine v3.2 is online"
    )

    log.info(
        "Runner range: MC=$%.2fK-$%.2fK | "
        "minimum liquidity=$%.2fK | "
        "minimum 5m volume=$%.2fK | "
        "scan interval=%.1fs",

        MIN_MC / 1000,
        MAX_MC / 1000,
        MIN_LIQUIDITY / 1000,
        MIN_VOLUME_5M / 1000,
        SCAN_INTERVAL
    )

    log.info(
        "Telegram alerts enabled: %s",
        ALERTS_ENABLED
    )

    load_state()

    offset = 0

    while running:

        try:

            offset = poll_telegram(
                offset
            )

            scan()

        except Exception as exc:

            log.exception(
                "Scan error: %s",
                exc
            )

        if not running:
            break

        time.sleep(
            SCAN_INTERVAL
        )

    save_state()

    log.info(
        "Runner Engine stopped"
    )


if __name__ == "__main__":
    main()
