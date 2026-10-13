"""
data_yahoo.py — Capa de datos (Yahoo Finance vía yahooquery) -> CompanyData normalizado.

Separación deliberada:
  * build_company(): lógica pura (recibe módulos y DataFrames ya descargados) -> testeable sin red.
  * load_company():  hace la descarga y llama a build_company().

Reglas de datos:
  * TTM: fila 'TTM' de Yahoo; si no, suma de los últimos 4 trimestres consecutivos (brechas 60-120 días).
    El último año fiscal solo se usa si allow_annual=True (por defecto NO).
  * Todos los montos se convierten a la moneda de COTIZACIÓN (fx spot) y se expresan en millones.
  * Acciones = capitalización / precio (equivale a acciones por ADR/ratio y maneja clases múltiples).
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from dcf_models import CompanyData

RF_FALLBACK = 0.0425
PENCE = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ZAC": ("ZAR", 100.0),
         "ILA": ("ILS", 100.0)}


class DataError(Exception):
    pass


FLOW_FIELDS = [
    "TotalRevenue", "OperatingIncome", "EBIT", "NetIncomeCommonStockholders", "NetIncome",
    "InterestExpense", "InterestExpenseNonOperating", "TaxProvision", "PretaxIncome", "TaxRateForCalcs",
    "DepreciationAndAmortization", "ReconciledDepreciation", "CapitalExpenditure", "ChangeInWorkingCapital",
    "CommonStockDividendPaid", "CashDividendsPaid", "RepurchaseOfCapitalStock", "GainLossOnSaleOfPPE",
]
BAL_FIELDS = [
    "TotalDebt", "CapitalLeaseObligations", "CashCashEquivalentsAndShortTermInvestments", "MinorityInterest",
    "StockholdersEquity", "CommonStockEquity", "InvestedCapital", "OrdinarySharesNumber",
]
ALL_FIELDS = FLOW_FIELDS + BAL_FIELDS


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def sf(x) -> Optional[float]:
    """float seguro: acepta números, {'raw': x}, strings numéricos; None si no válido."""
    if isinstance(x, dict):
        x = x.get("raw", None)
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else v


def mod(d, sym: str) -> dict:
    """Extrae el dict de un módulo de yahooquery ({sym: dict | str de error})."""
    if not isinstance(d, dict):
        return {}
    x = d.get(sym)
    return x if isinstance(x, dict) else {}


def _frame(df, sym: str) -> pd.DataFrame:
    """Normaliza la salida de get_financial_data a un DataFrame plano de un solo símbolo."""
    if df is None or isinstance(df, (str, dict)):
        return pd.DataFrame(columns=["asOfDate", "periodType"])
    d = df.reset_index() if "symbol" not in df.columns else df.copy()
    if "symbol" in d.columns:
        d = d[d["symbol"].astype(str).str.upper() == sym.upper()]
    if "asOfDate" not in d.columns:
        return pd.DataFrame(columns=["asOfDate", "periodType"])
    d["asOfDate"] = pd.to_datetime(d["asOfDate"])
    return d.sort_values("asOfDate")


def _series(df: pd.DataFrame, ptype: str, field: str) -> pd.DataFrame:
    if field not in df.columns:
        return pd.DataFrame(columns=["asOfDate", field])
    r = df[(df["periodType"] == ptype)][["asOfDate", field]].dropna()
    return r.sort_values("asOfDate")


class Fundamentals:
    """Acceso TTM / anual / último saldo sobre los DataFrames anual (a) y trimestral (q)."""

    def __init__(self, dfa: pd.DataFrame, dfq: pd.DataFrame, allow_annual: bool = False):
        self.a, self.q, self.allow_annual = dfa, dfq, allow_annual
        self.sources: Dict[str, str] = {}
        self.asof: Dict[str, pd.Timestamp] = {}

    def ttm(self, field: str) -> Optional[float]:
        for df in (self.q, self.a):
            s = _series(df, "TTM", field)
            if len(s):
                self.sources[field] = "TTM Yahoo"
                self.asof[field] = s["asOfDate"].iloc[-1]
                return float(s[field].iloc[-1])
        s = _series(self.q, "3M", field).tail(4)
        if len(s) == 4:
            gaps = s["asOfDate"].diff().dt.days.dropna()
            if all(60 <= g <= 120 for g in gaps):
                self.sources[field] = "suma 4 trimestres"
                self.asof[field] = s["asOfDate"].iloc[-1]
                return float(s[field].sum())
        if self.allow_annual:
            s = _series(self.a, "12M", field)
            if len(s):
                self.sources[field] = "ÚLTIMO AÑO FISCAL (no TTM)"
                self.asof[field] = s["asOfDate"].iloc[-1]
                return float(s[field].iloc[-1])
        return None

    def latest(self, field: str) -> Optional[float]:
        """Último saldo (balance): trimestral más reciente; si no, anual."""
        cands = []
        for df, pt in ((self.q, "3M"), (self.a, "12M")):
            s = _series(df, pt, field)
            if len(s):
                cands.append((s["asOfDate"].iloc[-1], float(s[field].iloc[-1])))
        if not cands:
            return None
        return max(cands, key=lambda x: x[0])[1]

    def annual_hist(self, field: str, n: int = 4) -> List[Optional[float]]:
        s = _series(self.a, "12M", field).tail(n)
        return [float(v) for v in s[field].tolist()]

    def annual_hist_dates(self, field: str, n: int = 4):
        s = _series(self.a, "12M", field).tail(n)
        return list(s["asOfDate"])

    def currency(self) -> Optional[str]:
        for df in (self.q, self.a):
            if "currencyCode" in df.columns and df["currencyCode"].notna().any():
                return str(df["currencyCode"].dropna().iloc[-1])
        return None


def _first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def analyst_growth(earnings_trend_mod: dict) -> Tuple[Optional[float], Optional[float]]:
    g0 = g1 = None
    trend = earnings_trend_mod.get("trend") if isinstance(earnings_trend_mod, dict) else None
    for item in trend or []:
        if not isinstance(item, dict):
            continue
        per = item.get("period")
        est = item.get("revenueEstimate") or {}
        g = sf(est.get("growth"))
        if per == "0y":
            g0 = g
        elif per == "+1y":
            g1 = g
    return g0, g1


# ----------------------------------------------------------------------------
# Construcción pura
# ----------------------------------------------------------------------------
def build_company(sym: str, price_m: dict, summary: dict, keystats: dict, profile: dict, fin: dict,
                  trend: dict, dfa, dfq, fx_fetcher: Callable[[str, str], Optional[float]],
                  rf: float = RF_FALLBACK, allow_annual: bool = False) -> Tuple[CompanyData, List[str]]:
    warns: List[str] = []
    qt = str(price_m.get("quoteType") or "EQUITY").upper()
    px = sf(price_m.get("regularMarketPrice"))
    if px is None or px <= 0:
        raise DataError(f"No encontré precio para '{sym}'. Verificá el ticker (ej.: BRK-B, SAP.DE, 7203.T, TSM).")

    qcur = str(price_m.get("currency") or summary.get("currency") or "USD")
    pence_div = 1.0
    if qcur in PENCE:
        qcur, pence_div = PENCE[qcur]
        px = px / pence_div
        warns.append(f"Precio cotizado en subunidad ({pence_div:.0f}:1): convertido a {qcur}.")

    dfa_, dfq_ = _frame(dfa, sym), _frame(dfq, sym)
    F = Fundamentals(dfa_, dfq_, allow_annual)

    fin_cur = str(fin.get("financialCurrency") or F.currency() or qcur)
    fxr = 1.0
    if fin_cur != qcur:
        fxr = fx_fetcher(fin_cur, qcur)
        if fxr is None or fxr <= 0:
            raise DataError(f"No pude obtener el tipo de cambio {fin_cur}->{qcur}.")

    def M(x):  # a millones de moneda de cotización
        return None if x is None else x * fxr / 1e6

    mc = sf(price_m.get("marketCap")) or sf(summary.get("marketCap"))
    if mc is not None and pence_div != 1.0:
        pass  # se verifica abajo contra las acciones reportadas
    rep_sh = _first(sf(keystats.get("sharesOutstanding")), F.latest("OrdinarySharesNumber"))
    if mc and mc > 0:
        shares = mc / px
    else:
        shares = _first(sf(keystats.get("impliedSharesOutstanding")), rep_sh)
        if not shares:
            raise DataError("No encontré capitalización ni acciones en circulación.")
        mc = shares * px
        warns.append("Capitalización estimada como acciones × precio.")
    if pence_div != 1.0 and rep_sh and rep_sh > 0:
        ratio = shares / rep_sh
        if 50 <= ratio <= 200:
            shares, mc = shares / pence_div, mc / pence_div
            warns.append("Capitalización de Yahoo venía en subunidad: corregida.")
    shares_m, mc_m = shares / 1e6, mc / 1e6
    rep_sh_m = rep_sh / 1e6 if rep_sh else None

    # --- flujos TTM
    rev = F.ttm("TotalRevenue")
    ebit = _first(F.ttm("OperatingIncome"), F.ttm("EBIT"))
    ni = _first(F.ttm("NetIncomeCommonStockholders"), F.ttm("NetIncome"))
    da = _first(F.ttm("DepreciationAndAmortization"), F.ttm("ReconciledDepreciation"))
    capex = F.ttm("CapitalExpenditure")
    dwc = F.ttm("ChangeInWorkingCapital")
    intexp = _first(F.ttm("InterestExpense"), F.ttm("InterestExpenseNonOperating"))
    tax = F.ttm("TaxProvision")
    pretax = F.ttm("PretaxIncome")
    div = _first(F.ttm("CommonStockDividendPaid"), F.ttm("CashDividendsPaid"))
    bb = F.ttm("RepurchaseOfCapitalStock")
    gains = F.ttm("GainLossOnSaleOfPPE")

    tax_eff = None
    if tax is not None and pretax is not None and pretax > 0:
        r = tax / pretax
        if 0.0 <= r <= 0.5:
            tax_eff = r
    if tax_eff is None:
        t_ann = _series(dfa_, "12M", "TaxRateForCalcs")
        if len(t_ann):
            v = float(t_ann["TaxRateForCalcs"].iloc[-1])
            tax_eff = v if 0 <= v <= 0.5 else None

    src = F.sources.get("TotalRevenue", "sin datos")
    asof = F.asof.get("TotalRevenue")
    if "ÚLTIMO AÑO" in src:
        warns.append("Se usó el ÚLTIMO AÑO FISCAL porque no hay TTM disponible (opción activada).")
    if asof is not None:
        age = (pd.Timestamp.today().normalize() - asof).days
        if age > 200:
            warns.append(f"Los últimos datos contables son de hace {age} días ({asof.date()}).")

    # --- balance
    cash = F.latest("CashCashEquivalentsAndShortTermInvestments")
    debt = F.latest("TotalDebt")
    lease = F.latest("CapitalLeaseObligations")
    minority = F.latest("MinorityInterest")
    equity = _first(F.latest("CommonStockEquity"), F.latest("StockholdersEquity"))
    ic = F.latest("InvestedCapital")
    if cash is None:
        warns.append("Sin dato de caja: se asumió 0.")
    if debt is None:
        warns.append("Sin dato de deuda: se asumió 0.")

    # --- historia anual
    rev_h = F.annual_hist("TotalRevenue")
    ebit_h_raw = F.annual_hist("OperatingIncome") or F.annual_hist("EBIT")
    ni_h = F.annual_hist("NetIncomeCommonStockholders") or F.annual_hist("NetIncome")
    eq_h = F.annual_hist("CommonStockEquity") or F.annual_hist("StockholdersEquity")
    # alinear EBIT con ingresos por fecha
    rd = F.annual_hist_dates("TotalRevenue")
    ed = F.annual_hist_dates("OperatingIncome") or F.annual_hist_dates("EBIT")
    emap = dict(zip(ed, ebit_h_raw))
    ebit_h = [emap.get(d) for d in rd]

    g0, g1 = analyst_growth(trend)

    co = CompanyData(
        ticker=sym.upper(),
        name=str(price_m.get("longName") or price_m.get("shortName") or sym),
        sector=str(profile.get("sector") or ""), industry=str(profile.get("industry") or ""),
        country=str(profile.get("country") or ""), quote_type=qt,
        price=px, currency=qcur, fin_currency=fin_cur, fx=fxr,
        shares_m=shares_m, reported_shares_m=rep_sh_m, market_cap_m=mc_m,
        beta=_first(sf(summary.get("beta")), sf(keystats.get("beta"))), rf=rf,
        revenue_ttm=M(rev) or 0.0, ebit_ttm=M(ebit) or 0.0, da_ttm=abs(M(da) or 0.0),
        capex_ttm=abs(M(capex) or 0.0), nwc_inv_ttm=-(M(dwc) or 0.0), tax_rate_eff=tax_eff,
        interest_expense_ttm=abs(M(intexp)) if intexp is not None else None,
        revenue_hist=[M(x) for x in rev_h], ebit_hist=[M(x) for x in ebit_h],
        invested_capital=M(ic), cash=M(cash) or 0.0, debt=M(debt) or 0.0,
        lease_debt=min(M(lease) or 0.0, M(debt) or 0.0), minority=M(minority) or 0.0,
        rev_g_y0=g0, rev_g_y1=g1,
        equity=M(equity), ni_ttm=M(ni), ni_hist=[M(x) for x in ni_h], equity_hist=[M(x) for x in eq_h],
        dividends_ttm=abs(M(div) or 0.0), buybacks_ttm=abs(M(bb) or 0.0),
        gains_adj_ttm=M(gains) or 0.0,
        target_price=sf(fin.get("targetMeanPrice")),
        n_analysts=int(sf(fin.get("numberOfAnalystOpinions")) or 0) or None,
        ttm_source=src, asof=str(asof.date()) if asof is not None else "",
    )
    if co.beta is None:
        warns.append("Yahoo no informa beta.")
    if g0 is None and g1 is None:
        warns.append("Sin estimaciones de analistas de ingresos: el crecimiento inicial sale de la historia.")
    if qt != "EQUITY":
        warns.append(f"Instrumento tipo {qt}: el DCF no aplica a fondos/ETF.")
    return co, warns


# ----------------------------------------------------------------------------
# Descarga
# ----------------------------------------------------------------------------
def get_fx(fin: str, quote: str) -> Optional[float]:
    """Cuántas unidades de 'quote' vale 1 unidad de 'fin' (spot de Yahoo)."""
    from yahooquery import Ticker
    for pair, inv in ((f"{fin}{quote}=X", False), (f"{quote}{fin}=X", True)):
        try:
            d = Ticker(pair).price
            v = sf(d.get(pair, {}).get("regularMarketPrice")) if isinstance(d, dict) else None
            if v and v > 0:
                return 1.0 / v if inv else v
        except Exception:
            continue
    return None


def get_risk_free() -> Tuple[float, bool]:
    """Rendimiento del bono a 10 años de EE.UU. (^TNX). (valor, vino_de_yahoo)"""
    try:
        from yahooquery import Ticker
        d = Ticker("^TNX").price
        v = sf(d.get("^TNX", {}).get("regularMarketPrice"))
        if v:
            if v > 20:
                v /= 10.0
            r = v / 100.0
            if 0.005 <= r <= 0.12:
                return r, True
    except Exception:
        pass
    return RF_FALLBACK, False


def load_company(sym: str, allow_annual: bool = False) -> Tuple[CompanyData, List[str]]:
    from yahooquery import Ticker
    sym = sym.strip().upper()
    if not sym:
        raise DataError("Ingresá un ticker.")
    t = Ticker(sym, timeout=20)
    price_m = mod(t.price, sym)
    if not price_m:
        raise DataError(f"Yahoo no devolvió datos para '{sym}'. Verificá el ticker y el sufijo de mercado.")
    summary, keystats = mod(t.summary_detail, sym), mod(t.key_stats, sym)
    profile, fin = mod(t.asset_profile, sym), mod(t.financial_data, sym)
    trend = mod(t.earnings_trend, sym)
    dfa = t.get_financial_data(ALL_FIELDS, frequency="a", trailing=True)
    dfq = t.get_financial_data(ALL_FIELDS, frequency="q", trailing=True)
    if isinstance(dfa, (str, dict)) and isinstance(dfq, (str, dict)):
        raise DataError(f"Yahoo no tiene estados contables para '{sym}'.")
    rf, ok = get_risk_free()
    co, warns = build_company(sym, price_m, summary, keystats, profile, fin, trend, dfa, dfq, get_fx,
                              rf=rf, allow_annual=allow_annual)
    if not ok:
        warns.append(f"No pude leer ^TNX: se usó Rf = {RF_FALLBACK:.2%}.")
    return co, warns
