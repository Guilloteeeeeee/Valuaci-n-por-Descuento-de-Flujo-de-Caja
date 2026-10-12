import streamlit as st
from yahooquery import Ticker

# Diseño de la página
st.set_page_config(page_title="Valuación DCF Automática - Estilo Simply Wall St", page_icon="📈", layout="wide")

st.title("📈 Calculadora Automática DCF (Estilo Simply Wall St)")
st.write("Herramienta comunitaria con modelo de 2 etapas y parámetros macroeconómicos automáticos.")

# Cuadro para que el usuario escriba el Ticker
ticker_simbolo = st.text_input("Escribe el Ticker de la empresa (ej. MSFT, AAPL, META):", "MSFT").upper()

# --- MOTOR MATEMÁTICO ESTILO SIMPLY WALL ST ---
def calcular_dcf_simply(fcf_inicial, cagr_inicial, acciones_millones):
    if acciones_millones <= 0 or fcf_inicial <= 0: 
        return 0, 0, []
    
    # Parámetros exactos extraídos del reporte de referencia para grandes techs
    tasa_descuento = 0.0866  # 8.66% Cost of Equity
    g_perp = 0.037           # 3.70% Perpetual Growth Rate (Bono a 5 años)
    
    fcf_descontado_total = 0
    fcf_actual_proyeccion = fcf_inicial
    tabla_proyecciones = []
    
    # Curva de desaceleración gradual (Fade Rate) a 10 años
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
        
    # Valor Terminal (Gordon Growth Model)
    fcf_anio_10 = fcf_actual_proyeccion
    valor_terminal = (fcf_anio_10 * (1 + g_perp)) / (tasa_descuento - g_perp)
    valor_terminal_descontado = valor_terminal / ((1 + tasa_descuento) ** 10)
    
    # Valor Total y por Acción
    valor_total_empresa = fcf_descontado_total + valor_terminal_descontado
    valor_por_accion = valor_total_empresa / acciones_millones
    
    # Margen de seguridad estándar del 21%
    valor_seguridad = valor_por_accion * 0.79 
    
    return valor_por_accion, valor_seguridad, tabla_proyecciones

# Botón para accionar la ejecución
if st.button("Ejecutar Valuación"):
    
    with st.spinner(f"Extrayendo datos de mercado y calculando modelo para {ticker_simbolo}..."):
        try:
            empresa = Ticker(ticker_simbolo)
            
            # 1. BÚSQUEDA ROBUSTA DE PRECIO (Evita el error de $0.00)
            precio = 0
            try:
                price_data = empresa.price.get(ticker_simbolo, {})
                if isinstance(price_data, dict):
                    precio = price_data.get('regularMarketPrice', 0)
                    if not precio or precio == 0:
                        precio = price_data.get('currentPrice', 0)
            except:
                pass
                
            if not precio or precio == 0:
                try:
                    summary = empresa.summary_detail.get(ticker_simbolo, {})
                    if isinstance(summary, dict):
                        precio = summary.get('previousClose', 0)
                except:
                    pass

            # 2. Obtener Acciones Dinámicas (Estrategia de 3 pasos)
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
            
            # 3. Obtener Free Cash Flow y CAGR
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
                    cagr_automatico = 0.10
            elif len(lista_fcf) == 1:
                fcf_actual = lista_fcf[0] / 1000000
                cagr_automatico = 0.10
            else:
                fcf_actual = 0.0
                cagr_automatico = 0.10
            
            # Límites de seguridad lógicos
            if cagr_automatico > 0.30: cagr_automatico = 0.30
            elif cagr_automatico < 0.04: cagr_automatico = 0.04
            
            # --- DASHBOARD VISUAL ---
            st.success("✅ ¡Datos procesados correctamente!")
            
            st.markdown("### 📊 Datos Base Extraídos")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Precio en Mercado", f"${precio:.2f}")
            col2.metric("Acciones en Circ.", f"{acciones_millones:.2f} M")
            col3.metric("Free Cash Flow Base", f"${fcf_actual:.2f} M")
            col4.metric("CAGR Automático Base", f"{cagr_automatico*100:.2f}%")
            
            st.divider()
            
            if fcf_actual <= 0:
                st.warning("⚠️ Esta empresa presenta Free Cash Flow negativo o no disponible.")
            elif acciones_millones <= 0:
                st.warning("⚠️ No se pudo determinar el número de acciones en circulación.")
            elif precio <= 0:
                st.warning("⚠️ No se pudo extraer el precio actual de mercado. Intenta nuevamente.")
            else:
                # Ejecutamos el modelo
                valor_justo, precio_compra, tabla_proyeccion = calcular_dcf_simply(fcf_actual, cagr_automatico, acciones_millones)
                
                # Diferencia porcentual con el precio de mercado
                diff_pct = ((valor_justo - precio) / precio) * 100
                
                st.markdown("### 🎯 Resultado del Valor Intrínseco")
                
                res1, res2, res3 = st.columns(3)
                res1.metric("Valor Justo Estimado", f"${valor_justo:.2f}", delta=f"{diff_pct:.1f}% vs Mercado")
                res2.metric("Precio de Compra (c/ 21% Margen)", f"${precio_compra:.2f}")
                res3.metric("Precio Actual en Pantalla", f"${precio:.2f}")
                
                if valor_justo > precio:
                    st.success("🟢 **Evaluación:** Según el modelo, la acción se encuentra **infravalorada** frente al mercado.")
                else:
                    st.warning("🟡 **Evaluación:** Según el modelo, la acción se encuentra cotizando por encima de su valor intrínseco.")

                # Tabla de auditoría
                with st.expander("🔍 Ver auditoría completa de flujos proyectados a 10 años"):
                    st.table(tabla_proyeccion)

        except Exception as e:
            st.error(f"Hubo un error al procesar los datos: {e}")
