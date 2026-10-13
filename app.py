"""
DCF v2 — Valuación por escenarios (Pesimista / Base / Optimista) para cualquier acción listada en Yahoo Finance.

Modelos:  FCFF desde ingresos (no financieras) · Retorno excedente (bancos, seguros, mREITs) · AFFO (REITs).
Ejecutar:  streamlit run app.py
"""

import io
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

import data_yahoo
from country_risk import country_crp
from dcf_models import (MOAT_SPREADS, Params, data_checks, route_model, run_scenarios,
                        sensitivity)

def _stretch():
    """Compatibilidad entre versiones de Streamlit (use_container_width fue reemplazado por width='stretch')."""
    try:
        major, minor = (int(x) for x in st.__version__.split(".")[:2])
        if (major, minor) >= (1, 50):
            return {"width": "stretch"}
    except Exception:  # noqa: BLE001
        pass
    return {"use_container_width": True}


STRETCH = _stretch()

APP_VERSION = "v2.5 · 13/10/2026"

st.set_page_config(page_title="DCF v2 · Valuación por escenarios", page_icon="📈", layout="wide")

MODEL_LABELS = {"FCFF": "Empresa común (todos los demás sectores)",
                "ER": "Banco / aseguradora / financiera",
                "REIT": "REIT (inmobiliaria que cobra alquileres)"}
SCEN_ORDER = ["Pesimista", "Base", "Optimista"]


# ----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def cached_load(sym: str, allow_annual: bool, adr_ratio: float):
    return data_yahoo.load_company(sym, allow_annual, adr_ratio or None)


def pct(x, d=1):
    return "n/d" if x is None or x != x else f"{x * 100:.{d}f}%"


def money(x, cur="", d=2):
    return "n/d" if x is None or x != x else f"{x:,.{d}f} {cur}".strip()


def verdict(price, res):
    """Frase simple: dónde está el precio respecto de los tres escenarios."""
    vp, vb, vo = (res[n].per_share if res[n].ok else None for n in SCEN_ORDER)
    if vp is not None and price < vp:
        return "🟢 El precio está por **debajo incluso del escenario pesimista**: margen de seguridad amplio según el modelo."
    if vb is not None and price < vb:
        return "🟢 El precio está **entre el pesimista y el base**: cotiza con descuento frente al caso base."
    if vo is not None and price < vo:
        return "🟡 El precio está **entre el base y el optimista**: el mercado ya descuenta parte del escenario favorable."
    if vo is not None:
        return "🔴 El precio está **por encima del optimista**: el mercado paga más que el mejor escenario del modelo."
    return "El precio supera al caso base y no hay escenario optimista disponible."


def build_excel(co, res, base, sens, p, model, mos):
    """Excel con resultados, supuestos, proyección base y sensibilidad (para guardar o comparar empresas)."""
    buf = io.BytesIO()
    resumen = pd.DataFrame([{"Escenario": n, "Valor por acción": res[n].per_share if res[n].ok else None,
                             "vs precio": (res[n].per_share / co.price - 1) if res[n].ok else None,
                             "Tasa de descuento": res[n].discount_rate if res[n].ok else None,
                             "g terminal": res[n].g_terminal if res[n].ok else None} for n in SCEN_ORDER])
    params = pd.DataFrame([
        ("Ticker", co.ticker), ("Empresa", co.name), ("Sector", co.sector), ("Industria", co.industry),
        ("Modelo", model), ("Precio", co.price), ("Moneda", co.currency), ("Cap. bursátil (M)", co.market_cap_m),
        ("Tasa libre de riesgo", p.rf if p.rf is not None else co.rf), ("Prima de mercado", p.erp),
        ("Prima de riesgo país", p.crp), ("Moat (spread sobre WACC)", p.moat_spread), ("g terminal", p.g_terminal),
        ("Horizonte (años)", p.horizon), ("Margen de seguridad", mos),
        ("Datos al", co.asof), ("Fuente TTM", co.ttm_source), ("Generado", datetime.now().strftime("%Y-%m-%d %H:%M")),
    ], columns=["Parámetro", "Valor"])
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        resumen.to_excel(xw, sheet_name="Resumen", index=False)
        params.to_excel(xw, sheet_name="Parametros", index=False)
        pd.DataFrame(base.table).to_excel(xw, sheet_name="Proyeccion_Base", index=False)
        if sens.get("ok"):
            pd.DataFrame(sens["grid"], index=[f"{x:.2%}" for x in sens["rates"]],
                         columns=[f"g {x:.2%}" for x in sens["gs"]]).to_excel(xw, sheet_name="Sensibilidad")
    return buf.getvalue()


