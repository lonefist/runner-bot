import json, os, time, urllib.parse, urllib.request
from datetime import datetime, timezone

# ============================================================
# RUNNER BOT V5.4 - DEXSCREENER BROAD DISCOVERY
# ============================================================
BOT_VERSION = "V5.4-DEXSCREENER-BROAD-DISCOVERY"
DEX_BASE = "https://api.dexscreener.com"
TELEGRAM_BASE = "https://api.telegram.org"
STATE_FILE = "runner_state.json"

# Discovery: user's DexScreener filter
MIN_MC = 30_000.0
MAX_MC = 350_000.0
MIN_LIQ = 30_000.0
MIN_AGE_H = 6.0
# NO MAXIMUM AGE

# Recovery
MIN_VOL_MC = 0.035
MIN_BS = 1.35
MIN_PREV_BS = 1.30
MIN_PRICE = 2.0
MAX_PRICE = 40.0
MIN_EXPANSION = 0.08
CONFIRMATIONS = 2

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "15"))
DISCOVERY_INTERVAL = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "300"))
VALIDATION_INTERVAL = int(os.getenv("VALIDATION_INTERVAL_SECONDS", "300"))
TRACKING_HOURS = 8.0
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALERTS_ENABLED = os.getenv("HEATING_ALERTS_ENABLED", "true").lower() == "true"
TIMEOUT = 20


def now(): return time.time()
def money(x):
    x=float(x)
    return f"${x/1e6:.2f}M" if x>=1e6 else f"${x/1e3:.1f}K" if x>=1e3 else f"${x:.0f}"
def agefmt(h): return f"{h:.1f}h" if h<24 else f"{h/24:.1f}d"
def num(x, d=0.0):
    try: return float(x) if x is not None else d
    except: return d


def get_json(url):
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"RunnerBot/5.4","Accept":"application/json"})
        with urllib.request.urlopen(req,timeout=TIMEOUT) as r: return json.loads(r.read().decode())
    except Exception as e:
        print(f"HTTP ERROR | {e}",flush=True); return None


def tg(method,payload):
    if not TOKEN: return None
    try:
        data=urllib.parse.urlencode(payload).encode()
        req=urllib.request.Request(f"{TELEGRAM_BASE}/bot{TOKEN}/{method}",data=data,headers={"User-Agent":"RunnerBot/5.4"})
        with urllib.request.urlopen(req,timeout=TIMEOUT) as r: return json.loads(r.read().decode())
    except Exception as e:
        print(f"TELEGRAM ERROR | {e}",flush=True); return None


def load():
    try:
        with open(STATE_FILE,encoding="utf-8") as f: s=json.load(f)
    except: s={}
    s.setdefault("tracked",{}); s.setdefault("subscribers",[]); s.setdefault("offset",0); s.setdefault("alerts_enabled",True)
    return s


def save(s):
    tmp=STATE_FILE+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(s,f,indent=2)
    os.replace(tmp,STATE_FILE)


def profiles():
    x=get_json(f"{DEX_BASE}/token-profiles/latest/v1")
    return x if isinstance(x,list) else []


def boosts():
    x=get_json(f"{DEX_BASE}/token-boosts/latest/v1")
    return x if isinstance(x,list) else []


def pairs(address):
    x=get_json(f"{DEX_BASE}/tokens/v1/solana/{address}")
    if isinstance(x,list): return x
    if isinstance(x,dict) and isinstance(x.get("pairs"),list): return x["pairs"]
    return []


def best_pair(ps):
    good=[]
    for p in ps:
        if not isinstance(p,dict) or p.get("chainId")!="solana": continue
        liq=num((p.get("liquidity") or {}).get("usd"))
        good.append((liq,p))
    return max(good,key=lambda z:z[0])[1] if good else None


def snap(p):
    base=p.get("baseToken") or {}; vol=p.get("volume") or {}; tx=p.get("txns") or {}; pc=p.get("priceChange") or {}
    mc=num(p.get("marketCap")) or num(p.get("fdv")); liq=num((p.get("liquidity") or {}).get("usd"))
    m5v=num(vol.get("m5")); h1v=num(vol.get("h1")); m5= num(pc.get("m5")); t=tx.get("m5") or {}
    buys=int(num(t.get("buys"))); sells=int(num(t.get("sells")))
    bs=buys/sells if sells else (float("inf") if buys else 0.0)
    created=num(p.get("pairCreatedAt")); age=max(0,(now()-created/1000)/3600) if created else 0
    return {"address":base.get("address",""),"symbol":base.get("symbol","?"),"name":base.get("name",base.get("symbol","?")),"url":p.get("url","") or "","pair":p.get("pairAddress",""),"mc":mc,"liq":liq,"age":age,"m5v":m5v,"h1v":h1v,"price":m5,"buys":buys,"sells":sells,"bs":bs,"volmc":m5v/mc if mc>0 else 0}


