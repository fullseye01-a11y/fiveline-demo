"""Financial-planning preview calculations (synthetic data, no client persistence).

Models here are explicit provisional assumptions, NOT Excel parity or investment
advice. All functions are deterministic and have no filesystem/network access.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from math import isfinite

ASSETS = ("tw_equity", "global_equity", "bonds", "cash")
LABELS = {"tw_equity": "台灣股票", "global_equity": "全球股票", "bonds": "債券", "cash": "現金"}
TARGETS = {
    1: (0.10, 0.10, 0.50, 0.30),
    2: (0.15, 0.25, 0.45, 0.15),
    3: (0.25, 0.35, 0.30, 0.10),
    4: (0.25, 0.50, 0.20, 0.05),
    5: (0.25, 0.65, 0.10, 0.00),
}
SAMPLE_POSITIONS = [
    {"name": "台灣股票型（示範）", "asset": "tw_equity", "value": 90000, "tech_pct": 65},
    {"name": "全球股票型（示範）", "asset": "global_equity", "value": 140000, "tech_pct": 25},
    {"name": "投資級債券型（示範）", "asset": "bonds", "value": 180000, "tech_pct": 0},
    {"name": "現金（示範）", "asset": "cash", "value": 90000, "tech_pct": 0},
]
DISCLAIMER = "僅示範研究，假設非市場預測；非投資建議、非適合度或 Excel 數值等值認證。"


def number(value, *, min_value=0.0, max_value=1_000_000_000.0):
    if isinstance(value, bool):
        raise ValueError("不接受布林數字")
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("請輸入有效數值") from None
    if not isfinite(x) or not min_value <= x <= max_value:
        raise ValueError("數值超出允許範圍")
    return x


def choice(value, limit, name):
    if isinstance(value, bool) or type(value) is not int or not 0 <= value <= limit:
        raise ValueError(name + " 選項不正確")
    return value


def risk_assessment(data):
    """Two-dimension capacity/willingness model, conservatively bounded.

    Inputs: horizon 0..3; emergency_fund 0..3; income_stability 0..3;
    loss_tolerance 0..4; reaction 0..3; knowledge 0..3.
    Final RR=min(capacity tier, willingness tier) to avoid aggressive leakage.
    """
    if not isinstance(data, dict):
        raise ValueError("問卷格式錯誤")
    a = choice(data.get("horizon"), 3, "投資年期")
    b = choice(data.get("emergency_fund"), 3, "緊急預備金")
    c = choice(data.get("income_stability"), 3, "收入穩定性")
    d = choice(data.get("loss_tolerance"), 4, "跌幅忍受度")
    e = choice(data.get("reaction"), 3, "下跌應對")
    f = choice(data.get("knowledge"), 3, "投資知識")
    capacity = round((a + b + c) / 9 * 100)
    willingness = round((d + e + f) / 10 * 100)
    def tier(score):
        return min(5, max(1, int(score // 20) + (0 if score % 20 == 0 else 1)))
    # 0..20=RR1, >20..40=RR2, ...
    rr = min(tier(capacity), tier(willingness))
    return {
        "rr": rr,
        "capacity_score": capacity,
        "willingness_score": willingness,
        "total_score": min(capacity, willingness),
        "method": "風險承受能力與風險意願取較保守分級（展示版）",
        "target_allocation": dict(zip(ASSETS, TARGETS[rr])),
        "warnings": [
            "RR1–RR5 是研究示範模型，尚未經主管機關或金融機構核准適合度制度認證。",
            "未納入完整資產負債、保障需求與正式商品風險分類。",
        ],
    }


def allocation_plan(data):
    """Generate a no-sale cash-top-up plan and a separate rebalance plan.

    Holding tech_pct is a subset of equity exposure and is never summed as a
    distinct asset class. No inferred fund holdings, NAV or live prices.
    """
    if not isinstance(data, dict):
        raise ValueError("配置格式錯誤")
    rr = choice(data.get("rr"), 5, "RR")
    if rr == 0:
        raise ValueError("請選擇 RR1 至 RR5")
    positions = data.get("positions")
    if not isinstance(positions, list) or not 1 <= len(positions) <= 50:
        raise ValueError("持倉筆數不符")
    totals = {k: 0.0 for k in ASSETS}
    tech = 0.0
    combined = 0.0
    for p in positions:
        if not isinstance(p, dict):
            raise ValueError("持倉欄位錯誤")
        typ = p.get("asset")
        if typ not in ASSETS:
            raise ValueError("未知資產類別")
        amt = number(p.get("value"))
        tech_pct = number(p.get("tech_pct", 0), max_value=100)
        if typ in ("bonds", "cash") and tech_pct:
            raise ValueError("債券與現金不應計入科技股票曝險")
        totals[typ] += amt
        tech += amt * tech_pct / 100
        combined += amt
    contribution = number(data.get("new_money", 0), max_value=100_000_000)
    if combined <= 0:
        raise ValueError("既有持倉總額必須大於零")
    target = dict(zip(ASSETS, TARGETS[rr]))
    final_total = combined + contribution
    current_weights = {k: round(totals[k] / combined * 100, 2) for k in ASSETS}
    # Iteratively fill the largest cash-underweight first. No selling.
    gap = {k: max(0.0, final_total * target[k] - totals[k]) for k in ASSETS}
    need = sum(gap.values())
    additions = ({k: contribution * gap[k] / need for k in ASSETS}
                 if need > 0 else {k: contribution * target[k] for k in ASSETS})
    # Round to 1 TWD and reconcile the rounding remainder without lost cash.
    adds = {k: round(additions[k], 2) for k in ASSETS}
    adds[ASSETS[-1]] = round(contribution - sum(adds[k] for k in ASSETS[:-1]), 2)
    gradual_after = {k: totals[k] + adds[k] for k in ASSETS}
    immediate = {k: round(final_total * target[k] - totals[k], 2) for k in ASSETS}
    immediate[ASSETS[-1]] = round(contribution - sum(immediate[k] for k in ASSETS[:-1]), 2)
    return {
        "rr": rr, "total_existing": round(combined, 2),
        "new_money": round(contribution, 2), "after_total": round(final_total, 2),
        "asset_labels": LABELS, "current_amounts": {k: round(v, 2) for k,v in totals.items()},
        "current_weights_pct": current_weights,
        "target_weights_pct": {k: round(v*100, 2) for k,v in target.items()},
        "gradual_buy_only": adds,
        "gradual_after_weights_pct": {k: round(gradual_after[k]/final_total*100, 2) for k in ASSETS},
        "immediate_net_trades": immediate,
        "tech_overlap_pct_of_existing": round(tech/combined*100, 2),
        "global_equity_pct_of_existing": current_weights["global_equity"],
        "tw_equity_pct_of_existing": current_weights["tw_equity"],
        "method_note": "漸進調整僅配置新資金；立即最佳化金額正數為買入、負數為賣出（未含費稅）。",
        "data_quality_note": "曝險百分比為示範假設，不代表基金實際持股、國家或最新穿透式重疊。",
    }


def retirement_projection(data):
    if not isinstance(data, dict):
        raise ValueError("試算格式錯誤")
    initial = number(data.get("initial"))
    monthly = number(data.get("monthly_contribution"), max_value=10_000_000)
    withdraw = number(data.get("monthly_withdrawal"), max_value=10_000_000)
    years = number(data.get("years"), min_value=1, max_value=60)
    if years != int(years):
        raise ValueError("年期須為整數")
    target = number(data.get("target"), max_value=1e12)
    inflation = number(data.get("inflation_pct", 2), min_value=0, max_value=20) / 100
    costs = number(data.get("annual_fee_pct", 0.6), min_value=0, max_value=10) / 100
    scenarios = {"保守": -0.01, "基準": 0.03, "樂觀": 0.06}
    projections = []
    for name, gross in scenarios.items():
        net = max(-0.99, (1 + gross) * (1 - costs) - 1)
        monthly_rate = (1 + net) ** (1 / 12) - 1
        balance = initial
        exhausted_month = None
        series = []
        for m in range(1, int(years)*12+1):
            balance = max(0.0, balance * (1 + monthly_rate) + monthly - withdraw)
            if balance <= 0.0000001 and withdraw > monthly and exhausted_month is None:
                exhausted_month = m
            if m % 12 == 0:
                series.append(round(balance, 2))
        real_balance = balance / (1 + inflation) ** years
        projections.append({
            "scenario": name, "gross_return_pct": gross*100, "fee_pct": costs*100,
            "ending_nominal": round(balance), "ending_today_value": round(real_balance),
            "target_achievement_pct": round(100 * balance/target, 1) if target else None,
            "yearly_nominal": series,
            "exhausted_year": round(exhausted_month/12, 2) if exhausted_month else None,
        })
    return {"scenarios": projections, "years": int(years), "initial": initial,
            "monthly_contribution": monthly, "monthly_withdrawal": withdraw,
            "target": target, "inflation_pct": inflation*100, "disclaimer": DISCLAIMER,
            "assumptions": "名目年報酬 -1% / 3% / 6%；扣年費後按月複利，月末投入與提領。非歷史回測或機率預測。" }
