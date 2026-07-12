from __future__ import annotations

from functools import lru_cache

from .models import (
    Compatibility, Consistency, ContractStatus, DailyLoss, DailyLossConsequence,
    Drawdown, DrawdownMode, DrawdownUpdateTiming, Economics, Identity, Inactivity,
    InactivityTimeBasis, LifecycleStage, Payouts, PositionLimits, RuleContract,
    RuleExactness, SourceReference, Sourced, ThresholdComparator, Transition,
)
from .validation import validate_contracts

ALPHA = ("Alpha Rules(5).pdf", "7802bc80a479c0ca5c398a0163b9f2e7e07f8ffc4593e9b8133d182a201e69a0")
FUNDEDNEXT = ("FundedNext Rules(3).pdf", "79e2c87ee2913bbac1b8168b9862d158901bdbaefa9dabc19b3600857c2fff13")
TPT = ("TPT Rules(1).pdf", "7405c8284178addfc397db1619ccafd62455ed9cab2518f77302347f58a3f36f")


def _s(value, document, page, article, exactness=RuleExactness.EXACT):
    return Sourced(value, SourceReference(document[0], document[1], page, article), exactness)


def _identity(firm, program, size, stage, name, document):
    return Identity(firm, program, size, stage, name, document[1])


def _alpha() -> list[RuleContract]:
    records: list[RuleContract] = []
    tables = {
        "Advanced": ((50,4000,1750,50),(100,8000,3500,100),(150,12000,5250,150)),
        "Premium": ((50,3000,2000,40),(100,6000,3000,80),(150,9000,4500,120)),
        "Zero": ((25,1500,1000,10),(50,3000,2000,30),(100,6000,4000,60)),
    }
    for program, rows in tables.items():
        for size, target, loss, micros in rows:
            drawdown = Drawdown(_s(loss,ALPHA,"14-15","Maximum Loss Limit"),_s(DrawdownMode.EOD_TRAILING,ALPHA,"14","Maximum Loss Limit"),_s(DrawdownUpdateTiming.END_OF_DAY,ALPHA,"14","Maximum Loss Limit"),_s(True,ALPHA,"14-15","Maximum Loss Limit"),_s(ThresholdComparator.LESS_THAN_OR_EQUAL,ALPHA,"15","Maximum Loss Limit"))
            economics = Economics(_s(.9,ALPHA,"2",f"{program} Account Overview"),_s(149.,ALPHA,"2",f"{program} Account Overview"))
            limits = PositionLimits(_s(micros,ALPHA,"2",f"{program} Account Overview"),_s(program != "Premium",ALPHA,"2",f"{program} Account Overview", RuleExactness.NON_RANKABLE if program == "Premium" else RuleExactness.EXACT))
            inactivity = Inactivity(_s(10,ALPHA,"11","Inactivity Rule"),_s(InactivityTimeBasis.TRADING_DAYS,ALPHA,"11","Inactivity Rule"))
            dll = DailyLoss(_s(size*20.,ALPHA,"9-10","Daily Loss Guard") if program == "Zero" else None,_s(DailyLossConsequence.SOFT_PAUSE if program == "Zero" else DailyLossConsequence.NONE,ALPHA,"9-10","Daily Loss Guard"),includes_unrealized=_s(True,ALPHA,"9-10","Daily Loss Guard") if program == "Zero" else None)
            eval_consistency = Consistency(_s(.5,ALPHA,"4-5","Consistency Rule") if program != "Zero" else None,_s(ThresholdComparator.LESS_THAN_OR_EQUAL,ALPHA,"4-5","Consistency Rule") if program != "Zero" else None)
            records.append(RuleContract(f"alpha_{program.lower()}_{size}k_evaluation",_identity("Alpha Futures",program,size*1000,LifecycleStage.EVALUATION,f"{program} {size}K Evaluation",ALPHA),ContractStatus.ENABLED,economics,drawdown,dll,limits,eval_consistency,Payouts(None,None,None,None,None),inactivity,Compatibility(automation_allowed=_s(False,ALPHA,"7","Copy Trading")),Transition(_s(float(target),ALPHA,"2",f"{program} Account Overview"),_s(True,ALPHA,"1",f"{program} Account Overview"),_s(True,ALPHA,"1",f"{program} Account Overview")),RuleExactness.NON_RANKABLE if program == "Premium" else RuleExactness.EXACT))
            max_payout = 15000 if program == "Advanced" else ({"Premium":{50:4000,100:5000,150:6000},"Zero":{25:1000,50:1500,100:2500}}[program][size])
            payout = Payouts(_s(200. if program == "Zero" else (500. if program == "Premium" else 1000.),ALPHA,"2",f"{program} Account Overview"),_s(float(max_payout),ALPHA,"2",f"{program} Account Overview"),_s(.5,ALPHA,"1-2",f"{program} Account Overview"),_s(5,ALPHA,"1-2",f"{program} Account Overview"),_s(200.,ALPHA,"1-2",f"{program} Account Overview"))
            funded_consistency = Consistency(_s(.4,ALPHA,"5","Consistency Rule") if program == "Zero" else None,_s(ThresholdComparator.LESS_THAN,ALPHA,"5","Consistency Rule") if program == "Zero" else None)
            records.append(RuleContract(f"alpha_{program.lower()}_{size}k_qualified",_identity("Alpha Futures",program,size*1000,LifecycleStage.FUNDED,f"{program} {size}K",ALPHA),ContractStatus.ENABLED,economics,drawdown,dll,limits,funded_consistency,payout,inactivity,Compatibility(automation_allowed=_s(False,ALPHA,"7","Copy Trading")),Transition(replacement_supported=_s(True,ALPHA,"15","Maximum Loss Limit")),RuleExactness.NON_RANKABLE if program == "Premium" else RuleExactness.EXACT))
    return records