def discovery_addresses():
    out=set(); ps=profiles(); bs=boosts()
    for x in ps+bs:
        if not isinstance(x,dict) or str(x.get("chainId","")).lower()!="solana": continue
        a=str(x.get("tokenAddress") or x.get("address") or "").strip()
        if a: out.add(a)
    return list(out),len(ps),len(bs)


def discover(s):
    print("\n"+"="*68); print(f"DISCOVERY | {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S UTC}"); print("="*68)
    addrs,np,nb=discovery_addresses(); tr=s["tracked"]
    c={"raw":len(addrs),"mc":0,"liq":0,"age":0,"pass":0,"tracked":0,"new":0}; rej={}
    for a in addrs:
        p=best_pair(pairs(a))
        if not p: rej["no_solana_pair"]=rej.get("no_solana_pair",0)+1; continue
        x=snap(p)
        if x["mc"]>=MIN_MC: c["mc"]+=1
        if MIN_MC<=x["mc"]<=MAX_MC and x["liq"]>=MIN_LIQ: c["liq"]+=1
        if MIN_MC<=x["mc"]<=MAX_MC and x["liq"]>=MIN_LIQ and x["age"]>=MIN_AGE_H: c["age"]+=1
        if not (MIN_MC<=x["mc"]<=MAX_MC): rej["mc_outside_30k_350k"]=rej.get("mc_outside_30k_350k",0)+1; continue
        if x["liq"]<MIN_LIQ: rej["liq_below_30k"]=rej.get("liq_below_30k",0)+1; continue
        if x["age"]<MIN_AGE_H: rej["age_below_6h"]=rej.get("age_below_6h",0)+1; continue
        c["pass"]+=1
        if a in tr: c["tracked"]+=1; tr[a]["last_seen"]=now(); continue
        tr[a]={"address":a,"symbol":x["symbol"],"name":x["name"],"url":x["url"],"discovered":now(),"streak":0,"previous_bs":None,"previous_m5v":None,"alerted":False,"last":None}
        c["new"]+=1
        print(f"TRACKING NEW | {x['symbol']} | MC {money(x['mc'])} | liq {money(x['liq'])} | age {agefmt(x['age'])}",flush=True)
    print("\nDISCOVERY AUDIT")
    print(f"Raw candidates:        {c['raw']}"); print(f"MC >= $30K:            {c['mc']}"); print(f"MC $30K-$350K + liq:   {c['liq']}"); print(f"Age >= 6h:             {c['age']}"); print(f"All discovery filters: {c['pass']}"); print(f"Already tracked:       {c['tracked']}"); print(f"NEW TRACKING:          {c['new']}")
    if rej:
        print("\nREJECTION BREAKDOWN"); [print(f"{k}: {v}") for k,v in sorted(rej.items(),key=lambda z:z[1],reverse=True)]
    save(s)


def alert(x,record,s):
    text=(f"ð¥ RUNNER CONFIRMED\n\nToken: {x['name']} ({x['symbol']})\nMC: {money(x['mc'])}\nLiquidity: {money(x['liq'])}\nAge: {agefmt(x['age'])}\n5m Price: {x['price']:+.2f}%\n5m B/S: {x['bs']:.2f}\n5m Vol/MC: {x['volmc']*100:.2f}%\nConfirmations: {record['streak']}/{CONFIRMATIONS}\n\nDexScreener:\n{x['url'] or 'N/A'}\n\nâ ï¸ Scanner signal only. Verify the token before acting.")
    for cid in s["subscribers"]: tg("sendMessage",{"chat_id":str(cid),"text":text,"disable_web_page_preview":"true"})
    print(f"ALERT | {x['symbol']} | MC {money(x['mc'])} | B/S {x['bs']:.2f}",flush=True)


