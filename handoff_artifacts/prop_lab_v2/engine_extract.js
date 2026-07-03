
"use strict";
/* ================= DATA ================= */
const SAMPLE = [["2026-07-01",2.6],["2026-07-02",105.4],["2026-07-06",-139.9],["2026-07-08",77.4],["2026-07-10",-63.6],["2026-07-13",68.4],["2026-07-15",-53.2],["2026-07-16",45.4],["2026-07-17",-59.9],["2026-07-21",105.0],["2026-07-22",-78.1],["2026-07-24",53.6],["2026-07-25",-82.5],["2026-07-28",92.2],["2026-07-29",-90.0],["2026-07-30",203.1],["2026-08-03",-46.3],["2026-08-04",126.4],["2026-08-06",-54.6],["2026-08-07",-1.8],["2026-08-11",87.8],["2026-08-13",5.2],["2026-08-18",111.4],["2026-08-20",-64.8],["2026-08-25",165.2],["2026-09-01",89.3],["2026-09-02",-70.4],["2026-09-03",57.7],["2026-09-04",3.4],["2026-09-07",49.2],["2026-09-08",-47.8],["2026-09-09",-38.1],["2026-09-10",103.7],["2026-09-14",78.4],["2026-09-15",-56.3],["2026-09-16",-4.2],["2026-09-17",-58.0],["2026-09-18",162.4],["2026-09-21",78.3],["2026-09-22",-63.2],["2026-09-23",98.5],["2026-09-24",49.4],["2026-09-25",-94.2],["2026-09-28",71.1],["2026-09-29",203.6],["2026-10-01",-77.1],["2026-10-02",-104.3],["2026-10-05",6.1],["2026-10-06",88.4],["2026-10-07",-3.2],["2026-10-08",-76.1],["2026-10-09",1.8],["2026-10-12",-68.4],["2026-10-13",134.9],["2026-10-14",-59.7],["2026-10-15",3.7],["2026-10-16",-52.3],["2026-10-19",-2.9],["2026-10-20",142.1],["2026-10-21",-97.7],["2026-10-22",-97.7],["2026-10-23",210.2],["2026-10-26",83.6],["2026-10-27",-76.2],["2026-10-28",4.3],["2026-10-29",104.0],["2026-10-30",-118.5],["2026-11-03",-5.1],["2026-11-04",94.2],["2026-11-05",-57.3],["2026-11-06",2.8],["2026-11-10",133.6],["2026-11-12",-44.5],["2026-11-13",68.9],["2026-11-17",6.3],["2026-11-18",-94.7],["2026-11-19",95.4],["2026-11-20",-88.3],["2026-11-24",102.7],["2026-11-25",-3.1],["2026-12-01",87.2],["2026-12-02",135.4],["2026-12-03",-46.8],["2026-12-04",5.7],["2026-12-08",162.3],["2026-12-09",-3.8],["2026-12-10",98.6],["2026-12-11",-37.2],["2026-12-15",204.8],["2026-12-16",-76.4],["2026-12-17",1.4],["2027-01-05",-80.4],["2027-01-06",53.7],["2027-01-07",4.2],["2027-01-08",75.3],["2027-01-09",-97.6],["2027-01-12",62.4],["2027-01-13",-2.1],["2027-01-14",-108.4],["2027-01-15",49.8],["2027-01-16",-89.2],["2027-01-20",3.7],["2027-01-21",72.1],["2027-01-22",-61.8],["2027-01-23",-5.3],["2027-01-26",52.6],["2027-01-27",-77.3],["2027-01-28",58.4],["2027-01-29",-95.1],["2027-01-30",68.3],["2027-01-31",-119.8],["2027-02-02",103.2],["2027-02-03",5.8],["2027-02-04",98.7],["2027-02-09",-4.1],["2027-02-10",-47.3],["2027-02-11",62.7],["2027-02-12",-38.5],["2027-02-17",3.2],["2027-02-18",-71.4],["2027-02-19",64.8],["2027-02-20",-63.8],["2027-02-23",89.4],["2027-02-24",-3.8],["2027-02-25",122.6],["2027-03-02",4.1],["2027-03-03",64.3],["2027-03-04",-55.2],["2027-03-05",-2.4],["2027-03-09",74.1],["2027-03-10",-83.4],["2027-03-11",56.4],["2027-03-12",3.8],["2027-03-16",-68.4],["2027-03-17",59.8],["2027-03-18",-84.6],["2027-03-19",-91.8],["2027-03-23",89.2],["2027-04-01",5.2],["2027-04-02",-68.7],["2027-04-03",-3.4],["2027-04-06",45.3],["2027-04-07",-94.2],["2027-04-08",67.8],["2027-04-09",2.1],["2027-04-10",-47.3],["2027-04-13",88.4],["2027-04-14",-42.1],["2027-04-15",3.8],["2027-04-16",83.4],["2027-04-22",-63.4],["2027-04-23",57.2],["2027-04-24",4.6],["2027-04-25",-57.8],["2027-04-28",-68.4],["2027-04-29",64.2],["2027-04-30",-5.1],["2027-05-01",-62.3],["2027-05-05",3.1],["2027-05-06",-82.4],["2027-05-07",63.7],["2027-05-08",-4.2],["2027-05-12",-72.4],["2027-05-13",99.8],["2027-05-14",-54.1],["2027-05-15",5.3],["2027-05-19",77.6],["2027-05-20",-45.8],["2027-05-21",-76.4],["2027-05-22",95.2],["2027-06-02",87.4],["2027-06-03",134.2],["2027-06-04",-58.3],["2027-06-05",78.6],["2027-06-09",-43.7],["2027-06-10",103.5],["2027-06-11",-74.2],["2027-06-12",92.1],["2027-06-16",-65.4],["2027-06-17",114.8],["2027-06-18",85.3],["2027-06-19",103.4],["2027-06-23",138.7],["2027-06-25",-44.6]];

