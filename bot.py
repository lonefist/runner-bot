import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone


# ============================================================
# RUNNER BOT V1.0 — CLEAN FOUNDATION
# ============================================================

BOT_VERSION = "V1.0-CLEAN"

DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"

CHAIN = "solana"

MIN_MC = 20_000
MAX_MC = 150_000
MIN_LIQUIDITY = 5_000

SCAN_INTERVAL = int(
    os.getenv("SCAN_INTERVAL_SECONDS", "15")
)

DISCOVERY_INTERVAL = int(
    os.getenv("DISCOVERY_INTERVAL_SECONDS", "300")
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

HEATING_ALERTS_ENABLED = (
    os.getenv(
        "HEATING_ALERTS_ENABLED",
        "true"
    ).lower()
    == "true"
)

HTTP_TIMEOUT = 15


# ============================================================
# STARTUP
# ============================================================

print("=" * 64, flush=True)
print(
    f"RUNNER BOT {BOT_VERSION}",
    flush=True
)
print("=" * 64, flush=True)

print(
    f"CHAIN: {CHAIN}",
    flush=True
)

print(
    f"MC RANGE: ${MIN_MC:,}-${MAX_MC:,}",
    flush=True
)

print(
    f"MIN LIQUIDITY: ${MIN_LIQUIDITY:,}",
    flush=True
)

print(
    f"SCAN INTERVAL: {SCAN_INTERVAL}s",
    flush=True
)

print(
    f"DISCOVERY INTERVAL: {DISCOVERY_INTERVAL}s",
    flush=True
)

print(
    f"TELEGRAM TOKEN: "
    f"{'CONFIGURED' if TELEGRAM_BOT_TOKEN else 'MISSING'}",
    flush=True
)


# ============================================================
# HTTP
# ============================================================

def http_get_json(url, headers=None):

    request = urllib.request.Request(
        url,
        headers=headers or {
            "User-Agent": "RunnerBot/1.0"
        },
        method="GET",
    )

    with urllib.request.urlopen(
        request,
        timeout=HTTP_TIMEOUT,
    ) as response:

        body = response.read().decode(
            "utf-8",
            errors="replace",
        )

    return json.loads(body)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, payload=None):

    if not TELEGRAM_BOT_TOKEN:
        return None

    url = (
        f"{TELEGRAM_BASE}/bot"
        f"{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

    data = None

    if payload is not None:

        data = urllib.parse.urlencode(
            payload
        ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type":
                "application/x-www-form-urlencoded",
            "User-Agent":
                "RunnerBot/1.0",
        },
        method="POST" if data else "GET",
    )

    with urllib.request.urlopen(
        request,
        timeout=HTTP_TIMEOUT,
    ) as response:

        body = response.read().decode(
            "utf-8",
            errors="replace",
        )

    return json.loads(body)


def telegram_send(chat_id, text):

    return telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
        },
    )


def telegram_get_me():

    return telegram_request(
        "getMe"
    )


# ============================================================
# TELEGRAM POLLING
# ============================================================

def telegram_poll(offset=None):

    payload = {
        "timeout": 10,
    }

    if offset is not None:
        payload["offset"] = offset

    return telegram_request(
        "getUpdates",
        payload,
    )


# ============================================================
# DEXSCREENER DISCOVERY
# ============================================================

def dex_discover():

    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
        "/community-takeovers/latest/v1",
    ]

    found = {}

    for endpoint in endpoints:

        url = DEX_BASE + endpoint

        try:

            data = http_get_json(url)

            if not isinstance(data, list):
                continue

            for item in data:

                if not isinstance(item, dict):
                    continue

                if item.get("chainId") != CHAIN:
                    continue

                address = (
                    item.get("tokenAddress")
                    or item.get("address")
                )

                if not address:
                    continue

                found[address] = item

        except Exception as exc:

            print(
                f"[DEX DISCOVERY ERROR] "
                f"{endpoint}: {exc}",
                flush=True,
            )

    return list(found.values())


# ============================================================
# TOKEN PAIRS
# ============================================================

