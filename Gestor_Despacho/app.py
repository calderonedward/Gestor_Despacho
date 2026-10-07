import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import hashlib

st.set_page_config(page_title="Gestor de Despacho", page_icon="⚖️", layout="wide")

# ==========================================
# 0. SEGURIDAD Y CONEXIÓN ROBUSTA
# ==========================================
db_url = st.secrets["connections"]["supabase"]["url"]
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif db_url.startswith("postgresql://") and "+" not in db_url.split("://")[0]:
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

_engine = create_engine(db_url)

class SimpleSessionContext:
    def __init__(self, engine):
        self.engine = engine
        self.conn = None
        self.trans = None

    def __enter__(self):
        self.conn = self.engine.connect()
        self.trans = self.conn.begin()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.trans.rollback()
        else:
            self.trans.commit()
        self.conn.close()

class SimpleConnectionWrapper:
    def __init__(self, engine):
        self.engine = engine
    
    def query(self, sql, ttl=0):
        with self.engine.connect() as connection:
            return pd.read_sql(text(sql), connection)
            
    @property
    def session(self):
        return SimpleSessionContext(self.engine)

conn = SimpleConnectionWrapper(_engine)

def generar_hash(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def inicializar_bd():
    with conn.session as s:
        s.execute(text('''CREATE TABLE IF NOT EXISTS inventario_expedientes (
            id SERIAL PRIMARY KEY, radicado TEXT, municipio TEXT, etapa TEXT, 
            estante TEXT, fila TEXT, puesto TEXT, ubicacion TEXT, status_activo INTEGER, 
            observaciones TEXT, acusado TEXT, delitos TEXT, usuario_propietario TEXT)'''))
            
        s.execute(text('''CREATE TABLE IF NOT EXISTS usuarios_despacho (
            usuario TEXT PRIMARY KEY, password TEXT, nombre_fiscalia TEXT)'''))
            
        s.execute(text('''CREATE TABLE IF NOT EXISTS mapas_personales (
            id SERIAL PRIMARY KEY, usuario TEXT, municipio TEXT, estante INTEGER, 
            fila_inicio INTEGER, fila_fin INTEGER, puestos_max INTEGER, ubic_max INTEGER)'''))
    
    for col in ['fecha_imputacion', 'detenido', 'fecha_detencion']:
        try:
            with conn.session as s:
                s.execute(text(f'ALTER TABLE inventario_expedientes ADD COLUMN {col} TEXT'))
        except:
            pass 
            
    try:
        with conn.session as s:
            s.execute(text('ALTER TABLE inventario_expedientes ADD COLUMN maniobras_dilatorias INTEGER DEFAULT 0'))
    except:
        pass

    for col in ['puestos_max', 'ubic_max']:
        try:
            with conn.session as s:
                s.execute(text(f'ALTER TABLE mapas_personales ADD COLUMN {col} INTEGER'))
        except:
            pass
    
    with conn.session as s:
        pwd_hash = hashlib.sha256("12345".encode()).hexdigest()
        s.execute(text("""
            INSERT INTO usuarios_despacho (usuario, password, nombre_fiscalia) 
            VALUES ('admin', :pwd, 'Fiscalía 01 Seccional')
            ON CONFLICT (usuario) DO UPDATE SET password = :pwd
        """), {"pwd": pwd_hash})

inicializar_bd()

def obtener_mapa(usr):
    df = conn.query(f"SELECT municipio, estante, fila_inicio, fila_fin, puestos_max, ubic_max FROM mapas_personales WHERE usuario = '{usr}'", ttl=0)
    if df.empty:
        with conn.session as s:
            s.execute(text('''INSERT INTO mapas_personales (usuario, municipio, estante, fila_inicio, fila_fin, puestos_max, ubic_max) VALUES 
                (:u, 'CERRITO', 1, 1, 2, 3, 20), (:u, 'CANDELARIA', 1, 3, 4, 3, 20), (:u, 'PALMIRA', 1, 5, 6, 3, 20), 
                (:u, 'FLORIDA', 2, 1, 2, 3, 20), (:u, 'PRADERA', 2, 3, 4, 3, 20), (:u, 'SENTENCIAS', 2, 5, 6, 3, 20)'''), {"u": usr})
        df = conn.query(f"SELECT municipio, estante, fila_inicio, fila_fin, puestos_max, ubic_max FROM mapas_personales WHERE usuario = '{usr}'", ttl=0)
    return df

def asignar_ubicacion_fisica(municipio, etapa, usr):
    mapa_df = obtener_mapa(usr)
    bloque = "SENTENCIAS" if etapa in ["Sentencia", "Preclusión", "Archivo"] else municipio.upper()
    regla = mapa_df[mapa_df['municipio'] == bloque]
    if regla.empty: return "Pendiente", "Pendiente", "Pendiente", "Pendiente"
    
    est = int(regla['estante'].iloc[0])
    filas = range(int(regla['fila_inicio'].iloc[0]), int(regla['fila_fin'].iloc[0]) + 1)
    max_puestos = int(regla['puestos_max'].iloc[0]) if 'puestos_max' in regla.columns and pd.notna(regla['puestos_max'].iloc[0]) else 3
    max_ubic = int(regla['ubic_max'].iloc[0]) if 'ubic_max' in regla.columns and pd.notna(regla['ubic_max'].iloc[0]) else 20
    
    slots = [(f"Fila {f}", f"Puesto {p}", str(u)) for f in filas for p in range(1, max_puestos + 1) for u in range(1, max_ubic + 1)]
    
    query = f"SELECT fila, puesto, ubicacion FROM inventario_expedientes WHERE estante = 'Estante {est}' AND usuario_propietario = '{usr}'"
    df_ocupados = conn.query(query, ttl=0)
    ocupados = set((r['fila'], r['puesto'], str(r['ubicacion'])) for _, r in df_ocupados.iterrows())
    
    for slot in slots:
        if slot not in ocupados:
            return f"Estante {est}", slot[0], slot[1], slot[2]
    return f"Estante {est}", "LLENO", "LLENO", "LLENO"

# ==========================================
# 2. SISTEMA DE LOGIN Y MENÚ
# ==========================================
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False
    st.session_state['usuario_actual'] = None
    st.session_state['fiscalia_actual'] = None

if not st.session_state['autenticado']:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("🔐 Acceso al Sistema")
        with st.form("login_form"):
            usuario = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            submit = st.form_submit_button("Ingresar", use_container_width=True)
            
            if submit:
                pwd_hash = generar_hash(password)
                df_user = conn.query(f"SELECT * FROM usuarios_despacho WHERE usuario='{usuario}' AND password='{pwd_hash}'", ttl=0)
                if not df_user.empty:
                    st.session_state['autenticado'] = True
                    st.session_state['usuario_actual'] = df_user.iloc[0]['usuario']
                    st.session_state['fiscalia_actual'] = df_user.iloc[0]['nombre_fiscalia']
                    st.rerun()
                else:
                    st.error("Credenciales incorrectas.")
else:
    usr = st.session_state['usuario_actual']
    
    st.sidebar.title(f"⚖️ {st.session_state['fiscalia_actual']}")
    st.sidebar.markdown(f"👤 **Usuario:** {usr}")
    
    if st.sidebar.button("🚪 Cerrar Sesión"):
        st.session_state['autenticado'] = False
        st.rerun()
        
    st.sidebar.divider()
    menu = [
        "⚙️ Configuración", 
        "🔎 Consulta Rápida", 
        "📝 Ingresar Nuevo Expediente", 
        "🔄 Actualizar / Cerrar Caso", 
        "📊 Ver Inventario", 
        "⏱️ Control de Términos",
        "📥 Carga Masiva (Excel)", 
        "🗺️ Configurar Mi Mapa Físico"
    ]
    eleccion = st.sidebar.radio("Navegación:", menu)

    # ==========================================
    # MÓDULOS DE NAVEGACIÓN
    # ==========================================
    if eleccion == "⚙️ Configuración":
        st.header("⚙️ Configuración del Despacho")
        nuevo_nombre = st.text_input("Nombre de la Fiscalía asignada:", value=st.session_state['fiscalia_actual'])
        if st.button("Actualizar Perfil"):
            with conn.session as s:
                s.execute(text("UPDATE usuarios_despacho SET nombre_fiscalia = :val WHERE usuario = :usr"), {"val": nuevo_nombre, "usr": usr})
            st.session_state['fiscalia_actual'] = nuevo_nombre
            st.success("Perfil actualizado.")
            st.rerun()
        st.write("---")
        st.write("### 🔑 Cambiar mi contraseña")
        with st.form("cambiar_pwd"):
            pwd_ant = st.text_input("Contraseña actual", type="password")
            pwd_nueva = st.text_input("Nueva contraseña", type="password")
            if st.form_submit_button("Actualizar mi contraseña"):
                hash_ant = generar_hash(pwd_ant)
                df_check = conn.query(f"SELECT * FROM usuarios_despacho WHERE usuario='{usr}' AND password='{hash_ant}'", ttl=0)
                if not df_check.empty:
                    with conn.session as s:
                        s.execute(text("UPDATE usuarios_despacho SET password = :p WHERE usuario = :u"), {"p": generar_hash(pwd_nueva), "u": usr})
                    st.success("Contraseña actualizada correctamente.")
                else:
                    st.error("La contraseña actual es incorrecta.")

    elif eleccion == "🔎 Consulta Rápida":
        st.header("🔎 Consulta Rápida")
        termino = st.text_input("Acusado o Radicado:")
        if st.button("Buscar") and len(termino) >= 3:
            query = f"SELECT * FROM inventario_expedientes WHERE usuario_propietario = '{usr}' AND (radicado ILIKE '%{termino}%' OR acusado ILIKE '%{termino}%')"
            df_resultado = conn.query(query, ttl=0)
            st.dataframe(df_resultado)
            if not df_resultado.empty:
                st.write("---")
                st.write("### 📝 Editar Observaciones")
                with st.form("form_editar_obs"):
                    radicado_actual = df_resultado.iloc[0]['radicado']
                    obs_actual = df_resultado.iloc[0]['observaciones']
                    if pd.isna(obs_actual) or obs_actual == "None" or obs_actual is None: obs_actual = ""
                    nueva_obs = st.text_area(f"Añadir o modificar observaciones para {radicado_actual}:", value=obs_actual)
                    if st.form_submit_button("Guardar Observación"):
                        with conn.session as s:
                            s.execute(text("UPDATE inventario_expedientes SET observaciones = :obs WHERE radicado = :rad AND usuario_propietario = :usr"), {"obs": nueva_obs, "rad": radicado_actual, "usr": usr})
                        st.success("¡Observaciones actualizadas!")

    elif eleccion == "📝 Ingresar Nuevo Expediente":
        st.header("📝 Ingresar Nuevo Expediente")
        with st.form("f1"):
            r = st.text_input("Radicado*")
            a = st.text_input("Acusado*")
            d = st.text_input("Delito*")
            m = st.selectbox("Municipio", obtener_mapa(usr)['municipio'].tolist())
            e = st.selectbox("Etapa", ["Indagación", "Imputación", "Acusación", "Sentencia", "Preclusión"])
            st.write("---")
            st.write("### 📅 Fechas y Detención")
            col1, col2, col3 = st.columns(3)
            with col1: f_imp = st.date_input("Fecha Imputación (Si aplica)")
            with col2:
                es_det = st.selectbox("¿Detenido?", ["No", "Sí"])
                f_det = st.date_input("Fecha Detención")
            with col3: maniobras = st.number_input("Maniobras Dilatorias (Días)", min_value=0, value=0, step=1)
            
            if st.form_submit_button("Guardar Expediente"):
                est, fil, pto, ubi = asignar_ubicacion_fisica(m, e, usr)
                with conn.session as s:
                    fecha_str = str(f_imp)
                    fecha_det_str = str(f_det) if es_det == "Sí" else ""
                    s.execute(text("""INSERT INTO inventario_expedientes 
                                        (radicado, acusado, delitos, municipio, etapa, estante, fila, puesto, ubicacion, status_activo, usuario_propietario, fecha_imputacion, detenido, fecha_detencion, maniobras_dilatorias) 
                                        VALUES (:r, :a, :d, :m, :e, :est, :fil, :pto, :ubi, 1, :usr, :f_imp, :det, :f_det, :man)"""), 
                                {"r":r, "a":a, "d":d, "m":m, "e":e, "est":est, "fil":fil, "pto":pto, "ubi":ubi, "usr":usr, "f_imp":fecha_str, "det":es_det, "f_det":fecha_det_str, "man":maniobras})
                st.success(f"Guardado en {est}, {fil}, {pto}, Ubi {ubi}")

    elif eleccion == "🔄 Actualizar / Cerrar Caso":
        st.header("🔄 Actualizar / Cerrar Caso")
        msg_container = st.container()
        with st.form("f2"):
            r = st.text_input("Radicado del caso a actualizar:")
            n = st.selectbox("Nueva Etapa", ["Indagación", "Imputación", "Acusación", "Sentencia", "Preclusión", "Archivo"])
            st.write("---")
            col1, col2, col3 = st.columns(3)
            with col1: f_imp = st.date_input("Fecha de Imputación:")
            with col2:
                es_det = st.selectbox("¿Detenido?", ["No", "Sí"])
                f_det = st.date_input("Fecha Detención:")
            with col3: maniobras = st.number_input("Maniobras Dilatorias (Días)", min_value=0, value=0, step=1)
            obs = st.text_area("Observaciones:")
            submit_btn = st.form_submit_button("Actualizar Expediente")
            
        if submit_btn:
            df_actual = conn.query(f"SELECT * FROM inventario_expedientes WHERE radicado='{r}' AND usuario_propietario='{usr}'", ttl=0)
            if not df_actual.empty:
                df_actual = df_actual.fillna("")
                st.session_state['backup_caso'] = df_actual.iloc[0].to_dict()
                with conn.session as s:
                    fecha_str = str(f_imp)
                    fecha_det_str = str(f_det) if es_det == "Sí" else ""
                    if n in ["Sentencia", "Preclusión", "Archivo"]:
                        e, f, p, u = asignar_ubicacion_fisica("SENTENCIAS", n, usr)
                        s.execute(text("""UPDATE inventario_expedientes SET etapa=:n, status_activo=0, estante=:e, fila=:f, puesto=:p, ubicacion=:u, observaciones=:obs, fecha_imputacion=:f_imp, detenido=:det, fecha_detencion=:f_det, maniobras_dilatorias=:man WHERE radicado=:r AND usuario_propietario=:usr"""), {"n":n, "e":e, "f":f, "p":p, "u":u, "obs":obs, "f_imp":fecha_str, "det":es_det, "f_det":fecha_det_str, "man":maniobras, "r":r, "usr":usr})
                        estado_str = "🔴 Inactivo"
                    else: 
                        s.execute(text("""UPDATE inventario_expedientes SET etapa=:n, status_activo=1, observaciones=:obs, fecha_imputacion=:f_imp, detenido=:det, fecha_detencion=:f_det, maniobras_dilatorias=:man WHERE radicado=:r AND usuario_propietario=:usr"""), {"n":n, "obs":obs, "f_imp":fecha_str, "det":es_det, "f_det":fecha_det_str, "man":maniobras, "r":r, "usr":usr})
                        estado_str = "🟢 Activo"
                msg_container.success(f"Caso actualizado a '{n}'. Estado: {estado_str}")
            else:
                msg_container.error(f"No se encontró el radicado {r}.")

        if 'backup_caso' in st.session_state and st.session_state['backup_caso'] is not None:
            backup = st.session_state['backup_caso']
            st.warning(f"⚠️ ¿Digitaste mal? El último modificado fue: **{backup['radicado']}**.")
            if st.button("↩️ Deshacer error"):
                det_bak = backup.get('detenido', '')
                f_det_bak = backup.get('fecha_detencion', '')
                man_bak = backup.get('maniobras_dilatorias', 0)
                if pd.isna(man_bak) or man_bak == "": man_bak = 0
                with conn.session as s:
                    s.execute(text("""UPDATE inventario_expedientes SET etapa=:eta, status_activo=:act, estante=:est, fila=:fil, puesto=:pue, ubicacion=:ubi, observaciones=:obs, fecha_imputacion=:f_imp, detenido=:det, fecha_detencion=:f_det, maniobras_dilatorias=:man WHERE radicado=:rad AND usuario_propietario=:usr"""), {"eta": backup['etapa'], "act": backup['status_activo'], "est": backup['estante'], "fil": backup['fila'], "pue": backup['puesto'], "ubi": backup['ubicacion'], "obs": backup['observaciones'], "f_imp": backup['fecha_imputacion'], "det": det_bak, "f_det": f_det_bak, "man": man_bak, "rad": backup['radicado'], "usr": usr})
                st.session_state['backup_caso'] = None
                st.success("¡Acción deshecha!")

        st.write("---")
        with st.form("form_eliminar"):
            rad_eliminar = st.text_input("Ingresa Radicado exacto a eliminar definitivamente:")
            confirmar = st.checkbox("Estoy seguro de borrarlo.")
            if st.form_submit_button("🚨 Eliminar"):
                if confirmar and len(rad_eliminar) >= 3:
                    df_check = conn.query(f"SELECT * FROM inventario_expedientes WHERE radicado='{rad_eliminar}' AND usuario_propietario='{usr}'", ttl=0)
                    if not df_check.empty:
                        with conn.session as s:
                            s.execute(text("DELETE FROM inventario_expedientes WHERE radicado=:r AND usuario_propietario=:u"), {"r": rad_eliminar, "u": usr})
                        st.success("¡Borrado!")

    elif eleccion == "📊 Ver Inventario":
        st.header("📊 Inventario de Expedientes")
        df = conn.query(f"SELECT * FROM inventario_expedientes WHERE usuario_propietario = '{usr}'", ttl=0)
        
        if not df.empty:
            if 'usuario_propietario' in df.columns: df = df.drop(columns=['usuario_propietario'])
            df_activos = df[df['status_activo'] == 1]
            df_inactivos = df[df['status_activo'] == 0]
            total_detenidos = len(df_activos[df_activos['detenido'] == 'Sí']) if 'detenido' in df.columns else 0
            
            col1, col2, col3 = st.columns(3)
            col1.metric("🟢 Casos Activos", len(df_activos))
            col2.metric("🔴 Casos Inactivos", len(df_inactivos))
            col3.metric("🔒 Detenidos (Activos)", total_detenidos)
            st.write("---")
            
            df_validos = df[df['radicado'].astype(str).str.strip() != ""]
            duplicados = df_validos[df_validos.duplicated(subset=['radicado'], keep=False)]
            
            tab1, tab2, tab3, tab4 = st.tabs(["🟢 Activos", "🔴 Inactivos", "📋 Todos", "⚠️ Duplicados"])
            with tab1: st.dataframe(df_activos, use_container_width=True)
            with tab2: st.dataframe(df_inactivos, use_container_width=True)
            with tab3: st.dataframe(df, use_container_width=True)
            with tab4:
                if not duplicados.empty:
                    st.dataframe(duplicados.sort_values(by='radicado'), use_container_width=True)
                    if st.button("🧹 Eliminar duplicados"):
                        with conn.session as s:
                            s.execute(text("""DELETE FROM inventario_expedientes a USING (SELECT MAX(id) as max_id, radicado FROM inventario_expedientes WHERE usuario_propietario = :usr AND radicado IS NOT NULL AND radicado != '' GROUP BY radicado HAVING COUNT(*) > 1) b WHERE a.radicado = b.radicado AND a.id <> b.max_id AND a.usuario_propietario = :usr"""), {"usr": usr})
                        st.rerun()
                else: st.success("No hay duplicados.")
            
            st.write("---")
            st.write("### ✏️ Edición Rápida y Descargas")
            df_editado = st.data_editor(df, num_rows="dynamic", key="editor_inventario", use_container_width=True, hide_index=True)
            
            col_save, col_auto = st.columns(2)
            with col_save:
                if st.button("💾 Guardar Cambios", use_container_width=True):
                    with _engine.connect() as eng_conn:
                        with eng_conn.begin():
                            eng_conn.execute(text(f"DELETE FROM inventario_expedientes WHERE usuario_propietario = '{usr}'"))
                            df_editado['usuario_propietario'] = usr
                            df_editado.to_sql('inventario_expedientes', eng_conn, if_exists='append', index=False)
                    st.rerun()
            with col_auto:
                if st.button("✨ Auto-Asignar Ubicaciones", use_container_width=True):
                    casos_sin = conn.query(f"SELECT id, municipio, etapa FROM inventario_expedientes WHERE usuario_propietario = '{usr}' AND (estante IS NULL OR estante='' OR estante='Pendiente')", ttl=0)
                    if not casos_sin.empty:
                        with conn.session as s:
                            for _, c in casos_sin.iterrows():
                                e, f, p, u = asignar_ubicacion_fisica(c['municipio'], c['etapa'], usr)
                                s.execute(text("UPDATE inventario_expedientes SET estante=:e, fila=:f, puesto=:p, ubicacion=:u WHERE id=:id"), {"e":e, "f":f, "p":p, "u":u, "id":c['id']})
                        st.rerun()

            st.write("#### 📥 Exportación")
            filtro_descarga = st.radio("Filtro:", ["🟢 Casos Activos", "🔴 Casos Inactivos (Cerrados)", "📋 Todos los Casos"], horizontal=True)
            df_descarga = df_editado.copy()
            if "Activos" in filtro_descarga: df_descarga = df_descarga[df_descarga['status_activo'] == 1]; nom = f"Reporte_Activos_{usr}.xlsx"
            elif "Inactivos" in filtro_descarga: df_descarga = df_descarga[df_descarga['status_activo'] == 0]; nom = f"Reporte_Inactivos_{usr}.xlsx"
            else: nom = f"Reporte_Completo_{usr}.xlsx"
            from io import BytesIO
            output = BytesIO()
            df_descarga.to_excel(output, index=False)
            st.download_button(label=f"📥 Descargar", data=output.getvalue(), file_name=nom, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.warning("No tienes expedientes.")

    elif eleccion == "⏱️ Control de Términos":
        st.header("⏱️ Control de Términos (Personas Detenidas)")
        
        st.write("### 🔍 Búsqueda Individual y Cálculo por Radicado")
        rad_consulta = st.text_input("Ingrese el número de radicado para calcular días:")
        if rad_consulta:
            df_caso = conn.query(f"SELECT * FROM inventario_expedientes WHERE radicado = '{rad_consulta}' AND usuario_propietario = '{usr}' AND detenido = 'Sí'", ttl=0)
            if not df_caso.empty:
                caso = df_caso.iloc[0]
                fecha_det_str = caso.get('fecha_detencion', '')
                maniobras = caso.get('maniobras_dilatorias', 0)
                if pd.isna(maniobras) or maniobras == "": maniobras = 0
                maniobras = int(maniobras)

                if fecha_det_str and str(fecha_det_str).strip() != "":
                    try:
                        f_det = pd.to_datetime(fecha_det_str)
                        hoy = pd.Timestamp.now().normalize()
                        dias_totales = (hoy - f_det).days
                        dias_efectivos = dias_totales - maniobras

                        st.success(f"**Acusado:** {caso['acusado']} | **Delito:** {caso['delitos']}")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("📅 Días Totales Detenido", dias_totales)
                        c2.metric("🛑 Maniobras Dilatorias", f"- {maniobras}")
                        c3.metric("⏳ Días EFECTIVOS", dias_efectivos)
                    except:
                        st.error("La fecha registrada no es válida.")
                else:
                    st.warning("Este radicado está marcado con detenido pero no tiene fecha.")
            else:
                st.warning("No se encontró un caso ACTIVO con persona detenida bajo ese radicado.")

        st.write("---")
        st.write("### 📋 Resumen Global de Detenidos")
        df_det = conn.query(f"SELECT radicado, acusado, delitos, etapa, fecha_detencion, maniobras_dilatorias, observaciones FROM inventario_expedientes WHERE usuario_propietario = '{usr}' AND detenido = 'Sí' AND status_activo = 1", ttl=0)
        
        if not df_det.empty:
            df_det = df_det[df_det['fecha_detencion'].astype(str).str.strip() != ""]
            if not df_det.empty:
                df_det['fecha_detencion_dt'] = pd.to_datetime(df_det['fecha_detencion'], errors='coerce')
                df_det = df_det.dropna(subset=['fecha_detencion_dt'])
                
                if not df_det.empty:
                    hoy = pd.Timestamp.now().normalize()
                    df_det['Días Totales'] = (hoy - df_det['fecha_detencion_dt']).dt.days
                    df_det['maniobras_dilatorias'] = pd.to_numeric(df_det['maniobras_dilatorias'], errors='coerce').fillna(0).astype(int)
                    df_det['Días Efectivos'] = df_det['Días Totales'] - df_det['maniobras_dilatorias']
                    
                    df_det = df_det.sort_values(by='Días Efectivos', ascending=False)
                    df_det['Fecha Captura'] = df_det['fecha_detencion_dt'].dt.strftime('%Y-%m-%d')
                    
                    cols_mostrar = ['radicado', 'acusado', 'etapa', 'Fecha Captura', 'Días Totales', 'maniobras_dilatorias', 'Días Efectivos']
                    st.dataframe(df_det[cols_mostrar].rename(columns={'maniobras_dilatorias': 'Maniobras (Días)'}), use_container_width=True)
                else: st.info("Fechas inválidas detectadas.")
            else: st.info("Ningún detenido tiene fecha registrada.")
        else:
            st.info("No tienes detenidos activos.")

    elif eleccion == "📥 Carga Masiva (Excel)":
        st.header("📥 Carga Masiva de Expedientes")
        archivo = st.file_uploader("Sube tu archivo Excel", type=["xlsx"])
        if archivo and st.button("Procesar y Cargar BD"):
            df = pd.read_excel(archivo, dtype=str).fillna("").replace(r'\.0$', '', regex=True)
            df['usuario_propietario'] = usr
            mapa_df = obtener_mapa(usr)
            df_ocupados = conn.query(f"SELECT estante, fila, puesto, ubicacion FROM inventario_expedientes WHERE usuario_propietario = '{usr}'", ttl=0)
            
            ocupados = {}
            for est in mapa_df['estante'].unique():
                est_str = f"Estante {int(est)}"
                subset = df_ocupados[df_ocupados['estante'] == est_str]
                ocupados[est_str] = set((str(r['fila']), str(r['puesto']), str(r['ubicacion'])) for _, r in subset.iterrows())

            for index, row in df.iterrows():
                mun = str(row.get('municipio', '')).upper()
                eta = str(row.get('etapa', ''))
                bloque = "SENTENCIAS" if eta in ["Sentencia", "Preclusión", "Archivo"] else mun
                regla = mapa_df[mapa_df['municipio'] == bloque]
                
                if regla.empty:
                    estante, fila, puesto, ubicacion = "Pendiente", "Pendiente", "Pendiente", "Pendiente"
                else:
                    est = f"Estante {int(regla['estante'].iloc[0])}"
                    filas = range(int(regla['fila_inicio'].iloc[0]), int(regla['fila_fin'].iloc[0]) + 1)
                    max_p = int(regla['puestos_max'].iloc[0]) if 'puestos_max' in regla.columns and pd.notna(regla['puestos_max'].iloc[0]) else 3
                    max_u = int(regla['ubic_max'].iloc[0]) if 'ubic_max' in regla.columns and pd.notna(regla['ubic_max'].iloc[0]) else 20
                    slots = [(f"Fila {f}", f"Puesto {p}", str(u)) for f in filas for p in range(1, max_p + 1) for u in range(1, max_u + 1)]
                    
                    if est not in ocupados: ocupados[est] = set()
                    slot_enc = next((s for s in slots if s not in ocupados[est]), None)
                    
                    if slot_enc:
                        estante, fila, puesto, ubicacion = est, slot_enc[0], slot_enc[1], slot_enc[2]
                        ocupados[est].add(slot_enc)
                    else:
                        estante, fila, puesto, ubicacion = est, "LLENO", "LLENO", "LLENO"

                df.loc[index, 'estante'] = str(estante)
                df.loc[index, 'fila'] = str(fila)
                df.loc[index, 'puesto'] = str(puesto)
                df.loc[index, 'ubicacion'] = str(ubicacion)
                df.loc[index, 'status_activo'] = 1
            
            cols = ['radicado', 'municipio', 'etapa', 'estante', 'fila', 'puesto', 'ubicacion', 'status_activo', 'observaciones', 'acusado', 'delitos', 'usuario_propietario', 'fecha_imputacion', 'detenido', 'fecha_detencion', 'maniobras_dilatorias']
            df_final = df[[c for c in cols if c in df.columns]]
            with _engine.connect() as eng_conn:
                df_final.to_sql('inventario_expedientes', eng_conn, if_exists='append', index=False)
            st.success("¡Carga masiva completada! Revisa en 'Ver Inventario'.")

    elif eleccion == "🗺️ Configurar Mi Mapa Físico":
        st.header("⚙️ Configuración de Espacios")
        mapa_actual = obtener_mapa(usr)
        if not mapa_actual.empty:
            mapa_editado = st.data_editor(mapa_actual, num_rows="dynamic", key="ed_mapa", use_container_width=True, hide_index=True)
            if st.button("💾 Guardar Mapa"):
                try:
                    with _engine.connect() as eng_conn:
                        with eng_conn.begin():
                            eng_conn.execute(text(f"DELETE FROM mapas_personales WHERE usuario = '{usr}'"))
                            mapa_editado['usuario'] = usr
                            mapa_editado.to_sql('mapas_personales', eng_conn, if_exists='append', index=False)
                    st.success("¡Configuración guardada!")
                    st.rerun()
                except Exception as e: st.error(f"Error: {e}")
        else: st.warning("No tienes registros.")
