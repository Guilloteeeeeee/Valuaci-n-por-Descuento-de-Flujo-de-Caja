import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import streamlit as st
from yahooquery import Ticker

# ============================================================
# DCF EQUITY VALUATION — two-stage, owner-cash-flow approach
# Data source: Yahoo Finance via yahooquery. Educational tool.
# ============================================================
st.set_page_config(page_title="DCF | Valor intrínseco", page_icon="📈", layout="wide")

st.title("📈 Valoración de empresas — DCF de dos etapas")
st.caption(
    "Estimación del valor intrínseco del capital propio mediante flujos de caja al accionista. "
    "Datos de mercado y estados financieros obtenidos de Yahoo Finance; pueden contener retrasos, "
    "omisiones o revisiones. No es asesoramiento de inversión."
)

# -------------------------- Helpers --------------------------
def safe_float(value, default=np.nan):
    try:
        if value is None:
            return default
        if isinstance(value, (list, tuple, np.ndarray, pd.Series)):
            if len(value) == 0:
                return default
            value = value[0]
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def first_dict(data, symbol):
    """Yahooquery endpoints usually return {symbol: {...}}; tolerate DataFrames too."""
    if isinstance(data, dict):
        candidate = data.get(symbol)
        if isinstance(candidate, dict):
            return candidate
        if all(not isinstance(v, dict) for v in data.values()):
            return data
    if isinstance(data, pd.DataFrame) and not data.empty:
        frame = data
        if "symbol" in frame.columns:
            frame = frame[frame["symbol"].astype(str).str.upper() == symbol.upper()]
        if not frame.empty:
            return frame.iloc[-1].to_dict()
    return {}


def get_field_from_endpoints(ticker, symbol, endpoints, fields):
    for endpoint_name in endpoints:
        try:
            endpoint = getattr(ticker, endpoint_name)
            data = endpoint.get(symbol, {}) if isinstance(endpoint, dict) else endpoint
            record = first_dict(data, symbol)
            for field in fields:
                value = safe_float(record.get(field))
                if math.isfinite(value) and value > 0:
                    return value
        except Exception:
            continue
    return np.nan


def get_price(ticker, symbol):
    value = get_field_from_endpoints(
        ticker, symbol,
        ["price", "summary_detail", "financial_data"],
        ["regularMarketPrice", "currentPrice", "previousClose", "postMarketPrice"],
    )
    return value


def get_beta(ticker, symbol):
    try:
        record = first_dict(ticker.summary_detail, symbol)
        beta = safe_float(record.get("beta"))
        return beta if math.isfinite(beta) else np.nan
    except Exception:
        return np.nan


def get_shares(ticker, symbol):
    value = get_field_from_endpoints(
        ticker, symbol, ["key_stats", "price", "financial_data"],
        ["impliedSharesOutstanding", "sharesOutstanding", "sharesOutstandingRaw"],
    )
    if math.isfinite(value) and value > 0:
        return value
    # Fallback to the latest balance-sheet share count, if supplied.
    try:
        bs = ticker.balance_sheet(frequency="a")
        if isinstance(bs, pd.DataFrame) and not bs.empty:
            row = bs
            if "symbol" in row.columns:
                row = row[row["symbol"].astype(str).str.upper() == symbol.upper()]
            for field in ["OrdinarySharesNumber", "ShareIssued"]:
                if field in row.columns:
                    vals = pd.to_numeric(row[field], errors="coerce").dropna()
                    if not vals.empty and vals.iloc[-1] > 0:
                        return float(vals.iloc[-1])
    except Exception:
        pass
    return np.nan


def get_profile(ticker, symbol):
    try:
        return first_dict(ticker.asset_profile, symbol)
    except Exception:
        return {}


def get_annual_cashflows(ticker, symbol):
    """Return annual cash-flow records sorted oldest to newest."""
    try:
        cf = ticker.cash_flow(frequency="a")
    except Exception:
        try:
            cf = ticker.cash_flow()
        except Exception:
            return pd.DataFrame()
    if not isinstance(cf, pd.DataFrame) or cf.empty:
        return pd.DataFrame()
    df = cf.copy()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    elif df.index.name and df.index.name not in df.columns:
        df = df.reset_index()
    if "symbol" in df.columns:
        df = df[df["symbol"].astype(str).str.upper() == symbol.upper()]
    if df.empty:
        return df
    # Keep annual rows where periodType is available.
    if "periodType" in df.columns:
        annual = df[df["periodType"].astype(str).str.lower().isin(["12m", "annual", "a"])]
        if not annual.empty:
            df = annual
    date_col = next((c for c in ["asOfDate", "endDate", "period", "date"] if c in df.columns), None)
    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", utc=True)
        df = df.sort_values(date_col)
    return df.reset_index(drop=True)


