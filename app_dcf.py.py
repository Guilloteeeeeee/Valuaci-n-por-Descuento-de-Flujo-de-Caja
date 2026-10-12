import streamlit as st
from yahooquery import Ticker

# Diseño de la página
st.set_page_config(page_title="Valuación DCF Automática", page_icon="📈", layout="wide")

st.title("📈 Calculadora Automática DCF (Estilo Simply Wall St)")
st.write("Herramienta comunitaria con modelo de 2 etapas y parámetros macroeconómicos dinámicos.")

# Cuadro para que el usuario escriba el Ticker
ticker_simbolo = st.text_input("Escribe el Ticker de la empresa (ej. MSFT, AAPL, META):", "MSFT").upper()

# --- MOTOR MATEMÁTICO DINÁMICO ---
def calcular_dcf_simply_dinamico(fcf_inicial, cagr_inicial, acciones_millones, beta_empresa):
    if acciones_millones <= 0 or fcf_inicial <= 0: 
        return 0, 0, []
    
    # Parámetros Macro (Aproximación estándar del mercado actual)
    risk_free_rate = 0.037       # 3.70% (Bono a 10 años aprox)
    equity_risk_premium = 0.0442 # 4.42% (S&P Global ERP)
    g_perp = risk_free_rate      # Crecimiento perpetuo (Gordon Growth)
    
    # Límite práctico para el Beta (evita tasas de descuento irreales)
    beta_ajustado = max(0.8, min(beta_empresa, 2.0))
    
    # Cálculo dinámico del Cost of Equity (CAPM)
    tasa_descuento = risk_free_rate + (beta_ajustado * equity_risk_premium)
    
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
    
    return valor_por_accion, valor_seguridad, tabla_proyecciones, tasa_descuento, beta_ajustado

# Botón para accionar la ejecución
if st.button("Ejecutar Valuación"):
    
    with st.spinner(f"Extrayendo datos de mercado y calculando modelo para {ticker_simbolo}..."):
        try:
            empresa = Ticker(ticker_simbolo)
            
            # 1. BÚSQUEDA ROBUSTA DE PRECIO
            precio = 0
            try:
                price_data = empresa.price.get(ticker_simbolo, {})
                if isinstance(price_data, dict):
                    precio = price_data.get('regularMarketPrice', 0)
                    if not precio or precio == 0:
                        precio = price_data.get('currentPrice', 0)
            except:
