import json, os, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

# ============================================================
# RUNNER BOT V5.7
# CANDIDATE QUALITY LOGGER
#
# V5.6 FILTERS ARE UNCHANGED.
# This version's purpose is DATA COLLECTION.
#
# It records:
#   - every validation snapshot
#   - B/S
#   - volume / MC
#   - volume expansion
#   - price
#   - liquidity
#   - MC
#   - buy/sell counts
#   - changes between validations
#   - alert timestamp
#   - post-alert snapshots
#
# No new quality filters are introduced yet.
# ============================================================

BOT_VERSION = "V5.7-CANDIDATE-QUALITY-LOGGER"

DEX = "https://api.dexscreener.com"
TG = "https://api.telegram.org"

STATE_FILE = "runner_state.json"
HISTORY_FILE = "runner_history.json"

# ============================================================
# V5.6 RULES — UNCHANGED
# ============================================================

MIN_MC = 30000.0
MAX_MC = 350000.0
MIN_LIQ = 30000.0
MIN_AGE_H = 6.0

MIN_VOL_MC = 0.035
MIN_BS = 1.35
MIN_PREV_BS = 1.30

MIN_PC5 = 2.0
MAX_PC5 = 40.0

MIN_VOL_EXP = 0.08

CONFIRMATIONS = 2
TRACK_H = 8.0

# ============================================================
# V5.7 DATA COLLECTION
# ============================================================

# How long after an alert we continue measuring the token.
POST_ALERT_H = 8.0

# We keep the existing 5-minute validation cadence.
VALIDATION = int(os.getenv("VALIDATION_INTERVAL_SECONDS", "300"))

SCAN = int(os.getenv("SCAN_INTERVAL_SECONDS", "15"))
DISCOVERY = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "300"))

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALERTS = os.getenv("HEATING_ALERTS_ENABLED", "true").lower() == "true"


# ============================================================
# DISCOVERY QUERIES
# ============================================================

QUERIES = [
    "solana","sol","usdc","usdt","coin","token","meme","cat","dog","doge",
    "shib","pepe","inu","frog","moon","pump","woof","baby","bear","bull",
    "ape","kitty","goat","fish","chad","elon","trump","game","arc","agent",
    "ai","ai16z","degen","based","bonk","wif","snek","penguin","rabbit",
    "horse","duck","bird","hamster","pizza","money","rich","gold","fire",
    "rocket","star","world","meta","bot","chain","labs","finance","swap",
    "club","dao","cult","mini","max","go","fun","cash","king","queen"
]

SUPPLEMENTARY = [
    "/token-profiles/latest/v1",
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
    "/community-takeovers/latest/v1",
    "/ads/latest/v1"
]


# ============================================================
# HELPERS
# ============================================================

def ts():
    return time.time()


def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def iso():
    return datetime.now(timezone.utc).isoformat()


def n(v, d=0.0):
    try:
        return float(v)
    except:
        return d


def money(v):
    v = n(v)

    if v >= 1e6:
        return f"{v / 1e6:.2f}M"

    if v >= 1e3:
        return f"{v / 1e3:.1f}K"

    return f"{v:.0f}"


def http(url, timeout=20, retries=2):

    last = None

    for i in range(retries + 1):

        try:

            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": f"RunnerBot/{BOT_VERSION}"
                }
            )

            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(
                    r.read().decode("utf-8", "replace")
                )

        except urllib.error.HTTPError as e:

            last = e

            if e.code == 429:
                time.sleep(2 + i * 2)

            elif 500 <= e.code < 600:
                time.sleep(1 + i)

            else:
                break

        except Exception as e:

            last = e
            time.sleep(1 + i)

    print(f"HTTP ERROR | {url} | {last}")

    return None


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "subscribers": [],
        "tracking": {},
        "telegram_offset": None,
        "last_discovery": 0,
        "last_validation": 0
    }


def load():

    try:

        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)

        d = default_state()

        if not isinstance(s, dict):
            return d

        for k, v in d.items():
            s.setdefault(k, v)

        return s

    except Exception:

        return default_state()


STATE = load()


# ============================================================
# HISTORY
# ============================================================

def load_history():

    try:

        with open(HISTORY_FILE, encoding="utf-8") as f:
            h = json.load(f)

        if isinstance(h, dict):
            return h

    except Exception:
        pass

    return {
        "version": BOT_VERSION,
        "created_at": iso(),
        "candidates": {}
    }


HISTORY = load_history()


def save_state():

    try:

        tmp = STATE_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE, f, indent=2)

        os.replace(tmp, STATE_FILE)

    except Exception as e:

        print(f"STATE SAVE ERROR | {e}")


def save_history():

    try:

        tmp = HISTORY_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(HISTORY, f, indent=2)

        os.replace(tmp, HISTORY_FILE)

    except Exception as e:

        print(f"HISTORY SAVE ERROR | {e}")


def save_all():

    save_state()
    save_history()


# ============================================================
# TELEGRAM
# ============================================================

def tg(method, payload):

    if not TOKEN:
        return None

    try:

        data = urllib.parse.urlencode(payload).encode()

        req = urllib.request.Request(
            f"{TG}/bot{TOKEN}/{method}",
            data=data,
            headers={
                "Content-Type":
                "application/x-www-form-urlencoded"
            }
        )

        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    except Exception as e:

        print(
            f"TELEGRAM ERROR | {method} | {e}"
        )

        return None


def send(cid, text):

    tg(
        "sendMessage",
        {
            "chat_id": str(cid),
            "text": text,
            "disable_web_page_preview": "true"
        }
    )


def poll():

    if not TOKEN:
        return

    q = {
        "timeout": "1",
        "allowed_updates":
            json.dumps(["message"])
    }

    if STATE.get("telegram_offset") is not None:
        q["offset"] = str(
            STATE["telegram_offset"]
        )

    r = tg("getUpdates", q)

    if not r or not r.get("ok"):
        return

    for u in r.get("result", []):

        STATE["telegram_offset"] = (
            u["update_id"] + 1
        )

        m = u.get("message") or {}
        c = m.get("chat") or {}

        cid = c.get("id")

        if cid is None:
            continue

        t = (m.get("text") or "").strip()

        if t == "/start":

            if cid not in STATE["subscribers"]:
                STATE["subscribers"].append(cid)

            send(
                cid,
                "Runner Bot is online.\n"
                "V5.7 Candidate Quality Logger is active."
            )

        elif t == "/stop":

            STATE["subscribers"] = [
                x for x in STATE["subscribers"]
                if x != cid
            ]

            send(cid, "Alerts stopped.")

        elif t == "/alerts":

            send(
                cid,
                f"Alerts: "
                f"{'ON' if cid in STATE['subscribers'] else 'OFF'}"
            )

        elif t == "/status":

            send(
                cid,
                f"{BOT_VERSION}\n"
                f"Tracking: {len(STATE['tracking'])}\n"
                f"Historical candidates: "
                f"{len(HISTORY['candidates'])}\n"
                f"Discovery: {DISCOVERY}s\n"
                f"Validation: {VALIDATION}s"
            )

        elif t == "/tracking":

            if not STATE["tracking"]:

                send(cid, "Tracking: none.")

            else:

                send(
                    cid,
                    "TRACKING\n" +
                    "\n".join(
                        f"- {r.get('symbol','?')} | "
                        f"MC ${money(r.get('mc',0))} | "
                        f"liq ${money(r.get('liq',0))}"
                        for r in
                        STATE["tracking"].values()
                    )
                )

        elif t == "/history":

            send(
                cid,
                "Historical candidates: "
                f"{len(HISTORY['candidates'])}\n\n"
                "Use the saved runner_history.json "
                "for the full dataset."
            )

    save_state()


# ============================================================
# DEXSCREENER DATA
# ============================================================

def mc(p):

    return (
        n(p.get("marketCap"))
        or n(p.get("fdv"))
    )


def liq(p):

    return n(
        (p.get("liquidity") or {}).get("usd")
    )


def age(p):

    x = p.get("pairCreatedAt")

    if x is None:
        return None

    return max(
        0,
        (ts() - n(x) / 1000) / 3600
    )


def key(p):

    return str(
        (p.get("baseToken") or {}).get(
            "address"
        ) or ""
    )


def tx5(p):

    x = (
        (p.get("txns") or {}).get("m5")
        or {}
    )

    return (
        int(n(x.get("buys"))),
        int(n(x.get("sells")))
    )


def vol5(p):

    return n(
        (p.get("volume") or {}).get("m5")
    )


def pc5(p):

    return n(
        (p.get("priceChange") or {}).get("m5")
    )