# ----------------------------------------------------------------------------
# Barra lateral: supuestos globales
# ----------------------------------------------------------------------------
def K(name):
    """Clave de widget que cambia con cada ticker nuevo => los valores se reinician."""
    return f"{name}_{st.session_state.get('rn', 0)}"


with st.sidebar:
    st.header("Supuestos")
    horizon = st.slider("Horizonte explícito (años)", 5, 15, 10, key=K("horizon"))
    g_term = st.number_input("Crecimiento terminal g (%)", 0.0, 4.0, 2.5, 0.25, key=K("gterm")) / 100
    erp = st.number_input("Prima de riesgo de mercado (%)", 3.0, 8.0, 4.5, 0.25, key=K("erp")) / 100
    crp_auto = st.checkbox("Prima de riesgo país automática", value=True, key=K("crpauto"),
                           help="Según el país de la empresa, con la tabla de Damodaran (enero 2026). "
                                "EE.UU., Europa desarrollada y Japón dan casi 0; emergentes suman varios puntos.")
    crp_manual = 0.0
    if not crp_auto:
        crp_manual = st.number_input("Prima de riesgo país (%)", 0.0, 30.0, 0.0, 0.25, key=K("crpman")) / 100
    moat = st.select_slider("Ventaja competitiva (moat)", list(MOAT_SPREADS), value="Estrecho", key=K("moat"),
                            help="Cuánto por encima del WACC puede seguir ganando la empresa sobre el capital "
                                 "que reinvierte, para siempre. Ninguno = 0 pp · Estrecho = +2 pp · Amplio = +5 pp. "
                                 "Es el supuesto que más mueve el valor; usalo con criterio (Dorsey).")
    mos = st.slider("Margen de seguridad deseado (%)", 0, 50, 20, key=K("mos")) / 100
    allow_annual = st.checkbox("Permitir último año fiscal si no hay TTM", value=False, key=K("annual"),
                               help="Por defecto se exigen datos de los últimos 12 meses (TTM).")
    adr_ratio = st.number_input("Acciones ordinarias por ADR (0 = no es ADR / automático)", 0.0, 1000.0, 0.0, 1.0, key=K("adr"),
                                help="SOLO para ADRs de empresas extranjeras. Dejalo en 0 para empresas de EE.UU. "
                                     "(KO, JPM, V, O…). Ej.: TSM = 5, ITUB = 1, BABA = 8. Se ignora si la empresa "
                                     "reporta en dólares.")
    with st.expander("Ajustes avanzados"):
        margin_mode = st.selectbox("Margen operativo objetivo", ["median", "ttm", "manual"], key=K("mmode"),
                                   format_func={"median": "Mediana histórica (3-4 años + TTM)",
                                                "ttm": "Mantener TTM", "manual": "Manual"}.get)
        margin_manual = None
        if margin_mode == "manual":
            margin_manual = st.number_input("Margen EBIT objetivo (%)", -20.0, 80.0, 15.0, 0.5, key=K("mman")) / 100
        margin_years = st.slider("Años para converger al margen objetivo", 1, 10, 5, key=K("myears"))
        use_g = st.checkbox("Fijar crecimiento de ingresos años 1 y 2", key=K("usg"))
        growth_override = None
        if use_g:
            g1 = st.number_input("Crecimiento año 1 (%)", -20.0, 50.0, 8.0, 0.5, key=K("g1")) / 100
            g2 = st.number_input("Crecimiento año 2 (%)", -20.0, 50.0, 6.0, 0.5, key=K("g2")) / 100
            growth_override = (g1, g2)
        beta_manual = st.number_input("Beta — 0 = la de Yahoo", 0.0, 3.0, 0.0, 0.05, key=K("beta"))
        wacc_manual = st.number_input("WACC fijo (%) — 0 = calculado", 0.0, 20.0, 0.0, 0.25, key=K("wacc")) / 100
        persistence = st.slider("Persistencia del exceso de ROE (financieras)", 0.0, 1.0, 0.5, 0.05, key=K("pers"),
                                help="Qué fracción del ROE por encima de Ke sobrevive en el largo plazo.")
        affo_ratio = st.slider("AFFO / FFO estimado (REITs)", 0.6, 1.0, 0.90, 0.01, key=K("affor"))
        affo_ps = st.number_input("AFFO por acción reportado — 0 = estimar", 0.0, 1000.0, 0.0, 0.05, key=K("affops"))
        lease_opt = st.selectbox("Arrendamientos como deuda", ["auto", "sí", "no"], key=K("lease"),
                                 help="Auto: EE.UU. = no (el gasto de alquiler ya está en el EBIT); resto = sí.")

