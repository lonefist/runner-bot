import os
import json
import time
import signal
import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.parse import quote

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALERTS_ENABLED = os.environ.get("HEATING_ALERTS_ENABLED", "false").lower() == "true"

SCAN_INTERVAL = float(os.environ.get("DEX_SCAN_INTERVAL_SECONDS", "15"))

MIN_MC = 20_000
MAX_MC = 200_000
MIN_LIQUIDITY = 10_000
MIN_VOLUME_5M = 5_000

STATE_FILE = "runner_state.json"
MAX_HISTORY = 120

DEX_BASE = "https://api.dexscreener.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s runner_bot: %(message)s"
)

log = logging.getLogger("runner_bot")

running = True
subscribers = set()

# ============================================================
# DATA
# ============================================================

@dataclass
class Snapshot:
    timestamp: float
    market_cap: float
    liquidity: float
    volume_5m: float
    buys_5m: int
    sells_5m: int
    price: float
    pair_created_at: int


class TokenHistory:
    def __init__(self):
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
            (self.snapshots[-1].market_cap / old.market_cap) - 1
        ) * 100

    def liquidity_change(self, steps=1):
        old = self.previous(steps)

        if not old or old.liquidity <= 0:
            return 0.0

        return (
            (self.snapshots[-1].liquidity / old.liquidity) - 1
        ) * 100

    def volume_change(self, steps=1):
        old = self.previous(steps)

        if not old or old.volume_5m <= 0:
            return 1.0

        return self.snapshots[-1].volume_5m / old.volume_5m

    def transaction_change(self, steps=1):
        old = self.previous(steps)

        if not old:
            return 1.0

        current_tx = (
            self.snapshots[-1].buys_5m +
            self.snapshots[-1].sells_5m
        )

        old_tx = old.buys_5m + old.sells_5m

        if old_tx <= 0:
            return 1.0

        return current_tx / old_tx

    def recent_high(self, lookback=20):
        data = self.snapshots[-lookback:]

        if not data:
            return 0

        return max(x.market_cap for x in data)

    def recent_low(self, lookback=20):
        data = self.snapshots[-lookback:]

        if not data:
            return 0

        return min(x.market_cap for x in data)

    def range_percent(self, lookback=20):
        high = self.recent_high(lookback)
        low = self.recent_low(lookback)

        if low <= 0:
            return 0

        return ((high - low) / low) * 100


histories: Dict[str, TokenHistory] = {}

# ============================================================
# HTTP
# ============================================================

def get_json(url):
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "RunnerBot/3.1"
            }
        )

        with urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())

    except Exception as e:
        log.warning("HTTP error: %s", e)
        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram(method, payload=None):
    if not BOT_TOKEN:
        return None

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    try:
        data = None

        if payload is not None:
            data = json.dumps(payload).encode()

        req = Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json"
            }
        )

        with urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode())

    except Exception as e:
        log.warning("Telegram error: %s", e)
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

    for update in result.get("result", []):
        offset = update["update_id"] + 1

        message = update.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")

        text = message.get("text", "")

        if chat_id and text.startswith("/start"):
            subscribers.add(chat_id)

            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "🔥 Runner Bot is online.\n\n"
                        "Runner detection is active.\n"
                        "Alerts are currently in testing mode."
                    )
                }
            )

    return offset


def send_alert(message):
    if not ALERTS_ENABLED:
        return

    for chat_id in list(subscribers):
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

def discover_pairs():

    urls = [
        f"{DEX_BASE}/token-profiles/latest/v1",
        f"{DEX_BASE}/token-boosts/latest/v1",
        f"{DEX_BASE}/token-boosts/top/v1"
    ]

    addresses = set()

    for url in urls:
        data = get_json(url)

        if not data:
            continue

        if isinstance(data, dict):
            items = data.get("pairs", []) or data.get("tokens", [])
        else:
            items = data

        for item in items:
            chain = item.get("chainId")

            if chain != "solana":
                continue

            address = (
                item.get("tokenAddress")
                or item.get("address")
            )

            if address:
                addresses.add(address)

    pairs = []

    for address in list(addresses)[:200]:

        url = f"{DEX_BASE}/tokens/{quote(address)}"

        data = get_json(url)

        if not data:
            continue

        for pair in data.get("pairs", []) or []:

            if pair.get("chainId") != "solana":
                continue

            pairs.append(pair)

    return pairs