def price_usd(p):

    return n(p.get("priceUsd"))


def best(ps):

    ps = [
        p for p in ps
        if isinstance(p, dict)
        and p.get("chainId") == "solana"
    ]

    return (
        max(
            ps,
            key=lambda p: (
                liq(p),
                mc(p)
            )
        )
        if ps else None
    )


# ============================================================
# DISCOVERY
# ============================================================

def search(q):

    u = (
        f"{DEX}/latest/dex/search"
        f"?q={urllib.parse.quote(q, safe='')}"
    )

    d = http(u)

    if (
        isinstance(d, dict)
        and isinstance(d.get("pairs"), list)
    ):
        return d["pairs"]

    return []


def discover():

    pairs = {}

    c = {
        "queries": 0,
        "search_results": 0,
        "supp_tokens": 0,
        "supp_pairs": 0
    }

    for q in QUERIES:

        c["queries"] += 1

        for p in search(q):

            c["search_results"] += 1

            if p.get("chainId") != "solana":
                continue

            pa = p.get("pairAddress")

            if not pa:
                continue

            if (
                pa not in pairs
                or
                (liq(p), mc(p))
                >
                (
                    liq(pairs[pa]),
                    mc(pairs[pa])
                )
            ):
                pairs[pa] = p

    extras = []

    for ep in SUPPLEMENTARY:

        d = http(DEX + ep)

        if isinstance(d, list):
            extras.extend(
                x for x in d
                if isinstance(x, dict)
            )

    c["supp_tokens"] = len(extras)

    addrs = []
    seen = set()

    for x in extras:

        a = x.get("tokenAddress")

        if (
            x.get("chainId") == "solana"
            and a
            and a not in seen
        ):

            seen.add(a)
            addrs.append(a)

    for i in range(0, len(addrs), 30):

        batch = ",".join(
            addrs[i:i + 30]
        )

        d = http(
            f"{DEX}/tokens/v1/solana/"
            f"{urllib.parse.quote(batch, safe=',')}"
        )

        if not isinstance(d, list):
            continue

        for p in d:

            if (
                not isinstance(p, dict)
                or p.get("chainId") != "solana"
            ):
                continue

            c["supp_pairs"] += 1

            pa = p.get("pairAddress")

            if (
                pa
                and
                (
                    pa not in pairs
                    or
                    (liq(p), mc(p))
                    >
                    (
                        liq(pairs[pa]),
                        mc(pairs[pa])
                    )
                )
            ):
                pairs[pa] = p

    tokens = {}

    for p in pairs.values():

        k = key(p)

        if not k:
            continue

        if (
            k not in tokens
            or
            (liq(p), mc(p))
            >
            (
                liq(tokens[k]),
                mc(tokens[k])
            )
        ):
            tokens[k] = p

    a = {
        "raw": len(pairs),
        "unique": len(tokens),
        "mc_low": 0,
        "mc_high": 0,
        "liq_low": 0,
        "age_low": 0,
        "missing_age": 0,
        "qualified": 0
    }

    good = []

    for p in tokens.values():

        a0 = age(p)

        if mc(p) < MIN_MC:
            a["mc_low"] += 1
            continue

        if mc(p) > MAX_MC:
            a["mc_high"] += 1
            continue

        if liq(p) < MIN_LIQ:
            a["liq_low"] += 1
            continue

        if a0 is None:
            a["missing_age"] += 1
            continue

        if a0 < MIN_AGE_H:
            a["age_low"] += 1
            continue

        good.append(p)

    a["qualified"] = len(good)

    return good, c, a


# ============================================================
# HISTORY RECORD
# ============================================================

def create_history_record(p):

    k = key(p)
    b = p.get("baseToken") or {}

    record = {
        "address": k,
        "symbol": b.get("symbol") or "?",
        "name": b.get("name") or "?",
        "pair_address": p.get("pairAddress"),

        "discovered_at": iso(),

        "discovery": {
            "mc": mc(p),
            "liquidity": liq(p),
            "age_hours": age(p)
        },

        "validations": [],

        "alert": None,

        "post_alert": [],

        "outcome": {
            "classified": False,
            "class": None,
            "max_mc": None,
            "max_price_after_alert": None,
            "min_price_after_alert": None,
            "max_liquidity_after_alert": None,
            "min_liquidity_after_alert": None
        }
    }

    HISTORY["candidates"][k] = record

    return record