def validate(s):
    tr=s["tracked"]
    if not tr: return
    print("\n"+"="*68); print("VALIDATION"); print("="*68)
    expired=[]
    for a,r in list(tr.items()):
        if now()-r.get("discovered",now())>TRACKING_HOURS*3600: expired.append(a); continue
        p=best_pair(pairs(a))
        if not p: continue
        x=snap(p); prevbs=r.get("previous_bs"); prevv=r.get("previous_m5v")
        priceok=MIN_PRICE<=x["price"]<=MAX_PRICE
        bsok=x["bs"]>=MIN_BS and x["buys"]>x["sells"]
        vmok=x["volmc"]>=MIN_VOL_MC
        prevok=True if prevbs is None else prevbs>=MIN_PREV_BS
        exp=(x["m5v"]/prevv-1) if prevv and prevv>0 else 0
        expok=prevv is not None and exp>=MIN_EXPANSION
        passed=priceok and bsok and vmok and prevok and expok
        r["streak"]=r.get("streak",0)+1 if passed else 0
        print(f"\nVALIDATION | {x['symbol']}")
        print(f"  MC: {money(x['mc'])}"); print(f"  Liquidity: {money(x['liq'])}"); print(f"  Age: {agefmt(x['age'])}")
        print(f"  5m Price: {x['price']:+.2f}% {'PASS' if priceok else 'FAIL'}")
        print(f"  B/S: {x['bs']:.2f} ({x['buys']}/{x['sells']}) {'PASS' if bsok else 'FAIL'}")
        print(f"  5m Vol/MC: {x['volmc']*100:.2f}% {'PASS' if vmok else 'FAIL'}")
        print(f"  Previous B/S: {'N/A' if prevbs is None else f'{prevbs:.2f}'} {'PASS' if prevbs is None or prevok else 'FAIL'}")
        print(f"  Volume expansion: {'N/A' if prevv is None else f'{exp*100:+.1f}%'} {'PASS' if expok else 'FAIL'}")
        print(f"  Recovery: {'PASS' if passed else 'FAIL'}"); print(f"  Streak: {r['streak']}/{CONFIRMATIONS}")
        if passed and r["streak"]>=CONFIRMATIONS and not r.get("alerted") and ALERTS_ENABLED and s.get("alerts_enabled",True): alert(x,r,s); r["alerted"]=True
        r["previous_bs"]=x["bs"]; r["previous_m5v"]=x["m5v"]; r["last"]=x
    for a in expired: print(f"TRACKING EXPIRED | {tr[a].get('symbol','?')}"); del tr[a]
    save(s)


def telegram(s):
    if not TOKEN:return
    d=get_json(f"{TELEGRAM_BASE}/bot{TOKEN}/getUpdates?timeout=1&offset={s['offset']}")
    if not isinstance(d,dict) or not d.get("ok"):return
    for u in d.get("result",[]):
        s["offset"]=int(u.get("update_id",s["offset"]-1))+1
        m=u.get("message") or {}; cid=(m.get("chat") or {}).get("id"); cmd=str(m.get("text") or "").split()[0].lower() if m.get("text") else ""
        if cid is None:continue
        if cmd=="/start":
            if cid not in s["subscribers"]:s["subscribers"].append(cid)
            tg("sendMessage",{"chat_id":str(cid),"text":"Runner bot V5.4 is online. You are subscribed."})
        elif cmd=="/stop":
            if cid in s["subscribers"]:s["subscribers"].remove(cid)
            tg("sendMessage",{"chat_id":str(cid),"text":"Runner alerts stopped."})
        elif cmd=="/status": tg("sendMessage",{"chat_id":str(cid),"text":f"V5.4\nMC: $30K-$350K\nLiquidity: $30K+\nAge: 6h+\nMax age: none\nTracked: {len(s['tracked'])}\nConfirmations: {CONFIRMATIONS}"})
        elif cmd=="/tracking":
            lines=["CURRENT TRACKING"]+[f"{r.get('symbol','?')} | {r.get('streak',0)}/{CONFIRMATIONS}" for r in list(s['tracked'].values())[:30]]
            tg("sendMessage",{"chat_id":str(cid),"text":"\n".join(lines)})
    save(s)


def main():
    s=load(); print("="*68); print(f"RUNNER BOT {BOT_VERSION}"); print("="*68); print("MC: $30K-$350K"); print("Liquidity: $30K+"); print("Pair age: 6h+"); print("Maximum age: NONE"); print("Recovery Vol/MC: 3.5%+"); print("Current B/S: 1.35+"); print("Previous B/S: 1.30+"); print("5m Price: +2% to +40%"); print("Volume expansion: 8%+"); print("Confirmation: 2 consecutive validations"); print(f"Tracking window: {TRACKING_HOURS}h")
    ld=lv=0
    while True:
        t=now(); telegram(s)
        if t-ld>=DISCOVERY_INTERVAL: discover(s); ld=t
        if t-lv>=VALIDATION_INTERVAL: validate(s); lv=t
        time.sleep(max(1,SCAN_INTERVAL-(now()-t)))

if __name__=="__main__": main()
