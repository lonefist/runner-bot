# RUNNER BOT V5.6-DEXSCREENER-VALID-SEARCH
# Full replacement for bot.py.
# Discovery uses only documented DexScreener endpoints.
# User filters/recovery rules are unchanged.

import json, os, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

BOT_VERSION="V5.6-DEXSCREENER-VALID-SEARCH"
DEX="https://api.dexscreener.com"
TG="https://api.telegram.org"
STATE_FILE="runner_state.json"

MIN_MC=30000.0
MAX_MC=350000.0
MIN_LIQ=30000.0
MIN_AGE_H=6.0

MIN_VOL_MC=.035
MIN_BS=1.35
MIN_PREV_BS=1.30
MIN_PC5=2.0
MAX_PC5=40.0
MIN_VOL_EXP=.08
CONFIRMATIONS=2
TRACK_H=8.0

SCAN=int(os.getenv("SCAN_INTERVAL_SECONDS","15"))
DISCOVERY=int(os.getenv("DISCOVERY_INTERVAL_SECONDS","300"))
VALIDATION=int(os.getenv("VALIDATION_INTERVAL_SECONDS","300"))
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
ALERTS=os.getenv("HEATING_ALERTS_ENABLED","true").lower()=="true"

# Meaningful queries only. One-character queries caused HTTP 400s.
# Search returns matching pairs; it is not a paginated copy of the website screener.
QUERIES=[
"solana","sol","usdc","usdt","coin","token","meme","cat","dog","doge",
"shib","pepe","inu","frog","moon","pump","woof","baby","bear","bull",
"ape","kitty","goat","fish","chad","elon","trump","game","arc","agent",
"ai","ai16z","degen","based","bonk","wif","snek","penguin","rabbit",
"horse","duck","bird","hamster","pizza","money","rich","gold","fire",
"rocket","star","world","meta","bot","chain","labs","finance","swap",
"club","dao","cult","mini","max","go","fun","cash","king","queen"
]

SUPPLEMENTARY=[
"/token-profiles/latest/v1",
"/token-boosts/latest/v1",
"/token-boosts/top/v1",
"/community-takeovers/latest/v1",
"/ads/latest/v1"
]