def history_record(addr, p=None):

    if addr in HISTORY["candidates"]:
        return HISTORY["candidates"][addr]

    if p is not None:
        return create_history_record(p)

    return None


# ============================================================
# SNAPSHOT
# ============================================================

def make_snapshot(p):

    buy, sell = tx5(p)

    v = vol5(p)
    m = mc(p)

    bs = (
        buy / sell
        if sell
        else (
            float("inf")
            if buy
            else 0
        )
    )

    vm = (
        v / m
        if m
        else 0
    )

    return {
        "timestamp": iso(),
        "unix": ts(),

        "mc": m,
        "liquidity": liq(p),
        "age_hours": age(p),

        "price_usd": price_usd(p),
        "price_change_5m": pc5(p),

        "buys_5m": buy,
        "sells_5m": sell,
        "bs_ratio": bs,

        "volume_5m": v,
        "volume_mc_ratio": vm,

        "pair_address": p.get("pairAddress"),
        "dex": p.get("dexId")
    }


# ============================================================
# DERIVED CHANGE METRICS
# ============================================================

def add_changes(snapshot, previous):

    if not previous:
        snapshot["changes"] = None
        return

    def pct_change(a, b):

        if b is None or b == 0:
            return None

        return (a - b) / abs(b)

    snapshot["changes"] = {

        "mc_pct":
            pct_change(
                snapshot["mc"],
                previous.get("mc")
            ),

        "liquidity_pct":
            pct_change(
                snapshot["liquidity"],
                previous.get("liquidity")
            ),

        "price_change_5m_delta":
            (
                snapshot["price_change_5m"]
                -
                previous.get(
                    "price_change_5m",
                    0
                )
            ),

        "bs_delta":
            (
                snapshot["bs_ratio"]
                -
                previous.get(
                    "bs_ratio",
                    0
                )
            ),

        "volume_mc_delta":
            (
                snapshot["volume_mc_ratio"]
                -
                previous.get(
                    "volume_mc_ratio",
                    0
                )
            ),

        "buys_delta":
            (
                snapshot["buys_5m"]
                -
                previous.get(
                    "buys_5m",
                    0
                )
            ),

        "sells_delta":
            (
                snapshot["sells_5m"]
                -
                previous.get(
                    "sells_5m",
                    0
                )
            ),

        "volume_delta":
            (
                snapshot["volume_5m"]
                -
                previous.get(
                    "volume_5m",
                    0
                )
            )
    }


# ============================================================
# VALIDATION
# ============================================================

