def calcular_dcf_simply_dinamico(fcf_inicial, cagr_inicial, acciones_millones, beta_empresa):
    if acciones_millones <= 0 or fcf_inicial <= 0: 
        return 0, 0, []
    
    # Variables Macro Dinámicas (Aproximación estándar del mercado actual)
    risk_free_rate = 0.037   # 3.70% (Bono a 5/10 años)
    equity_risk_premium = 0.0442 # 4.42% (S&P Global ERP)
    g_perp = risk_free_rate  # El crecimiento perpetuo suele igualar a la tasa libre de riesgo
    
    # Límite práctico para el Beta (evita tasas irreales en empresas extremas)
    beta_ajustado = max(0.8, min(beta_empresa, 2.0))
    
    # Cálculo dinámico del Cost of Equity (Tasa de Descuento)
    tasa_descuento = risk_free_rate + (beta_ajustado * equity_risk_premium)
    
    fcf_descontado_total = 0
    fcf_actual_proyeccion = fcf_inicial
    tabla_proyecciones = []
    
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
            "FCF Descontado": f"${fcf_descontado:,.2f} M",
            "Tasa Descuento (WACC)": f"{tasa_descuento*100:.2f}%"
        })
        
    fcf_anio_10 = fcf_actual_proyeccion
    valor_terminal = (fcf_anio_10 * (1 + g_perp)) / (tasa_descuento - g_perp)
    valor_terminal_descontado = valor_terminal / ((1 + tasa_descuento) ** 10)
    
    valor_total_empresa = fcf_descontado_total + valor_terminal_descontado
    valor_por_accion = valor_total_empresa / acciones_millones
    
    valor_seguridad = valor_por_accion * 0.79 
    
    return valor_por_accion, valor_seguridad, tabla_proyecciones
