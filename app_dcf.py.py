import streamlit as st
from yahooquery import Ticker
import pandas as pd

# Configuración de la página
st.set_page_config(page_title="Valuación DCF Multi-Escenario", page_icon="📈", layout="wide")

st.title("📈 Calculadora DCF Multi-Escenario (Estilo Simply Wall St)")
st.write("Herramienta comunitaria con valuación por Descuento de Flujos de Caja en 3 escenarios: Pesimista, Base y Optimista.")

# --- SIDEBAR: CONTROLES Y AJUSTES MANUALES ---
st.sidebar.header("⚙️ Ajustes del Modelo Base")

override_cagr = st.sidebar.checkbox("Ajustar CAGR Base manualmente", value=False)
cagr_manual_val = st.sidebar.slider("CAGR Proyectado Base (Años 1-5)", 0.0, 40.0, 15.0, 0.5) / 100 if override_cagr else None

override_wacc = st.sidebar.checkbox("Ajustar WACC Base manualmente", value=False)
wacc_manual_val = st.sidebar.slider("Tasa de Descuento (WACC) Base", 5.0, 15.0, 8.66, 0.1) / 100 if override_wacc else None

override_g = st.sidebar.slider("Tasa Crecimiento Perpetuo (g) Base", 2.0, 5.0, 3.7, 0.1) / 100

# Entrada de Ticker
ticker_simbolo = st.text_input("Escribe el Ticker de la empresa (ej. MSFT, AAPL, META):", "MSFT").upper()

# --- MOTOR MATEMÁTICO DCF ---
def calcular_dcf_motor(fcf_inicial, cagr, acciones_millones, tasa_descuento, g_perp):
    if acciones_millones <= 0 or fcf_inicial <= 0 or tasa_descuento <= g_perp:
        return 0, 0, []
    
    fcf_descontado_total = 0
    fcf_actual_proyeccion = fcf_inicial
    tabla_proyecciones = []
    
    # Curva de desaceleración gradual (Fade Rate) a 10 años
    tasa_actual = cagr
    paso_reduccion = (cagr - g_perp) / 10 if cagr > g_perp else 0
    
    for i in range(1, 11):
        tasa_actual = max(tasa_actual - paso_reduccion, g_perp)
        fcf_actual_proyeccion = fcf_actual_proyeccion * (1 + tasa_actual)
        fcf_descontado = fcf_actual_proyeccion / ((1 + tasa_descuento) ** i)
        fcf_descontado_total += fcf_descontado
        
        tabla_proyecciones.append({
            "Año": f"Año {2026 + i}",
            "Crecimiento Est.": f"{tasa_actual*100:.2f}%",
            "FCF Proyectado": f"${fcf_actual_proyeccion:,.2f} M",
            "FCF Descontado": f"${fcf_descontado:,.2f} M"
        })
        
    # Valor Terminal (Gordon Growth Model)
    fcf_anio_10 = fcf_actual_proyeccion
    valor_terminal = (fcf_anio_10 * (1 + g_perp)) / (tasa_descuento - g_perp)
    valor_terminal_descontado = valor_terminal / ((1 + tasa_descuento) ** 10)
    
    # Valor Total y por Acción
    valor_total_empresa = fcf_descontado_total + valor_terminal_descontado
    valor_por_accion = valor_total_empresa / acciones_millones
    valor_seguridad = valor_por_accion * 0.79 
    
    return valor_por_accion, valor_seguridad, tabla_proyecciones

