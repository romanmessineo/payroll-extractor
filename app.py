import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Conversor v0.01", layout="centered")

# --- ESTILOS CSS PERSONALIZADOS (Color más claro e icono) ---
st.markdown("""
    <style>
    .titulo-ejecutivo {
        font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;
        color: #5D6D7E; /* Color gris acero más claro */
        font-size: 26px;
        font-weight: 600;
        text-align: center;
        margin-bottom: 20px;
        padding-bottom: 10px;
        border-bottom: 1px solid #eee;
    }
    </style>
""", unsafe_allow_html=True)

def limpiar_monto(texto):
    if not texto:
        return 0.0
    # Elimina puntos de mil y cambia coma decimal por punto
    temp = texto.replace('.', '').replace(',', '.')
    try:
        return float(temp)
    except:
        return 0.0

def procesar_liquidacion(uploaded_file):
    data_empleados = []
    data_patronales = []
    
    # --- REGEX ---
    regex_concepto = re.compile(r'(\d{3})\s+([A-Za-zÁÉÍÓÚÑ0-9.\s/()+ -]{4,30})\s+([-?.?\d,]+)')
    regex_patronal = re.compile(r'^(13[6-9]|14\d|150)\s+[\d,.]+\s+([A-Za-zÁÉÍÓÚÑ0-9.\s/()+, -]{4,30})\s+([-?.?\d,]+)')
    
    BLACKLIST_HEADER = [
        "cuit", "planilla", "remuneraciones", "centro de costos", "convenio",
        "fec. ing", "ingr. rel", "fec.nac", "domicilio", "nacionalidad", 
        "est.civil", "categoria", "sueldo basico", "cuil", "documento", "pag."
    ]

    with pdfplumber.open(uploaded_file) as pdf:
        current_legajo = "S/L"
        buscando_legajo = False
        
        for page in pdf.pages:
            texto_pagina = page.extract_text()
            if not texto_pagina:
                continue
            
            lines = texto_pagina.split('\n')
            for line in lines:
                clean_line = line.strip()
                if not clean_line:
                    continue
                line_low = clean_line.lower()

                # 1. DETECCIÓN DE PATRONALES
                linea_preparada = clean_line.replace("%", "").replace("/ ", " ")
                match_patronal = regex_patronal.search(linea_preparada)
                if match_patronal:
                    data_patronales.append({
                        "Código": match_patronal.group(1),
                        "Concepto Patronal": match_patronal.group(2).strip(),
                        "Total Importe": limpiar_monto(match_patronal.group(3))
                    })
                    continue

                # 2. LÓGICA DE LEGAJO
                if "legajo" in line_low:
                    match_inmed = re.search(r'legajo\s*[:\s]*(\d+)', line_low)
                    if match_inmed:
                        current_legajo = match_inmed.group(1)
                        buscando_legajo = False
                    else:
                        buscando_legajo = True
                    continue

                if buscando_legajo:
                    match_num = re.search(r'^(\d+)', clean_line)
                    if match_num and len(match_num.group(1)) <= 5:
                        current_legajo = match_num.group(1)
                        buscando_legajo = False
                        continue

                # 3. FILTRO DE ENCABEZADOS
                if any(word in line_low for word in BLACKLIST_HEADER):
                    continue

                # 4. PROCESAMIENTO EMPLEADOS
                matches = regex_concepto.findall(linea_preparada)
                for item in matches:
                    cod_str, desc, monto_str = item
                    if '/' in monto_str and len(monto_str) <= 10:
                        continue
                    monto = limpiar_monto(monto_str)
                    if monto == 0:
                        continue
                    
                    cod_int = int(cod_str)
                    if 136 <= cod_int <= 150:
                        continue
                        
                    desc_upper = desc.upper()
                    remu, no_remu, reten = 0, 0, 0
                    
                    if "ANR" in desc_upper or "NO REM" in desc_upper:
                        no_remu = monto
                    elif 1 <= cod_int <= 65:
                        remu = monto
                    elif 66 <= cod_int <= 110:
                        no_remu = monto
                    elif 111 <= cod_int <= 135:
                        reten = monto
                    else:
                        reten = monto

                    data_empleados.append({
                        "Legajo": current_legajo, "Código": cod_str, "Concepto": desc.strip(),
                        "Remunerativo": remu, "No Remunerativo": no_remu, "Retenciones": reten
                    })

    # --- GENERACIÓN DEL EXCEL EN MEMORIA ---
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if data_empleados:
            df_empleados = pd.DataFrame(data_empleados)
            df_empleados.to_excel(writer, sheet_name='Detalle Empleados', index=False)
            
            # Totales por Legajo
            df_tot = df_empleados.groupby('Legajo', as_index=False).agg({
                'Remunerativo': 'sum', 'No Remunerativo': 'sum', 'Retenciones': 'sum'
            })
            df_tot['Sueldo Neto'] = df_tot['Remunerativo'] + df_tot['No Remunerativo'] - df_tot['Retenciones']
            
            # --- CORRECCIÓN SEGURA DE ORDENAMIENTO ---
            # Convierte a numero lo que puede, lo que no (S/L) lo deja como NaN y lo pone al final
            df_tot['Legajo_Num'] = pd.to_numeric(df_tot['Legajo'], errors='coerce')
            df_tot = df_tot.sort_values(by='Legajo_Num').drop(columns=['Legajo_Num'])
            
            df_tot.rename(columns={
                'Remunerativo': 'Total Remunerativo', 
                'No Remunerativo': 'Total No Remunerativo', 
                'Retenciones': 'Total Retenciones'
            }, inplace=True)
            df_tot.to_excel(writer, sheet_name='Totales por Legajo', index=False)

        if data_patronales:
            df_patronales = pd.DataFrame(data_patronales)
            df_patronales = df_patronales.sort_values(by='Código', key=lambda x: x.astype(int))
            total_gral = df_patronales['Total Importe'].sum()
            row_total = pd.DataFrame([{'Código': 'TOT', 'Concepto Patronal': 'TOTAL GENERAL', 'Total Importe': total_gral}])
            df_patronales = pd.concat([df_patronales, row_total], ignore_index=True)
            df_patronales.to_excel(writer, sheet_name='Aportes Patronales', index=False)
            
    return output.getvalue()