def numeric_series(df, candidates):
    for col in candidates:
        if col in df.columns:
            series = pd.to_numeric(df[col], errors="coerce").dropna()
            if not series.empty:
                return series.reset_index(drop=True)
    return pd.Series(dtype=float)


def get_risk_free_rate():
    """Try the current US 10Y Treasury yield proxy (^TNX); return NaN on failure."""
    try:
        treasury = Ticker("^TNX")
        record = first_dict(treasury.price, "^TNX")
        # Yahoo's ^TNX quote is typically expressed in percentage points (e.g. 4.25).
        for field in ["regularMarketPrice", "postMarketPrice", "previousClose"]:
            value = safe_float(record.get(field))
            if math.isfinite(value) and 0.0 < value < 20.0:
                return value / 100.0
    except Exception:
        pass
    return np.nan


def clamp(value, low, high):
    return max(low, min(high, value))


def project_dcf(cfo_latest_m, capex_ratio, growth_start, discount_rate, terminal_growth,
                shares_m, years=10, margin_of_safety=0.20):
    """Equity DCF: forecast CFO and CapEx separately; discount levered FCF at cost of equity."""
    if not all(math.isfinite(x) for x in [cfo_latest_m, capex_ratio, growth_start, discount_rate, terminal_growth, shares_m]):
        return None
    if cfo_latest_m <= 0 or shares_m <= 0 or discount_rate <= terminal_growth:
        return None
    if terminal_growth <= -1 or discount_rate <= -1:
        return None

    # Fade from the scenario's initial CFO growth toward terminal growth by year 10.
    # CapEx/CFO gradually converges from its latest/normalized level toward a long-run
    # 5-year median where available (passed in as capex_ratio by caller).
    cfo = cfo_latest_m
    capex_to_cfo = clamp(capex_ratio, 0.0, 1.5)
    pv_explicit = 0.0
    rows = []
    for year in range(1, years + 1):
        growth = growth_start + (terminal_growth - growth_start) * (year / years)
        cfo *= (1 + growth)
        capex = cfo * capex_to_cfo
        fcf = cfo - capex
        pv = fcf / ((1 + discount_rate) ** year)
        pv_explicit += pv
        rows.append({
            "Año proyectado": year,
            "Crecimiento CFO": growth,
            "CFO proyectado (USD M)": cfo,
            "CapEx estimado (USD M)": capex,
            "FCF estimado (USD M)": fcf,
            "FCF a valor presente (USD M)": pv,
        })

    fcf_year_10 = rows[-1]["FCF estimado (USD M)"]
    fcf_year_11 = fcf_year_10 * (1 + terminal_growth)
    terminal_value = fcf_year_11 / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / ((1 + discount_rate) ** years)
    equity_value_m = pv_explicit + pv_terminal
    intrinsic = equity_value_m / shares_m
    return {
        "intrinsic_value": intrinsic,
        "entry_price": intrinsic * (1 - margin_of_safety),
        "equity_value_m": equity_value_m,
        "pv_explicit_m": pv_explicit,
        "pv_terminal_m": pv_terminal,
        "terminal_share": pv_terminal / equity_value_m if equity_value_m != 0 else np.nan,
        "rows": rows,
    }


def fmt_money(value, decimals=2):
    if not math.isfinite(safe_float(value)):
        return "N/D"
    return f"${value:,.{decimals}f}"

# ------------------------- User inputs -------------------------
with st.sidebar:
    st.header("Parámetros del modelo")
    ticker_symbol = st.text_input("Ticker estadounidense", "MSFT").strip().upper()
    st.caption("Ejemplos: MSFT, AAPL, GOOGL, META. Usá tickers de acciones ordinarias.")
    st.subheader("Supuestos de valoración")
    erp = st.number_input("Prima de riesgo de mercado (ERP, %)", min_value=0.0, max_value=12.0, value=4.5, step=0.1) / 100
    rf_manual = st.number_input("Tasa libre de riesgo manual (%) — fallback", min_value=0.0, max_value=15.0, value=4.25, step=0.05) / 100
    use_live_rf = st.checkbox("Intentar obtener Treasury 10Y automáticamente", value=True)
    terminal_growth = st.number_input("Crecimiento perpetuo base (%)", min_value=0.0, max_value=4.0, value=2.5, step=0.25) / 100
    margin_of_safety = st.slider("Margen de seguridad (%)", min_value=0, max_value=50, value=20, step=1) / 100
    forecast_years = st.select_slider("Años de proyección", options=[5, 7, 10, 12, 15], value=10)
    st.caption("La ERP, el crecimiento perpetuo y el margen de seguridad son supuestos editables, no datos observados.")

run = st.button("Calcular valoración", type="primary", use_container_width=True)

