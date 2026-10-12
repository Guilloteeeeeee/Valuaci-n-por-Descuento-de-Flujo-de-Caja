import streamlit as st
from yahooquery import Ticker
import pandas as pd

# Diseño de la página
st.set_page_config(page_title="Calculadora DCF Automática", page_icon="📈", layout="wide")

st.title("📈 Calculadora Automática DCF (Estilo Simply Wall St)")
st.write("Herramienta comunitaria con modelo de 2 etapas y parámetros ajustables.")

# Sidebar de Parámetros
st.sidebar.header("⚙️ Ajustes del Modelo DCF")
override_cagr = st.sidebar.checkbox("Ajustar CAGR manualmente", value=False)
cagr_manual_val = st.sidebar.slider("CAGR Proyectado (Años 1-5)", 0.0, 40.0, 15.0, 0.5) / 100 if override_cagr else None

override_wacc = st.sidebar.checkbox("Ajustar Tasa Descuento (WACC) manualmente", value=False)
wacc_manual_val = st.sidebar.slider("Tasa de Descuento (WACC)", 5.0, 15.0, 8.66, 0.1) / 100 if override_wacc else None

# Cuadro para que el usuario escriba el Ticker
ticker_simbolo = st.text_input("Escribe el Ticker de la empresa (ej. MSFT, AAPL, META):", "MSFT").upper()

# --- MOTOR MATEMÁTICO DINÁMICO ---
def calcular_dcf_simply_dinamico(fcf_inicial, cagr_inicial, acciones_millones, beta_empresa, wacc_custom=None):
    if acciones_millones <= 0 or fcf_inicial <= 0: 
        return 0, 0, [], 0, 0
    
    # Parámetros Macro
    risk_free_rate = 0.037       # 3.70% (Bono a 10 años)
    equity_risk_premium = 0.0442 # 4.42% (ERP)
    g_perp = risk_free_rate      # Crecimiento perpetuo
    
    beta_ajustado = max(0.8, min(beta_empresa, 2.0))
    
    # Elección de Tasa de Descuento (Manual o CAPM)
    if wacc_custom:
        tasa_descuento = wacc_custom
    else:
        tasa_descuento = risk_free_rate + (beta_ajustado * equity_risk_premium)
    
    fcf_descontado_total = 0
    fcf_actual_proyeccion = fcf_inicial
    tabla_proyecciones = []
    
    # Desaceleración gradual (Fade Rate) a 10 años
    tasa_actual = cagr_inicial
    paso_reduccion = (cagr_inicial - g_perp) / 10 if cagr_inicial > g_perp else 0
    
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
        
    # Valor Terminal
    fcf_anio_10 = fcf_actual_proyeccion
    valor_terminal = (fcf_anio_10 * (1 + g_perp)) / (tasa_descuento - g_perp)
    valor_terminal_descontado = valor_terminal / ((1 + tasa_descuento) ** 10)
    
    valor_total_empresa = fcf_descontado_total + valor_terminal_descontado
    valor_por_accion = valor_total_empresa / acciones_millones
    valor_seguridad = valor_por_accion * 0.79 
    
    return valor_por_accion, valor_seguridad, tabla_proyecciones, tasa_descuento, beta_ajustado

# Botón para accionar la ejecución
if st.button("Ejecutar Valuación"):
    
    with st.spinner(f"Extrayendo datos de mercado y calculando modelo para {ticker_simbolo}..."):
        try:
            empresa = Ticker(ticker_simbolo)
            
            # 1. BÚSQUEDA A PRUEBA DE ERRORES DE PRECIO
            precio = 0
            try:
                # Intento 1: Summary Detail
                sd = empresa.summary_detail.get(ticker_simbolo, {})
                if isinstance(sd, dict):
                    precio = sd.get('previousClose', 0) or sd.get('regularMarketPrice', 0)
                
                # Intento 2: Financial Data (Backup de precio)
                if not precio or precio == 0:
                    fd = empresa.financial_data.get(ticker_simbolo, {})
                    if isinstance(fd, dict):
                        precio = fd.get('currentPrice', 0)
                        
                # Intento 3: Price Module
                if not precio or precio == 0:
                    pr = empresa.price.get(ticker_simbolo, {})
                    if isinstance(pr, dict):
                        precio = pr.get('regularMarketPrice', 0) or pr.get('currentPrice', 0)
            except Exception as e:
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
            
            # 4. FREE CASH FLOW Y CAGR
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
            
            # Elección final de CAGR
            cagr_final = cagr_manual_val if override_cagr else cagr_auto
            
            # Límites de seguridad para el CAGR automático
            if not override_cagr:
                if cagr_final > 0.30: cagr_final = 0.30
                elif cagr_final < 0.04: cagr_final = 0.04
            
            # --- DASHBOARD VISUAL ---
            st.success("✅ ¡Datos procesados correctamente!")
            
            st.markdown("### 📊 Datos Base Extraídos")
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Precio en Mercado", f"${precio:.2f}")
            col2.metric("Acciones en Circ.", f"{acciones_millones:,.0f} M")
            col3.metric("Free Cash Flow", f"${fcf_actual:,.2f} M")
            col4.metric("CAGR Usado", f"{cagr_final*100:.2f}%")
            col5.metric("Beta", f"{beta_mercado:.2f}")
            
            st.divider()
            
            if fcf_actual <= 0:
                st.warning("⚠️ Esta empresa presenta Free Cash Flow negativo o no disponible.")
            elif acciones_millones <= 0:
                st.warning("⚠️ No se pudo determinar el número de acciones en circulación.")
            elif precio <= 0:
                st.warning("⚠️ No se pudo extraer el precio actual de mercado. Revisa el ticker ingresado.")
            else:
                # Ejecutamos el modelo
                valor_justo, precio_compra, tabla_proyeccion, wacc, beta_usado = calcular_dcf_simply_dinamico(
                    fcf_actual, cagr_final, acciones_millones, beta_mercado, wacc_manual_val
                )
                
                # Diferencia porcentual
                diff_pct = ((valor_justo - precio) / precio) * 100
                
                st.markdown("### 🎯 Resultado del Valor Intrínseco")
                
                res1, res2, res3 = st.columns(3)
                res1.metric("Valor Justo Estimado", f"${valor_justo:.2f}", delta=f"{diff_pct:.1f}% vs Mercado")
                res2.metric("Precio c/ 21% Margen", f"${precio_compra:.2f}")
                res3.metric("Tasa Descuento (WACC)", f"{wacc*100:.2f}%")
                
                if valor_justo > (precio * 1.05):
                    st.success("🟢 **Evaluación:** Según el modelo, la acción se encuentra **infravalorada** frente al mercado.")
                elif valor_justo < (precio * 0.95):
                    st.error("🔴 **Evaluación:** Según el modelo, la acción se encuentra cotizando **por encima** de su valor intrínseco (Sobrevalorada).")
                else:
                    st.warning("🟡 **Evaluación:** Según el modelo, la acción cotiza a su valor justo (Fair Value).")

                # Tabla de auditoría
                with st.expander("🔍 Ver auditoría completa de flujos proyectados a 10 años"):
                    st.table(tabla_proyeccion)

        except Exception as e:
            st.error(f"Hubo un error al procesar los datos: {e}")