def validate(addr, r):

    p = token_pair(addr)

    if not p:

        print(
            f"VALIDATION | "
            f"{r['symbol']} | no pair data"
        )

        return

    buy, sell = tx5(p)

    v = vol5(p)
    price = pc5(p)
    m = mc(p)

    bs = (
        buy / sell
        if sell
        else (
            float("inf")
            if buy
            else 0
        )
    )

    vm = (
        v / m
        if m
        else 0
    )

    prev_bs = r.get("previous_bs")
    previous_volume = r.get(
        "previous_volume"
    )

    prevpass = (
        prev_bs is None
        or prev_bs >= MIN_PREV_BS
    )

    exp = (
        None
        if previous_volume is None
        or previous_volume <= 0
        else
        (v - previous_volume)
        / previous_volume
    )

    exppass = (
        exp is not None
        and exp >= MIN_VOL_EXP
    )

    ppass = (
        MIN_PC5
        <= price
        <= MAX_PC5
    )

    bpass = bs >= MIN_BS
    vpass = vm >= MIN_VOL_MC

    ok = (
        ppass
        and bpass
        and vpass
        and prevpass
        and exppass
    )

    # --------------------------------------------------------
    # RECORD SNAPSHOT
    # --------------------------------------------------------

    h = history_record(addr, p)

    snapshot = make_snapshot(p)

    previous_snapshot = (
        h["validations"][-1]
        if h["validations"]
        else None
    )

    add_changes(
        snapshot,
        previous_snapshot
    )

    snapshot["filters"] = {
        "price_pass": ppass,
        "bs_pass": bpass,
        "vol_mc_pass": vpass,
        "previous_bs_pass": prevpass,
        "volume_expansion_pass": exppass,
        "overall_pass": ok
    }

    h["validations"].append(snapshot)

    # --------------------------------------------------------
    # TRACKING STATE
    # --------------------------------------------------------

    r["streak"] = (
        r.get("streak", 0) + 1
        if ok
        else 0
    )

    r["previous_bs"] = bs
    r["previous_volume"] = v

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    print(
        f"VALIDATION | {r['symbol']}"
    )

    print(
        f"  MC: ${money(m)}"
    )

    print(
        f"  Liquidity: ${money(liq(p))}"
    )

    print(
        f"  Age: {(age(p) or 0):.1f}h"
    )

    print(
        f"  5m Price: "
        f"{price:+.2f}% "
        f"{'PASS' if ppass else 'FAIL'}"
    )

    print(
        f"  B/S: "
        f"{bs:.2f} "
        f"({buy}/{sell}) "
        f"{'PASS' if bpass else 'FAIL'}"
    )

    print(
        f"  5m Vol/MC: "
        f"{vm*100:.2f}% "
        f"{'PASS' if vpass else 'FAIL'}"
    )

    if prev_bs is None:

        print(
            "  Previous B/S: N/A PASS"
        )

    else:

        print(
            f"  Previous B/S: "
            f"{prev_bs:.2f} "
            f"{'PASS' if prevpass else 'FAIL'}"
        )

    if exp is None:

        print(
            "  Volume expansion: "
            "N/A FAIL"
        )

    else:

        print(
            f"  Volume expansion: "
            f"{exp*100:+.1f}% "
            f"{'PASS' if exppass else 'FAIL'}"
        )

    print(
        f"  Recovery: "
        f"{'PASS' if ok else 'FAIL'}"
    )

    print(
        f"  Streak: "
        f"{r['streak']}/{CONFIRMATIONS}"
    )

    # --------------------------------------------------------
    # ALERT
    # --------------------------------------------------------

    if (
        ok
        and
        r["streak"] >= CONFIRMATIONS
        and
        not r.get("alerted")
    ):

        alert(
            p,
            r,
            bs,
            buy,
            sell,
            v,
            vm,
            price,
            exp
        )

    # --------------------------------------------------------
    # POST ALERT COLLECTION
    # --------------------------------------------------------

    collect_post_alert(h, p)

    save_all()


# ============================================================
# ALERT
# ============================================================

def alert(
    p,
    r,
    bs,
    buy,
    sell,
    v,
    vm,
    pc,
    exp
):

    b = p.get("baseToken") or {}

    msg = (
        "🔥 RUNNER RECOVERY CONFIRMED\n\n"

        f"Token: "
        f"{b.get('symbol') or r['symbol']}\n"

        f"MC: ${money(mc(p))}\n"

        f"Liquidity: "
        f"${money(liq(p))}\n"

        f"Age: "
        f"{(age(p) or 0):.1f}h\n\n"

        f"5m Price: "
        f"{pc:+.2f}%\n"

        f"5m Buys/Sells: "
        f"{buy}/{sell}\n"

        f"B/S: {bs:.2f}\n"

        f"5m Volume: "
        f"${money(v)}\n"

        f"Vol/MC: "
        f"{vm*100:.2f}%\n"

        f"Volume expansion: "
        f"{exp*100:+.1f}%\n"

        f"Confirmations: "
        f"{CONFIRMATIONS}/{CONFIRMATIONS}\n\n"

        f"DexScreener: "
        f"{p.get('url','')}\n\n"

        "⚠️ Scanner signal only. "
        "Not financial advice."
    )

    print("=" * 68)
    print(msg)
    print("=" * 68)

    if ALERTS:

        for cid in STATE["subscribers"]:
            send(cid, msg)

    # --------------------------------------------------------
    # SAVE EXACT ALERT SNAPSHOT
    # --------------------------------------------------------

    h = HISTORY["candidates"].get(
        key(p)
    )

    if h:

        h["alert"] = {
            "timestamp": iso(),
            "unix": ts(),
            "snapshot": make_snapshot(p)
        }

    r["alerted"] = True

    save_all()


# ============================================================
# POST-ALERT TRACKING
# ============================================================

def collect_post_alert(h, p):

    if not h:
        return

    if not h.get("alert"):
        return

    alert_time = n(
        h["alert"].get("unix")
    )

    if not alert_time:
        return

    elapsed = ts() - alert_time

    if elapsed < 0:
        return

    if elapsed > POST_ALERT_H * 3600:

        classify_outcome(h)

        return

    snapshot = make_snapshot(p)

    snapshot["minutes_after_alert"] = (
        elapsed / 60
    )

    h["post_alert"].append(
        snapshot
    )

    update_outcome_extremes(h, snapshot)