st.sidebar.caption(f"Versión de la app: {APP_VERSION}")
st.title("📈 DCF v2 — Valuación por escenarios")
st.caption("Ingresá un ticker de Yahoo Finance (ej.: AAPL, KO, JPM, O, TSM, SAP.DE, 7203.T, NESN.SW, ITUB). "
           "Resultado: valor por acción en tres escenarios. Herramienta educativa, no es una recomendación.")

with st.form("f"):
    c1, c2, c3 = st.columns([3, 3, 2])
    ticker = c1.text_input("Ticker", value=st.session_state.get("last", "")).strip().upper()
    rf_in = c3.number_input("Tasa libre de riesgo 10 años (%)", 0.0, 15.0, 0.0, 0.05,
                            help="Opcional. Si la dejás en 0, la app la busca sola (Yahoo / Reserva Federal). "
                                 "Si la cargás, se usa esta.")
    model_choice = c2.selectbox("Modelo", ["auto", "FCFF", "ER", "REIT"], key=K("model"),
                                format_func=lambda k: "Automático (detecta el sector)" if k == "auto" else MODEL_LABELS[k],
                                help="Dejalo en Automático: la app detecta el sector y elige el modelo. "
                                     "Cambialo solo si el aviso te dice que no pudo detectarlo.")
    go = st.form_submit_button("Valuar", type="primary")

st.caption("📌 Tasa libre de riesgo = rendimiento del bono del Tesoro de EE.UU. a 10 años. "
           "Dónde mirarla hoy: [FRED – Reserva Federal (valor diario oficial)](https://fred.stlouisfed.org/series/DGS10) · "
           "[CNBC – cotización en vivo](https://www.cnbc.com/quotes/US10Y). "
           "Cargala arriba en % (ej.: 4,35) o dejá 0 para que la app la busque sola.")

if go and ticker:
    if ticker != st.session_state.get("last", ""):
        # Ticker nuevo: la barra lateral y el modelo vuelven a sus valores por defecto
        st.session_state["rn"] = st.session_state.get("rn", 0) + 1
        st.session_state["last"] = ticker
        st.rerun()
    st.session_state["last"] = ticker
sym = st.session_state.get("last", "")
if not sym:
    st.info("Escribí un ticker y apretá **Valuar**.")
    st.stop()

# ----------------------------------------------------------------------------
# Datos
# ----------------------------------------------------------------------------
try:
    with st.spinner(f"Descargando datos de {sym}…"):
        co, load_warns = cached_load(sym, allow_annual, adr_ratio)
except Exception as e:  # noqa: BLE001
    st.error(f"No pude cargar **{sym}**: {e}")
    st.stop()