if run:
    if not ticker_symbol:
        st.error("Ingresá un ticker válido.")
        st.stop()

    with st.spinner(f"Obteniendo datos de {ticker_symbol} y calculando el DCF..."):
        try:
            company = Ticker(ticker_symbol)
            price = get_price(company, ticker_symbol)
            beta_raw = get_beta(company, ticker_symbol)
            shares = get_shares(company, ticker_symbol)
            profile = get_profile(company, ticker_symbol)
            sector = profile.get("sector", "No informado")
            industry = profile.get("industry", "No informada")
            cashflows = get_annual_cashflows(company, ticker_symbol)
            if cashflows.empty:
                st.error("Yahoo Finance no devolvió estados de flujo de caja anuales para este ticker.")
                st.stop()

            cfo_series = numeric_series(cashflows, ["OperatingCashFlow", "OperatingCashFlowFromContinuingOperations"])
            capex_series = numeric_series(cashflows, ["CapitalExpenditure", "CapitalExpenditures"])
            reported_fcf_series = numeric_series(cashflows, ["FreeCashFlow"])

            if cfo_series.empty or capex_series.empty:
                st.error("Faltan datos de CFO o CapEx. No se calculará una valoración basada en datos incompletos.")
                st.stop()

            # Work in USD millions. Yahoo may report CapEx as a negative cash outflow.
            cfo_series = cfo_series.astype(float)
            capex_abs = capex_series.astype(float).abs()
            n = min(len(cfo_series), len(capex_abs))
            cfo_series = cfo_series.iloc[-n:].reset_index(drop=True)
            capex_abs = capex_abs.iloc[-n:].reset_index(drop=True)
            fcf_calculated = cfo_series - capex_abs
            if not reported_fcf_series.empty:
                fcf_reported_latest = float(reported_fcf_series.iloc[-1])
            else:
                fcf_reported_latest = float(fcf_calculated.iloc[-1])

            cfo_latest = float(cfo_series.iloc[-1])
            capex_latest = float(capex_abs.iloc[-1])
            fcf_latest = fcf_reported_latest
            if abs(fcf_latest) > 1e12 or abs(cfo_latest) > 1e12:
                st.warning("Los valores recibidos parecen estar expresados en una escala inesperada. Revisá la fuente antes de usar la valoración.")

            # CAGR across available annual observations, only when endpoints are positive.
            cfo_growths = []
            cfo_vals = cfo_series.tolist()
            if len(cfo_vals) >= 2:
                for idx in range(1, len(cfo_vals)):
                    prev, curr = cfo_vals[idx - 1], cfo_vals[idx]
                    if prev > 0 and curr > 0:
                        cfo_growths.append((curr / prev) - 1)
            valid_positive = [v for v in cfo_vals if v > 0]
            if len(valid_positive) >= 2 and cfo_vals[0] > 0 and cfo_vals[-1] > 0:
                cagr_historical = (cfo_vals[-1] / cfo_vals[0]) ** (1 / (len(cfo_vals) - 1)) - 1
            else:
                cagr_historical = np.nan

            # Robust growth input: blend historical CAGR with median annual growth, then cap
            # the result to a disclosed reasonable range; no silent 4% minimum assumption.
            median_annual_growth = float(np.median(cfo_growths)) if cfo_growths else np.nan
            if math.isfinite(cagr_historical) and math.isfinite(median_annual_growth):
                cagr_base = 0.5 * cagr_historical + 0.5 * median_annual_growth
            elif math.isfinite(cagr_historical):
                cagr_base = cagr_historical
            elif math.isfinite(median_annual_growth):
                cagr_base = median_annual_growth
            else:
                cagr_base = np.nan
            if math.isfinite(cagr_base):
                cagr_base = clamp(cagr_base, -0.10, 0.25)

            # CapEx intensity: use the median of available annual CapEx/CFO ratios,
            # avoiding one unusual year driving the entire forecast.
            ratios = []
            for cfo_val, capex_val in zip(cfo_series.tolist(), capex_abs.tolist()):
                if cfo_val > 0:
                    ratios.append(capex_val / cfo_val)
            capex_ratio_latest = capex_latest / cfo_latest if cfo_latest > 0 else np.nan
            capex_ratio_median = float(np.median(ratios[-5:])) if ratios else np.nan
            capex_ratio = capex_ratio_median if math.isfinite(capex_ratio_median) else capex_ratio_latest

            rf_live = get_risk_free_rate() if use_live_rf else np.nan
            risk_free = rf_live if math.isfinite(rf_live) else rf_manual
            rf_source = "Yahoo Finance (^TNX), rendimiento Treasury 10Y" if math.isfinite(rf_live) else "Supuesto manual (fallback)"
            beta_used = beta_raw if math.isfinite(beta_raw) else 1.0
            # Do not silently clamp beta. Use the observed value and warn when it is extreme.
            cost_of_equity = risk_free + beta_used * erp

            st.success(f"Datos recibidos para {ticker_symbol}. Verificá los supuestos y la calidad de la información antes de usar el resultado.")
            st.markdown(f"### {ticker_symbol} — {profile.get('longName', ticker_symbol)}")
            st.caption(f"Sector: {sector} · Industria: {industry} · Fuente financiera: Yahoo Finance · Fecha de consulta: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Precio de mercado", fmt_money(price))
            m2.metric("Acciones (millones)", f"{shares / 1e6:,.2f}" if math.isfinite(shares) else "N/D")
            m3.metric("FCF reportado/estimado (USD M)", f"{fcf_latest / 1e6:,.1f}")
            m4.metric("CFO más reciente (USD M)", f"{cfo_latest / 1e6:,.1f}")
            m5.metric("Costo de capital propio", f"{cost_of_equity * 100:.2f}%" if math.isfinite(cost_of_equity) else "N/D")

            # The data from Yahoo is in dollars, convert to USD millions for model calculations.
            cfo_latest_m = cfo_latest / 1e6
            fcf_latest_m = fcf_latest / 1e6
            capex_ratio = capex_ratio

            warnings = []
            if not math.isfinite(price) or price <= 0:
                warnings.append("No se pudo validar el precio de mercado.")
            if not math.isfinite(shares) or shares <= 0:
                warnings.append("No se pudo validar la cantidad de acciones en circulación.")
            if not math.isfinite(beta_raw):
                warnings.append("Beta no disponible: se usa beta neutral de 1,00 como supuesto, no como dato observado.")
            elif beta_raw < 0 or beta_raw > 3:
                warnings.append("La beta observada es extrema; verificá la fuente y la idoneidad del CAPM.")
            if len(cfo_series) < 3:
                warnings.append("Hay menos de tres observaciones anuales de CFO: el crecimiento histórico es poco robusto.")
            if cfo_latest <= 0:
                warnings.append("CFO más reciente no positivo: el DCF de este tipo no es adecuado sin una proyección de recuperación.")
            if not math.isfinite(cagr_base):
                warnings.append("No se pudo estimar una tasa de crecimiento histórica fiable.")
            if math.isfinite(cagr_base) and (cagr_base <= -0.05 or cagr_base >= 0.20):
                warnings.append("El crecimiento histórico está en un extremo; se limita el uso mecánico de la extrapolación y conviene revisar los estados financieros.")
            if math.isfinite(capex_ratio) and capex_ratio > 0.8:
                warnings.append("CapEx/CFO es muy alto; pequeñas variaciones en la inversión pueden cambiar sustancialmente el FCF proyectado.")
            if math.isfinite(fcf_latest) and fcf_latest <= 0:
                warnings.append("El FCF más reciente es negativo o nulo. El modelo no lo proyecta multiplicándolo por una tasa; requiere un plan explícito de recuperación.")
            if cost_of_equity <= terminal_growth:
                warnings.append("La tasa de descuento debe ser superior al crecimiento perpetuo. No se puede calcular un valor terminal válido con estos parámetros.")
            if str(sector).lower() in {"financial services", "financial"} or any(x in str(industry).lower() for x in ["bank", "insurance", "reit"]):
                warnings.append("Este sector puede requerir un modelo específico (bancos/aseguradoras: exceso de rentabilidad o dividendos; REITs: AFFO). Interpretá el DCF general con cautela.")

            if warnings:
                with st.expander("⚠️ Controles de calidad y advertencias", expanded=True):
                    for warning in warnings:
                        st.warning(warning)

            if not (math.isfinite(shares) and shares > 0 and cfo_latest_m > 0 and math.isfinite(cagr_base) and math.isfinite(capex_ratio)):
                st.error("No hay datos suficientes para generar una valoración defendible. No se muestra un precio objetivo ficticio.")
                st.stop()

            if not math.isfinite(fcf_latest) or fcf_latest <= 0:
                st.error("El FCF más reciente es negativo o no está disponible. Esta versión no estima una recuperación de caja positiva automáticamente; hace falta definir un escenario explícito de recuperación antes de valorar la acción.")
                st.stop()

            if cost_of_equity <= terminal_growth:
                st.error("El costo del capital propio debe ser mayor que el crecimiento perpetuo. Ajustá los supuestos en la barra lateral.")
                st.stop()

            # Scenario assumptions: moderate adjustments, transparent and editable in the sidebar.
            scenarios = [
                ("Pesimista", max(-0.15, cagr_base - 0.05), cost_of_equity + 0.01, max(0.0, terminal_growth - 0.005)),
                ("Base", cagr_base, cost_of_equity, terminal_growth),
                ("Optimista", min(0.30, cagr_base + 0.05), max(0.01, cost_of_equity - 0.01), min(0.04, terminal_growth + 0.005)),
            ]
            results = {}
            for name, growth, discount, g in scenarios:
                # A scenario is invalid if discount rate is not strictly above perpetual growth.
                if discount <= g:
                    results[name] = None
                else:
                    results[name] = project_dcf(
                        cfo_latest_m=cfo_latest_m,
                        capex_ratio=capex_ratio,
                        growth_start=growth,
                        discount_rate=discount,
                        terminal_growth=g,
                        shares_m=shares / 1e6,
                        years=forecast_years,
                        margin_of_safety=margin_of_safety,
                    )

            st.divider()
            st.subheader("Valor intrínseco estimado por escenario")
            columns = st.columns(3)
            for col, (name, growth, discount, g) in zip(columns, scenarios):
                result = results[name]
                with col:
                    st.markdown(f"#### {name}")
                    if result is None:
                        st.error("Escenario inválido: tasa de descuento ≤ crecimiento perpetuo.")
                        continue
                    value = result["intrinsic_value"]
                    entry = result["entry_price"]
                    upside = (value / price - 1) * 100 if math.isfinite(price) and price > 0 else np.nan
                    st.metric("Valor intrínseco por acción", fmt_money(value), delta=f"{upside:+.1f}% vs. mercado" if math.isfinite(upside) else None)
                    st.metric(f"Precio con margen ({margin_of_safety:.0%})", fmt_money(entry))
                    st.caption(f"Crec. inicial CFO: {growth:.2%} · Costo equity: {discount:.2%} · g perpetuo: {g:.2%}")
                    st.caption(f"Valor terminal: {result['terminal_share']:.1%} del valor total")

            base_result = results.get("Base")
            if base_result:
                st.divider()
                st.subheader("Lectura de la valoración base")
                a, b, c, d = st.columns(4)
                a.metric("Potencial al valor intrínseco", f"{(base_result['intrinsic_value'] / price - 1):.1%}" if math.isfinite(price) and price > 0 else "N/D")
                b.metric("Precio de entrada estimado", fmt_money(base_result["entry_price"]))
                c.metric("Valor terminal / valor total", f"{base_result['terminal_share']:.1%}")
                d.metric("CapEx / CFO usado", f"{capex_ratio:.1%}")
                if base_result["terminal_share"] > 0.75:
                    st.warning("Más del 75% de la valoración proviene del valor terminal. El resultado es especialmente sensible a los supuestos de largo plazo.")

                # Sensitivity table: discount rate vs terminal growth, with initial CFO growth fixed.
                st.subheader("Matriz de sensibilidad — escenario base")
                discount_grid = sorted(set([max(0.01, cost_of_equity - 0.02), max(0.01, cost_of_equity - 0.01), cost_of_equity, cost_of_equity + 0.01, cost_of_equity + 0.02]))
                g_grid = sorted(set([max(0.0, terminal_growth - 0.01), max(0.0, terminal_growth - 0.005), terminal_growth, min(0.04, terminal_growth + 0.005), min(0.04, terminal_growth + 0.01)]))
                matrix = []
                for disc in discount_grid:
                    row = {"Costo equity ↓ / g →": f"{disc:.2%}"}
                    for g in g_grid:
                        if disc <= g:
                            row[f"g = {g:.2%}"] = "N/V"
                        else:
                            sens = project_dcf(cfo_latest_m, capex_ratio, cagr_base, disc, g, shares / 1e6, forecast_years, margin_of_safety)
                            row[f"g = {g:.2%}"] = fmt_money(sens["intrinsic_value"]) if sens else "N/V"
                    matrix.append(row)
                st.dataframe(pd.DataFrame(matrix), use_container_width=True, hide_index=True)

                with st.expander("🔎 Proyección anual detallada — escenario base"):
                    table = pd.DataFrame(base_result["rows"])
                    for colname in ["Crecimiento CFO"]:
                        table[colname] = table[colname].map(lambda x: f"{x:.2%}")
                    for colname in ["CFO proyectado (USD M)", "CapEx estimado (USD M)", "FCF estimado (USD M)", "FCF a valor presente (USD M)"]:
                        table[colname] = table[colname].map(lambda x: f"${x:,.1f} M")
                    st.dataframe(table, use_container_width=True, hide_index=True)

            with st.expander("📚 Metodología y limitaciones del modelo"):
                st.markdown(
                    """
                    **Tipo de valoración.** Este modelo aproxima el valor del capital propio mediante un flujo de caja al accionista: CFO menos CapEx. Como el CFO normalmente ya incorpora intereses pagados, el flujo se descuenta al costo del capital propio estimado por CAPM y no se resta nuevamente la deuda neta. Es una aproximación, no un FCFE completo, porque no incorpora explícitamente la financiación neta mediante deuda.

                    **Proyección.** El CFO se proyecta desde el último dato anual y su crecimiento converge linealmente hacia el crecimiento perpetuo. El CapEx se estima como proporción del CFO usando la mediana histórica disponible (hasta cinco observaciones), en vez de suponer que el FCF crece automáticamente al mismo ritmo que el CFO. No modela por separado CapEx de mantenimiento y expansión ni el retorno de cada inversión.

                    **Tasa de descuento.** CAPM = tasa libre de riesgo + beta × prima de riesgo de mercado. La beta proviene de Yahoo Finance; si no está disponible, se usa beta 1,00 como supuesto explícito. La ERP es editable. El Treasury 10Y se intenta obtener de ^TNX; si falla, se utiliza la tasa manual indicada en la barra lateral.

                    **Valor terminal.** Se utiliza Gordon Growth: FCF del año siguiente dividido por (costo del capital propio − crecimiento perpetuo). El costo de capital debe superar la tasa perpetua. El valor terminal puede representar una parte importante de la estimación, por lo que se informa su participación y una matriz de sensibilidad.

                    **Limitaciones.** Los datos de Yahoo Finance pueden estar incompletos o tener retrasos. El modelo no es apropiado sin ajustes para bancos, aseguradoras, REITs, empresas con CFO/FCF negativo, compañías en reestructuración ni negocios extremadamente cíclicos. No incorpora explícitamente SBC, dilución futura, adquisiciones, cambios extraordinarios de capital de trabajo, ni escenarios de CapEx separados. La salida es una estimación dependiente de supuestos, no un precio objetivo garantizado.
                    """
                )

        except Exception as exc:
            st.error(f"No se pudo completar la valoración de {ticker_symbol}: {exc}")
            st.info("Verificá el ticker, la conectividad y la versión de yahooquery. Si el error persiste, revisá los registros de Streamlit Cloud.")
else:
    st.info("Ingresá un ticker y presioná **Calcular valoración**. La aplicación mostrará los datos utilizados, los tres escenarios, la matriz de sensibilidad y las advertencias de calidad.")

st.divider()
st.caption("Herramienta educativa de análisis financiero. No constituye asesoramiento de inversión ni una recomendación de compra o venta.")
