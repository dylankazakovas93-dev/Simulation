import json
# (key, firm, plan, size, target, dd, trail, dailyLoss, minDaysEval, minDaysPayout, minDailyProfit,
#  cons, split, firstThresh, buffer, minPayout, capSchedule, payoutPct, minBetween, mode, maxPayouts,
#  evalFee, actFee, monthly, verified)  -- lockAt = size for all (EOD/intraday trailing lock at start)
P=[]
def add(**k): P.append(k)

# ---- APEX EOD (verified) ----
apex=[(25000,1500,1000,500,100,[1000]*6),(50000,3000,2000,1000,250,[1500,1500,2000,2500,2500,3000]),
      (100000,6000,3000,1500,300,[2000,2500,2500,3000,4000,4000]),(150000,9000,4000,2000,350,[2500,3000,3000,3000,4000,5000])]
apexPrice={25000:18,50000:35,100000:53,150000:80}
for s,tg,dd,dll,mdp,caps in apex:
    add(key=f"apex{s//1000}",firm="Apex",plan=f"EOD {s//1000}K",size=s,target=tg,dd=dd,trail="eod",dailyLoss=dll,
        minDaysEval=0,minDaysPayout=5,minDailyProfit=mdp,cons=0.5,split=1.0,firstThresh=0,buffer=dd+100,
        minPayout=500,capSchedule=caps,payoutPct=None,minBetween=0,mode="standard",maxPayouts=6,
        evalFee=apexPrice[s],actFee=0,monthly=False,verified=True)

# ---- ALPHA (verified). EOD trailing 4% (Standard 3.5%), lock start. Payout: 5 winning-days $200, 50%/request, 90% of request.
# Zero: 40% consistency, daily-loss guard. Premium: NO consistency(net-pos), daily-loss guard. Advanced/Standard: no DL, qualified no-cons.
# Alpha caps ramp; use 5-length schedule.
alpha_zero=[(25000,1500,1000,500,[1000]*5),(50000,3000,2000,1000,[1500]*5),(100000,6000,3000,2000,[2500]*5)]
zeroPrice={25000:79,50000:119,100000:239}
for s,tg,dd,dll,caps in alpha_zero:
    add(key=f"alphaZero{s//1000}",firm="Alpha",plan=f"Zero {s//1000}K",size=s,target=tg,dd=dd,trail="eod",dailyLoss=dll,
        minDaysEval=1,minDaysPayout=5,minDailyProfit=200,cons=0.4,split=0.9,firstThresh=0,buffer=0,minPayout=200,
        capSchedule=caps,payoutPct=0.5,minBetween=0,mode="standard",maxPayouts=None,evalFee=zeroPrice[s],actFee=0,monthly=True,verified=True)
# Premium: two pricing paths (act vs no-act). caps ramp per size.
prem=[(50000,3000,2000,1000,[2000,2250,2500,3000,4000],(79,149),159),
      (100000,6000,3000,2000,[2500,3000,3500,4000,5000],(159,149),269),
      (150000,9000,4500,None,[3000,3500,4000,5000,6000],(239,149),379)]
for s,tg,dd,dll,caps,(mA,act),mNo in prem:
    common=dict(firm="Alpha",size=s,target=tg,dd=dd,trail="eod",dailyLoss=dll,minDaysEval=1,minDaysPayout=5,
        minDailyProfit=200,cons=None,split=0.9,firstThresh=0,buffer=0,minPayout=500,capSchedule=caps,
        payoutPct=0.5,minBetween=0,mode="standard",maxPayouts=None,monthly=True,verified=True)
    add(key=f"alphaPremAct{s//1000}",plan=f"Premium {s//1000}K (act)",evalFee=mA,actFee=act,**common)
    add(key=f"alphaPremNo{s//1000}",plan=f"Premium {s//1000}K (no-act)",evalFee=mNo,actFee=0,**common)
