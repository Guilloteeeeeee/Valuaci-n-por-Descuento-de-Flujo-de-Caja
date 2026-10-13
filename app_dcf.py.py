import math
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st
from yahooquery import Ticker

st.set_page_config(page_title="DCF | Valor intrínseco", page_icon="📈", layout="wide")
st.title("📈 Valoración de empresas — DCF de dos etapas")
st.caption(
    "Estimación orientativa del valor del capital propio a partir de CFO menos CapEx. "
    "Los datos provienen de Yahoo Finance y pueden contener omisiones o revisiones."
)

# ------------------------------- Helpers -------------------------------
def safe_float(value, default=np.nan):
    try:
        if value is None:
            return default
        if isinstance(value, (list, tuple, np.ndarray, pd.Series)):
            if len(value) == 0:
                return default
            value = value[0]
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def first_dict(data, symbol):
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


def endpoint_field(ticker, symbol, endpoints, fields):
    for endpoint_name in endpoints:
        try:
            endpoint = getattr(ticker, endpoint_name)
            record = first_dict(endpoint, symbol)
            for field in fields:
                value = safe_float(record.get(field))
                if math.isfinite(value) and value > 0:
                    return value
        except Exception:
            continue
    return np.nan


def get_price(ticker, symbol):
    return endpoint_field(ticker, symbol, ["price", "summary_detail", "financial_data"],
                          ["regularMarketPrice", "currentPrice", "previousClose", "postMarketPrice"])


def get_beta(ticker, symbol):
    try:
        return safe_float(first_dict(ticker.summary_detail, symbol).get("beta"))
    except Exception:
        return np.nan


def get_shares(ticker, symbol):
    value = endpoint_field(ticker, symbol, ["key_stats", "price", "financial_data"],
                           ["impliedSharesOutstanding", "sharesOutstanding", "sharesOutstandingRaw"])
    if math.isfinite(value) and value > 0:
        return value
    try:
        bs = ticker.balance_sheet(frequency="a")
        if isinstance(bs, pd.DataFrame) and not bs.empty:
            df = bs.reset_index() if isinstance(bs.index, pd.MultiIndex) or bs.index.name else bs.copy()
            if "symbol" in df.columns:
                df = df[df["symbol"].astype(str).str.upper() == symbol.upper()]
            for field in ["OrdinarySharesNumber", "ShareIssued"]:
                if field in df.columns:
                    vals = pd.to_numeric(df[field], errors="coerce").dropna()
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
    try:
        raw = ticker.cash_flow(frequency="a")
    except Exception:
        try:
            raw = ticker.cash_flow()
        except Exception:
            return pd.DataFrame()
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    if isinstance(df.index, pd.MultiIndex) or (df.index.name and df.index.name not in df.columns):
        df = df.reset_index()
    if "symbol" in df.columns:
        df = df[df["symbol"].astype(str).str.upper() == symbol.upper()]
    if df.empty:
        return df
    if "periodType" in df.columns:
        annual = df[df["periodType"].astype(str).str.lower().isin(["12m", "annual", "a"])]
        if not annual.empty:
            df = annual
    date_col = next((c for c in ["asOfDate", "endDate", "period", "date"] if c in df.columns), None)
    if date_col is None:
        st.warning("Yahoo no devolvió una fecha fiscal identificable. No es posible verificar la alineación por ejercicio.")
        return pd.DataFrame()
    df["Fiscal date"] = pd.to_datetime(df[date_col], errors="coerce", utc=True).dt.tz_localize(None)
    df = df.dropna(subset=["Fiscal date"]).sort_values("Fiscal date")
    # One record per fiscal date, keeping the last record if Yahoo duplicates a period.
    return df.drop_duplicates(subset=["Fiscal date"], keep="last").reset_index(drop=True)


def pick_column(df, candidates):
    for col in candidates:
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            if vals.notna().any():
                return col
    return None


def get_risk_free_rate():
    try:
        t = Ticker("^TNX")
        record = first_dict(t.price, "^TNX")
        for field in ["regularMarketPrice", "postMarketPrice", "previousClose"]:
            value = safe_float(record.get(field))
            if math.isfinite(value) and 0 < value < 20:
                return value / 100
    except Exception:
        pass
    return np.nan


def clamp(value, low, high):
    return max(low, min(high, value))