def ts(): return time.time()
def utc(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
def n(v,d=0.0):
    try:return float(v)
    except:return d

def http(url, timeout=20, retries=2):
    last=None
    for i in range(retries+1):
        try:
            req=urllib.request.Request(url,headers={"Accept":"application/json","User-Agent":f"RunnerBot/{BOT_VERSION}"})
            with urllib.request.urlopen(req,timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8","replace"))
        except urllib.error.HTTPError as e:
            last=e
            if e.code==429: time.sleep(2+i*2)
            elif 500<=e.code<600: time.sleep(1+i)
            else: break
        except Exception as e:
            last=e; time.sleep(1+i)
    print(f"HTTP ERROR | {url} | {last}")
    return None

def default_state():
    return {"subscribers":[],"tracking":{},"telegram_offset":None,"last_discovery":0,"last_validation":0}

def load():
    try:
        with open(STATE_FILE,encoding="utf-8") as f:s=json.load(f)
        d=default_state()
        if not isinstance(s,dict):return d
        for k,v in d.items():s.setdefault(k,v)
        return s
    except Exception:return default_state()

STATE=load()

def save():
    try:
        tmp=STATE_FILE+".tmp"
        with open(tmp,"w",encoding="utf-8") as f:json.dump(STATE,f,indent=2)
        os.replace(tmp,STATE_FILE)
    except Exception as e:print(f"STATE SAVE ERROR | {e}")

def tg(method,payload):
    if not TOKEN:return None
    try:
        data=urllib.parse.urlencode(payload).encode()
        req=urllib.request.Request(f"{TG}/bot{TOKEN}/{method}",data=data,headers={"Content-Type":"application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
    except Exception as e:
        print(f"TELEGRAM ERROR | {method} | {e}");return None

def send(cid,text):
    tg("sendMessage",{"chat_id":str(cid),"text":text,"disable_web_page_preview":"true"})

def poll():
    if not TOKEN:return
    q={"timeout":"1","allowed_updates":json.dumps(["message"])}
    if STATE.get("telegram_offset") is not None:q["offset"]=str(STATE["telegram_offset"])
    r=tg("getUpdates",q)
    if not r or not r.get("ok"):return
    for u in r.get("result",[]):
        STATE["telegram_offset"]=u["update_id"]+1
        m=u.get("message") or {};c=m.get("chat") or {};cid=c.get("id")
        if cid is None:continue
        t=(m.get("text") or "").strip()
        if t=="/start":
            if cid not in STATE["subscribers"]:STATE["subscribers"].append(cid)
            send(cid,"Runner Bot is online.\nV5.6 DexScreener valid-search discovery is active.")
        elif t=="/stop":
            STATE["subscribers"]=[x for x in STATE["subscribers"] if x!=cid];send(cid,"Alerts stopped.")
        elif t=="/alerts":send(cid,f"Alerts: {'ON' if cid in STATE['subscribers'] else 'OFF'}")
        elif t=="/status":send(cid,f"{BOT_VERSION}\nTracking: {len(STATE['tracking'])}\nDiscovery: {DISCOVERY}s\nValidation: {VALIDATION}s")
        elif t=="/tracking":
            if not STATE["tracking"]:send(cid,"Tracking: none.")
            else:send(cid,"TRACKING\n"+"\n".join(f"- {r.get('symbol','?')} | MC ${money(r.get('mc',0))} | liq ${money(r.get('liq',0))}" for r in STATE["tracking"].values()))
    save()

def money(v):
    v=n(v)
    return f"{v/1e6:.2f}M" if v>=1e6 else f"{v/1e3:.1f}K" if v>=1e3 else f"{v:.0f}"

def mc(p):return n(p.get("marketCap")) or n(p.get("fdv"))
def liq(p):return n((p.get("liquidity") or {}).get("usd"))
def age(p):
    x=p.get("pairCreatedAt")
    if x is None:return None
    return max(0,(ts()-n(x)/1000)/3600)
def key(p):return str((p.get("baseToken") or {}).get("address") or "")
def tx5(p):
    x=(p.get("txns") or {}).get("m5") or {}
    return int(n(x.get("buys"))),int(n(x.get("sells")))
def vol5(p):return n((p.get("volume") or {}).get("m5"))
def pc5(p):return n((p.get("priceChange") or {}).get("m5"))

def search(q):
    u=f"{DEX}/latest/dex/search?q={urllib.parse.quote(q,safe='')}"
    d=http(u)
    return d.get("pairs",[]) if isinstance(d,dict) and isinstance(d.get("pairs"),list) else []

def best(ps):
    ps=[p for p in ps if isinstance(p,dict) and p.get("chainId")=="solana"]
    return max(ps,key=lambda p:(liq(p),mc(p))) if ps else None

def discover():
    pairs={}
    c={"queries":0,"search_results":0,"supp_tokens":0,"supp_pairs":0}
    for q in QUERIES:
        c["queries"]+=1
        for p in search(q):
            c["search_results"]+=1
            if p.get("chainId")!="solana":continue
            pa=p.get("pairAddress")
            if not pa:continue
            if pa not in pairs or (liq(p),mc(p))>(liq(pairs[pa]),mc(p)):pairs[pa]=p

    extras=[]
    for ep in SUPPLEMENTARY:
        d=http(DEX+ep)
        if isinstance(d,list):extras.extend(x for x in d if isinstance(x,dict))
    c["supp_tokens"]=len(extras)

    addrs=[];seen=set()
    for x in extras:
        a=x.get("tokenAddress")
        if x.get("chainId")=="solana" and a and a not in seen:
            seen.add(a);addrs.append(a)

    for i in range(0,len(addrs),30):
        batch=",".join(addrs[i:i+30])
        d=http(f"{DEX}/tokens/v1/solana/{urllib.parse.quote(batch,safe=',')}")
        if not isinstance(d,list):continue
        for p in d:
            if not isinstance(p,dict) or p.get("chainId")!="solana":continue
            c["supp_pairs"]+=1
            pa=p.get("pairAddress")
            if pa and (pa not in pairs or (liq(p),mc(p))>(liq(pairs[pa]),mc(pairs[pa]))):pairs[pa]=p

    tokens={}
    for p in pairs.values():
        k=key(p)
        if k and (k not in tokens or (liq(p),mc(p))>(liq(tokens[k]),mc(tokens[k]))):tokens[k]=p

    a={"raw":len(pairs),"unique":len(tokens),"mc_low":0,"mc_high":0,"liq_low":0,"age_low":0,"missing_age":0,"qualified":0}
    good=[]
    for p in tokens.values():
        a0=age(p)
        if mc(p)<MIN_MC:a["mc_low"]+=1;continue
        if mc(p)>MAX_MC:a["mc_high"]+=1;continue
        if liq(p)<MIN_LIQ:a["liq_low"]+=1;continue
        if a0 is None:a["missing_age"]+=1;continue
        if a0<MIN_AGE_H:a["age_low"]+=1;continue
        good.append(p)
    a["qualified"]=len(good)
    return good,c,a

def prune():
    cutoff=ts()-TRACK_H*3600
    for k in list(STATE["tracking"]):
        if n(STATE["tracking"][k].get("discovered_at"))<=cutoff:
            print(f"TRACKING EXPIRED | {STATE['tracking'][k].get('symbol','?')}")
            del STATE["tracking"][k]

def add(good):
    new=0
    for p in good:
        k=key(p)
        if not k or k in STATE["tracking"]:continue
        b=p.get("baseToken") or {}
        STATE["tracking"][k]={"address":k,"pair_address":p.get("pairAddress"),"symbol":b.get("symbol") or "?","name":b.get("name") or "?","mc":mc(p),"liq":liq(p),"discovered_at":ts(),"streak":0,"previous_bs":None,"previous_volume":None,"alerted":False}
        print(f"TRACKING NEW | {b.get('symbol','?')} | MC ${money(mc(p))} | liq ${money(liq(p))} | age {(age(p) or 0):.1f}h")
        new+=1
    return new

def token_pair(addr):
    d=http(f"{DEX}/token-pairs/v1/solana/{urllib.parse.quote(addr,safe='')}")
    return best(d) if isinstance(d,list) else None

def alert(p,r,bs,buy,sell,v,vm,pc,exp):
    b=p.get("baseToken") or {}
    msg=(f"ð¥ RUNNER RECOVERY CONFIRMED\n\nToken: {b.get('symbol') or r['symbol']}\nMC: ${money(mc(p))}\nLiquidity: ${money(liq(p))}\nAge: {(age(p) or 0):.1f}h\n\n5m Price: {pc:+.2f}%\n5m Buys/Sells: {buy}/{sell}\nB/S: {bs:.2f}\n5m Volume: ${money(v)}\nVol/MC: {vm*100:.2f}%\nVolume expansion: {exp*100:+.1f}%\nConfirmations: {CONFIRMATIONS}/{CONFIRMATIONS}\n\nDexScreener: {p.get('url','')}\n\nâ ï¸ Scanner signal only. Not financial advice.")
    print("="*68);print(msg);print("="*68)
    if ALERTS:
        for cid in STATE["subscribers"]:send(cid,msg)

def validate(addr,r):
    p=token_pair(addr)
    if not p:print(f"VALIDATION | {r['symbol']} | no pair data");return
    buy,sell=tx5(p);v=vol5(p);price=pc5(p);m=mc(p)
    bs=buy/sell if sell else (float("inf") if buy else 0)
    vm=v/m if m else 0
    prev=r.get("previous_bs");pv=r.get("previous_volume")
    prevpass=prev is None or prev>=MIN_PREV_BS
    exp=None if pv is None or pv<=0 else (v-pv)/pv
    exppass=exp is not None and exp>=MIN_VOL_EXP
    ppass=MIN_PC5<=price<=MAX_PC5; bpass=bs>=MIN_BS; vpass=vm>=MIN_VOL_MC
    ok=ppass and bpass and vpass and prevpass and exppass
    r["streak"]=r.get("streak",0)+1 if ok else 0
    print(f"VALIDATION | {r['symbol']}")
    print(f"  MC: ${money(m)}");print(f"  Liquidity: ${money(liq(p))}");print(f"  Age: {(age(p) or 0):.1f}h")
    print(f"  5m Price: {price:+.2f}% {'PASS' if ppass else 'FAIL'}")
    print(f"  B/S: {bs:.2f} ({buy}/{sell}) {'PASS' if bpass else 'FAIL'}")
    print(f"  5m Vol/MC: {vm*100:.2f}% {'PASS' if vpass else 'FAIL'}")
    print(f"  Previous B/S: {'N/A PASS' if prev is None else f'{prev:.2f} '+('PASS' if prevpass else 'FAIL')}")
    print(f"  Volume expansion: {'N/A FAIL' if exp is None else f'{exp*100:+.1f}% '+('PASS' if exppass else 'FAIL')}")
    print(f"  Recovery: {'PASS' if ok else 'FAIL'}");print(f"  Streak: {r['streak']}/{CONFIRMATIONS}")
    r["previous_bs"]=bs;r["previous_volume"]=v
    if ok and r["streak"]>=CONFIRMATIONS and not r.get("alerted"):
        alert(p,r,bs,buy,sell,v,vm,price,exp);r["alerted"]=True

def discovery_cycle():
    print("="*68);print(f"DISCOVERY | {utc()}");print("="*68);prune()
    good,c,a=discover();before=len(STATE["tracking"]);new=add(good)
    print("DISCOVERY AUDIT")
    print(f"Valid search queries:     {c['queries']}")
    print(f"Search pair results:      {c['search_results']}")
    print(f"Supplementary tokens:     {c['supp_tokens']}")
    print(f"Supplementary pairs:      {c['supp_pairs']}")
    print(f"Raw unique pairs:         {a['raw']}")
    print(f"Unique tokens:            {a['unique']}")
    print(f"MC below $30K:            {a['mc_low']}")
    print(f"MC above $350K:           {a['mc_high']}")
    print(f"Liquidity below $30K:     {a['liq_low']}")
    print(f"Age below 6h:             {a['age_low']}")
    print(f"Missing age:              {a['missing_age']}")
    print(f"ALL DISCOVERY FILTERS:    {a['qualified']}")
    print(f"Already tracked before:   {before}")
    print(f"NEW TRACKING:             {new}")
    for p in sorted(good,key=liq,reverse=True):
        b=p.get("baseToken") or {}
        print(f"QUALIFIED | {b.get('symbol','?')} | MC ${money(mc(p))} | liq ${money(liq(p))} | age {(age(p) or 0):.1f}h")
    STATE["last_discovery"]=ts();save()

def validation_cycle():
    print("="*68);print("VALIDATION");print("="*68);prune()
    for k,r in list(STATE["tracking"].items()):
        try:validate(k,r)
        except Exception as e:print(f"VALIDATION ERROR | {r.get('symbol','?')} | {e}")
        time.sleep(.15)
    STATE["last_validation"]=ts();save()

def main():
    print("="*68);print(f"RUNNER BOT {BOT_VERSION}");print("="*68)
    print("MC: $30K-$350K");print("Liquidity: $30K+");print("Pair age: 6h+");print("Maximum age: NONE")
    print("Recovery Vol/MC: 3.5%+");print("Current B/S: 1.35+");print("Previous B/S: 1.30+")
    print("5m Price: +2% to +40%");print("Volume expansion: 8%+");print("Confirmation: 2 consecutive validations")
    print("Tracking window: 8.0h");print(f"Valid search queries: {len(QUERIES)}");print("="*68)
    print("Telegram: connected" if TOKEN else "Telegram: NOT CONNECTED")
    ld=lv=0
    while True:
        try:
            poll();t=ts()
            if t-ld>=DISCOVERY:discovery_cycle();ld=ts()
            t=ts()
            if t-lv>=VALIDATION:validation_cycle();lv=ts()
            time.sleep(max(1,SCAN))
        except KeyboardInterrupt:print("STOPPED");break
        except Exception as e:print(f"MAIN LOOP ERROR | {e}");time.sleep(5)

if __name__=="__main__":main()
