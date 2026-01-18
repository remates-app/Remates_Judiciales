import streamlit as st
import pandas as pd
import json
import time
import tempfile
import os 
import subprocess # <--- AGREGA ESTO SI NO ESTÁ
from io import BytesIO
from google import genai
from playwright.sync_api import sync_playwright
# ... resto de imports ...

# === AGREGA ESTE BLOQUE JUSTO DEBAJO DE LOS IMPORTS ===
# Esto instala el navegador automáticamente si no existe
subprocess.run(["playwright", "install", "chromium"])
# ==========================================
# CONFIGURACIÓN DE LA PÁGINA
# ==========================================
st.set_page_config(page_title="Extractor Remates Judiciales", layout="wide", page_icon="⚖️")

st.title("⚖️ Extractor de Remates Judiciales - Cloud Edition")
st.markdown("""
Esta herramienta automatiza la extracción de detalles de remates judiciales, 
analiza riesgos con IA y genera reportes de inversión.
""")

# ==========================================
# BARRA LATERAL (CONFIGURACIÓN)
# ==========================================
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # Gestión de API Key Segura
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        api_key = st.text_input("Ingresa tu Google API Key:", type="password")
    
    usar_ia = st.toggle("Activar Inteligencia Artificial", value=True)
    
    st.divider()
    
    uploaded_file = st.file_uploader("📂 Cargar Listado (Excel)", type=["xlsx"])

# ==========================================
# FUNCIONES AUXILIARES
# ==========================================
def forzar_texto(entrada):
    if entrada is None: return ""
    texto = str(entrada)
    remplazos = {'\u2013': '-', '\u2014': '-', '\u201c': '"', '\u201d': '"', '\u2018': "'", '\u2019': "'"}
    for original, nuevo in remplazos.items():
        texto = texto.replace(original, nuevo)
    return texto.strip()