def project_dcf(cfo_latest_m, latest_capex_ratio, long_run_capex_ratio, growth_start,
                discount_rate, terminal_growth, shares_m, years=10, margin_of_safety=0.20):
    vals = [cfo_latest_m, latest_capex_ratio, long_run_capex_ratio, growth_start,
            discount_rate, terminal_growth, shares_m]
    if not all(math.isfinite(x) for x in vals):
        return None
    if cfo_latest_m <= 0 or shares_m <= 0 or discount_rate <= terminal_growth:
        return None
    if discount_rate <= -1 or terminal_growth <= -1:
        return None

    cfo = cfo_latest_m
    pv_explicit = 0.0
    rows = []
    for year in range(1, years + 1):
        # CFO growth fades linearly toward terminal growth.
        growth = growth_start + (terminal_growth - growth_start) * (year / years)
        cfo *= (1 + growth)
        # CapEx intensity converges from the latest fiscal-year ratio to the
        # historical median ratio; no automatic assumption that CapEx falls.
        weight = year / years
        capex_ratio = latest_capex_ratio + (long_run_capex_ratio - latest_capex_ratio) * weight
        capex = cfo * capex_ratio
        fcf = cfo - capex
        pv = fcf / ((1 + discount_rate) ** year)
        pv_explicit += pv
        rows.append({
            "Año proyectado": year,
            "Crecimiento CFO": growth,
            "CFO proyectado (USD M)": cfo,
            "CapEx/CFO": capex_ratio,
            "CapEx estimado (USD M)": capex,
            "FCF estimado (USD M)": fcf,
            "FCF a valor presente (USD M)": pv,
        })

    fcf_next = rows[-1]["FCF estimado (USD M)"] * (1 + terminal_growth)
    if fcf_next <= 0:
        return None
    terminal_value = fcf_next / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / ((1 + discount_rate) ** years)
    equity_value_m = pv_explicit + pv_terminal
    if equity_value_m <= 0:
        return None
    intrinsic = equity_value_m / shares_m
    return {
        "intrinsic_value": intrinsic,
        "entry_price": intrinsic * (1 - margin_of_safety),
        "equity_value_m": equity_value_m,
        "pv_explicit_m": pv_explicit,
        "pv_terminal_m": pv_terminal,
        "terminal_share": pv_terminal / equity_value_m,
        "rows": rows,
    }


def fmt_money(value, decimals=2):
    value = safe_float(value)
    return "N/D" if not math.isfinite(value) else f"${value:,.{decimals}f}"

# ----------------------------- User inputs -----------------------------
with st.sidebar:
    st.header("Parámetros del modelo")
    ticker_symbol = st.text_input("Ticker estadounidense", "MSFT").strip().upper()
    st.caption("Ejemplos: MSFT, AAPL, AMZN, GOOGL, META. Acciones ordinarias.")
    st.subheader("Supuestos de valoración")
    erp = st.number_input("Prima de riesgo de mercado (ERP, %)", 0.0, 12.0, 4.5, 0.1) / 100
    rf_manual = st.number_input("Tasa libre de riesgo manual (%) — fallback", 0.0, 15.0, 4.25, 0.05) / 100
    use_live_rf = st.checkbox("Intentar obtener Treasury 10Y automáticamente", value=True)
    terminal_growth = st.number_input("Crecimiento perpetuo base (%)", 0.0, 4.0, 2.5, 0.25) / 100
    margin_of_safety = st.slider("Margen de seguridad (%)", 0, 50, 20, 1) / 100
    forecast_years = st.select_slider("Años de proyección", options=[5, 7, 10, 12, 15], value=10)
    capex_long_run_multiplier = st.number_input(
        "Multiplicador del CapEx/CFO de largo plazo", min_value=0.25, max_value=1.50, value=1.00, step=0.05,
        help="Se multiplica por la mediana histórica de CapEx/CFO. 1,00 conserva la mediana; valores menores o mayores son hipótesis explícitas."
    )
    st.caption("Los supuestos editables no son datos observados ni se calibran a proveedores externos.")

