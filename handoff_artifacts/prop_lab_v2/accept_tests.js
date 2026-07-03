let passN=0,failN=0;
function T(name,cond,detail){ if(cond){passN++;console.log('PASS',name);} else {failN++;console.log('FAIL',name,detail||'');} }
const apex50=PRESETS.find(p=>p.key==='apex50');
const cfg0={dpp:1,ct:1,reserve:0,payReq:0};

/* ---- Scenario A: no prop rules — monthly ledger identity, negatives preserved ---- */
const fixedA=[["2026-01-05",1000],["2026-01-20",-400],["2026-02-10",-900],["2026-03-15",2000]]
  .map(x=>({date:x[0],pts:x[1]}));
const loose={...apex50, dd:1e9, dailyLoss:null, buffer:1e12, firstThresh:1e12}; // no fail, no payouts
const rA=runAccount(fixedA,loose,{...cfg0,phase:"funded"});
T('A1 monthly Jan = +600', rA.monthly['2026-01'].tr===600);
T('A2 monthly Feb = -900 (negative NOT clamped)', rA.monthly['2026-02'].tr===-900);
T('A3 cumulative = 1700', rA.grossTrading===1700 && rA.finalBal===apex50.size+1700);
T('A4 identity finalBal=start+trading-gross', Math.abs(rA.finalBal-(rA.startBal+rA.grossTrading-rA.grossPay))<1e-6);

/* ---- Scenario B: same path through 4 firms — identical gross trading until failure ---- */
const pathB=SAMPLE.slice(0,60).map(x=>({date:x[0],pts:x[1]}));
const gts=['apex50','alphaPremAct50','fnLegacy50','tptPro50'].map(k=>{
  const R={...PRESETS.find(p=>p.key===k), dd:1e9, dailyLoss:null}; // disable failure
  return runAccount(pathB,R,{...cfg0,dpp:2,ct:2,phase:"funded"}).grossTrading;});
T('B1 identical gross trading across firms (no failure)', gts.every(g=>Math.abs(g-gts[0])<1e-6), gts.join(','));

/* ---- Scenario C: exact payout math ---- */
// Apex50: buffer 2100, cap#1 1500, minPayout 500, split 1.0. Start funded at 52,600 (profit 2600).
// qualifying: 5 days >= $250. Feed 5 days of +$300(150pts*2dpp*1ct)... use dpp=1 ct=1 pts=300.
const daysC=[]; for(let i=1;i<=5;i++)daysC.push({date:`2026-02-0${i}`,pts:300});
daysC.push({date:'2026-02-08',pts:10}); // small day to trigger eligibility check after 5 qual days
const rC=runAccount(daysC,apex50,{...cfg0,phase:"funded",initBal:52600,initPeak:52600});
// after +1510: profit=4110; withdrawable=4110-2100=2010; consistency: best day 300 of 1510 total OK
// cap#1=1500 -> gross=1500, split 1.0 -> paid 1500; balance = 52600+1510-1500 = 52610
T('C1 gross approved = 1500 (cap #1)', rC.grossPay===1500, rC.grossPay);
T('C2 trader paid = 1500 (100% split)', rC.netPay===1500);
T('C3 balance after = 52,610', rC.finalBal===52610, rC.finalBal);
// C4: user requests only $600
const rC2=runAccount(daysC,apex50,{...cfg0,phase:"funded",initBal:52600,initPeak:52600,payReq:600});
T('C4 requested 600 -> paid 600, balance 53,510', rC2.grossPay===600&&rC2.finalBal===53510, rC2.finalBal);
// C5: 90% split firm
const alpha=PRESETS.find(p=>p.key==='alphaPremAct50');
const rC3=runAccount(daysC,{...alpha,minDaysPayout:5,minDailyProfit:250},{...cfg0,phase:"funded",initBal:52600,initPeak:52600,payReq:1000});
T('C5 request 1000 @90% split -> trader gets 900, balance -1000', Math.abs(rC3.netPay-900)<1e-6&&rC3.grossPay===1000, rC3.netPay);

/* ---- Scenario D: live-state init ---- */
// start funded with 3 qualifying days done; only need 2 more
const daysD=[{date:'2026-03-02',pts:300},{date:'2026-03-03',pts:300},{date:'2026-03-04',pts:10}];
const rD=runAccount(daysD,apex50,{...cfg0,phase:"funded",initBal:52600,initPeak:52600,initQual:3});
T('D1 initQual=3 + 2 new days -> payout occurs', rD.payouts===1, rD.payouts);
const rD0=runAccount(daysD,apex50,{...cfg0,phase:"funded",initBal:52600,initPeak:52600,initQual:0});
T('D2 initQual=0 same trades -> no payout yet', rD0.payouts===0);
// payoutsDone advances the cap schedule: payout #3 cap = 2000
const rD2=runAccount(daysC,apex50,{...cfg0,phase:"funded",initBal:53000,initPeak:53000,payoutsDone:2});
T('D3 payoutsDone=2 -> cap#3 (2000) applies', rD2.grossPay===2000, rD2.grossPay);

/* ---- Scenario E: evaluation ---- */
const daysE=[{date:'2026-04-01',pts:3200}];
const rE=runAccount(daysE,apex50,{...cfg0,phase:"eval",chargeFees:true,dcE:1,dcA:1});
T('E1 Apex eval passes in 1 day (no min days), fee charged', rE.passed===true&&rE.fees===apex50.evalFee);
const rE2=runAccount([{date:'2026-04-01',pts:-2100}],apex50,{...cfg0,phase:"eval",chargeFees:true,dcE:1,dcA:1});
T('E2 eval blows on drawdown', rE2.blew===true);

/* ---- Regressions ---- */
// cushion not double-counted: reserve < buffer changes nothing
const r1=runAccount(daysC,apex50,{...cfg0,phase:"funded",initBal:52600,initPeak:52600,reserve:1000});
T('R1 reserve<buffer identical to no reserve', r1.grossPay===rC.grossPay&&r1.finalBal===rC.finalBal);
// terminated ≠ zero: after failure months carry no tr record, unearned tracked
const failPath=[{date:'2026-01-05',pts:-2500},{date:'2026-02-05',pts:1000}];
const rF=runAccount(failPath,apex50,{...cfg0,phase:"funded"});
T('R2 failed in Jan; Feb has NO monthly record (terminated, not zero)', rF.blew&&!rF.monthly['2026-02']);
T('R3 post-failure P&L tracked as unearned', rF.unearned===1000);
// determinism
const s1=fundedDist(SAMPLE.map(x=>({date:x[0],pts:x[1]})),apex50,{...cfg0,dpp:2,ct:2},6,50,777,1,false);
const s2=fundedDist(SAMPLE.map(x=>({date:x[0],pts:x[1]})),apex50,{...cfg0,dpp:2,ct:2},6,50,777,1,false);
T('R4 same seed -> identical results', s1.net===s2.net&&s1.blow===s2.blow);
// preset uniqueness + tpt sizes distinct
const keys=PRESETS.map(p=>p.key);
T('R5 preset keys unique', new Set(keys).size===keys.length);
const t100=PRESETS.find(p=>p.key==='tptPro100'),t150=PRESETS.find(p=>p.key==='tptPro150');
T('R6 TPT 100K/150K use own buffers (3000/4500)', t100.buffer===3000&&t150.buffer===4500);
// waterfall identity across a real dist
T('R7 waterfall identity: trading-retained=grossPay (mean)', Math.abs(s1.wf.grossTrading-s1.wf.retained-s1.wf.grossPay)<1);
console.log(`\n${passN} passed, ${failN} failed`);
process.exit(failN?1:0);