# Advanced: eval 2 days, qualified no consistency, no daily loss, MLL 4%, cap $15k flat, min payout $1000, act $149
adv=[(50000,3000,2000,139),(100000,6000,3000,279),(150000,9000,4500,419)]
for s,tg,dd,pr in adv:
    add(key=f"alphaAdv{s//1000}",firm="Alpha",plan=f"Advanced {s//1000}K",size=s,target=tg,dd=dd,trail="eod",dailyLoss=None,
        minDaysEval=2,minDaysPayout=5,minDailyProfit=200,cons=None,split=0.9,firstThresh=0,buffer=0,minPayout=1000,
        capSchedule=[15000]*5,payoutPct=0.5,minBetween=0,mode="standard",maxPayouts=None,evalFee=pr,actFee=149,monthly=True,verified=True)

# ---- FUNDEDNEXT Futures (verified dd/cons/split; EOD trailing lock start; 40% consistency; 80% split) ----
# Legacy: 25k tgt? dd1000; 50k tgt3000 dd2000; 100k tgt6000 dd3000
fnLegacy=[(25000,1250,1000),(50000,3000,2000),(100000,6000,3000)]
for s,tg,dd in fnLegacy:
    fncap={25000:1000,50000:1500,100000:2500}[s]
    add(key=f"fnLegacy{s//1000}",firm="FundedNext",plan=f"Legacy {s//1000}K",size=s,target=tg,dd=dd,trail="eod",dailyLoss=None,
        minDaysEval=1,minDaysPayout=5,minDailyProfit=200,cons=0.4,split=0.8,firstThresh=0,buffer=100,minPayout=0,
        capSchedule=[fncap]*5,cap=fncap,payoutPct=None,minBetween=5,mode="standard",maxPayouts=None,evalFee=135,actFee=0,monthly=False,verified=True)
# Rapid: 25k tgt1500 dd1000; 50k tgt3000 dd2000; 100k tgt5000 dd3000
fnRapid=[(25000,1500,1000),(50000,3000,2000),(100000,5000,3000)]
for s,tg,dd in fnRapid:
    fncap={25000:1000,50000:1500,100000:2500}[s]
    add(key=f"fnRapid{s//1000}",firm="FundedNext",plan=f"Rapid {s//1000}K",size=s,target=tg,dd=dd,trail="eod",dailyLoss=None,
        minDaysEval=1,minDaysPayout=5,minDailyProfit=200,cons=0.4,split=0.8,firstThresh=0,buffer=100,minPayout=0,
        capSchedule=[fncap]*5,cap=fncap,payoutPct=None,minBetween=5,mode="standard",maxPayouts=None,evalFee=99,actFee=0,monthly=False,verified=True)

# ---- TPT PRO (verified dd/split/buffer; intraday trailing lock start; test target ~6% ASSUMED). ----
tpt=[(25000,1500,1500),(50000,3000,2000),(100000,6000,3000),(150000,9000,4500)]
tptPrice={25000:150,50000:170,100000:330,150000:360}
for s,tg,dd in tpt:
    add(key=f"tptPro{s//1000}",firm="Take Profit",plan=f"PRO {s//1000}K",size=s,target=tg,dd=dd,trail="eot",dailyLoss=None,
        minDaysEval=0,minDaysPayout=0,minDailyProfit=0,cons=None,split=0.8,firstThresh=dd,buffer=dd,minPayout=0,
        capSchedule=None,cap=None,payoutPct=None,minBetween=0,mode="standard",maxPayouts=None,evalFee=tptPrice[s],actFee=0,monthly=True,verified=False)

# emit JS
def jv(v):
    if v is None: return "null"
    if v is True: return "true"
    if v is False: return "false"
    if isinstance(v,str): return json.dumps(v)
    if isinstance(v,list): return "["+",".join(str(x) for x in v)+"]"
    return str(v)
order=["key","firm","plan","size","target","dd","trail","lockAt","dailyLoss","minDaysEval","minDaysPayout",
 "minDailyProfit","cons","split","firstThresh","buffer","minPayout","capSchedule","cap","payoutPct","minBetween",
 "mode","maxPayouts","evalFee","actFee","monthly","verified"]
lines=[]
for p in P:
    p.setdefault("lockAt",p["size"]); p.setdefault("cap",None)
    obj=",".join(f"{k}:{jv(p.get(k))}" for k in order if k in p)
    lines.append(" {"+obj+"}")
print("const PRESETS = [\n"+",\n".join(lines)+"\n];")
print(f"// {len(P)} presets", flush=True)
import sys; print(f"COUNT={len(P)}", file=sys.stderr)