run = st.button("Calcular valoración", type="primary", use_container_width=True)
if run:
    if not ticker_symbol:
        st.error("Ingresá un ticker válido.")
        st.stop()
    with st.spinner(f"Obteniendo datos de {ticker_symbol}..."):
        try:
            company = Ticker(ticker_symbol)
            price = get_price(company, ticker_symbol)
            beta_raw = get_beta(company, ticker_symbol)
            shares = get_shares(company, ticker_symbol)
            profile = get_profile(company, ticker_symbol)
            cashflows = get_annual_cashflows(company, ticker_symbol)
            if cashflows.empty:
                st.error("Yahoo Finance no devolvió flujos anuales con fechas fiscales identificables.")
                st.stop()

            cfo_col = pick_column(cashflows, ["OperatingCashFlow", "OperatingCashFlowFromContinuingOperations"])
            capex_col = pick_column(cashflows, ["CapitalExpenditure", "CapitalExpenditures"])
            fcf_col = pick_column(cashflows, ["FreeCashFlow"])
            if not cfo_col or not capex_col:
                st.error(f"Faltan campos de CFO o CapEx. Columnas recibidas: {', '.join(map(str, cashflows.columns))}")
                st.stop()

            # Keep all metrics on the same fiscal-date rows; never drop each series separately.
            cols = ["Fiscal date", cfo_col, capex_col] + ([fcf_col] if fcf_col else [])
            financials = cashflows[cols].copy().rename(columns={cfo_col: "CFO", capex_col: "CapEx"})
            if fcf_col:
                financials = financials.rename(columns={fcf_col: "FCF Yahoo"})
            financials["CFO"] = pd.to_numeric(financials["CFO"], errors="coerce")
            financials["CapEx bruto"] = pd.to_numeric(financials["CapEx"], errors="coerce").abs()
            financials["FCF calculado"] = financials["CFO"] - financials["CapEx bruto"]
            if "FCF Yahoo" in financials.columns:
                financials["FCF Yahoo"] = pd.to_numeric(financials["FCF Yahoo"], errors="coerce")
            financials = financials.dropna(subset=["CFO", "CapEx bruto"]).sort_values("Fiscal date").reset_index(drop=True)
            if len(financials) < 2:
                st.error("Hay menos de dos ejercicios con CFO y CapEx completos y alineados.")
                st.stop()

            latest = financials.iloc[-1]
            cfo_latest = float(latest["CFO"])
            capex_latest = float(latest["CapEx bruto"])
            fcf_calculated_latest = float(latest["FCF calculado"])
            fcf_yahoo_latest = safe_float(latest.get("FCF Yahoo"))
            fiscal_date = latest["Fiscal date"].strftime("%Y-%m-%d")
            financials["CapEx/CFO"] = np.where(financials["CFO"] > 0, financials["CapEx bruto"] / financials["CFO"], np.nan)
            ratios = financials["CapEx/CFO"].replace([np.inf, -np.inf], np.nan).dropna()
            ratios = ratios[ratios >= 0]
            latest_ratio = capex_latest / cfo_latest if cfo_latest > 0 else np.nan
            long_run_ratio = float(ratios.tail(5).median()) if not ratios.empty else np.nan

            # Calculate growth using date-aligned annual CFO observations.
            cfo_values = financials["CFO"].astype(float).tolist()
            annual_growths = []
            for prev, curr in zip(cfo_values[:-1], cfo_values[1:]):
                if prev > 0 and curr > 0:
                    annual_growths.append(curr / prev - 1)
            positive = financials.loc[financials["CFO"] > 0, ["Fiscal date", "CFO"]]
            cagr_historical = np.nan
            if len(positive) >= 2:
                first = positive.iloc[0]
                last = positive.iloc[-1]
                elapsed_years = (last["Fiscal date"] - first["Fiscal date"]).days / 365.25
                if elapsed_years > 0 and first["CFO"] > 0 and last["CFO"] > 0:
                    cagr_historical = (last["CFO"] / first["CFO"]) ** (1 / elapsed_years) - 1
            median_growth = float(np.median(annual_growths)) if annual_growths else np.nan
            if math.isfinite(cagr_historical) and math.isfinite(median_growth):
                growth_raw = 0.5 * cagr_historical + 0.5 * median_growth
            elif math.isfinite(cagr_historical):
                growth_raw = cagr_historical
            else:
                growth_raw = median_growth
            growth_base = clamp(growth_raw, -0.10, 0.25) if math.isfinite(growth_raw) else np.nan

            rf_live = get_risk_free_rate() if use_live_rf else np.nan
            risk_free = rf_live if math.isfinite(rf_live) else rf_manual
            rf_source = "Treasury 10Y (^TNX) de Yahoo Finance" if math.isfinite(rf_live) else "Tasa manual (fallback)"
            beta_used = beta_raw if math.isfinite(beta_raw) else 1.0
            cost_equity = risk_free + beta_used * erp
            shares_m = shares / 1e6 if math.isfinite(shares) else np.nan
            cfo_m = cfo_latest / 1e6

            st.markdown(f"### {ticker_symbol} — {profile.get('longName', ticker_symbol)}")
            st.caption(f"Sector: {profile.get('sector', 'No informado')} · Industria: {profile.get('industry', 'No informada')} · Consulta: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            st.success(f"Ejercicio fiscal utilizado: {fiscal_date}. CFO y CapEx se emparejan por fecha fiscal.")

            m = st.columns(5)
            m[0].metric("Precio de mercado", fmt_money(price))
            m[1].metric("Acciones (millones)", f"{shares_m:,.2f}" if math.isfinite(shares_m) else "N/D")
            m[2].metric("CFO (USD M)", f"{cfo_m:,.1f}")
            m[3].metric("FCF calculado (USD M)", f"{fcf_calculated_latest / 1e6:,.1f}")
            m[4].metric("Costo equity (CAPM)", f"{cost_equity:.2%}")

            # Explicitly compare Yahoo's reported FCF with CFO minus gross CapEx.
            st.subheader("Control de consistencia de los estados financieros")
            recon = financials.tail(5).copy()
            recon["Ejercicio"] = recon["Fiscal date"].dt.strftime("%Y-%m-%d")
            recon["CFO (USD M)"] = recon["CFO"] / 1e6
            recon["CapEx bruto (USD M)"] = recon["CapEx bruto"] / 1e6
            recon["FCF calculado (USD M)"] = recon["FCF calculado"] / 1e6
            if "FCF Yahoo" in recon.columns:
                recon["FCF Yahoo (USD M)"] = recon["FCF Yahoo"] / 1e6
                recon["Diferencia FCF (USD M)"] = (recon["FCF Yahoo"] - recon["FCF calculado"]) / 1e6
            show_cols = ["Ejercicio", "CFO (USD M)", "CapEx bruto (USD M)", "FCF calculado (USD M)"]
            if "FCF Yahoo (USD M)" in recon.columns:
                show_cols += ["FCF Yahoo (USD M)", "Diferencia FCF (USD M)"]
            st.dataframe(recon[show_cols].round(1), use_container_width=True, hide_index=True)
            if math.isfinite(fcf_yahoo_latest) and abs(fcf_yahoo_latest - fcf_calculated_latest) > max(1e9, abs(fcf_calculated_latest) * 0.10):
                st.warning("El FCF informado por Yahoo difiere materialmente de CFO − CapEx bruto. El DCF usa CFO − CapEx bruto; revisá la conciliación antes de interpretar el resultado.")

            # Let the user inspect/adjust the long-run CapEx intensity without forcing it lower.
            st.subheader("Supuesto de inversión a largo plazo")
            p, q, r = st.columns(3)
            p.metric("CapEx/CFO del último año", f"{latest_ratio:.1%}" if math.isfinite(latest_ratio) else "N/D")
            q.metric("Mediana histórica (hasta 5 años)", f"{long_run_ratio:.1%}" if math.isfinite(long_run_ratio) else "N/D")
            r.metric("Crecimiento CFO calculado", f"{growth_base:.1%}" if math.isfinite(growth_base) else "N/D")
            if not math.isfinite(long_run_ratio):
                st.error("No se pudo estimar un ratio histórico de CapEx/CFO válido.")
                st.stop()
            long_run_capex_ratio = clamp(long_run_ratio * capex_long_run_multiplier, 0.0, 1.5)
            st.metric("CapEx/CFO de largo plazo aplicado", f"{long_run_capex_ratio:.1%}")
            st.caption("La intensidad de CapEx converge linealmente desde el ratio del último ejercicio hasta el supuesto elegido. Multiplicador 1,00 conserva la mediana histórica; el modelo no presume automáticamente una caída de la inversión.")

            warnings = []
            if not math.isfinite(price) or price <= 0:
                warnings.append("No se pudo validar el precio de mercado.")
            if not math.isfinite(shares_m) or shares_m <= 0:
                warnings.append("No se pudo validar la cantidad de acciones en circulación.")
            if not math.isfinite(beta_raw):
                warnings.append("Beta no disponible: se usa beta 1,00 como supuesto explícito.")
            elif beta_raw < 0 or beta_raw > 3:
                warnings.append("Beta extrema; revisá la fuente y la idoneidad del CAPM.")
            if len(financials) < 3:
                warnings.append("Menos de tres ejercicios completos: la estimación de crecimiento es poco robusta.")
            if not math.isfinite(growth_base):
                warnings.append("No se pudo estimar crecimiento histórico fiable.")
            elif abs(growth_raw - growth_base) > 1e-8:
                warnings.append(f"El crecimiento bruto estimado ({growth_raw:.1%}) fue limitado a {growth_base:.1%} para evitar extrapolación mecánica extrema. Revisá el dato bruto antes de aceptar el límite.")
            if math.isfinite(latest_ratio) and latest_ratio > 0.8:
                warnings.append("CapEx/CFO del último año supera 80%: el FCF es muy sensible a las inversiones.")
            if cost_equity <= terminal_growth:
                warnings.append("El costo de capital debe superar el crecimiento perpetuo.")
            sector = str(profile.get("sector", "")).lower()
            industry = str(profile.get("industry", "")).lower()
            if sector in {"financial services", "financial"} or any(x in industry for x in ["bank", "insurance", "reit"]):
                warnings.append("El sector puede requerir un modelo específico (bancos/aseguradoras: dividendos o exceso de rentabilidad; REITs: AFFO).")
            if warnings:
                with st.expander("⚠️ Controles de calidad y advertencias", expanded=True):
                    for item in warnings:
                        st.warning(item)

            if not (math.isfinite(shares_m) and shares_m > 0 and cfo_m > 0 and math.isfinite(growth_base)):
                st.error("Datos insuficientes para una valoración defendible. No se muestra un precio objetivo ficticio.")
                st.stop()
            if cost_equity <= terminal_growth:
                st.error("El costo de capital debe ser mayor que el crecimiento perpetuo.")
                st.stop()

            scenarios = [
                ("Pesimista", max(-0.15, growth_base - 0.05), cost_equity + 0.01,
                 max(0.0, terminal_growth - 0.005), min(1.5, long_run_capex_ratio + 0.10)),
                ("Base", growth_base, cost_equity, terminal_growth, long_run_capex_ratio),
                ("Optimista", min(0.30, growth_base + 0.05), max(0.01, cost_equity - 0.01),
                 min(0.04, terminal_growth + 0.005), max(0.0, long_run_capex_ratio - 0.10)),
            ]
            results = {}
            for name, growth, discount, g, long_capex in scenarios:
                results[name] = project_dcf(cfo_m, latest_ratio, long_capex, growth, discount, g,
                                            shares_m, forecast_years, margin_of_safety) if discount > g else None

            st.divider()
            st.subheader("Valor intrínseco estimado por escenario")
            cards = st.columns(3)
            for col, (name, growth, discount, g, long_capex) in zip(cards, scenarios):
                result = results[name]
                with col:
                    st.markdown(f"#### {name}")
                    if result is None:
                        st.error("No se pudo calcular: flujo terminal no positivo o supuestos inválidos.")
                        continue
                    upside = (result["intrinsic_value"] / price - 1) * 100 if math.isfinite(price) and price > 0 else np.nan
                    st.metric("Valor intrínseco por acción", fmt_money(result["intrinsic_value"]),
                              delta=f"{upside:+.1f}% vs. mercado" if math.isfinite(upside) else None)
                    st.metric(f"Precio con margen ({margin_of_safety:.0%})", fmt_money(result["entry_price"]))
                    st.caption(f"Crec. inicial CFO: {growth:.1%} · Ke: {discount:.2%} · g: {g:.2%}")
                    st.caption(f"CapEx/CFO largo plazo: {long_capex:.1%} · Valor terminal: {result['terminal_share']:.1%}")

            base = results.get("Base")
            if base:
                st.divider()
                st.subheader("Sensibilidad del escenario base")
                st.caption("Matriz de valor intrínseco por acción. Se modifica Ke y g; el crecimiento y CapEx/CFO de largo plazo permanecen fijos.")
                discount_grid = sorted(set([max(0.01, cost_equity - 0.02), max(0.01, cost_equity - 0.01), cost_equity, cost_equity + 0.01, cost_equity + 0.02]))
                g_grid = sorted(set([max(0.0, terminal_growth - 0.01), max(0.0, terminal_growth - 0.005), terminal_growth, min(0.04, terminal_growth + 0.005), min(0.04, terminal_growth + 0.01)]))
                matrix = []
                for disc in discount_grid:
                    row = {"Ke ↓ / g →": f"{disc:.2%}"}
                    for g in g_grid:
                        sens = project_dcf(cfo_m, latest_ratio, long_run_capex_ratio, growth_base, disc, g,
                                           shares_m, forecast_years, margin_of_safety) if disc > g else None
                        row[f"g = {g:.2%}"] = fmt_money(sens["intrinsic_value"]) if sens else "N/V"
                    matrix.append(row)
                st.dataframe(pd.DataFrame(matrix), use_container_width=True, hide_index=True)

                with st.expander("🔎 Proyección anual detallada — escenario base", expanded=False):
                    table = pd.DataFrame(base["rows"])
                    table["Crecimiento CFO"] = table["Crecimiento CFO"].map(lambda x: f"{x:.2%}")
                    table["CapEx/CFO"] = table["CapEx/CFO"].map(lambda x: f"{x:.1%}")
                    for col in ["CFO proyectado (USD M)", "CapEx estimado (USD M)", "FCF estimado (USD M)", "FCF a valor presente (USD M)"]:
                        table[col] = table[col].map(lambda x: f"${x:,.1f} M")
                    st.dataframe(table, use_container_width=True, hide_index=True)

            with st.expander("📚 Metodología y limitaciones"):
                st.markdown(
                    """
                    **Flujo utilizado.** CFO menos CapEx bruto. Se descuenta al costo del capital propio (CAPM) como aproximación de caja para el accionista; no es FCFE completo porque no modela financiación neta mediante deuda. No se resta deuda neta otra vez.

                    **Integridad de datos.** CFO, CapEx y FCF se emparejan por fecha fiscal. Se muestra una conciliación entre FCF calculado (CFO − CapEx bruto) y el FCF que informa Yahoo, que puede diferir por definiciones, incentivos o partidas adicionales. El DCF utiliza CFO − CapEx bruto de forma consistente.

                    **Proyección.** El crecimiento del CFO converge linealmente al crecimiento perpetuo. La intensidad de CapEx converge desde el último ejercicio hasta una hipótesis de largo plazo; por defecto, esta es la mediana de los últimos cinco ejercicios disponibles. El usuario puede modificarla con una tesis fundamentada, no para perseguir el valor de otro proveedor.

                    **Escenarios.** El escenario pesimista usa crecimiento 5 puntos porcentuales menor, Ke 1 punto mayor y CapEx/CFO de largo plazo 10 puntos mayor; el optimista aplica los cambios inversos. Son pruebas de estrés ilustrativas, no intervalos estadísticos de confianza.

                    **CAPM y valor terminal.** Ke = tasa libre de riesgo + beta × ERP. Si no hay beta, se supone 1,00 y se advierte. El valor terminal se calcula con Gordon Growth y requiere Ke > g.

                    **Limitaciones.** No se modelan por separado CapEx de mantenimiento y expansión, retorno incremental del capital, SBC/dilución futura, adquisiciones ni cambios extraordinarios de capital de trabajo. Para empresas con inversiones extraordinarias, CFO/FCF negativos o negocios financieros, se requiere una metodología específica y/o proyecciones explícitas más completas.
                    """
                )
        except Exception as exc:
            st.error(f"No se pudo completar la valoración de {ticker_symbol}: {exc}")
            st.info("Verificá ticker, conectividad y versión de yahooquery. Si persiste, revisá los registros de Streamlit Cloud.")
else:
    st.info("Ingresá un ticker y presioná **Calcular valoración** para ver los datos fiscales, la conciliación de FCF, los escenarios y la sensibilidad.")

st.divider()
st.caption("Herramienta educativa. No constituye asesoramiento financiero ni recomendación de compra o venta.")
