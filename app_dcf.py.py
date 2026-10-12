import streamlit as st
from yahooquery import Ticker

# Diseño de la página
st.set_page_config(page_title="Valuación DCF Automática - Estilo Institucional", page_icon="📈", layout="wide")

st.title("📈 Calculadora Automática DCF (Modelo Institucional)")
st.write("Herramienta automatizada para la comunidad financiera. Valuación intrínseca de 2 etapas con desaceleración gradual.")

# Cuadro para que el usuario escriba el Ticker
ticker_simbolo = st.text_input("Escribe el Ticker de la empresa (ej. AAPL, MSFT, META):", "MSFT").upper()

# --- MOTOR MATEMÁTICO INSTITUCIONAL (Automático con Desaceleración) ---
def calcular_dcf_institucional(fcf_inicial, cagr_inicial, acciones_millones):
    if acciones_millones <= 0 or fcf_inicial <= 0: 
        return 0, 0, []
    
    # Parámetros macro automáticos estilo Simply Wall St para empresas grandes
    tasa_descuento = 0.087  # ~8.7% (Costo de capital promedio ajustado)
    g_perp = 0.035          # ~3.5% (Crecimiento perpetuo de largo plazo)
    
    fcf_descontado_total = 0
    fcf_actual_proyeccion = fcf_inicial
    tabla_proyecciones = []
    
    # Curva de desaceleración gradual (Fade Growth Rate) a lo largo de 10 años
    # El crecimiento inicial va cediendo año a año hasta converger con la perpetuidad
    tasa_actual = cagr_inicial
    paso_reduccion = (cagr_inicial - g_perp) / 10 if cagr_inicial > g_perp else 0
    
    for i in range(1, 11):
        # Reducir gradualmente la tasa de crecimiento hacia la perpetuidad
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
        
    # FCF del último año para Valor Terminal
    fcf_anio_10 = fcf_actual_proyeccion
    
    # Valor Terminal (Gordon Growth Model)
    valor_terminal = (fcf_anio_10 * (1 + g_perp)) / (tasa_descuento - g_perp)
    valor_terminal_descontado = valor_terminal / ((1 + tasa_descuento) ** 10)
    
    # Valor Total de la Empresa y por Acción
    valor_total_empresa = fcf_descontado_total + valor_terminal_descontado
    valor_por_accion = valor_total_empresa / acciones_millones
    
    # Margen de seguridad estándar del 21%
    valor_seguridad = valor_por_accion * 0.79 
    
    return valor_por_accion, valor_seguridad, tabla_proyecciones

# Botón para accionar la ejecución
if st.button("Ejecutar Valuación Automática"):
    
    with st.spinner(f"Extrayendo estados financieros y aplicando modelo de 2 etapas para {ticker_simbolo}..."):
        try:
            empresa = Ticker(ticker_simbolo)
            
            # 1. Obtener Precio Actual
            precio_data = empresa.price.get(ticker_simbolo, {})
            precio = precio_data.get('regularMarketPrice', 0) if isinstance(precio_data, dict) else 0
            
            # 2. Obtener Acciones Dinámicas
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
            
            # 3. Obtener Free Cash Flow y CAGR Automático
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
                    cagr_automatico = 0.08
            elif len(lista_fcf) == 1:
                fcf_actual = lista_fcf[0] / 1000000
                cagr_automatico = 0.08
            else:
                fcf_actual = 0.0
                cagr_automatico = 0.08
            
            # Acotar el CAGR para que sea realista y automático
            if cagr_automatico > 0.25: cagr_automatico = 0.25
            elif cagr_automatico < 0.04: cagr_automatico = 0.04
            
            # --- DASHBOARD VISUAL ---
            st.success("✅ ¡Valuación institucional calculada con éxito!")
            
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
            else:
                # Ejecutamos el modelo automático
                valor_justo, precio_compra, tabla_proyeccion = calcular_dcf_institucional(fcf_actual, cagr_automatico, acciones_millones)
                
                # Diferencia porcentual con el precio de mercado
                diff_pct = ((valor_justo - precio) / precio) * 100
                
                st.markdown("### 🎯 Resultado del Valor Intrínseco")
                
                res1, res2, res3 = st.columns(3)
                res1.metric("Valor Justo Estimado", f"${valor_justo:.2f}", delta=f"{diff_pct:.1f}% vs Mercado")
                res2.metric("Precio de Compra (c/ 21% Margen)", f"${precio_compra:.2f}")
                res3.metric("Precio Actual en Pantalla", f"${precio:.2f}")
                
                if valor_justo > precio:
                    st.success("🟢 **Evaluación:** Según el modelo de flujos descontados, la acción se encuentra **infravalorada** frente al precio de mercado.")
                else:
                    st.warning("🟡 **Evaluación:** Según el modelo de flujos descontados, la acción cotiza por encima de su valor intrínseco estimado.")

                # Tabla de auditoría de los 10 años
                with st.expander("🔍 Ver auditoría completa de flujos proyectados a 10 años (Modelo de Desaceleración)"):
                    st.table(tabla_proyeccion)

        except Exception as e:
            st.error(f"Hubo un error al procesar los datos: {e}")
