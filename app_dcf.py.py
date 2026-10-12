import streamlit as st
from yahooquery import Ticker

# Diseño de la página
st.set_page_config(page_title="Valuación DCF Automática", page_icon="📈", layout="wide")

st.title("📈 Calculadora Automática DCF")
st.write("Herramienta exclusiva para la comunidad financiera. Ingresa un ticker para obtener la valuación en tiempo real.")

# Cuadro para que el usuario escriba el Ticker
ticker_simbolo = st.text_input("Escribe el Ticker de la empresa (ej. AAPL, META, CVX):", "AAPL").upper()

# --- CEREBRO MATEMÁTICO ---
def calcular_dcf(fcf_inicial, cagr, tasa_descuento, g_perp, acciones_millones):
    if acciones_millones <= 0: return 0, 0 # Evitar división por cero
    
    fcf_descontado_total = 0
    fcf_futuro = fcf_inicial
    fcf_descontado_10 = 0
    
    for i in range(1, 11):
        fcf_futuro = fcf_futuro * (1 + cagr)
        fcf_descontado = fcf_futuro / ((1 + tasa_descuento) ** i)
        fcf_descontado_total += fcf_descontado
        if i == 10:
            fcf_descontado_10 = fcf_descontado
            
    valor_perpetuidad_descontado = fcf_descontado_10 * (1 + g_perp) / (tasa_descuento - g_perp)
    valor_total = fcf_descontado_total + valor_perpetuidad_descontado
    valor_por_accion = valor_total / acciones_millones
    valor_seguridad = valor_por_accion * 0.79 
    
    return valor_por_accion, valor_seguridad

# Botón para accionar la magia
if st.button("Calcular Precio Justo (DCF)"):
    
    with st.spinner(f"Descargando datos y calculando modelos para {ticker_simbolo}..."):
        try:
            empresa = Ticker(ticker_simbolo)
            
            # 1. Obtenemos Precio con seguridad
            precio_data = empresa.price.get(ticker_simbolo, {})
            precio = precio_data.get('regularMarketPrice', 0) if isinstance(precio_data, dict) else 0
            
            # 2. Obtenemos Acciones Dinámicas seguras
            datos_stats = empresa.key_stats.get(ticker_simbolo, {})
            acciones = 0
            if isinstance(datos_stats, dict):
                acciones = datos_stats.get('impliedSharesOutstanding', 0)
                if not acciones or acciones < 1000000:
                    acciones = datos_stats.get('sharesOutstanding', 0)
            
            if not acciones or acciones < 1000000:
                try:
                    bs = empresa.balance_sheet()
                    if hasattr(bs, 'columns') and 'OrdinarySharesNumber' in bs.columns:
                        acciones = bs['OrdinarySharesNumber'].dropna().iloc[-1]
                except:
                    pass
                    
            acciones_millones = acciones / 1000000 if acciones else 0
            
            # 3. Obtenemos el FCF y CAGR con BLINDAJE MATEMÁTICO
            cf = empresa.cash_flow()
            lista_fcf = []
            if hasattr(cf, 'columns') and 'FreeCashFlow' in cf.columns:
                lista_fcf = cf['FreeCashFlow'].dropna().tolist()
            
            # Prevenir errores si hay pocos años de datos o flujos negativos
            if len(lista_fcf) >= 2:
                fcf_actual = lista_fcf[-1] / 1000000
                fcf_historico = lista_fcf[0] / 1000000
                periodos = len(lista_fcf) - 1
                
                if fcf_historico > 0 and fcf_actual > 0:
                    cagr_automatico = (fcf_actual / fcf_historico) ** (1 / periodos) - 1
                else:
                    cagr_automatico = 0.05 # Tasa conservadora por defecto si hay flujos negativos históricos
            elif len(lista_fcf) == 1:
                fcf_actual = lista_fcf[0] / 1000000
                cagr_automatico = 0.05
            else:
                fcf_actual = 0.0
                cagr_automatico = 0.05
            
            # Límites de seguridad del CAGR
            if cagr_automatico > 0.15: cagr_automatico = 0.15
            elif cagr_automatico < 0.03: cagr_automatico = 0.03
            
            # --- SECCIÓN 1: DASHBOARD DE DATOS ---
            st.success("✅ ¡Datos financieros extraídos y procesados con éxito!")
            
            st.markdown("### 📊 Resumen Financiero Actual")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Precio en Mercado", f"${precio:.2f}")
            col2.metric("Acciones en Circ.", f"{acciones_millones:.2f} M")
            col3.metric("Free Cash Flow Inicial", f"${fcf_actual:.2f} M")
            col4.metric("Crecimiento Proy. (CAGR)", f"{cagr_automatico*100:.2f}%")
            
            st.divider()
            
            # --- SECCIÓN 2: RESULTADOS DCF EN PANTALLA ---
            if fcf_actual <= 0:
                st.warning("⚠️ **Atención:** El Free Cash Flow actual de esta empresa es negativo o no está disponible en la base de datos. Los modelos de Descuento de Flujos de Caja requieren beneficios positivos para proyectarse y calcular el valor intrínseco.")
            elif acciones_millones <= 0:
                st.warning("⚠️ **Atención:** No se pudo encontrar el dato oficial de acciones en circulación para procesar el valor por acción.")
            else:
                st.markdown("### 🎯 Valuación DCF: Precio Justo Estimado")
                st.write("Proyección a 10 años | Crecimiento Perpetuo: **3%** | Margen de Seguridad: **21%**")
                
                # El motor calcula los 3 escenarios
                val_cons, seg_cons = calcular_dcf(fcf_actual, cagr_automatico, 0.11, 0.03, acciones_millones) # 11% descuento
                val_med, seg_med = calcular_dcf(fcf_actual, cagr_automatico, 0.09, 0.03, acciones_millones)   # 9% descuento
                val_opt, seg_opt = calcular_dcf(fcf_actual, cagr_automatico, 0.07, 0.03, acciones_millones)   # 7% descuento
                
                sc1, sc2, sc3 = st.columns(3)
                
                with sc1:
                    st.info("📉 **Escenario Conservador**\n\n*(Tasa de Descuento: 11%)*")
                    st.metric("Valor por Acción", f"${val_cons:.2f}")
                    st.metric("Con Margen (Compra)", f"${seg_cons:.2f}")
                    
                with sc2:
                    st.warning("⚖️ **Escenario Medio**\n\n*(Tasa de Descuento: 9%)*")
                    st.metric("Valor por Acción", f"${val_med:.2f}")
                    st.metric("Con Margen (Compra)", f"${seg_med:.2f}")
                    
                with sc3:
                    st.success("🚀 **Escenario Optimista**\n\n*(Tasa de Descuento: 7%)*")
                    st.metric("Valor por Acción", f"${val_opt:.2f}")
                    st.metric("Con Margen (Compra)", f"${seg_opt:.2f}")

        except Exception as e:
            st.error(f"Hubo un error al procesar los datos. Detalle técnico para diagnóstico: {e}")