# --- BOTÓN DE EJECUCIÓN ---
if st.button("Ejecutar Valuación Multi-Escenario"):
    
    with st.spinner(f"Extrayendo datos de mercado y procesando escenarios para {ticker_simbolo}..."):
        try:
            empresa = Ticker(ticker_simbolo)
            
            # 1. PRECIO
            precio = 0
            try:
                sd = empresa.summary_detail.get(ticker_simbolo, {})
                if isinstance(sd, dict):
                    precio = sd.get('previousClose', 0) or sd.get('regularMarketPrice', 0)
                if not precio or precio == 0:
                    fd = empresa.financial_data.get(ticker_simbolo, {})
                    if isinstance(fd, dict):
                        precio = fd.get('currentPrice', 0)
                if not precio or precio == 0:
                    pr = empresa.price.get(ticker_simbolo, {})
                    if isinstance(pr, dict):
                        precio = pr.get('regularMarketPrice', 0) or pr.get('currentPrice', 0)
            except:
                precio = 0

            # 2. BETA
            beta_mercado = 1.0
            try:
                summary_data = empresa.summary_detail.get(ticker_simbolo, {})
                if isinstance(summary_data, dict) and 'beta' in summary_data:
                    beta_val = summary_data.get('beta')
                    if beta_val and not pd.isna(beta_val):
                        beta_mercado = float(beta_val)
            except:
                pass

            # 3. ACCIONES EN CIRCULACIÓN
            acciones = 0
            try:
                datos_stats = empresa.key_stats.get(ticker_simbolo, {})
                if isinstance(datos_stats, dict):
                    acciones = datos_stats.get('impliedSharesOutstanding', 0) or datos_stats.get('sharesOutstanding', 0)
            except:
                pass
                
            if not acciones or acciones < 1000000:
                try:
                    bs = empresa.balance_sheet()
                    if hasattr(bs, 'columns') and 'OrdinarySharesNumber' in bs.columns:
                        acciones = bs['OrdinarySharesNumber'].dropna().iloc[-1]
                except:
                    pass
                    
            acciones_millones = acciones / 1000000 if acciones else 0
            
            # 4. FREE CASH FLOW Y CAGR BASE
            cf = empresa.cash_flow()
            lista_fcf = []
            if hasattr(cf, 'columns') and 'FreeCashFlow' in cf.columns:
                lista_fcf = cf['FreeCashFlow'].dropna().tolist()
            
            if len(lista_fcf) >= 2:
                fcf_actual = lista_fcf[-1] / 1000000
                fcf_historico = lista_fcf[0] / 1000000
                periodos = len(lista_fcf) - 1
                
                if fcf_historico > 0 and fcf_actual > 0:
                    cagr_auto = (fcf_actual / fcf_historico) ** (1 / periodos) - 1
                else:
                    cagr_auto = 0.10
            elif len(lista_fcf) == 1:
                fcf_actual = lista_fcf[0] / 1000000
                cagr_auto = 0.10
            else:
                fcf_actual = 0.0
                cagr_auto = 0.10
            
            # CAGR Base
            cagr_base = cagr_manual_val if override_cagr else cagr_auto
            if not override_cagr:
                if cagr_base > 0.30: cagr_base = 0.30
                elif cagr_base < 0.04: cagr_base = 0.04
            
            # WACC Base (CAPM)
            risk_free_rate = 0.037
            equity_risk_premium = 0.0442
            beta_ajustado = max(0.8, min(beta_mercado, 2.0))
            wacc_base = wacc_manual_val if override_wacc else (risk_free_rate + (beta_ajustado * equity_risk_premium))
            
            # --- DASHBOARD VISUAL DE DATOS EXTRAÍDOS ---
            st.success("✅ ¡Datos extraídos correctamente!")
            
            st.markdown("### 📊 Datos Base de Mercado")
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Precio en Mercado", f"${precio:.2f}" if precio > 0 else "N/D")
            col2.metric("Acciones en Circ.", f"{acciones_millones:,.0f} M")
            col3.metric("Free Cash Flow", f"${fcf_actual:,.2f} M")
            col4.metric("CAGR Base", f"{cagr_base*100:.2f}%")
            col5.metric("WACC Base", f"{wacc_base*100:.2f}%")
            
            st.divider()
            
            if fcf_actual <= 0 or acciones_millones <= 0 or precio <= 0:
                st.warning("⚠️ No se pudieron validar todos los datos fundamentales de la empresa.")
            else:
                # --- CÁLCULO DE LOS 3 ESCENARIOS ---
                
                # 🔴 1. Escenario Pesimista
                cagr_pes = max(0.02, cagr_base * 0.80)
                wacc_pes = wacc_base + 0.0075
                g_pes = max(0.02, override_g - 0.007)
                vi_pes, margin_pes, tab_pes = calcular_dcf_motor(fcf_actual, cagr_pes, acciones_millones, wacc_pes, g_pes)
                
                # 🟡 2. Escenario Base
                vi_base, margin_base, tab_base = calcular_dcf_motor(fcf_actual, cagr_base, acciones_millones, wacc_base, override_g)
                
                # 🟢 3. Escenario Optimista
                cagr_opt = min(0.40, cagr_base * 1.20)
                wacc_opt = max(0.05, wacc_base - 0.005)
                g_opt = override_g + 0.005
                vi_opt, margin_opt, tab_opt = calcular_dcf_motor(fcf_actual, cagr_opt, acciones_millones, wacc_opt, g_opt)
                
                st.markdown("### 🎯 Comparativa de Valor Intrínseco por Escenarios")
                
                col_pes, col_base, col_opt = st.columns(3)
                
                with col_pes:
                    diff_pes = ((vi_pes - precio) / precio) * 100
                    st.error("🔴 **Escenario Pesimista**")
                    st.metric("Valor Justo", f"${vi_pes:.2f}", delta=f"{diff_pes:.1f}% vs Mercado")
                    st.metric("Precio c/ Margen (21%)", f"${margin_pes:.2f}")
                    st.caption(f"Parámetros: CAGR {cagr_pes*100:.2f}% | WACC {wacc_pes*100:.2f}% | g {g_pes*100:.2f}%")
                    
                with col_base:
                    diff_base = ((vi_base - precio) / precio) * 100
                    st.warning("🟡 **Escenario Base**")
                    st.metric("Valor Justo", f"${vi_base:.2f}", delta=f"{diff_base:.1f}% vs Mercado")
                    st.metric("Precio c/ Margen (21%)", f"${margin_base:.2f}")
                    st.caption(f"Parámetros: CAGR {cagr_base*100:.2f}% | WACC {wacc_base*100:.2f}% | g {override_g*100:.2f}%")

                with col_opt:
                    diff_opt = ((vi_opt - precio) / precio) * 100
                    st.success("🟢 **Escenario Optimista**")
                    st.metric("Valor Justo", f"${vi_opt:.2f}", delta=f"{diff_opt:.1f}% vs Mercado")
                    st.metric("Precio c/ Margen (21%)", f"${margin_opt:.2f}")
                    st.caption(f"Parámetros: CAGR {cagr_opt*100:.2f}% | WACC {wacc_opt*100:.2f}% | g {g_opt*100:.2f}%")

                # Auditoría de Flujos
                st.divider()
                with st.expander("🔍 Ver detalle de flujos proyectados (Escenario Base)"):
                    st.table(tab_base)

        except Exception as e:
            st.error(f"Hubo un error al procesar los datos: {e}")
