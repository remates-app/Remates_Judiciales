import streamlit as st
import pandas as pd
import json
import time
import random
import subprocess
from io import BytesIO
from google import genai
from playwright.sync_api import sync_playwright
from fpdf import FPDF, XPos, YPos

# ==========================================
# 0. INSTALACIÓN AUTOMÁTICA DE NAVEGADOR
# ==========================================
try:
    # Intentamos verificar si el navegador está instalado
    subprocess.run(["playwright", "install", "chromium"], check=True)
except Exception as e:
    st.error(f"Error instalando navegador: {e}")

# ==========================================
# 1. CONFIGURACIÓN DE LA PÁGINA
# ==========================================
st.set_page_config(page_title="Extractor Remates Judiciales", layout="wide", page_icon="⚖️")

st.title("⚖️ Extractor de Remates Judiciales - Cloud Pro")
st.markdown("""
Esta versión incluye simulación de comportamiento humano (Scroll, Tiempos de espera) 
para evitar bloqueos de seguridad en la nube.
""")

# ==========================================
# 2. BARRA LATERAL
# ==========================================
with st.sidebar:
    st.header("⚙️ Configuración")
    
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        api_key = st.text_input("Ingresa tu Google API Key:", type="password")
    
    usar_ia = st.toggle("Activar Inteligencia Artificial", value=True)
    st.divider()
    uploaded_file = st.file_uploader("📂 Cargar Listado (Excel)", type=["xlsx"])

# ==========================================
# 3. FUNCIONES AUXILIARES
# ==========================================
def forzar_texto(entrada):
    """Limpia el texto y elimina ruido."""
    if entrada is None: return ""
    texto = str(entrada)
    remplazos = {'\u2013': '-', '\u2014': '-', '\u201c': '"', '\u201d': '"', '\u2018': "'", '\u2019': "'"}
    for original, nuevo in remplazos.items():
        texto = texto.replace(original, nuevo)
    
    frases_ruido = [
        "Recuerda tener en cuenta la fecha de remate",
        "Al utilizar esta información el usuario se hace responsable"
    ]
    for frase in frases_ruido:
        texto = texto.replace(frase, "")
    return texto.strip()

def analizar_con_ia(client, model, texto_sucio):
    """Conecta con Gemini para estructurar la data."""
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
    """Genera el PDF compatible con FPDF2."""
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
    
    pdf_buffer = BytesIO()
    pdf_output = pdf.output(dest='S').encode('latin-1')
    pdf_buffer.write(pdf_output)
    return pdf_buffer

# ==========================================
# 4. LÓGICA PRINCIPAL (SCRAPING ROBUSTO)
# ==========================================

if uploaded_file:
    try:
        # Cargar excel preliminar
        df_raw = pd.read_excel(uploaded_file, header=None)
        header_idx = df_raw[df_raw.isin(['CÓDIGO']).any(axis=1)].index[0]
        df = pd.read_excel(uploaded_file, skiprows=header_idx + 1)
        df.columns = df_raw.iloc[header_idx].values
        
        # Filtros UI
        col1, col2 = st.columns(2)
        with col1:
            deps = st.multiselect("Filtrar Departamento", options=df['Departamento'].unique())
        with col2:
            ciudades = st.multiselect("Filtrar Ciudad", options=df['Ciudad'].unique())
            
        rango = st.slider("Rango de filas a procesar", 0, len(df), (0, 5)) # Default bajo para pruebas
        
        # Aplicar filtros
        df_filtrado = df.copy()
        if deps: df_filtrado = df_filtrado[df_filtrado['Departamento'].isin(deps)]
        if ciudades: df_filtrado = df_filtrado[df_filtrado['Ciudad'].isin(ciudades)]
        df_filtrado = df_filtrado.iloc[rango[0]:rango[1]]
        
        st.info(f"📊 Registros a procesar: {len(df_filtrado)}")
        
        if st.button("🚀 INICIAR EXTRACCIÓN PRO", type="primary"):
            
            # Setup IA
            client_ai = None
            if usar_ia and api_key:
                try:
                    client_ai = genai.Client(api_key=api_key)
                except:
                    st.error("Error conectando con Gemini.")

            # UI Progress
            progress_bar = st.progress(0)
            status_text = st.empty()
            resultados_ia = []
            detalles_txt = []
            
            # --- INICIO PLAYWRIGHT ---
            with sync_playwright() as p:
                # CONFIGURACIÓN "STEALTH" (NUEVO)
                # Simulamos ser un navegador real de Windows
                browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080}
                )
                page = context.new_page()
                
                # Warm-up: Visitar home para cookies
                status_text.text("Iniciando sesión segura...")
                try:
                    page.goto("https://rematesjudiciales.com.co/", timeout=60000)
                    time.sleep(2)
                except:
                    pass # Si falla el home, intentamos directo los códigos

                total = len(df_filtrado)
                
                for i, (idx, row) in enumerate(df_filtrado.iterrows()):
                    codigo = str(row['CÓDIGO']).replace('.0', '')
                    status_text.text(f"🔍 Procesando {i+1}/{total}: Código {codigo}")
                    progress_bar.progress((i + 1) / total)
                    
                    try:
                        # Navegación Directa
                        page.goto(f"https://rematesjudiciales.com.co/?s={codigo}", wait_until="domcontentloaded")
                        
                        # --- LA MAGIA: SCROLL HUMANO (RECUPERADO) ---
                        # Esperamos selector visible
                        selector = "div.entry-content, article, .td-post-content"
                        page.wait_for_selector(selector, state="visible", timeout=15000)
                        
                        # Scroll abajo y arriba para activar triggers de carga
                        page.mouse.wheel(0, 500)
                        time.sleep(1) # Espera humana
                        page.mouse.wheel(0, -200)
                        time.sleep(0.5)
                        
                        # Extracción
                        texto_raw = page.locator(selector).first.inner_text()
                        
                        # IA o Texto
                        if usar_ia and client_ai:
                            analisis, limpio, _ = analizar_con_ia(client_ai, "gemini-2.0-flash", texto_raw)
                            resultados_ia.append(analisis)
                            detalles_txt.append(limpio)
                        else:
                            detalles_txt.append(forzar_texto(texto_raw))
                        
                        # Pausa entre peticiones para no saturar
                        time.sleep(random.uniform(1.0, 2.0))
                            
                    except Exception as e:
                        # Si falla, intentamos capturar al menos el error sin romper el loop
                        print(f"Error en {codigo}: {e}")
                        detalles_txt.append(f"No accesible / Error: {str(e)}")
                        if usar_ia: resultados_ia.append({})
                
                browser.close()
            
            # --- EXPORTACIÓN ---
            df_filtrado['Detalles Extraídos'] = detalles_txt
            
            if usar_ia and resultados_ia:
                df_ia = pd.DataFrame.from_records(resultados_ia)
                df_final = pd.concat([df_filtrado.reset_index(drop=True), df_ia.reset_index(drop=True)], axis=1)
                
                pdf_bytes = generar_pdf(resultados_ia)
                st.download_button("📄 Descargar Dossier PDF", pdf_bytes, "Dossier_Remates.pdf", "application/pdf")
            else:
                df_final = df_filtrado

            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, index=False)
            
            st.success("✅ ¡Extracción Completada!")
            st.download_button("📊 Descargar Excel", output.getvalue(), "Remates_Final.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    except Exception as e:
        st.error(f"Error crítico en la app: {e}")