def get_token_pairs(address):

    url = (
        f"{DEX_BASE}/latest/dex/tokens/"
        f"{urllib.parse.quote(address)}"
    )

    data = http_get_json(url)

    pairs = data.get(
        "pairs",
        [],
    )

    return [
        pair
        for pair in pairs
        if pair.get("chainId") == CHAIN
    ]


# ============================================================
# HIGHEST LIQUIDITY PAIR
# ============================================================

def choose_pair(pairs):

    if not pairs:
        return None

    return max(
        pairs,
        key=lambda pair: float(
            (
                pair.get("liquidity") or {}
            ).get(
                "usd",
                0
            ) or 0
        ),
    )


# ============================================================
# SNAPSHOT
# ============================================================

def get_snapshot(address):

    pairs = get_token_pairs(
        address
    )

    pair = choose_pair(
        pairs
    )

    if not pair:
        return None

    base = pair.get(
        "baseToken"
    ) or {}

    quote = pair.get(
        "quoteToken"
    ) or {}

    liquidity = (
        pair.get("liquidity")
        or {}
    )

    volume = (
        pair.get("volume")
        or {}
    )

    txns = (
        pair.get("txns")
        or {}
    )

    m5 = (
        txns.get("m5")
        or {}
    )

    buys = int(
        m5.get(
            "buys",
            0
        ) or 0
    )

    sells = int(
        m5.get(
            "sells",
            0
        ) or 0
    )

    total_tx = buys + sells

    if sells > 0:
        bs_ratio = buys / sells
    elif buys > 0:
        bs_ratio = 999.0
    else:
        bs_ratio = 0.0

    market_cap = (
        pair.get("marketCap")
        or pair.get("fdv")
        or 0
    )

    price_change = (
        pair.get("priceChange")
        or {}
    ).get(
        "m5",
        0
    ) or 0

    volume_5m = (
        volume.get(
            "m5",
            0
        ) or 0
    )

    return {
        "address": address,

        "symbol": base.get(
            "symbol",
            "UNKNOWN"
        ),

        "name": base.get(
            "name",
            "Unknown"
        ),

        "market_cap": float(
            market_cap or 0
        ),

        "liquidity": float(
            liquidity.get(
                "usd",
                0
            ) or 0
        ),

        "volume_5m": float(
            volume_5m
        ),

        "buys_5m": buys,

        "sells_5m": sells,

        "transactions_5m":
            total_tx,

        "bs_ratio":
            bs_ratio,

        "price_change_5m":
            float(price_change),

        "pair_address":
            pair.get(
                "pairAddress",
                ""
            ),

        "dex_id":
            pair.get(
                "dexId",
                ""
            ),

        "url":
            pair.get(
                "url",
                ""
            ),
    }


# ============================================================
# BASIC FILTER
# ============================================================

def qualifies(snapshot):

    if not snapshot:
        return False

    if not (
        MIN_MC
        <= snapshot["market_cap"]
        <= MAX_MC
    ):
        return False

    if (
        snapshot["liquidity"]
        < MIN_LIQUIDITY
    ):
        return False

    return True


# ============================================================
# PRINT TOKEN
# ============================================================

def print_token(snapshot):

    print(
        "-" * 64,
        flush=True
    )

    print(
        f"{snapshot['name']} "
        f"(${snapshot['symbol']})",
        flush=True
    )

    print(
        f"CA: {snapshot['address']}",
        flush=True
    )

    print(
        f"MC: ${snapshot['market_cap']:,.0f}",
        flush=True
    )

    print(
        f"Liquidity: "
        f"${snapshot['liquidity']:,.0f}",
        flush=True
    )

    print(
        f"5m Volume: "
        f"${snapshot['volume_5m']:,.0f}",
        flush=True
    )

    print(
        f"5m Buys/Sells: "
        f"{snapshot['buys_5m']}/"
        f"{snapshot['sells_5m']}",
        flush=True
    )

    print(
        f"5m B/S: "
        f"{snapshot['bs_ratio']:.2f}",
        flush=True
    )

    print(
        f"5m Transactions: "
        f"{snapshot['transactions_5m']}",
        flush=True
    )

    print(
        f"5m Price: "
        f"{snapshot['price_change_5m']:+.2f}%",
        flush=True
    )

    print(
        f"DEX: "
        f"{snapshot['dex_id']}",
        flush=True
    )

    if snapshot["url"]:

        print(
            f"DEX URL: "
            f"{snapshot['url']}",
            flush=True
        )