/* Presets from help centers + uploaded rule PDFs (2026-07). src/eff on each. */
const PRESETS = [
 {key:"apex25",firm:"Apex",plan:"EOD 25K",size:25000,target:1500,dd:1000,trail:"eod",lockAt:25000,dailyLoss:500,minDaysEval:0,minDaysPayout:5,minDailyProfit:100,cons:0.5,split:1.0,firstThresh:0,buffer:1100,minPayout:500,capSchedule:[1000,1000,1000,1000,1000,1000],cap:null,payoutPct:null,minBetween:0,mode:"standard",maxPayouts:6,evalFee:18,actFee:0,monthly:false,verified:true,src:"apex help center (EOD Evaluations/Payouts/Consistency)",eff:"2026-07"},
 {key:"apex50",firm:"Apex",plan:"EOD 50K",size:50000,target:3000,dd:2000,trail:"eod",lockAt:50000,dailyLoss:1000,minDaysEval:0,minDaysPayout:5,minDailyProfit:250,cons:0.5,split:1.0,firstThresh:0,buffer:2100,minPayout:500,capSchedule:[1500,1500,2000,2500,2500,3000],cap:null,payoutPct:null,minBetween:0,mode:"standard",maxPayouts:6,evalFee:35,actFee:0,monthly:false,verified:true,src:"apex help center (EOD Evaluations/Payouts/Consistency)",eff:"2026-07"},
 {key:"apex100",firm:"Apex",plan:"EOD 100K",size:100000,target:6000,dd:3000,trail:"eod",lockAt:100000,dailyLoss:1500,minDaysEval:0,minDaysPayout:5,minDailyProfit:300,cons:0.5,split:1.0,firstThresh:0,buffer:3100,minPayout:500,capSchedule:[2000,2500,2500,3000,4000,4000],cap:null,payoutPct:null,minBetween:0,mode:"standard",maxPayouts:6,evalFee:53,actFee:0,monthly:false,verified:true,src:"apex help center (EOD Evaluations/Payouts/Consistency)",eff:"2026-07"},
 {key:"apex150",firm:"Apex",plan:"EOD 150K",size:150000,target:9000,dd:4000,trail:"eod",lockAt:150000,dailyLoss:2000,minDaysEval:0,minDaysPayout:5,minDailyProfit:350,cons:0.5,split:1.0,firstThresh:0,buffer:4100,minPayout:500,capSchedule:[2500,3000,3000,3000,4000,5000],cap:null,payoutPct:null,minBetween:0,mode:"standard",maxPayouts:6,evalFee:80,actFee:0,monthly:false,verified:true,src:"apex help center (EOD Evaluations/Payouts/Consistency)",eff:"2026-07"},
 {key:"alphaZero25",firm:"Alpha",plan:"Zero 25K",size:25000,target:1500,dd:1000,trail:"eod",lockAt:25000,dailyLoss:500,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:0.4,split:0.9,firstThresh:0,buffer:0,minPayout:200,capSchedule:[1000,1000,1000,1000,1000],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:79,actFee:0,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaZero50",firm:"Alpha",plan:"Zero 50K",size:50000,target:3000,dd:2000,trail:"eod",lockAt:50000,dailyLoss:1000,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:0.4,split:0.9,firstThresh:0,buffer:0,minPayout:200,capSchedule:[1500,1500,1500,1500,1500],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:119,actFee:0,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaZero100",firm:"Alpha",plan:"Zero 100K",size:100000,target:6000,dd:3000,trail:"eod",lockAt:100000,dailyLoss:2000,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:0.4,split:0.9,firstThresh:0,buffer:0,minPayout:200,capSchedule:[2500,2500,2500,2500,2500],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:239,actFee:0,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaPremAct50",firm:"Alpha",plan:"Premium 50K (act)",size:50000,target:3000,dd:2000,trail:"eod",lockAt:50000,dailyLoss:1000,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:null,split:0.9,firstThresh:0,buffer:0,minPayout:500,capSchedule:[2000,2250,2500,3000,4000],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:79,actFee:149,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaPremNo50",firm:"Alpha",plan:"Premium 50K (no-act)",size:50000,target:3000,dd:2000,trail:"eod",lockAt:50000,dailyLoss:1000,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:null,split:0.9,firstThresh:0,buffer:0,minPayout:500,capSchedule:[2000,2250,2500,3000,4000],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:159,actFee:0,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaPremAct100",firm:"Alpha",plan:"Premium 100K (act)",size:100000,target:6000,dd:3000,trail:"eod",lockAt:100000,dailyLoss:2000,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:null,split:0.9,firstThresh:0,buffer:0,minPayout:500,capSchedule:[2500,3000,3500,4000,5000],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:159,actFee:149,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaPremNo100",firm:"Alpha",plan:"Premium 100K (no-act)",size:100000,target:6000,dd:3000,trail:"eod",lockAt:100000,dailyLoss:2000,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:null,split:0.9,firstThresh:0,buffer:0,minPayout:500,capSchedule:[2500,3000,3500,4000,5000],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:269,actFee:0,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaPremAct150",firm:"Alpha",plan:"Premium 150K (act)",size:150000,target:9000,dd:4500,trail:"eod",lockAt:150000,dailyLoss:null,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:null,split:0.9,firstThresh:0,buffer:0,minPayout:500,capSchedule:[3000,3500,4000,5000,6000],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:239,actFee:149,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaPremNo150",firm:"Alpha",plan:"Premium 150K (no-act)",size:150000,target:9000,dd:4500,trail:"eod",lockAt:150000,dailyLoss:null,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:null,split:0.9,firstThresh:0,buffer:0,minPayout:500,capSchedule:[3000,3500,4000,5000,6000],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:379,actFee:0,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaAdv50",firm:"Alpha",plan:"Advanced 50K",size:50000,target:3000,dd:2000,trail:"eod",lockAt:50000,dailyLoss:null,minDaysEval:2,minDaysPayout:5,minDailyProfit:200,cons:null,split:0.9,firstThresh:0,buffer:0,minPayout:1000,capSchedule:[15000,15000,15000,15000,15000],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:139,actFee:149,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaAdv100",firm:"Alpha",plan:"Advanced 100K",size:100000,target:6000,dd:3000,trail:"eod",lockAt:100000,dailyLoss:null,minDaysEval:2,minDaysPayout:5,minDailyProfit:200,cons:null,split:0.9,firstThresh:0,buffer:0,minPayout:1000,capSchedule:[15000,15000,15000,15000,15000],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:279,actFee:149,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"alphaAdv150",firm:"Alpha",plan:"Advanced 150K",size:150000,target:9000,dd:4500,trail:"eod",lockAt:150000,dailyLoss:null,minDaysEval:2,minDaysPayout:5,minDailyProfit:200,cons:null,split:0.9,firstThresh:0,buffer:0,minPayout:1000,capSchedule:[15000,15000,15000,15000,15000],cap:null,payoutPct:0.5,minBetween:0,mode:"standard",maxPayouts:null,evalFee:419,actFee:149,monthly:true,verified:true,src:"alpha help center + uploaded Alpha Rules PDF",eff:"2026-07"},
 {key:"fnLegacy25",firm:"FundedNext",plan:"Legacy 25K",size:25000,target:1250,dd:1000,trail:"eod",lockAt:25000,dailyLoss:null,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:0.4,split:0.8,firstThresh:0,buffer:100,minPayout:0,capSchedule:[1000,1000,1000,1000,1000],cap:1000,payoutPct:null,minBetween:5,mode:"standard",maxPayouts:null,evalFee:135,actFee:0,monthly:false,verified:true,src:"uploaded FundedNext Rules PDF (futures challenge terms)",eff:"2026-07"},
 {key:"fnLegacy50",firm:"FundedNext",plan:"Legacy 50K",size:50000,target:3000,dd:2000,trail:"eod",lockAt:50000,dailyLoss:null,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:0.4,split:0.8,firstThresh:0,buffer:100,minPayout:0,capSchedule:[1500,1500,1500,1500,1500],cap:1500,payoutPct:null,minBetween:5,mode:"standard",maxPayouts:null,evalFee:135,actFee:0,monthly:false,verified:true,src:"uploaded FundedNext Rules PDF (futures challenge terms)",eff:"2026-07"},
 {key:"fnLegacy100",firm:"FundedNext",plan:"Legacy 100K",size:100000,target:6000,dd:3000,trail:"eod",lockAt:100000,dailyLoss:null,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:0.4,split:0.8,firstThresh:0,buffer:100,minPayout:0,capSchedule:[2500,2500,2500,2500,2500],cap:2500,payoutPct:null,minBetween:5,mode:"standard",maxPayouts:null,evalFee:135,actFee:0,monthly:false,verified:true,src:"uploaded FundedNext Rules PDF (futures challenge terms)",eff:"2026-07"},
 {key:"fnRapid25",firm:"FundedNext",plan:"Rapid 25K",size:25000,target:1500,dd:1000,trail:"eod",lockAt:25000,dailyLoss:null,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:0.4,split:0.8,firstThresh:0,buffer:100,minPayout:0,capSchedule:[1000,1000,1000,1000,1000],cap:1000,payoutPct:null,minBetween:5,mode:"standard",maxPayouts:null,evalFee:99,actFee:0,monthly:false,verified:true,src:"uploaded FundedNext Rules PDF (futures challenge terms)",eff:"2026-07"},
 {key:"fnRapid50",firm:"FundedNext",plan:"Rapid 50K",size:50000,target:3000,dd:2000,trail:"eod",lockAt:50000,dailyLoss:null,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:0.4,split:0.8,firstThresh:0,buffer:100,minPayout:0,capSchedule:[1500,1500,1500,1500,1500],cap:1500,payoutPct:null,minBetween:5,mode:"standard",maxPayouts:null,evalFee:99,actFee:0,monthly:false,verified:true,src:"uploaded FundedNext Rules PDF (futures challenge terms)",eff:"2026-07"},
 {key:"fnRapid100",firm:"FundedNext",plan:"Rapid 100K",size:100000,target:5000,dd:3000,trail:"eod",lockAt:100000,dailyLoss:null,minDaysEval:1,minDaysPayout:5,minDailyProfit:200,cons:0.4,split:0.8,firstThresh:0,buffer:100,minPayout:0,capSchedule:[2500,2500,2500,2500,2500],cap:2500,payoutPct:null,minBetween:5,mode:"standard",maxPayouts:null,evalFee:99,actFee:0,monthly:false,verified:true,src:"uploaded FundedNext Rules PDF (futures challenge terms)",eff:"2026-07"},
 {key:"tptPro25",firm:"Take Profit",plan:"PRO 25K",size:25000,target:1500,dd:1500,trail:"eot",lockAt:25000,dailyLoss:null,minDaysEval:0,minDaysPayout:0,minDailyProfit:0,cons:null,split:0.8,firstThresh:1500,buffer:1500,minPayout:0,capSchedule:null,cap:null,payoutPct:null,minBetween:0,mode:"standard",maxPayouts:null,evalFee:150,actFee:0,monthly:true,verified:false,src:"uploaded TPT Rules PDF (PRO rules/split/buffer)",eff:"2026-07"},
 {key:"tptPro50",firm:"Take Profit",plan:"PRO 50K",size:50000,target:3000,dd:2000,trail:"eot",lockAt:50000,dailyLoss:null,minDaysEval:0,minDaysPayout:0,minDailyProfit:0,cons:null,split:0.8,firstThresh:2000,buffer:2000,minPayout:0,capSchedule:null,cap:null,payoutPct:null,minBetween:0,mode:"standard",maxPayouts:null,evalFee:170,actFee:0,monthly:true,verified:false,src:"uploaded TPT Rules PDF (PRO rules/split/buffer)",eff:"2026-07"},
 {key:"tptPro100",firm:"Take Profit",plan:"PRO 100K",size:100000,target:6000,dd:3000,trail:"eot",lockAt:100000,dailyLoss:null,minDaysEval:0,minDaysPayout:0,minDailyProfit:0,cons:null,split:0.8,firstThresh:3000,buffer:3000,minPayout:0,capSchedule:null,cap:null,payoutPct:null,minBetween:0,mode:"standard",maxPayouts:null,evalFee:330,actFee:0,monthly:true,verified:false,src:"uploaded TPT Rules PDF (PRO rules/split/buffer)",eff:"2026-07"},
 {key:"tptPro150",firm:"Take Profit",plan:"PRO 150K",size:150000,target:9000,dd:4500,trail:"eot",lockAt:150000,dailyLoss:null,minDaysEval:0,minDaysPayout:0,minDailyProfit:0,cons:null,split:0.8,firstThresh:4500,buffer:4500,minPayout:0,capSchedule:null,cap:null,payoutPct:null,minBetween:0,mode:"standard",maxPayouts:null,evalFee:360,actFee:0,monthly:true,verified:false,src:"uploaded TPT Rules PDF (PRO rules/split/buffer)",eff:"2026-07"}
];
PRESETS.forEach(R=>{ R.capSchedule=R.capSchedule||null; R.maxPayouts=R.maxPayouts||null;
  R.minDailyProfit=R.minDailyProfit||0; R.minPayout=R.minPayout||0; R.payoutPct=R.payoutPct||null;
  R.minBetween=R.minBetween||0; R.dailyLoss=(R.dailyLoss===undefined)?null:R.dailyLoss; });