def analizar_con_ia(client, model, texto_sucio):
    texto_para_ia = forzar_texto(texto_sucio)
    if not client: return None, texto_para_ia, False
    
    prompt = f"""Analiza este edicto y genera un JSON con: radicado, juzgado, avaluo (número), postura (número), matricula, direccion, riesgo, score (1-5). 
    Si no encuentras un dato, pon null.
    TEXTO: {texto_para_ia}"""
    
    try:
        response = client.models.generate_content(
            model=model, contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        data = json.loads(response.text)
        if isinstance(data, list) and len(data) > 0: data = data[0]
        return data, texto_para_ia, True
    except Exception as e:
        return {"riesgo": f"Error IA: {str(e)}"}, texto_para_ia, False

def generar_pdf(datos_fichas):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    for f in datos_fichas:
        if not isinstance(f, dict): continue
        pdf.add_page()
        pdf.set_fill_color(31, 73, 125); pdf.set_text_color(255, 255, 255)
        pdf.set_font('Helvetica', 'B', 14)
        rad = forzar_texto(f.get('radicado', 'N/A'))
        pdf.cell(0, 12, f"FICHA: {rad}", fill=True, align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        
        pdf.set_text_color(0, 0, 0); pdf.ln(5); pdf.set_font('Helvetica', '', 10)
        cuerpo = f"Juzgado: {f.get('juzgado')}\nDirección: {f.get('direccion')}\nAvalúo: ${f.get('avaluo', 0):,}\n\nRIESGO: {f.get('riesgo')}"
        pdf.multi_cell(0, 7, cuerpo)
    
    # Guardar en buffer de memoria
    pdf_buffer = BytesIO()
    pdf_output = pdf.output(dest='S').encode('latin-1') # FPDF legacy output trick
    pdf_buffer.write(pdf_output)
    return pdf_buffer

# ==========================================
# LÓGICA PRINCIPAL
# ==========================================

if uploaded_file:
    try:
        # Cargar excel preliminar para leer filtros
        df_raw = pd.read_excel(uploaded_file, header=None)
        # Buscar encabezado automáticamente
        header_idx = df_raw[df_raw.isin(['CÓDIGO']).any(axis=1)].index[0]
        df = pd.read_excel(uploaded_file, skiprows=header_idx + 1)
        df.columns = df_raw.iloc[header_idx].values
        
        # --- FILTROS DINÁMICOS ---
        col1, col2 = st.columns(2)
        with col1:
            deps = st.multiselect("Filtrar Departamento", options=df['Departamento'].unique())
        with col2:
            ciudades = st.multiselect("Filtrar Ciudad", options=df['Ciudad'].unique())
            
        rango = st.slider("Rango de filas a procesar", 0, len(df), (0, 10))
        
        # Aplicar filtros
        df_filtrado = df.copy()
        if deps: df_filtrado = df_filtrado[df_filtrado['Departamento'].isin(deps)]
        if ciudades: df_filtrado = df_filtrado[df_filtrado['Ciudad'].isin(ciudades)]
        df_filtrado = df_filtrado.iloc[rango[0]:rango[1]]
        
        st.info(f"📊 Registros a procesar: {len(df_filtrado)}")
        
        if st.button("🚀 INICIAR EXTRACCIÓN", type="primary"):
            if usar_ia and not api_key:
                st.error("⚠️ Necesitas una API Key para usar IA.")
                st.stop()

            # Configurar Cliente IA
            client_ai = None
            if usar_ia:
                try:
                    client_ai = genai.Client(api_key=api_key)
                except:
                    st.error("Error conectando con Gemini.")

            # --- PROCESO DE SCRAPING ---
            progress_bar = st.progress(0)
            status_text = st.empty()
            resultados_ia = []
            detalles_txt = []
            
            with sync_playwright() as p:
                # IMPORTANTE: headless=True para servidores nube
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                
                # Login (Si es necesario, aunque en el script original no había credenciales explícitas,
                # simulamos navegación a la home primero)
                page.goto("https://rematesjudiciales.com.co/", timeout=60000)
                
                total = len(df_filtrado)
                for i, (idx, row) in enumerate(df_filtrado.iterrows()):
                    codigo = str(row['CÓDIGO']).replace('.0', '')
                    status_text.text(f"Procesando {i+1}/{total}: Código {codigo}")
                    progress_bar.progress((i + 1) / total)
                    
                    try:
                        page.goto(f"https://rematesjudiciales.com.co/?s={codigo}")
                        selector = "div.entry-content, article, .td-post-content"
                        
                        try:
                            page.wait_for_selector(selector, state="visible", timeout=5000)
                            texto_raw = page.locator(selector).first.inner_text()
                        except:
                            texto_raw = "No se pudo extraer contenido."

                        # Procesamiento
                        if usar_ia and client_ai:
                            analisis, limpio, _ = analizar_con_ia(client_ai, "gemini-2.0-flash", texto_raw)
                            resultados_ia.append(analisis)
                            detalles_txt.append(limpio)
                        else:
                            detalles_txt.append(forzar_texto(texto_raw))
                            
                    except Exception as e:
                        st.warning(f"Error en {codigo}: {e}")
                        detalles_txt.append("Error")
                        if usar_ia: resultados_ia.append({})
                
                browser.close()
            
            # --- GENERAR SALIDAS ---
            df_filtrado['Detalles Extraídos'] = detalles_txt
            
            if usar_ia and resultados_ia:
                df_ia = pd.DataFrame.from_records(resultados_ia)
                df_final = pd.concat([df_filtrado.reset_index(drop=True), df_ia.reset_index(drop=True)], axis=1)
                
                # PDF
                pdf_bytes = generar_pdf(resultados_ia)
                st.download_button("📄 Descargar Dossier PDF", pdf_bytes, "Dossier_Remates.pdf", "application/pdf")
            else:
                df_final = df_filtrado

            # Excel en memoria
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, index=False)
            
            st.success("✅ ¡Proceso Terminado!")
            st.download_button("📊 Descargar Excel Consolidado", output.getvalue(), "Remates_Procesados.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    except Exception as e:

        st.error(f"Error leyendo el archivo: {e}")