# --- INTERFAZ DE USUARIO ---

# Título personalizado con icono y color nuevo
st.markdown('<div class="titulo-ejecutivo">📑 Conversor Planilla Condensada a XLSX</div>', unsafe_allow_html=True)

st.markdown("""
1. **Sube** el PDF. 
2. **Procesa** la información.
3. **Descarga** el reporte.
""")

uploaded_file = st.file_uploader("Arrastra aquí tu archivo PDF", type=["pdf"])

if uploaded_file is not None:
    with st.spinner('Procesando datos contables...'):
        try:
            excel_data = procesar_liquidacion(uploaded_file)
            
            # --- NUEVA DISPOSICIÓN EN COLUMNAS ---
            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.success("✅ ¡Procesamiento completado con éxito!")
            
            with col2:
                st.download_button(
                    label="📥 Descargar Reporte",
                    data=excel_data,
                    file_name=f"Liquidacion_{uploaded_file.name.replace('.pdf', '')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
        except Exception as e:
            st.error("Hubo un error al leer el archivo.")
            # --- ERROR AMIGABLE (Detalle oculto) ---
            with st.expander("Ver detalle técnico del error"):
                st.write(f"Error: {e}")

# --- PRIVACIDAD ---
st.markdown("---")
with st.expander("🔒 Seguridad de tus datos"):
    st.write("""
        - Tus archivos **no se guardan** en ningún servidor.
        - El procesamiento ocurre en la memoria volátil (RAM) de la aplicación.
        - Al cerrar esta pestaña, toda la información se elimina permanentemente.
    """)