import streamlit as st
from yahooquery import Ticker

# Diseño de la página
st.set_page_config(page_title="Valuación DCF Automática - Estilo Pro", page_icon="📈", layout="wide")

st.title("📈 Calculadora Automática DCF (Modelo 2 Etapas)")
st.write("Herramienta inspirada en metodologías institucionales para calcular el valor intrínseco en tiempo real.")

# Cuadro para que el usuario escriba el Ticker
ticker_simbolo = st.text_input("Escribe el Ticker de la empresa (ej. AAPL, META, MSFT):", "AAPL").upper()

# --- MOTOR MATEMÁTICO DE 2 ETAPAS (Estilo Simply Wall St) ---
def calcular_dcf_2_etapas(fcf_inicial, cagr_inicial, tasa_descuento, g_perp, acciones_millones):
    if acciones_millones <= 0 or fcf_inicial <= 0: 
        return 0, 0, []
    
    fcf_descontado_total = 0
    fcf_actual_proyeccion = fcf_inicial
    tabla_proyecciones = []
    
    # Etapa 1: Proyección a 10 años (disminuyendo gradualmente el crecimiento si es muy alto)
    cagr_actual = cagr_inicial
    for i in range(1, 11):
        # Descenso gradual opcional hacia la perpetuidad o mantener el CAGR con tope
        fcf_actual_proyeccion = fcf_actual_proyeccion * (1 + cagr_actual)
        fcf_descontado = fcf_actual_proyeccion / ((1 + tasa_descuento) ** i)
        fcf_descontado_total += fcf_descontado
        
        tabla_proyecciones.append({
            "Año": f"Año {i}",
            "FCF Proyectado": f"${fcf_actual_proyeccion:,.2f} M",
            "FCF Descontado": f"${fcf_descontado:,.2f} M"
        })
        
    # El FCF del último año (Año 10) para el cálculo de perpetuidad
    fcf_anio_10 = fcf_actual_proyeccion
    
    # Etapa 2: Valor Terminal (Gordon Growth Model)
    # TV = [FCF_10 * (1 + g)] / (Tasa Descuento - g)
    valor_terminal = (fcf_anio_10 * (1 + g_perp)) / (tasa_descuento - g_perp)
    
    # Descontar el Valor Terminal a hoy
    valor_terminal_descontado = valor_terminal / ((1 + tasa_descuento) ** 10)
    
    # Valor Total de la Empresa (Equity Value)
    valor_total_empresa = fcf_descontado_total + valor_terminal_descontado
    
    # Valor por Acción
    valor_por_accion = valor_total_empresa / acciones_millones
    
    # Margen de Seguridad del 21% (Precio de Compra Sugerido)
    valor_seguridad = valor_por_accion * 0.79 
    
    return valor_por_accion, valor_seguridad, tabla_proyecciones

# Botón para accionar la ejecución
if st.button("Calcular Valuación Institucional"):
    
    with st.spinner(f"Analizando estados financieros y proyectando flujos para {ticker_simbolo}..."):
        try:
            empresa = Ticker(ticker_simbolo)
            
            # 1. Obtener Precio Actual
            precio_data = empresa.price.get(ticker_simbolo, {})
            precio = precio_data.get('regularMarketPrice', 0) if isinstance(precio_data, dict) else 0
            
            # 2. Obtener Acciones Dinámicas (Estrategia robusta)
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
            
            # 3. Obtener Free Cash Flow Histórico y CAGR
            cf = empresa.cash_flow()
            lista_fcf = []
            if hasattr(cf, 'columns') and 'FreeCashFlow' in cf.columns:
                lista_fcf = cf['FreeCashFlow'].dropna().tolist()
            
            if len(lista_fcf) >= 2:
                fcf_actual = lista_fcf[-1] / 1000000
                fcf_historico = lista_fcf[0] / 1000000
                periodos = len(lista_fcf) - 1
                
                if fcf_historico > 0 and fcf_actual > 0:
                    cagr_automatico = (fcf_actual / fcf_historico) ** (1 / periodos) - 1
                else:
                    cagr_automatico = 0.05
            elif len(lista_fcf) == 1:
                fcf_actual = lista_fcf[0] / 1000000
                cagr_automatico = 0.05
            else:
                fcf_actual = 0.0
                cagr_automatico = 0.05
            
            # Límites de seguridad razonables para el crecimiento
            if cagr_automatico > 0.20: cagr_automatico = 0.20
            elif cagr_automatico < 0.02: cagr_automatico = 0.02
            
            # --- DASHBOARD VISUAL ---
            st.success("✅ ¡Datos institucionales procesados correctamente!")
            
            st.markdown("### 📊 Métricas Base de la Empresa")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Precio en Mercado", f"${precio:.2f}")
            col2.metric("Acciones en Circ.", f"{acciones_millones:.2f} M")
            col3.metric("Free Cash Flow Base", f"${fcf_actual:.2f} M")
            col4.metric("Crecimiento Hist./Est.", f"{cagr_automatico*100:.2f}%")
            
            st.divider()
            
            if fcf_actual <= 0:
                st.warning("⚠️ Esta empresa presenta Free Cash Flow negativo o no disponible, por lo que el modelo DCF no puede aplicarse.")
            elif acciones_millones <= 0:
                st.warning("⚠️ No se pudo determinar el número de acciones en circulación.")
            else:
                st.markdown("### 🎯 Valor Intrínseco Estimado (Modelo 2 Etapas)")
                st.write("Supuestos: Proyección a 10 años | Tasa Perpetua (g): **2.5%** | Margen de Seguridad: **21%**")
                
                # Tasas de descuento para los 3 escenarios (Conservador, Medio, Optimista)
                val_cons, seg_cons, tabla_cons = calcular_dcf_2_etapas(fcf_actual, cagr_automatico, 0.11, 0.025, acciones_millones)
                val_med, seg_med, tabla_med = calcular_dcf_2_etapas(fcf_actual, cagr_automatico, 0.09, 0.025, acciones_millones)
                val_opt, seg_opt, tabla_opt = calcular_dcf_2_etapas(fcf_actual, cagr_automatico, 0.07, 0.025, acciones_millones)
                
                sc1, sc2, sc3 = st.columns(3)
                
                with sc1:
                    st.info("📉 **Escenario Conservador**\n\n*(Descuento: 11%)*")
                    st.metric("Valor por Acción", f"${val_cons:.2f}")
                    st.metric("Precio de Compra (c/ Margen)", f"${seg_cons:.2f}")
                    
                with sc2:
                    st.warning("⚖️ **Escenario Medio**\n\n*(Descuento: 9%)*")
                    st.metric("Valor por Acción", f"${val_med:.2f}")
                    st.metric("Precio de Compra (c/ Margen)", f"${seg_med:.2f}")
                    
                with sc3:
                    st.success("🚀 **Escenario Optimista**\n\n*(Descuento: 7%)*")
                    st.metric("Valor por Acción", f"${val_opt:.2f}")
                    st.metric("Precio de Compra (c/ Margen)", f"${seg_opt:.2f}")
                    
                # Desplegable opcional para ver el desglose de los flujos proyectados (estilo reporte profesional)
                with st.expander("🔍 Ver desglose de flujos de caja proyectados (Escenario Medio)"):
                    st.table(tabla_med)

        except Exception as e:
            st.error(f"Hubo un error al procesar los datos: {e}")