# ============================================================
# PAIR NORMALIZATION
# ============================================================

def choose_best_pairs(pairs):

    best = {}

    for pair in pairs:

        base = pair.get("baseToken", {})
        address = base.get("address")

        if not address:
            continue

        liquidity = (
            pair.get("liquidity", {})
            .get("usd")
            or 0
        )

        try:
            liquidity = float(liquidity)
        except:
            liquidity = 0

        current = best.get(address)

        if current is None:

            best[address] = pair

        else:

            current_liq = (
                current.get("liquidity", {})
                .get("usd")
                or 0
            )

            try:
                current_liq = float(current_liq)
            except:
                current_liq = 0

            if liquidity > current_liq:
                best[address] = pair

    return list(best.values())


# ============================================================
# SNAPSHOT
# ============================================================

def make_snapshot(pair):

    base = pair.get("baseToken", {})

    address = base.get("address")
    symbol = base.get("symbol", "?")

    market_cap = (
        pair.get("marketCap")
        or pair.get("fdv")
        or 0
    )

    liquidity = (
        pair.get("liquidity", {})
        .get("usd")
        or 0
    )

    volume_5m = (
        pair.get("volume", {})
        .get("m5")
        or 0
    )

    txns = (
        pair.get("txns", {})
        .get("m5", {})
        or {}
    )

    buys = txns.get("buys") or 0
    sells = txns.get("sells") or 0

    price = pair.get("priceUsd") or 0

    created = pair.get("pairCreatedAt") or 0

    try:
        market_cap = float(market_cap)
    except:
        market_cap = 0

    try:
        liquidity = float(liquidity)
    except:
        liquidity = 0

    try:
        volume_5m = float(volume_5m)
    except:
        volume_5m = 0

    try:
        price = float(price)
    except:
        price = 0

    try:
        buys = int(buys)
    except:
        buys = 0

    try:
        sells = int(sells)
    except:
        sells = 0

    try:
        created = int(created)
    except:
        created = 0

    return address, symbol, Snapshot(
        timestamp=time.time(),
        market_cap=market_cap,
        liquidity=liquidity,
        volume_5m=volume_5m,
        buys_5m=buys,
        sells_5m=sells,
        price=price,
        pair_created_at=created
    )


# ============================================================
# ANALYSIS
# ============================================================

