import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Conversor v0.06", layout="wide", page_icon="📊")

# --- ESTILOS CSS PERSONALIZADOS ---
st.markdown("""
    <style>
    .titulo-ejecutivo {
        font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;
        color: #2C3E50;
        font-size: 28px;
        font-weight: 700;
        text-align: center;
        margin-bottom: 5px;
    }
    .subtitulo {
        color: #7F8C8D;
        text-align: center;
        font-size: 16px;
        margin-bottom: 30px;
        padding-bottom: 10px;
        border-bottom: 1px solid #eee;
    }
    </style>
""", unsafe_allow_html=True)

def limpiar_monto(texto):
    if not texto: return 0.0
    temp = texto.replace('.', '').replace(',', '.')
    try: return float(temp)
    except: return 0.0

def procesar_liquidacion(uploaded_file):
    data_empleados = []
    data_patronales = []
    
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
            if not texto_pagina: continue
            
            lines = texto_pagina.split('\n')
            for line in lines:
                clean_line = line.strip()
                if not clean_line: continue
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
                    if match_inmed: current_legajo, buscando_legajo = match_inmed.group(1), False
                    else: buscando_legajo = True
                    continue

                if buscando_legajo:
                    match_num = re.search(r'^(\d+)', clean_line)
                    if match_num and len(match_num.group(1)) <= 5:
                        current_legajo, buscando_legajo = match_num.group(1), False
                        continue

                # 3. FILTRO DE ENCABEZADOS
                if any(word in line_low for word in BLACKLIST_HEADER): continue

                # 4. PROCESAMIENTO EMPLEADOS
                matches = regex_concepto.findall(linea_preparada)
                for item in matches:
                    cod_str, desc, monto_str = item
                    if '/' in monto_str and len(monto_str) <= 10: continue
                    monto = limpiar_monto(monto_str)
                    if monto == 0: continue
                    
                    cod_int = int(cod_str)
                    if 136 <= cod_int <= 150: continue
                        
                    desc_upper = desc.upper()
                    remu, no_remu, reten, grupo, monto_final = 0, 0, 0, "", 0
                    
                    if "ANR" in desc_upper or "NO REM" in desc_upper:
                        no_remu, grupo, monto_final = monto, "NO REM", monto
                    elif 1 <= cod_int <= 65:
                        remu, grupo, monto_final = monto, "REM", monto
                    elif 66 <= cod_int <= 110:
                        no_remu, grupo, monto_final = monto, "NO REM", monto
                    elif 111 <= cod_int <= 135:
                        reten, grupo, monto_final = monto, "RET", monto
                    else:
                        reten, grupo, monto_final = monto, "RET", monto

                    # --- SOLUCIÓN DEL APÓSTROFE EN EL LEGAJO ---
                    # Lo convertimos a número real si es posible, sino queda como texto (ej: "S/L")
                    legajo_limpio = int(current_legajo) if str(current_legajo).isdigit() else current_legajo

                    data_empleados.append({
                        "Legajo": legajo_limpio, 
                        "Código": cod_str, 
                        "Concepto": desc.strip(),
                        "Remunerativo": remu, 
                        "No Remunerativo": no_remu, 
                        "Retenciones": reten,
                        "Grupo": grupo,
                        "Monto": monto_final
                    })

    # --- GENERACIÓN DEL EXCEL ---
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if data_empleados:
            df_base = pd.DataFrame(data_empleados)
            
            # --- HOJA 1: INFORME VISUAL ---
            unique_concepts = set((d['Grupo'], d['Código'], d['Concepto']) for d in data_empleados)
            order_grupo = {'REM': 1, 'NO REM': 2, 'RET': 3}
            sorted_concepts = sorted(list(unique_concepts), key=lambda x: (order_grupo.get(x[0], 4), int(x[1])))
            
            row1 = [""] + [x[0] for x in sorted_concepts]
            row2 = ["Nro"] + [x[1] for x in sorted_concepts]
            row3 = ["Legajo"] + [x[2] for x in sorted_concepts]
            
            legajos_data = {}
            for d in data_empleados:
                leg = d['Legajo']
                if leg not in legajos_data: legajos_data[leg] = {}
                legajos_data[leg][(d['Grupo'], d['Código'], d['Concepto'])] = legajos_data[leg].get((d['Grupo'], d['Código'], d['Concepto']), 0) + d['Monto']
                
            def parse_legajo(x):
                try: return float(x)
                except: return 999999
            
            sorted_legajos = sorted(legajos_data.keys(), key=parse_legajo)
            
            data_rows = []
            for leg in sorted_legajos:
                row = [leg] + [legajos_data[leg].get(concept, 0) for concept in sorted_concepts]
                data_rows.append(row)
                
            pd.DataFrame([row1, row2, row3] + data_rows).to_excel(writer, sheet_name='Informe', index=False, header=False)
            
            # --- HOJA 2: TOTALES ---
            df_tot = df_base.groupby('Legajo', as_index=False).agg({'Remunerativo': 'sum', 'No Remunerativo': 'sum', 'Retenciones': 'sum'})
            df_tot['Sueldo Neto'] = df_tot['Remunerativo'] + df_tot['No Remunerativo'] - df_tot['Retenciones']
            df_tot['Legajo_Num'] = pd.to_numeric(df_tot['Legajo'], errors='coerce')
            df_tot.sort_values(by='Legajo_Num').drop(columns=['Legajo_Num']).rename(columns={
                'Remunerativo': 'Total Remunerativo', 'No Remunerativo': 'Total No Remunerativo', 'Retenciones': 'Total Retenciones'
            }).to_excel(writer, sheet_name='Totales', index=False)

            # --- HOJA 3: BASE DE DATOS (TIDY) ---
            df_tablas = df_base[['Legajo', 'Grupo', 'Código', 'Concepto', 'Monto']].copy()
            df_tablas = df_tablas[df_tablas['Monto'] != 0].copy()
            df_tablas.insert(1, 'Nombre y Apellido', '')
            df_tablas['Impacto Neto'] = df_tablas.apply(lambda row: row['Monto'] if row['Grupo'] in ['REM', 'NO REM'] else -row['Monto'], axis=1)
            df_tablas['Legajo_Num'] = pd.to_numeric(df_tablas['Legajo'], errors='coerce')
            df_tablas.sort_values(by=['Legajo_Num', 'Grupo', 'Código']).drop(columns=['Legajo_Num']).to_excel(writer, sheet_name='Base_Tablas', index=False)

        # --- HOJA 4: APORTES PATRONALES ---
        if data_patronales:
            df_patronales = pd.DataFrame(data_patronales).sort_values(by='Código', key=lambda x: x.astype(int))
            total_gral = df_patronales['Total Importe'].sum()
            df_patronales = pd.concat([df_patronales, pd.DataFrame([{'Código': 'TOT', 'Concepto Patronal': 'TOTAL GENERAL', 'Total Importe': total_gral}])], ignore_index=True)
            df_patronales.to_excel(writer, sheet_name='Aportes Patronales', index=False)
            
        # --- BONUS: AUTO-AJUSTE DE ANCHO DE COLUMNAS EN EXCEL ---
        for sheetname in writer.sheets:
            worksheet = writer.sheets[sheetname]
            for col in worksheet.columns:
                max_length = 0
                column = col[0].column_letter # Letra de la columna (A, B, C...)
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except: pass
                adjusted_width = (max_length + 2) # Le damos un poquito de margen
                worksheet.column_dimensions[column].width = adjusted_width

    return output.getvalue()