# ============================================================
# DISCOVERY SCAN
# ============================================================

def run_discovery():

    print(
        "\n[DISCOVERY] Scanning...",
        flush=True
    )

    candidates = dex_discover()

    print(
        f"[DISCOVERY] "
        f"Found {len(candidates)} Solana candidates",
        flush=True
    )

    qualified = []

    for item in candidates:

        address = (
            item.get("tokenAddress")
            or item.get("address")
        )

        if not address:
            continue

        try:

            snapshot = get_snapshot(
                address
            )

        except Exception as exc:

            print(
                f"[TOKEN ERROR] "
                f"{address}: {exc}",
                flush=True,
            )

            continue

        if not snapshot:
            continue

        if qualifies(snapshot):

            qualified.append(
                snapshot
            )

    qualified.sort(
        key=lambda x: (
            x["market_cap"],
            -x["liquidity"],
        )
    )

    print(
        f"[DISCOVERY] "
        f"Qualified: {len(qualified)}",
        flush=True
    )

    for snapshot in qualified[:20]:

        print_token(
            snapshot
        )

    return qualified


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

def handle_updates(offset):

    if not TELEGRAM_BOT_TOKEN:
        return offset

    try:

        result = telegram_poll(
            offset
        )

        if not result:
            return offset

        if not result.get("ok"):
            print(
                f"[TELEGRAM ERROR] "
                f"{result}",
                flush=True,
            )
            return offset

        updates = result.get(
            "result",
            []
        )

        for update in updates:

            update_id = update.get(
                "update_id"
            )

            if update_id is not None:
                offset = update_id + 1

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
                message.get(
                    "text",
                    ""
                )
                or ""
            ).strip()

            if not chat_id:
                continue

            if text == "/start":

                telegram_send(
                    chat_id,
                    (
                        "Runner Bot is online.\n\n"
                        "V1 clean scanner active.\n"
                        "Solana market scanner connected."
                    ),
                )

                print(
                    f"[TELEGRAM] "
                    f"/start from {chat_id}",
                    flush=True,
                )

        return offset

    except Exception as exc:

        print(
            f"[TELEGRAM POLLING ERROR] "
            f"{exc}",
            flush=True,
        )

        return offset


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n[STARTUP] Testing Telegram...",
        flush=True
    )

    if TELEGRAM_BOT_TOKEN:

        try:

            me = telegram_get_me()

            if me and me.get("ok"):

                username = (
                    me.get("result", {})
                    .get("username", "")
                )

                print(
                    f"[TELEGRAM] Connected "
                    f"@{username}",
                    flush=True,
                )

            else:

                print(
                    "[TELEGRAM] "
                    "Connection failed",
                    flush=True,
                )

        except Exception as exc:

            print(
                f"[TELEGRAM ERROR] "
                f"{exc}",
                flush=True,
            )

    else:

        print(
            "[TELEGRAM] TOKEN MISSING",
            flush=True,
        )

    print(
        "\n[STARTUP] Testing DexScreener...",
        flush=True
    )

    try:

        test_candidates = dex_discover()

        print(
            f"[DEXSCREENER] Connected",
            flush=True
        )

        print(
            f"[DEXSCREENER] "
            f"Initial candidates: "
            f"{len(test_candidates)}",
            flush=True
        )

    except Exception as exc:

        print(
            f"[DEXSCREENER ERROR] "
            f"{exc}",
            flush=True,
        )

    print(
        "\n[STARTUP] Scanner running.",
        flush=True
    )

    last_discovery = 0
    telegram_offset = None

    while True:

        now = time.time()

        telegram_offset = handle_updates(
            telegram_offset
        )

        if (
            now - last_discovery
            >= DISCOVERY_INTERVAL
        ):

            try:

                run_discovery()

            except Exception as exc:

                print(
                    f"[DISCOVERY ERROR] "
                    f"{exc}",
                    flush=True,
                )

            last_discovery = now

        time.sleep(
            SCAN_INTERVAL
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