if crp_auto:
    crp, crp_note = country_crp(co.country)
else:
    crp, crp_note = crp_manual, "Cargada a mano."
auto_model, why = route_model(co)
model = auto_model if model_choice == "auto" else model_choice

p = Params(
    horizon=horizon, g_terminal=g_term, erp=erp, crp=crp, moat_spread=MOAT_SPREADS[moat],
    margin_mode=margin_mode, margin_manual=margin_manual, margin_years=margin_years,
    growth_override=growth_override, rf=(rf_in / 100) or None, beta=beta_manual or None,
    wacc_override=wacc_manual or None, persistence=persistence, affo_ratio=affo_ratio,
    affo_ps_override=affo_ps or None,
    include_leases={"auto": None, "sí": True, "no": False}[lease_opt],
)

st.subheader(f"{co.name} ({co.ticker})")
st.write(f"**Sector:** {co.sector or 'n/d'} · **Industria:** {co.industry or 'n/d'} · **País:** {co.country or 'n/d'} · "
         f"**Precio:** {money(co.price, co.currency)} · **Cap. bursátil:** {money(co.market_cap_m, co.currency, 0)} M · "
         f"**Datos contables al:** {co.asof or 'n/d'}")
_rf_used = p.rf if p.rf is not None else co.rf
st.write(f"**Tasa libre de riesgo usada:** {_rf_used:.2%} "
         f"({'cargada por vos' if p.rf is not None else 'automática'})")
st.write(f"**Prima de riesgo país aplicada:** {crp:.2%} — {crp_note}")
if model_choice == "auto":
    st.info(f"**Modelo: {MODEL_LABELS[model]}.** {why}")
elif model != auto_model:
    st.warning(f"Modelo elegido a mano: {MODEL_LABELS[model]}. El automático sería {MODEL_LABELS[auto_model]}. {why}")

res = run_scenarios(co, p, model)
base = res["Base"]
if (model_choice == "auto" and model == "FCFF" and not base.ok and "EBIT" in base.error
        and (co.ni_ttm or 0) > 0 and (co.equity or 0) > 0):
    model = "ER"
    why = ("Sin resultado operativo pero con ganancias y patrimonio (típico de bancos y aseguradoras): "
           "se pasó automáticamente a retorno excedente. Confirmalo si no es una financiera.")
    res = run_scenarios(co, p, model)
    base = res["Base"]

all_warns = list(dict.fromkeys(load_warns + data_checks(co)))
if model == "FCFF" and (co.sector or "") in ("Energy", "Basic Materials"):
    all_warns.append("Sector cíclico: el margen EBIT está normalizado con la mediana histórica, pero revisá "
                     "si el ciclo actual es pico o piso.")
if crp_auto and (not co.country or "no figura" in crp_note):
    all_warns.append(crp_note)

if not base.ok:
    st.error(f"**No se puede valuar con este modelo:** {base.error}")
    for w in all_warns:
        st.warning(w)
    st.stop()

# ----------------------------------------------------------------------------
# Resultado principal
# ----------------------------------------------------------------------------
cols = st.columns(3)
for col, name in zip(cols, SCEN_ORDER):
    r = res[name]
    with col:
        if r.ok:
            up = r.per_share / co.price - 1
            st.metric(f"{name}", money(r.per_share, co.currency), f"{up:+.0%} vs precio",
                      delta_color="normal")
        else:
            st.metric(f"{name}", "n/d")
            st.caption(r.error)

buy = base.per_share * (1 - mos)
st.write(f"**Precio de compra con {mos:.0%} de margen de seguridad sobre el escenario Base:** "
         f"{money(buy, co.currency)}  ·  precio actual: {money(co.price, co.currency)} "
         f"({'por debajo ✅' if co.price <= buy else 'por encima'} de ese nivel)")