/* ================= CORE ================= */
const DAY=86400000;
const d=s=>new Date(s+"T00:00:00Z").getTime();
const addM=(s,m)=>{const t=new Date(s+"T00:00:00Z");t.setUTCMonth(t.getUTCMonth()+m);return t.getTime();};
const ym=s=>s.slice(0,7);
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
function gauss(r){let u=0,v=0;while(!u)u=r();while(!v)v=r();return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v);}
function shufflePath(tr,sig,seed){ if(sig<=0)return tr; const N=tr.length,r=mulberry32(seed);
  const k=tr.map((t,i)=>({i,k:i+gauss(r)*sig*0.22*N})); k.sort((a,b)=>a.k-b.k);
  return tr.map((t,i)=>({date:t.date,pts:tr[k[i].i].pts})); }
const quant=(s,q)=>!s.length?null:s[Math.min(s.length-1,Math.max(0,Math.round(q*(s.length-1))))];
const mean=a=>a.length?a.reduce((x,y)=>x+y,0)/a.length:0;

/*
 ACCOUNT ENGINE. One run of one account over one trade sequence.
 cfg: {dpp, ct, phase:'eval'|'funded', chargeFees, initBal, initPeak, initQual,
       payoutsDone, reserve, payReq, dcE, dcA, trace}
 ACCOUNTING IDENTITY (asserted in self-test):
   finalBalance = startBalance + grossTrading − grossPayouts
   netPayouts   = Σ gross×split ;  splitWithheld = grossPayouts − netPayouts
   netPersonal  = netPayouts − fees
 Negative monthly trading P&L is NEVER clamped. After failure the account is
 terminated: months after failure carry active:false (not zero P&L).
*/
function runAccount(trades,R,cfg){
  const dpp=cfg.dpp, ct=cfg.ct, reserve=cfg.reserve||0, payReq=cfg.payReq||0;
  let phase=cfg.phase;
  const startBal=(cfg.initBal!=null&&phase==="funded")?cfg.initBal:R.size;
  let bal=startBal;
  let peak=(cfg.initPeak!=null&&phase==="funded")?Math.max(cfg.initPeak,bal):bal;
  let curDay=null,dayStart=null;
  const evalDays=new Set(),fundedDaySet=new Set(),dayProfit={};
  const qualOffset=cfg.initQual||0;
  let payouts=cfg.payoutsDone||0, grossPay=0, netPay=0, lastPayDay=null, firstPayTs=null;
  let blew=false,failTs=null,passed=(phase==="funded"),activTs=null,done=false,doneReason=null;
  let fees=cfg.chargeFees?R.evalFee*(cfg.dcE!=null?cfg.dcE:1):0;
  let grossTrading=0, unearned=0;
  const M={}; const mrec=k=>M[k]||(M[k]={tr:0,gp:0,np:0});
  const EV=cfg.trace?[]:null;
  const start=trades.length?trades[0].date:null;
  if(phase==="funded") activTs=start?d(start):null;
  // Trailing failure floor is MONOTONE: ratchets up with the peak, caps at the
  // lock level, and NEVER decreases — including after withdrawals.
  let hardFloor=-Infinity;
  const floorOf=()=>{let f=peak-R.dd; if(R.lockAt!=null)f=Math.min(f,R.lockAt);
    if(f>hardFloor)hardFloor=f; return hardFloor;};

  for(const t of trades){
    const pnl=t.pts*dpp*ct;
    if(done){ if(blew) unearned+=pnl; continue; }
    if(t.date!==curDay){ if(R.trail==="eod"&&curDay!==null)peak=Math.max(peak,bal); curDay=t.date; dayStart=bal; }
    const fBefore=floorOf(), bBefore=bal;
    bal+=pnl; grossTrading+=pnl; mrec(ym(t.date)).tr+=pnl;
    if(R.trail==="eot")peak=Math.max(peak,bal);
    if(phase==="eval")evalDays.add(t.date);
    else{ fundedDaySet.add(t.date); dayProfit[t.date]=(dayProfit[t.date]||0)+pnl; }
    if(EV)EV.push({date:t.date,type:"trade",pnl,bb:bBefore,ba:bal,fl:floorOf()});
    // failure checks (realized-only)
    if(bal<=floorOf()){ blew=true;failTs=d(t.date);done=true;doneReason="drawdown";
      if(EV)EV.push({date:t.date,type:"FAIL — drawdown floor",pnl:0,bb:bal,ba:bal,fl:floorOf()}); continue; }
    if(R.dailyLoss!=null&&dayStart!=null&&(dayStart-bal)>=R.dailyLoss){ blew=true;failTs=d(t.date);done=true;doneReason="daily loss";
      if(EV)EV.push({date:t.date,type:"FAIL — daily loss",pnl:0,bb:bal,ba:bal,fl:floorOf()}); continue; }
    if(phase==="eval"){
      if(bal-R.size>=R.target && evalDays.size>=(R.minDaysEval||0)){
        if(cfg.chargeFees)fees+=(R.actFee||0)*(cfg.dcA!=null?cfg.dcA:1);
        phase="funded";passed=true;bal=R.size;peak=R.size;hardFloor=-Infinity;activTs=d(t.date);
        curDay=null;dayStart=null;fundedDaySet.clear();for(const k in dayProfit)delete dayProfit[k];
        if(EV)EV.push({date:t.date,type:"PASSED → funded (fresh balance)",pnl:0,bb:bal,ba:bal,fl:floorOf()});
      }
      continue;
    }
    // funded payout logic
    const profit=bal-R.size;
    const cushion=Math.max(R.buffer||0,reserve);           // TOTAL cushion, never stacked
    const withdrawable=profit-cushion;
    if(withdrawable<=0)continue;
    let qual=qualOffset; for(const k in dayProfit){ if(dayProfit[k]>=(R.minDailyProfit||0))qual++; }
    let ok=(profit>=((payouts===0)?(R.firstThresh||0):0)) && (qual>=(R.minDaysPayout||0));
    if(ok&&R.mode==="daily"&&lastPayDay!=null&&d(t.date)<=d(lastPayDay))ok=false;
    if(ok&&R.mode!=="daily"&&R.minBetween>0){ const ref=lastPayDay||start; if(ref&&(d(t.date)-d(ref))/DAY<R.minBetween)ok=false; }
    if(ok&&R.cons!=null){ let tot=0,best=-1e18; for(const k in dayProfit){const v=dayProfit[k];if(v>0)tot+=v;if(v>best)best=v;} if(tot>0&&best>=R.cons*tot)ok=false; }
    if(!ok)continue;
    const capNow=R.capSchedule?R.capSchedule[Math.min(payouts,R.capSchedule.length-1)]:R.cap;
    let gross=withdrawable;
    if(capNow!=null)gross=Math.min(gross,capNow);
    if(R.payoutPct!=null)gross=Math.min(gross,R.payoutPct*profit);
    if(payReq>0)gross=Math.min(gross,payReq);
    if(gross<(R.minPayout||0))continue;
    bal-=gross; peak-=gross; // floor is monotone via hardFloor; withdrawal never lowers it
    if(dayStart!=null)dayStart-=gross;
    grossPay+=gross; const nn=gross*R.split; netPay+=nn; payouts++;
    lastPayDay=t.date; if(firstPayTs==null)firstPayTs=d(t.date);
    const m=mrec(ym(t.date)); m.gp+=gross; m.np+=nn;
    for(const k in dayProfit)delete dayProfit[k];
    if(EV)EV.push({date:t.date,type:"PAYOUT approved",pnl:0,bb:bal+gross,ba:bal,fl:floorOf(),gross,paid:nn});
    if(R.maxPayouts!=null&&payouts>=R.maxPayouts){ done=true;doneReason="max payouts (retired)";
      if(EV)EV.push({date:t.date,type:"RETIRED — max payouts",pnl:0,bb:bal,ba:bal,fl:floorOf()}); }
  }
  const refTs=activTs||(start?d(start):null);
  return {passed,blew,failTs,doneReason,payouts,grossPay,netPay,fees,
    netPersonal:netPay-fees, grossTrading, unearned, startBal, finalBal:bal,
    retained:bal-startBal-( -grossPay+grossPay)*0 + 0 + (0), // computed below for clarity
    retainedInAccount: bal-startBal, // = grossTrading − grossPay (identity)
    splitWithheld: grossPay-netPay,
    eligibleEnd: Math.max(0,(bal-R.size)-Math.max(R.buffer||0,reserve)),
    monthly:M, firstPayTs, daysToPay:(firstPayTs&&refTs)?(firstPayTs-refTs)/DAY:null,
    daysToPass:(cfg.phase==="eval"&&activTs&&start)?(activTs-d(start))/DAY:null,
    events:EV};
}