def analyze(address, symbol, snapshot):

    history = histories.setdefault(
        address,
        TokenHistory()
    )

    history.add(snapshot)

    observations = len(history.snapshots)

    if observations < 4:
        state = "NEW"

    else:

        mc_move = history.market_cap_change(3)
        vol_accel = history.volume_change(3)
        txn_accel = history.transaction_change(3)

        range_pct = history.range_percent(
            min(20, observations)
        )

        high = history.recent_high(
            min(20, observations)
        )

        breakout = (
            snapshot.market_cap >= high
        )

        if range_pct <= 20:

            state = "CONSOLIDATION"

        elif vol_accel >= 1.20 and txn_accel >= 1.15:

            state = "EXPANSION"

        elif breakout and mc_move >= 3:

            state = "STRUCTURE BREAK"

        else:

            state = "OBSERVING"

        # ----------------------------------------------------
        # IGNITION
        # ----------------------------------------------------

        if (
            observations >= 6
            and
            range_pct <= 25
            and
            vol_accel >= 1.25
            and
            txn_accel >= 1.15
            and
            breakout
            and
            mc_move >= 5
            and
            history.liquidity_change(3) >= -5
        ):

            state = "IGNITION"

            log.info(
                "🔥 IGNITION candidate: %s | "
                "MC=$%.2fK | "
                "5mVol=$%.2fK | "
                "buys/sells=%s/%s | "
                "volAccel=%.2fx | "
                "txnAccel=%.2fx | "
                "MCmove=%.2f%% | "
                "liqMove=%.2f%% | "
                "range=%.2f%%",
                symbol,
                snapshot.market_cap / 1000,
                snapshot.volume_5m / 1000,
                snapshot.buys_5m,
                snapshot.sells_5m,
                vol_accel,
                txn_accel,
                mc_move,
                history.liquidity_change(3),
                range_pct
            )

            send_alert(
                f"🔥 IGNITION DETECTED\n\n"
                f"${symbol}\n"
                f"MC: ${snapshot.market_cap:,.0f}\n"
                f"5m Volume: ${snapshot.volume_5m:,.0f}\n"
                f"Buys/Sells: "
                f"{snapshot.buys_5m}/{snapshot.sells_5m}\n"
                f"Vol acceleration: {vol_accel:.2f}x\n"
                f"Transaction acceleration: {txn_accel:.2f}x\n\n"
                f"CA:\n{address}\n\n"
                f"⚠️ Experimental runner detection."
            )

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

    try:

        with open(STATE_FILE, "w") as f:
            json.dump(output, f)

        log.info(
            "Persistent state saved: %s tokens",
            len(output)
        )

    except Exception as e:

        log.warning(
            "Could not save state: %s",
            e
        )


def load_state():

    global histories

    if not os.path.exists(STATE_FILE):

        log.info(
            "No previous state found; starting fresh"
        )

        return

    try:

        with open(STATE_FILE, "r") as f:
            data = json.load(f)

        for address, value in data.items():

            history = TokenHistory()

            for raw in value.get("snapshots", []):

                history.add(
                    Snapshot(**raw)
                )

            histories[address] = history

        log.info(
            "Persistent state loaded: %s tokens",
            len(histories)
        )

    except Exception as e:

        log.warning(
            "Could not load state: %s",
            e
        )


# ============================================================
# SCAN
# ============================================================

def scan():

    pairs = discover_pairs()

    best_pairs = choose_best_pairs(pairs)

    discovered = len(pairs)
    unique = len(best_pairs)

    evaluated = 0

    rejected_mc = 0
    rejected_liquidity = 0
    rejected_volume = 0
    missing_data = 0

    ignition = 0

    for pair in best_pairs:

        address, symbol, snapshot = make_snapshot(pair)

        # ----------------------------------------------------
        # DATA QUALITY
        # ----------------------------------------------------

        if snapshot.market_cap <= 0:

            missing_data += 1
            continue

        # ----------------------------------------------------
        # COUNT EVERY TOKEN THAT REACHES THE FILTER
        # ----------------------------------------------------

        evaluated += 1

        # ----------------------------------------------------
        # MARKET CAP
        # ----------------------------------------------------

        if not (
            MIN_MC <= snapshot.market_cap <= MAX_MC
        ):

            rejected_mc += 1
            continue

        # ----------------------------------------------------
        # LIQUIDITY
        # ----------------------------------------------------

        if snapshot.liquidity < MIN_LIQUIDITY:

            rejected_liquidity += 1
            continue

        # ----------------------------------------------------
        # VOLUME
        # ----------------------------------------------------

        if snapshot.volume_5m < MIN_VOLUME_5M:

            rejected_volume += 1
            continue

        # ----------------------------------------------------
        # RUNNER ENGINE
        # ----------------------------------------------------

        state = analyze(
            address,
            symbol,
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

def shutdown(signum=None, frame=None):

    global running

    running = False

    log.info("Shutdown requested")


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

    log.info("Runner Engine v3.1 is online")

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

            offset = poll_telegram(offset)

            scan()

        except Exception as e:

            log.exception(
                "Scan error: %s",
                e
            )

        if not running:
            break

        time.sleep(SCAN_INTERVAL)

    save_state()

    log.info(
        "Runner Engine stopped"
    )


if __name__ == "__main__":
    main()