def _fundednext() -> list[RuleContract]:
    tables = {"Rapid":((25,1500,1000),(50,3000,2000),(100,5000,2500)),"Legacy":((25,1250,1000),(50,3000,2000),(100,6000,3000)),"Flex":((50,2500,1500),(100,5000,2500),(150,8000,4000)),"Bolt":((50,3000,2000),)}
    records: list[RuleContract] = []
    for program, rows in tables.items():
        for size,target,loss in rows:
            drawdown=Drawdown(_s(loss,FUNDEDNEXT,"1-40","Futures Trading Objectives"),_s(DrawdownMode.EOD_TRAILING,FUNDEDNEXT,"1-40","Futures Trading Objectives"),_s(DrawdownUpdateTiming.END_OF_DAY,FUNDEDNEXT,"1-40","Futures Trading Objectives"),_s(True,FUNDEDNEXT,"1-40","Futures Trading Objectives"),_s(ThresholdComparator.LESS_THAN_OR_EQUAL,FUNDEDNEXT,"1-40","Futures Trading Objectives"))
            common=(Economics(_s(.8,FUNDEDNEXT,"1-40","Performance Reward")),drawdown,DailyLoss(_s(1000.,FUNDEDNEXT,"1-40","Bolt DLL") if program=="Bolt" else None,_s(DailyLossConsequence.SOFT_PAUSE if program=="Bolt" else DailyLossConsequence.NONE,FUNDEDNEXT,"1-40","Futures Trading Objectives"),_s("17:00 America/Chicago",FUNDEDNEXT,"1-40","Bolt DLL") if program=="Bolt" else None),PositionLimits(None,_s(False,FUNDEDNEXT,"1-40","Futures Trading Objectives",RuleExactness.NON_RANKABLE)),Consistency(_s(.4,FUNDEDNEXT,"1-40","Consistency Rule"),_s(ThresholdComparator.LESS_THAN_OR_EQUAL,FUNDEDNEXT,"1-40","Consistency Rule"),_s(True,FUNDEDNEXT,"1-40","Consistency Rule")),Compatibility())
            records.append(RuleContract(f"fundednext_{program.lower()}_{size}k_challenge",_identity("FundedNext Futures",program,size*1000,LifecycleStage.EVALUATION,f"{program} {size}K Challenge",FUNDEDNEXT),ContractStatus.ENABLED,*common[:5],Payouts(None,None,None,None,None),Inactivity(_s(10,FUNDEDNEXT,"1-40","Inactivity"),_s(InactivityTimeBasis.CALENDAR_DAYS,FUNDEDNEXT,"1-40","Inactivity")),common[5],Transition(_s(float(target),FUNDEDNEXT,"1-40","Futures Trading Objectives"),_s(True,FUNDEDNEXT,"1-40","Futures Trading Objectives"),_s(True,FUNDEDNEXT,"1-40","Futures Trading Objectives")),RuleExactness.NON_RANKABLE))
            caps={25:800,50:1500,100:2500}
            records.append(RuleContract(f"fundednext_{program.lower()}_{size}k_funded",_identity("FundedNext Futures",program,size*1000,LifecycleStage.FUNDED,f"{program} {size}K",FUNDEDNEXT),ContractStatus.ENABLED,*common[:5],Payouts(_s(250.,FUNDEDNEXT,"1-40","Performance Reward") if program=="Rapid" else None,_s(float(caps[size]),FUNDEDNEXT,"1-40","Performance Reward") if program=="Rapid" else None,_s(1.,FUNDEDNEXT,"1-40","Performance Reward"),None,None),Inactivity(_s(30,FUNDEDNEXT,"1-40","Inactivity"),_s(InactivityTimeBasis.CALENDAR_DAYS,FUNDEDNEXT,"1-40","Inactivity")),common[5],Transition(replacement_supported=_s(True,FUNDEDNEXT,"1-40","Futures Trading Objectives")),RuleExactness.NON_RANKABLE))
    return records


def _tpt() -> list[RuleContract]:
    records: list[RuleContract] = []
    for size, loss in ((25,1500),(50,2000),(75,2500),(100,3000),(150,4500)):
        records.append(RuleContract(
            f"tpt_pro_{size}k_funded", _identity("TakeProfitTrader","PRO",size*1000,LifecycleStage.FUNDED,f"PRO {size}K",TPT), ContractStatus.ENABLED,
            Economics(_s(.8,TPT,"5-6","PRO Account Profit Split & Withdrawal Rules")),
            Drawdown(_s(loss,TPT,"1-4","PRO Account Rules"),_s(DrawdownMode.INTRADAY_TRAILING,TPT,"2-3","PRO Account Rules"),_s(DrawdownUpdateTiming.INTRATRADE,TPT,"2-3","PRO Account Rules"),_s(True,TPT,"2-3","PRO Account Rules"),_s(ThresholdComparator.LESS_THAN_OR_EQUAL,TPT,"3","PRO Account Rules")),
            DailyLoss(None,_s(DailyLossConsequence.NONE,TPT,"1-4","PRO Account Rules")),
            PositionLimits(None,_s(False,TPT,"1-4","PRO Account Rules",RuleExactness.NON_RANKABLE)), Consistency(None,None),
            Payouts(None,None,_s(1.,TPT,"5-6","PRO Account Profit Split & Withdrawal Rules"),None,None,buffer=_s(float(loss),TPT,"5-6","PRO Account Profit Split & Withdrawal Rules")),
            Inactivity(_s(1,TPT,"1-2","PRO Account Rules"),_s(InactivityTimeBasis.CALENDAR_WEEK,TPT,"1-2","PRO Account Rules")),
            Compatibility(manual_only=_s(True,TPT,"1-2","PRO Account Rules"),automation_allowed=_s(False,TPT,"1-2","PRO Account Rules"),news_requires_calendar=_s(True,TPT,"3","PRO Account Rules"),price_limit_requires_data=_s(True,TPT,"1-4","PRO Account Rules")),
            Transition(replacement_supported=_s(False,TPT,"1-4","PRO Account Rules")),RuleExactness.NON_RANKABLE,
        ))
        records.append(RuleContract(f"tpt_test_{size}k_evaluation",_identity("TakeProfitTrader","Test",size*1000,LifecycleStage.EVALUATION,f"Test {size}K",TPT),ContractStatus.SOURCE_GAP,None,None,None,None,None,None,None,None,None,RuleExactness.SOURCE_GAP,"Complete Test account table is absent from the supplied PDF."))
        records.append(RuleContract(f"tpt_pro_plus_{size}k_funded",_identity("TakeProfitTrader","PRO+",size*1000,LifecycleStage.FUNDED,f"PRO+ {size}K",TPT),ContractStatus.CONDITIONAL,None,None,None,None,None,None,None,None,None,RuleExactness.CONDITIONAL,"Manual invitation only; no size-specific drawdown table was supplied."))
    return records


@lru_cache(maxsize=1)
def load_contracts() -> tuple[RuleContract, ...]:
    contracts = tuple(_alpha() + _fundednext() + _tpt())
    validate_contracts(contracts)
    return contracts


def enabled_contracts() -> tuple[RuleContract, ...]:
    return tuple(contract for contract in load_contracts() if contract.status is ContractStatus.ENABLED)


def contract_for_profile(profile_key: str) -> RuleContract | None:
    return next((contract for contract in load_contracts() if contract.profile_key == profile_key), None)