def update_outcome_extremes(
    h,
    snapshot
):

    o = h["outcome"]

    mc_value = snapshot["mc"]
    price = snapshot["price_change_5m"]
    liquidity = snapshot["liquidity"]

    if (
        o["max_mc"] is None
        or mc_value > o["max_mc"]
    ):
        o["max_mc"] = mc_value

    if (
        o["max_price_after_alert"] is None
        or price > o["max_price_after_alert"]
    ):
        o["max_price_after_alert"] = price

    if (
        o["min_price_after_alert"] is None
        or price < o["min_price_after_alert"]
    ):
        o["min_price_after_alert"] = price

    if (
        o["max_liquidity_after_alert"] is None
        or liquidity > o["max_liquidity_after_alert"]
    ):
        o["max_liquidity_after_alert"] = liquidity

    if (
        o["min_liquidity_after_alert"] is None
        or liquidity < o["min_liquidity_after_alert"]
    ):
        o["min_liquidity_after_alert"] = liquidity


# ============================================================
# OUTCOME CLASSIFICATION
#
# IMPORTANT:
# These are DATA LABELS, not trading recommendations.
#
# We deliberately keep them simple until enough data exists.
# ============================================================

def classify_outcome(h):

    o = h["outcome"]

    if o.get("classified"):
        return

    post = h.get("post_alert") or []

    if not post:
        return

    alert = h.get("alert") or {}
    base = alert.get("snapshot") or {}

    base_mc = n(base.get("mc"))
    base_liq = n(base.get("liquidity"))

    max_mc = n(o.get("max_mc"))
    min_liq = n(o.get("min_liquidity_after_alert"))

    if base_mc <= 0:
        return

    mc_multiple = max_mc / base_mc

    liq_ratio = (
        min_liq / base_liq
        if base_liq > 0
        else 0
    )

    # --------------------------------------------------------
    # These are deliberately broad labels.
    # We are NOT using them to create filters yet.
    # --------------------------------------------------------

    if (
        mc_multiple >= 1.50
        and
        liq_ratio >= 0.70
    ):

        label = "CONTINUATION"

    elif (
        mc_multiple < 0.80
        or
        liq_ratio < 0.50
    ):

        label = "FAILURE"

    else:

        label = "MIXED"

    o["classified"] = True
    o["class"] = label

    o["mc_multiple"] = mc_multiple
    o["min_liquidity_ratio"] = liq_ratio

    print(
        f"OUTCOME | "
        f"{h.get('symbol','?')} | "
        f"{label} | "
        f"MC x{mc_multiple:.2f} | "
        f"MinLiq {liq_ratio*100:.1f}%"
    )


# ============================================================
# TOKEN PAIR
# ============================================================

def token_pair(addr):

    d = http(
        f"{DEX}/token-pairs/v1/solana/"
        f"{urllib.parse.quote(addr, safe='')}"
    )

    return (
        best(d)
        if isinstance(d, list)
        else None
    )


# ============================================================
# TRACKING
# ============================================================

def prune():

    cutoff = (
        ts()
        -
        TRACK_H * 3600
    )

    for k in list(
        STATE["tracking"]
    ):

        if (
            n(
                STATE["tracking"][k]
                .get("discovered_at")
            )
            <= cutoff
        ):

            print(
                "TRACKING EXPIRED | "
                f"{STATE['tracking'][k].get('symbol','?')}"
            )

            del STATE["tracking"][k]


def add(good):

    new = 0

    for p in good:

        k = key(p)

        if (
            not k
            or
            k in STATE["tracking"]
        ):
            continue

        b = p.get("baseToken") or {}

        STATE["tracking"][k] = {

            "address": k,

            "pair_address":
                p.get("pairAddress"),

            "symbol":
                b.get("symbol") or "?",

            "name":
                b.get("name") or "?",

            "mc": mc(p),

            "liq": liq(p),

            "discovered_at": ts(),

            "streak": 0,

            "previous_bs": None,

            "previous_volume": None,

            "alerted": False
        }

        # Create history immediately.
        history_record(k, p)

        print(
            f"TRACKING NEW | "
            f"{b.get('symbol','?')} | "
            f"MC ${money(mc(p))} | "
            f"liq ${money(liq(p))} | "
            f"age {(age(p) or 0):.1f}h"
        )

        new += 1

    return new


