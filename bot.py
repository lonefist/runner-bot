import json, os, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

BOT_VERSION = "V5.5-DEXSCREENER-BROAD-SEARCH"
DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"
STATE_FILE = "runner_state.json"

# DISCOVERY: intentionally broad; NO maximum age
MIN_MARKET_CAP = 30_000.0
MAX_MARKET_CAP = 350_000.0
MIN_LIQUIDITY = 30_000.0
MIN_PAIR_AGE_HOURS = 6.0
MAX_PAIR_AGE_HOURS = None

# SECOND-WAVE / RECOVERY
MIN_RECOVERY_VOL_MC = 0.035
MIN_CURRENT_BS = 1.35
MIN_PREVIOUS_BS = 1.30
MIN_PRICE_CHANGE_5M = 2.0
MAX_PRICE_CHANGE_5M = 40.0
MIN_VOLUME_EXPANSION = 0.08
CONFIRMATIONS_REQUIRED = 2
TRACKING_WINDOW_HOURS = 8.0

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "15"))
DISCOVERY_INTERVAL_SECONDS = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "300"))
VALIDATION_INTERVAL_SECONDS = int(os.getenv("VALIDATION_INTERVAL_SECONDS", "300"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALERTS_ENABLED = os.getenv("HEATING_ALERTS_ENABLED", "true").lower() == "true"

# DexScreener search is a query endpoint, not a full screener endpoint.
# Search returns matching pairs and is rate-limited at 300 requests/minute.
# We use a broad sweep and deduplicate by token/pair before filtering.
SEARCH_QUERIES = list("abcdefghijklmnopqrstuvwxyz0123456789") + [
    "sol", "usdc", "usdt", "usd", "coin", "token", "cat", "dog", "ai",
    "meme", "inu", "pepe", "frog", "moon", "pump", "doge", "shib",
    "woof", "baby", "bear", "bull", "ape", "kitty", "goat", "fish",
    "chad", "elon", "trump", "game", "arc", "agent", "ai16z", "solana",
]
SUPPLEMENTARY_ENDPOINTS = [
    "/token-profiles/latest/v1",
    "/token-boosts/latest/v1",
    "/token-boosts/top/v1",
    "/community-takeovers/latest/v1",
    "/ads/latest/v1",
]


def http_get_json(url: str, timeout: int = 20, retries: int = 2) -> Any:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"Accept":"application/json", "User-Agent":f"RunnerBot/{BOT_VERSION}"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            last = e
            time.sleep((2 if e.code == 429 else 1) + attempt)
        except Exception as e:
            last = e
            time.sleep(1 + attempt)
    print(f"HTTP ERROR | {url} | {last}")
    return None


def now() -> float: return time.time()
def utc_text() -> str: return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
def sf(v: Any, default=0.0) -> float:
    try: return float(v)
    except Exception: return default


def money(v: Any) -> str:
    v = sf(v)
    if v >= 1_000_000: return f"{v/1_000_000:.2f}M"
    if v >= 1_000: return f"{v/1_000:.1f}K"
    return f"{v:.0f}"


def age_text(hours: float) -> str:
    return f"{hours:.1f}h" if hours < 24 else f"{hours/24:.1f}d"


def market_cap(p: Dict[str,Any]) -> float:
    return sf(p.get("marketCap")) or sf(p.get("fdv"))


def liquidity(p: Dict[str,Any]) -> float:
    return sf((p.get("liquidity") or {}).get("usd"))


def age_hours(p: Dict[str,Any]) -> Optional[float]:
    x = p.get("pairCreatedAt")
    if x is None: return None
    try: return max(0.0, (now() - float(x)/1000) / 3600)
    except Exception: return None


def token_address(p: Dict[str,Any]) -> str:
    return str((p.get("baseToken") or {}).get("address") or "")


def pair_key(p: Dict[str,Any]) -> str:
    return str(p.get("pairAddress") or "")


def pair_quality(p: Dict[str,Any]) -> Tuple[float,float]:
    return (liquidity(p), market_cap(p))


def best_solana_pair(pairs: List[Dict[str,Any]]) -> Optional[Dict[str,Any]]:
    x = [p for p in pairs if isinstance(p,dict) and p.get("chainId") == "solana"]
    return max(x, key=pair_quality) if x else None


# ---------------- STATE ----------------
def default_state():
    return {"subscribers":[], "tracking":{}, "last_discovery":0, "last_validation":0, "telegram_offset":None}


def load_state():
    if not os.path.exists(STATE_FILE): return default_state()
    try:
        with open(STATE_FILE, encoding="utf-8") as f: s=json.load(f)
        b=default_state(); b.update(s if isinstance(s,dict) else {})
        return b
    except Exception as e:
        print(f"STATE LOAD ERROR | {e}"); return default_state()


STATE = load_state()


def save_state():
    try:
        tmp=STATE_FILE+".tmp"
        with open(tmp,"w",encoding="utf-8") as f: json.dump(STATE,f,indent=2)
        os.replace(tmp,STATE_FILE)
    except Exception as e: print(f"STATE SAVE ERROR | {e}")


# ---------------- TELEGRAM ----------------
def tg(method: str, payload: Dict[str,Any]):
    if not TELEGRAM_BOT_TOKEN: return None
    try:
        data=urllib.parse.urlencode(payload).encode()
        req=urllib.request.Request(f"{TELEGRAM_BASE}/bot{TELEGRAM_BOT_TOKEN}/{method}", data=data, headers={"Content-Type":"application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req,timeout=20) as r: return json.loads(r.read().decode())
    except Exception as e:
        print(f"TELEGRAM ERROR | {method} | {e}"); return None


def send(chat_id, text): tg("sendMessage", {"chat_id":str(chat_id),"text":text,"disable_web_page_preview":"true"})


def poll_telegram():
    if not TELEGRAM_BOT_TOKEN: return
    payload={"timeout":"1","allowed_updates":json.dumps(["message"])}
    if STATE.get("telegram_offset") is not None: payload["offset"]=str(STATE["telegram_offset"])
    r=tg("getUpdates",payload)
    if not r or not r.get("ok"): return
    for u in r.get("result",[]):
        STATE["telegram_offset"]=u["update_id"]+1
        m=u.get("message") or {}; chat=(m.get("chat") or {}).get("id"); text=(m.get("text") or "").strip()
        if chat is None: continue
        if text=="/start":
            if chat not in STATE["subscribers"]: STATE["subscribers"].append(chat)
            send(chat,"Runner Bot is online.\nDexScreener broad discovery is active.\nUse /status or /tracking.")
        elif text=="/stop":
            STATE["subscribers"]=[x for x in STATE["subscribers"] if x!=chat]; send(chat,"Alerts stopped for this chat.")
        elif text=="/alerts": send(chat,f"Alerts: {'ON' if chat in STATE['subscribers'] else 'OFF'}")
        elif text=="/status": send(chat,f"{BOT_VERSION}\nTracking: {len(STATE['tracking'])}\nDiscovery: every {DISCOVERY_INTERVAL_SECONDS}s\nValidation: every {VALIDATION_INTERVAL_SECONDS}s\nSubscribers: {len(STATE['subscribers'])}")
        elif text=="/tracking":
            if not STATE["tracking"]: send(chat,"Tracking: none.")
            else:
                lines=["TRACKING"]
                for x in STATE["tracking"].values(): lines.append(f"- {x.get('symbol','?')} | MC ${money(x.get('market_cap',0))} | liq ${money(x.get('liquidity',0))}")
                send(chat,"\n".join(lines))
    save_state()


# ---------------- DISCOVERY ----------------
def search(q: str) -> List[Dict[str,Any]]:
    u=f"{DEX_BASE}/latest/dex/search?q={urllib.parse.quote(q,safe='')}"
    d=http_get_json(u)
    return d.get("pairs",[]) if isinstance(d,dict) and isinstance(d.get("pairs"),list) else []


def supplementary() -> List[Dict[str,Any]]:
    out=[]
    for ep in SUPPLEMENTARY_ENDPOINTS:
        d=http_get_json(DEX_BASE+ep)
        if isinstance(d,list): out.extend(x for x in d if isinstance(x,dict))
    return out


def discover_raw() -> Tuple[List[Dict[str,Any]],Dict[str,int]]:
    pairs={}; stats={"queries":0,"search_results":0,"supplementary_tokens":0,"supplementary_pairs":0}
    for q in SEARCH_QUERIES:
        stats["queries"]+=1
        for p in search(q):
            stats["search_results"]+=1
            if p.get("chainId")!="solana": continue
            k=pair_key(p)
            if k and (k not in pairs or pair_quality(p)>pair_quality(pairs[k])): pairs[k]=p
    supp=supplementary(); stats["supplementary_tokens"]=len(supp)
    addrs=[]; seen=set()
    for x in supp:
        if x.get("chainId")!="solana": continue
        a=x.get("tokenAddress")
        if a and a not in seen: seen.add(a); addrs.append(a)
    # /tokens/v1/solana/{tokenAddresses} accepts up to 30 token addresses.
    for i in range(0,len(addrs),30):
        joined=",".join(addrs[i:i+30])
        d=http_get_json(f"{DEX_BASE}/tokens/v1/solana/{urllib.parse.quote(joined,safe=',')}")
        if not isinstance(d,list): continue
        for p in d:
            if not isinstance(p,dict) or p.get("chainId")!="solana": continue
            stats["supplementary_pairs"]+=1
            k=pair_key(p)
            if k and (k not in pairs or pair_quality(p)>pair_quality(pairs[k])): pairs[k]=p
    return list(pairs.values()),stats


def filter_discovery(raw):
    audit={"raw":len(raw),"solana":0,"unique":0,"mc_below":0,"mc_above":0,"liq_below":0,"age_below":0,"missing_age":0,"qualified":0}
    best={}
    for p in raw:
        if p.get("chainId")!="solana": continue
        audit["solana"]+=1; k=token_address(p)
        if k and (k not in best or pair_quality(p)>pair_quality(best[k])): best[k]=p
    audit["unique"]=len(best); out=[]
    for p in best.values():
        mc=market_cap(p); liq=liquidity(p); age=age_hours(p)
        if mc<MIN_MARKET_CAP: audit["mc_below"]+=1; continue
        if mc>MAX_MARKET_CAP: audit["mc_above"]+=1; continue
        if liq<MIN_LIQUIDITY: audit["liq_below"]+=1; continue
        if age is None: audit["missing_age"]+=1; continue
        if age<MIN_PAIR_AGE_HOURS: audit["age_below"]+=1; continue
        out.append(p)
    audit["qualified"]=len(out); return out,audit


def prune():
    cutoff=now()-TRACKING_WINDOW_HOURS*3600
    for k in list(STATE["tracking"]):
        if sf(STATE["tracking"][k].get("discovered_at"))<=cutoff:
            print(f"TRACKING EXPIRED | {STATE['tracking'][k].get('symbol','?')}"); del STATE["tracking"][k]


def discovery_cycle():
    print("="*68); print(f"DISCOVERY | {utc_text()}"); print("="*68); prune()
    raw,src=discover_raw(); qualified,a=filter_discovery(raw); before=len(STATE["tracking"]); new=0
    for p in qualified:
        k=token_address(p)
        if not k or k in STATE["tracking"]: continue
        b=p.get("baseToken") or {}; STATE["tracking"][k]={
            "address":k,"pair_address":p.get("pairAddress"),"symbol":b.get("symbol") or "?","name":b.get("name") or "?","url":p.get("url") or "","discovered_at":now(),"streak":0,"previous_bs":None,"previous_volume":None,"alerted":False,
            "market_cap":market_cap(p),"liquidity":liquidity(p)
        }; new+=1
        print(f"TRACKING NEW | {b.get('symbol','?')} | MC ${money(market_cap(p))} | liq ${money(liquidity(p))} | age {age_text(age_hours(p) or 0)}")
    print("DISCOVERY AUDIT")
    print(f"Search queries:          {src['queries']}")
    print(f"Search pair results:     {src['search_results']}")
    print(f"Supplementary tokens:    {src['supplementary_tokens']}")
    print(f"Supplementary Solana:    {src['supplementary_pairs']}")
    print(f"Raw unique pairs:        {a['raw']}")
    print(f"Solana pairs:            {a['solana']}")
    print(f"Unique tokens:           {a['unique']}")
    print(f"MC below $30K:           {a['mc_below']}")
    print(f"MC above $350K:          {a['mc_above']}")
    print(f"Liquidity below $30K:    {a['liq_below']}")
    print(f"Age below 6h:            {a['age_below']}")
    print(f"Missing age:             {a['missing_age']}")
    print(f"ALL DISCOVERY FILTERS:   {a['qualified']}")
    print(f"Already tracked before:  {before}")
    print(f"NEW TRACKING:            {new}")
    if qualified:
        print("QUALIFIED DISCOVERY TOKENS")
        for p in sorted(qualified,key=liquidity,reverse=True):
            print(f"  {(p.get('baseToken') or {}).get('symbol','?')} | MC ${money(market_cap(p))} | liq ${money(liquidity(p))} | age {age_text(age_hours(p) or 0)}")
    STATE["last_discovery"]=now(); save_state()


# ---------------- VALIDATION ----------------
def fetch_pair(address):
    d=http_get_json(f"{DEX_BASE}/token-pairs/v1/solana/{urllib.parse.quote(address,safe='')}")
    return best_solana_pair(d) if isinstance(d,list) else None


def validate(address,r):
    p=fetch_pair(address)
    if not p: print(f"VALIDATION | {r.get('symbol','?')} | no pair data"); return
    mc=market_cap(p); liq=liquidity(p); age=age_hours(p) or 0
    t=(p.get("txns") or {}).get("m5") or {}; buys=int(sf(t.get("buys"))); sells=int(sf(t.get("sells")))
    vol=sf((p.get("volume") or {}).get("m5")); price=sf((p.get("priceChange") or {}).get("m5"))
    bs=(buys/sells) if sells else (float("inf") if buys else 0.0); vmc=(vol/mc) if mc else 0
    prev_bs=r.get("previous_bs"); prev_vol=r.get("previous_volume")
    prev_pass=True if prev_bs is None else prev_bs>=MIN_PREVIOUS_BS
    exp=None if prev_vol is None or prev_vol<=0 else (vol-prev_vol)/prev_vol
    exp_pass=exp is not None and exp>=MIN_VOLUME_EXPANSION
    price_pass=MIN_PRICE_CHANGE_5M<=price<=MAX_PRICE_CHANGE_5M; bs_pass=bs>=MIN_CURRENT_BS; vmc_pass=vmc>=MIN_RECOVERY_VOL_MC
    recovery=price_pass and bs_pass and vmc_pass and prev_pass and exp_pass
    r["streak"]=int(r.get("streak",0))+1 if recovery else 0
    print(f"VALIDATION | {r.get('symbol','?')}")
    print(f"  MC: ${money(mc)}"); print(f"  Liquidity: ${money(liq)}"); print(f"  Age: {age_text(age)}")
    print(f"  5m Price: {price:+.2f}% {'PASS' if price_pass else 'FAIL'}")
    print(f"  B/S: {bs:.2f} ({buys}/{sells}) {'PASS' if bs_pass else 'FAIL'}")
    print(f"  5m Vol/MC: {vmc*100:.2f}% {'PASS' if vmc_pass else 'FAIL'}")
    print(f"  Previous B/S: {'N/A PASS' if prev_bs is None else f'{prev_bs:.2f} '+('PASS' if prev_pass else 'FAIL')}")
    print(f"  Volume expansion: {'N/A FAIL' if exp is None else f'{exp*100:+.1f}% '+('PASS' if exp_pass else 'FAIL')}")
    print(f"  Recovery: {'PASS' if recovery else 'FAIL'}"); print(f"  Streak: {r['streak']}/{CONFIRMATIONS_REQUIRED}")
    r["previous_bs"]=bs; r["previous_volume"]=vol; r["last_validation"]=now(); r["market_cap"]=mc; r["liquidity"]=liq
    if recovery and r["streak"]>=CONFIRMATIONS_REQUIRED and not r.get("alerted"):
        alert(p,r,bs,buys,sells,vol,vmc,price,exp or 0); r["alerted"]=True


def alert(p,r,bs,buys,sells,vol,vmc,price,exp):
    symbol=(p.get("baseToken") or {}).get("symbol") or r.get("symbol") or "?"; age=age_hours(p) or 0
    msg=(f"ð¥ RUNNER RECOVERY CONFIRMED\\n\\nToken: {symbol}\\nMC: ${money(market_cap(p))}\\nLiquidity: ${money(liquidity(p))}\\nAge: {age_text(age)}\\n\\n5m Price: {price:+.2f}%\\n5m Buys/Sells: {buys}/{sells}\\nB/S: {bs:.2f}\\n5m Volume: ${money(vol)}\\nVol/MC: {vmc*100:.2f}%\\nVolume expansion: {exp*100:+.1f}%\\nConfirmations: {CONFIRMATIONS_REQUIRED}/{CONFIRMATIONS_REQUIRED}\\n\\nDexScreener: {p.get('url') or r.get('url','')}\\n\\nâ ï¸ Scanner signal only. Not financial advice.")
    print("="*68); print(msg); print("="*68)
    if ALERTS_ENABLED:
        for chat in STATE["subscribers"]: send(chat,msg)


def validation_cycle():
    print("="*68); print("VALIDATION"); print("="*68); prune()
    for addr,r in list(STATE["tracking"].items()):
        try: validate(addr,r)
        except Exception as e: print(f"VALIDATION ERROR | {r.get('symbol','?')} | {e}")
        time.sleep(.15)
    STATE["last_validation"]=now(); save_state()


def config():
    print("="*68); print(f"RUNNER BOT {BOT_VERSION}"); print("="*68)
    print(f"MC: ${MIN_MARKET_CAP/1000:.0f}K-${MAX_MARKET_CAP/1000:.0f}K")
    print(f"Liquidity: ${MIN_LIQUIDITY/1000:.0f}K+"); print(f"Pair age: {MIN_PAIR_AGE_HOURS:.0f}h+"); print("Maximum age: NONE")
    print(f"Recovery Vol/MC: {MIN_RECOVERY_VOL_MC*100:.1f}%+"); print(f"Current B/S: {MIN_CURRENT_BS:.2f}+"); print(f"Previous B/S: {MIN_PREVIOUS_BS:.2f}+")
    print(f"5m Price: {MIN_PRICE_CHANGE_5M:+.0f}% to {MAX_PRICE_CHANGE_5M:+.0f}%"); print(f"Volume expansion: {MIN_VOLUME_EXPANSION*100:.0f}%+")
    print(f"Confirmation: {CONFIRMATIONS_REQUIRED} consecutive validations"); print(f"Tracking window: {TRACKING_WINDOW_HOURS:.1f}h"); print(f"Search queries: {len(SEARCH_QUERIES)}"); print("="*68)


def main():
    config(); print("WARNING | TELEGRAM_BOT_TOKEN missing" if not TELEGRAM_BOT_TOKEN else "Telegram: connected")
    last_d=0; last_v=0
    while True:
        try:
            poll_telegram(); t=now()
            if t-last_d>=DISCOVERY_INTERVAL_SECONDS: discovery_cycle(); last_d=now()
            if t-last_v>=VALIDATION_INTERVAL_SECONDS: validation_cycle(); last_v=now()
            time.sleep(max(1,SCAN_INTERVAL_SECONDS))
        except KeyboardInterrupt: print("STOPPED"); break
        except Exception as e: print(f"MAIN LOOP ERROR | {e}"); time.sleep(5)

if __name__ == "__main__": main()
