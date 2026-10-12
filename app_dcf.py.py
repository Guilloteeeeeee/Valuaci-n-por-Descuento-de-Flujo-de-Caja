import streamlit as st
from yahooquery import Ticker
import pandas as pd

# Configuración de la página
st.set_page_config(page_title="Valuación DCF Automática", page_icon="📈", layout="wide")

st.title("📈 Calculadora Automática DCF (Estilo Simply Wall St)")
st.write("Modelo institucional de 2 etapas: FCF reportado proyectado al CAGR del Cash Flow Operativo (CFO) en 3 escenarios.")

# Entrada de Ticker (Único parámetro de entrada)
ticker_simbolo = st.text_input("Escribe el Ticker de la empresa (ej. MSFT, AAPL, META, GOOGL):", "MSFT").upper()

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
if st.button("Ejecutar Valuación Automática"):
    
    with st.spinner(f"Extrayendo datos financieros y calculando escenarios para {ticker_simbolo}..."):
        try:
            empresa = Ticker(ticker_simbolo)
            
            # 1. PRECIO DE MERCADO
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

            # 2. BETA DE MERCADO
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
            
            # 4. FCF INFORMADO Y CAGR DEL CFO (HISTÓRICO)
            cf = empresa.cash_flow()
            
            fcf_actual = 0.0
            cagr_cfo_auto = 0.10
            
            if hasattr(cf, 'columns'):
                # FCF Informado (Base Oficial)
                if 'FreeCashFlow' in cf.columns:
                    lista_fcf = cf['FreeCashFlow'].dropna().tolist()
                    if len(lista_fcf) > 0:
                        fcf_actual = lista_fcf[-1] / 1000000

                # CAGR derivado del Cash Flow Operativo (CFO)
                if 'OperatingCashFlow' in cf.columns:
                    lista_cfo = cf['OperatingCashFlow'].dropna().tolist()
                    if len(lista_cfo) >= 2:
                        cfo_act = lista_cfo[-1] / 1000000
                        cfo_hist = lista_cfo[0] / 1000000
                        p_cfo = len(lista_cfo) - 1
                        if cfo_hist > 0 and cfo_act > 0:
                            cagr_cfo_auto = (cfo_act / cfo_hist) ** (1 / p_cfo) - 1

            # Acotar el CAGR dentro de rangos lógicos institucionales
            cagr_base = max(0.04, min(cagr_cfo_auto, 0.30))
            
            # PARÁMETROS MACRO FIJOS (SIMPLY WALL ST)
            risk_free_rate = 0.037       # 3.70% (Bono del tesoro)
            equity_risk_premium = 0.0442 # 4.42% (S&P Global ERP)
            g_base = 0.037               # 3.70% Crecimiento Perpetuo Estándar
            
            # WACC Dinámico vía CAPM
            beta_ajustado = max(0.8, min(beta_mercado, 2.0))
            wacc_base = risk_free_rate + (beta_ajustado * equity_risk_premium)
            
            # --- DASHBOARD VISUAL DE DATOS EXTRAÍDOS ---
            st.success("✅ ¡Datos financieros procesados automáticamente!")
            
            st.markdown("### 📊 Datos Base de Mercado")
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Precio Mercado", f"${precio:.2f}" if precio > 0 else "N/D")
            col2.metric("Acciones Circ.", f"{acciones_millones:,.0f} M")
            col3.metric("FCF Informado Base", f"${fcf_actual:,.2f} M")
            col4.metric("CAGR (CFO Auto)", f"{cagr_base*100:.2f}%")
            col5.metric("WACC Base (CAPM)", f"{wacc_base*100:.2f}%")
            
            st.divider()
            
            if fcf_actual <= 0 or acciones_millones <= 0 or precio <= 0:
                st.warning("⚠️ No se pudieron validar los datos fundamentales o el Free Cash Flow es negativo.")
            else:
                # --- CÁLCULO DE LOS 3 ESCENARIOS