# --- INTERFAZ DE USUARIO ---
st.markdown('<div class="titulo-ejecutivo">📑 Payroll Extractor Premium</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitulo">Transformación de Planillas PDF a Modelos de Datos</div>', unsafe_allow_html=True)

uploaded_file = st.file_uploader("📂 Arrastra o selecciona tu liquidación en PDF aquí", type=["pdf"])

if uploaded_file is not None:
    with st.spinner('Procesando algoritmos de extracción y construyendo Excel...'):
        try:
            excel_data = procesar_liquidacion(uploaded_file)
            
            # Cajita elegante de éxito
            st.success("✅ ¡Procesamiento completado! El archivo está listo para trabajar con Tablas Dinámicas.")
            
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                st.download_button(
                    label="📥 Descargar Reporte Completo (.xlsx)",
                    data=excel_data,
                    file_name=f"Resumen_Liquidacion_{uploaded_file.name.replace('.pdf', '')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True # Hace que el botón sea grande y centrado
                )
        except Exception as e:
            st.error("Hubo un error al leer el archivo. Verificá que el PDF sea el correcto.")
            with st.expander("🛠️ Ver detalle técnico del error"):
                st.write(f"Error: {e}")

# --- PRIVACIDAD ---
st.markdown("<br><br>", unsafe_allow_html=True)
with st.expander("🔒 Seguridad de tus datos"):
    st.write("""
        - Tus archivos **no se guardan** en ningún servidor ni base de datos externa.
        - El procesamiento ocurre íntegramente en la memoria volátil (RAM) del servidor.
        - Al cerrar esta pestaña, la información se destruye instantáneamente.
    """)