st.markdown(verdict(co.price, res))
sens = sensitivity(co, p, model)
st.download_button("⬇️ Descargar resultado en Excel",
                   build_excel(co, res, base, sens, p, model, mos),
                   file_name=f"DCF_{co.ticker}_{datetime.now():%Y%m%d}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

chart_df = pd.DataFrame({"Escenario": [n for n in SCEN_ORDER if res[n].ok],
                         "Valor": [res[n].per_share for n in SCEN_ORDER if res[n].ok]})
bars = alt.Chart(chart_df).mark_bar().encode(
    x=alt.X("Escenario:N", sort=SCEN_ORDER, axis=alt.Axis(labelAngle=0)), y=alt.Y("Valor:Q", title=f"Valor por acción ({co.currency})"),
    color=alt.Color("Escenario:N", sort=SCEN_ORDER, legend=None,
                    scale=alt.Scale(domain=SCEN_ORDER, range=["#c0504d", "#4f81bd", "#4e9a51"])))
rule = alt.Chart(pd.DataFrame({"y": [co.price]})).mark_rule(strokeDash=[6, 4], color="#444", size=2).encode(y="y:Q")
label = alt.Chart(pd.DataFrame({"y": [co.price], "t": [f"Precio actual {co.price:,.2f}"]})).mark_text(
    align="left", dx=4, dy=-6, color="#444").encode(y="y:Q", text="t:N", x=alt.value(4))
st.altair_chart((bars + rule + label).properties(height=300), **STRETCH)

if all_warns:
    with st.expander(f"⚠️ Avisos y controles ({len(all_warns)})", expanded=True):
        for w in all_warns:
            st.write("• " + w)
# ----------------------------------------------------------------------------
# Pestañas
# ----------------------------------------------------------------------------
t1, t2, t3, t4, t5 = st.tabs(["Supuestos por escenario", "Proyección (Base)", "Sensibilidad", "Controles de cordura",
                              "Metodología y límites"])

with t1:
    def row(label, fn):
        return {"": label, **{n: (fn(res[n]) if res[n].ok else "n/d") for n in SCEN_ORDER}}

    rows = []
    if model == "FCFF":
        rows = [
            row("WACC", lambda r: pct(r.extras["wacc"], 2)),
            row("Costo del capital propio (Ke)", lambda r: pct(r.extras["ke"], 2)),
            row("Crecimiento ingresos año 1", lambda r: pct(r.extras["g1"])),
            row("Crecimiento ingresos año 2", lambda r: pct(r.extras["g2"])),
            row("g terminal", lambda r: pct(r.g_terminal, 2)),
            row("Margen EBIT actual (TTM)", lambda r: pct(r.extras["margin0"])),
            row("Margen EBIT objetivo", lambda r: pct(r.extras["margin_target"])),
            row("ROIC inicial", lambda r: pct(r.extras["roic0"])),
            row("ROIC terminal (WACC + spread)", lambda r: pct(r.extras["roicT"])),
            row("Valor empresa (EV, M)", lambda r: money(r.ev, "", 0)),
            row("Caja (M)", lambda r: money(r.extras["cash"], "", 0)),
            row("Deuda (M)", lambda r: money(r.extras["debt"], "", 0)),
            row("Valor por acción", lambda r: money(r.per_share, co.currency)),
        ]
    elif model == "ER":
        rows = [
            row("Costo del capital propio (Ke)", lambda r: pct(r.ke, 2)),
            row("ROE TTM", lambda r: pct(r.extras["roe_ttm"])),
            row("ROE normalizado de partida", lambda r: pct(r.extras["roe_norm"])),
            row("ROE de largo plazo", lambda r: pct(r.extras["roe_T"])),
            row("Persistencia", lambda r: f"{r.extras['persistence']:.2f}"),
            row("Payout total inicial", lambda r: pct(r.extras["payout0"], 0)),
            row("g terminal", lambda r: pct(r.g_terminal, 2)),
            row("Book value por acción", lambda r: money(r.extras["bv_ps"], co.currency)),
            row("P/B implícito", lambda r: f"{r.extras['pb_implied']:.2f}x"),
            row("Valor por acción", lambda r: money(r.per_share, co.currency)),
        ]
    else:
        rows = [
            row("Costo del capital propio (Ke)", lambda r: pct(r.ke, 2)),
            row("AFFO por acción (partida)", lambda r: money(r.extras["affo_ps"], co.currency)),
            row("Crecimiento AFFO inicial", lambda r: pct(r.extras["g0"])),
            row("g terminal", lambda r: pct(r.g_terminal, 2)),
            row("Retorno de lo reinvertido (ROI)", lambda r: pct(r.extras["roi"], 2)),
            row("P/AFFO implícito", lambda r: f"{r.extras['p_affo_implied']:.1f}x"),
            row("Valor por acción", lambda r: money(r.per_share, co.currency)),
        ]
    st.dataframe(pd.DataFrame(rows).set_index(""), **STRETCH)
    st.caption("Los escenarios cambian supuestos (crecimiento, márgenes, tasa, ventaja competitiva); "
               "no se multiplica el resultado final. Pesimista: moat reducido en 2 pp y sin persistencia en "
               "financieras. Optimista: +2 pp de moat.")

with t2:
    df = pd.DataFrame(base.table)
    fmt = {c: "{:,.1f}" for c in df.columns if c not in ("Año",)}
    for c in ("Crecimiento", "Margen", "Impuesto", "Reinversión", "ROIC", "ROE", "Payout"):
        if c in df.columns:
            fmt[c] = "{:.1%}"
    fmt["Año"] = "{:d}"
    st.dataframe(df.style.format(fmt), **STRETCH, hide_index=True)
    ex = base.extras
    if model == "FCFF":
        st.write(f"**Valor terminal:** {ex['tv']:,.0f} M (VP {ex['pv_tv']:,.0f} M) · "
                 f"**VP flujos explícitos:** {ex['pv_explicit']:,.0f} M · "
                 f"**EV:** {base.ev:,.0f} M → **Patrimonio:** {base.equity_value:,.0f} M")
    else:
        st.write(f"**Valor del patrimonio:** {base.equity_value:,.0f} M")
    st.caption(f"Montos en millones de {co.currency}. Estados contables originales en {co.fin_currency}.")

with t3:
    s = sens
    if s.get("ok"):
        idx = [f"{'WACC' if model == 'FCFF' else 'Ke'} {x:.2%}" for x in s["rates"]]
        colz = [f"g {x:.2%}" for x in s["gs"]]
        sdf = pd.DataFrame(s["grid"], index=idx, columns=colz)

        def colorize(v):
            if v != v:
                return ""
            return "background-color:#e3f1e3" if v >= co.price else "background-color:#f7e1e0"
        st.write(f"Valor por acción (escenario Base). Verde = por encima del precio actual ({co.price:,.2f}).")
        st.dataframe(sdf.style.format("{:,.2f}").map(colorize), **STRETCH)
    else:
        st.info("Sin sensibilidad disponible.")

with t4:
    ex = base.extras
    lines = []
    if model == "FCFF":
        lines += [
            ("Peso del valor terminal en el EV", pct(base.tv_share, 0), "Alerta si > 75 %"),
            ("EV/EBIT de salida implícito", f"{ex['exit_ev_ebit']:.1f}x", "Razonable: ~8–20x según sector"),
            ("ROIC terminal vs WACC", f"{pct(ex['roicT'], 1)} vs {pct(ex['wacc'], 1)}", "Spread = moat elegido"),
            ("Beta usada / Rf", f"{ex['beta']:.2f} / {pct(ex['rf'], 2)}", "Blume: 0,67·β + 0,33"),
        ]
    elif model == "ER":
        lines += [
            ("P/B actual del mercado", f"{ex['pb_current']:.2f}x", ""),
            ("P/B implícito (Base)", f"{ex['pb_implied']:.2f}x", "Debe ser coherente con ROE vs Ke"),
            ("P/E implícito (Base)", f"{ex['pe_implied']:.1f}x", ""),
        ]
    else:
        lines += [
            ("P/AFFO actual", f"{ex['p_affo_current']:.1f}x", ""),
            ("P/AFFO implícito (Base)", f"{ex['p_affo_implied']:.1f}x", ""),
            ("Fuente del AFFO", ex["affo_src"], ""),
        ]
    if co.target_price:
        lines.append(("Precio objetivo de analistas (solo referencia)", money(co.target_price, co.currency),
                      f"{co.n_analysts or '?'} analistas"))
    lines.append(("Fuente de datos TTM", co.ttm_source or "n/d", f"al {co.asof}" if co.asof else ""))
    st.dataframe(pd.DataFrame(lines, columns=["Control", "Valor", "Referencia"]).set_index("Control"),
                 **STRETCH)
    for n in SCEN_ORDER:
        if res[n].ok and res[n].warnings:
            with st.expander(f"Avisos del escenario {n}"):
                for w in res[n].warnings:
                    st.write("• " + w)

with t5:
    st.markdown("""
**Qué hace cada modelo**

* **FCFF desde ingresos** (no financieras). Los ingresos de los años 1–2 siguen las estimaciones de analistas
  (si no hay, la mediana histórica acotada) y se desvanecen linealmente hacia *g*. El margen EBIT converge desde el TTM al
  objetivo (mediana histórica por defecto). La **reinversión sale del crecimiento**: reinversión = *g* / ROIC, con ROIC
  que converge a **WACC + spread de moat**. Así no hay dos perillas independientes que premien dos veces el mismo crecimiento
  y el crecimiento solo vale si hay ventaja competitiva. En los primeros años se mezcla con la reinversión real observada (capex − D&A + capital de trabajo).
  Valor terminal de Gordon sobre NOPAT × (1 − g/ROIC). EV + caja − deuda − minoritarios = patrimonio, dividido por las acciones implícitas.
* **Retorno excedente** (bancos, aseguradoras, mREITs, brokers). Valor = book value + VP de (ROE − Ke) × patrimonio. El ROE
  parte de un valor normalizado y cae hacia Ke + persistencia × (ROE normalizado − Ke); el payout converge al compatible con *g*.
  Si ROE = Ke, el valor es exactamente el book value.
* **AFFO** (REITs). AFFO ≈ (resultado neto + amortización ± resultados por venta de activos) × ratio, o el AFFO/acción que
  ingreses. Lo retenido se reinvierte a Ke + spread, de ahí sale el crecimiento. Se descuenta a Ke.

**Costo de capital.** Ke = Rf (bono 10 años EE.UU.) + (0,67·β + 0,33) × prima de mercado (+ prima país). Kd = intereses/deuda acotado.
Ponderado con capitalización y deuda (con arrendamientos si corresponde).

**ADRs y acciones extranjeras.** Los estados se convierten a la moneda de cotización a tipo spot. Las acciones se calculan como
capitalización ÷ precio, lo que respeta el ratio del ADR y las clases múltiples. Precios en centavos (GBp, ZAc) se pasan a unidad.

**Límites que conviene conocer**

* Los datos son de Yahoo Finance: pueden tener huecos o errores; los avisos señalan lo que se detectó, no todo lo posible.
* WACC único (sin convergencia) y tasa libre de riesgo en dólares; para monedas con inflación alta, usá mercado en USD o cargá prima país y Rf.
* Empresas con EBIT ≤ 0 se rechazan (usar EV/Ventas). Cíclicas (energía, materiales): revisar la normalización del margen.
* Redes de pago (V, MA) van por FCFF; prestamistas de tarjetas (AXP, COF) conviene pasarlas a Retorno excedente a mano.
* Gestoras de activos alternativas (BX, KKR) tampoco encajan bien en retorno excedente: probar FCFF.
* El AFFO se estima si no lo cargás; lo ideal es usar el reportado por la compañía.
* Sin ajuste por opciones/dilución futura más allá de las acciones actuales; el EBIT ya incluye el gasto en acciones (SBC).
* Los modelos de financieras y REITs son una primera versión: calibrar con empresas conocidas antes de confiar.
""")