/* ---- distribution over samples, with month-indexed aggregation ---- */
function monthKeys(startDate,H){ const out=[]; let t=new Date(startDate+"T00:00:00Z");
  for(let i=0;i<H;i++){ out.push(t.toISOString().slice(0,7)); t.setUTCMonth(t.getUTCMonth()+1);} return out; }

function fundedDist(trades,R,cfg,H,samples,seed,sigma,collectMonthly){
  const last=d(trades[trades.length-1].date), rng=mulberry32(seed);
  let n=0,blow=0,pay=0,allPaid=0;
  const nets=[],ttp=[],ttf=[],WF={gt:0,un:0,gp:0,np:0,ret:0};
  const MO=collectMonthly?Array.from({length:H},()=>({tr:[],gp:[],np:[],cumNp:[],fail:0,pay1:0,active:0})):null;
  const paths=[]; // for inspector: store start info
  for(let k=0;k<samples;k++){
    const path=shufflePath(trades,sigma,seed+k*131+7);
    const cands=path.filter(t=>addM(t.date,H)<=last+DAY).map(t=>t.date);
    if(!cands.length)continue;
    const s=cands[Math.floor(rng()*cands.length)];
    const sub=path.filter(t=>{const x=d(t.date);return x>=d(s)&&x<addM(s,H);});
    if(!sub.length)continue;
    const r=runAccount(sub,R,{...cfg,phase:"funded",chargeFees:false});
    n++; if(r.blew)blow++; if(r.payouts>0)pay++;
    if(R.maxPayouts!=null&&r.payouts>=R.maxPayouts)allPaid++;
    nets.push(r.netPersonal); if(r.daysToPay!=null)ttp.push(r.daysToPay);
    if(r.failTs!=null)ttf.push((r.failTs-d(s))/DAY);
    WF.gt+=r.grossTrading;WF.un+=r.unearned;WF.gp+=r.grossPay;WF.np+=r.netPay;WF.ret+=r.retainedInAccount;
    paths.push({start:s,seedK:seed+k*131+7});
    if(MO){ const mks=monthKeys(s,H); let cum=0;
      const failIdx=r.failTs!=null?mks.findIndex(mk=>ym(new Date(r.failTs).toISOString())===mk):-1;
      const firstPayIdx=r.firstPayTs!=null?mks.findIndex(mk=>ym(new Date(r.firstPayTs).toISOString())===mk):-1;
      mks.forEach((mk,i)=>{ const rec=r.monthly[mk];
        const activeAtStart=(failIdx<0||i<=failIdx);
        if(activeAtStart){ MO[i].active++;
          MO[i].tr.push(rec?rec.tr:0); MO[i].gp.push(rec?rec.gp:0); MO[i].np.push(rec?rec.np:0);
          cum+=(rec?rec.np:0); MO[i].cumNp.push(cum);
        }
        if(failIdx===i)MO[i].fail++;
        if(firstPayIdx===i)MO[i].pay1++;
      });
    }
  }
  if(!n)return null;
  nets.sort((a,b)=>a-b);ttp.sort((a,b)=>a-b);ttf.sort((a,b)=>a-b);
  const wf={grossTrading:WF.gt/n,unearned:WF.un/n,grossPay:WF.gp/n,netPay:WF.np/n,
    retained:WF.ret/n,splitWithheld:(WF.gp-WF.np)/n};
  return {n,blow:blow/n,pay:pay/n,allPaid:allPaid/n,net:mean(nets),p5:quant(nets,.05),p95:quant(nets,.95),
    payDays50:quant(ttp,.5),payDays90:quant(ttp,.9),failDays50:quant(ttf,.5),wf,MO,paths};
}