# ============================================================
# DISCOVERY CYCLE
# ============================================================

def discovery_cycle():

    print("=" * 68)
    print(f"DISCOVERY | {utc()}")
    print("=" * 68)

    prune()

    good, c, a = discover()

    before = len(
        STATE["tracking"]
    )

    new = add(good)

    print("DISCOVERY AUDIT")

    print(
        f"Valid search queries:     "
        f"{c['queries']}"
    )

    print(
        f"Search pair results:      "
        f"{c['search_results']}"
    )

    print(
        f"Supplementary tokens:     "
        f"{c['supp_tokens']}"
    )

    print(
        f"Supplementary pairs:      "
        f"{c['supp_pairs']}"
    )

    print(
        f"Raw unique pairs:         "
        f"{a['raw']}"
    )

    print(
        f"Unique tokens:            "
        f"{a['unique']}"
    )

    print(
        f"MC below $30K:            "
        f"{a['mc_low']}"
    )

    print(
        f"MC above $350K:           "
        f"{a['mc_high']}"
    )

    print(
        f"Liquidity below $30K:     "
        f"{a['liq_low']}"
    )

    print(
        f"Age below 6h:             "
        f"{a['age_low']}"
    )

    print(
        f"Missing age:              "
        f"{a['missing_age']}"
    )

    print(
        f"ALL DISCOVERY FILTERS:    "
        f"{a['qualified']}"
    )

    print(
        f"Already tracked before:   "
        f"{before}"
    )

    print(
        f"NEW TRACKING:             "
        f"{new}"
    )

    for p in sorted(
        good,
        key=liq,
        reverse=True
    ):

        b = p.get("baseToken") or {}

        print(
            f"QUALIFIED | "
            f"{b.get('symbol','?')} | "
            f"MC ${money(mc(p))} | "
            f"liq ${money(liq(p))} | "
            f"age {(age(p) or 0):.1f}h"
        )

    STATE["last_discovery"] = ts()

    save_all()


# ============================================================
# VALIDATION CYCLE
# ============================================================

def validation_cycle():

    print("=" * 68)
    print("VALIDATION")
    print("=" * 68)

    prune()

    for k, r in list(
        STATE["tracking"].items()
    ):

        try:

            validate(k, r)

        except Exception as e:

            print(
                f"VALIDATION ERROR | "
                f"{r.get('symbol','?')} | "
                f"{e}"
            )

        time.sleep(.15)

    STATE["last_validation"] = ts()

    save_all()


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 68)

    print(
        f"RUNNER BOT {BOT_VERSION}"
    )

    print("=" * 68)

    print(
        "MC: $30K-$350K"
    )

    print(
        "Liquidity: $30K+"
    )

    print(
        "Pair age: 6h+"
    )

    print(
        "Maximum age: NONE"
    )

    print(
        "Recovery Vol/MC: 3.5%+"
    )

    print(
        "Current B/S: 1.35+"
    )

    print(
        "Previous B/S: 1.30+"
    )

    print(
        "5m Price: +2% to +40%"
    )

    print(
        "Volume expansion: 8%+"
    )

    print(
        "Confirmation: "
        "2 consecutive validations"
    )

    print(
        "Tracking window: 8.0h"
    )

    print(
        "Post-alert logging: 8.0h"
    )

    print(
        f"Valid search queries: "
        f"{len(QUERIES)}"
    )

    print(
        f"History file: "
        f"{HISTORY_FILE}"
    )

    print("=" * 68)

    print(
        "Telegram: connected"
        if TOKEN
        else
        "Telegram: NOT CONNECTED"
    )

    ld = 0
    lv = 0

    while True:

        try:

            poll()

            t = ts()

            if (
                t - ld
                >= DISCOVERY
            ):

                discovery_cycle()
                ld = ts()

            t = ts()

            if (
                t - lv
                >= VALIDATION
            ):

                validation_cycle()
                lv = ts()

            time.sleep(
                max(1, SCAN)
            )

        except KeyboardInterrupt:

            print("STOPPED")
            break

        except Exception as e:

            print(
                f"MAIN LOOP ERROR | {e}"
            )

            time.sleep(5)


if __name__ == "__main__":
    main()