function evalDist(trades,R,cfg,samples,seed,sigma){
  const rng=mulberry32(seed+99); let n=0,pass=0; const days=[];
  const passByMonth=[0,0,0,0,0,0]; // cumulative pass by month 1..6
  const cut=Math.max(1,Math.floor(trades.length*0.6));
  for(let k=0;k<Math.min(samples,400);k++){
    const path=shufflePath(trades,sigma,seed+k*211+3);
    const sub=path.slice(Math.floor(rng()*cut)); if(!sub.length)continue;
    const r=runAccount(sub,R,{...cfg,phase:"eval",chargeFees:true}); n++;
    if(r.passed){ pass++; if(r.daysToPass!=null){days.push(r.daysToPass);
      for(let m=0;m<6;m++)if(r.daysToPass<=30*(m+1))passByMonth[m]++;} }
  }
  days.sort((a,b)=>a-b);
  return {n,pass:n?pass/n:0,d50:quant(days,.5),d90:quant(days,.9),
    passByMonth:passByMonth.map(x=>n?x/n:0)};
}

/* ================= PARSE ================= */
function parseData(text){
  text=text.trim(); if(!text)throw new Error("No data.");
  let rows=[];
  if(text[0]==="["){ for(const it of JSON.parse(text)){
    if(Array.isArray(it))rows.push({date:String(it[0]).slice(0,10),pts:+it[1]});
    else rows.push({date:String(it.sd||it.date||it.day).slice(0,10),pts:+(it.pnl!=null?it.pnl:it.pnl_pts)});}}
  else{ const L=text.split(/\r?\n/).filter(l=>l.trim()); let di=0,pi=1,h=false;
    const H=L[0].split(/[,\t]/).map(x=>x.trim().toLowerCase());
    if(H.some(x=>/date|^sd$/.test(x))||H.some(x=>/pnl/.test(x))){h=true;di=Math.max(0,H.findIndex(x=>/date|^sd$/.test(x)));pi=Math.max(1,H.findIndex(x=>/pnl/.test(x)));}
    for(let i=h?1:0;i<L.length;i++){const c=L[i].split(/[,\t]/);const dt=(c[di]||"").trim().slice(0,10);const p=parseFloat(c[pi]);if(dt&&!isNaN(p))rows.push({date:dt,pts:p});}}
  rows=rows.filter(r=>/^\d{4}-\d{2}-\d{2}$/.test(r.date)&&!isNaN(r.pts));
  if(!rows.length)throw new Error("Couldn't find (date, pnl) rows.");
  rows.sort((a,b)=>d(a.date)-d(b.date)); return rows;
}

