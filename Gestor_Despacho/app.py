import streamlit as st
import pandas as pd
from sqlalchemy import text
import hashlib

st.set_page_config(page_title="Gestor de Despacho", page_icon="⚖️", layout="wide")

# ==========================================
# 0. SEGURIDAD Y CONEXIÓN
# ==========================================
conn = st.connection("supabase", type="sql")

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
        
        # NUEVA TABLA: Mapas independientes por usuario
        s.execute(text('''CREATE TABLE IF NOT EXISTS mapas_personales (
            id SERIAL PRIMARY KEY, usuario TEXT, municipio TEXT, estante INTEGER, 
            fila_inicio INTEGER, fila_fin INTEGER)'''))
        
        res_users = s.execute(text("SELECT COUNT(*) FROM usuarios_despacho")).scalar()
        if res_users == 0:
            pwd_hash = generar_hash("Admin123")
            s.execute(text("INSERT INTO usuarios_despacho (usuario, password, nombre_fiscalia) VALUES ('admin', :pwd, 'Fiscalía 01 Seccional')"), {"pwd": pwd_hash})
        
        s.commit()

inicializar_bd()

def obtener_mapa(usr):
    # Consulta el mapa exclusivo del usuario actual
    df = conn.query(f"SELECT municipio, estante, fila_inicio, fila_fin FROM mapas_personales WHERE usuario = '{usr}'", ttl=0)
    if df.empty:
        # Si no tiene mapa (es un colega nuevo), le creamos uno por defecto
        with conn.session as s:
            s.execute(text('''INSERT INTO mapas_personales (usuario, municipio, estante, fila_inicio, fila_fin) VALUES 
            (:u, 'CERRITO', 1, 1, 2), (:u, 'CANDELARIA', 1, 3, 4), (:u, 'PALMIRA', 1, 5, 6),
            (:u, 'FLORIDA', 2, 1, 2), (:u, 'PRADERA', 2, 3, 4), (:u, 'SENTENCIAS', 2, 5, 6)'''), {"u": usr})
            s.commit()
        df = conn.query(f"SELECT municipio, estante, fila_inicio, fila_fin FROM mapas_personales WHERE usuario = '{usr}'", ttl=0)
    return df

# ==========================================
# 1. LÓGICA DE ASIGNACIÓN FÍSICA INDEPENDIENTE
# ==========================================
def asignar_ubicacion_fisica(municipio, etapa, usr):
    mapa_df = obtener_mapa(usr)
    bloque = "SENTENCIAS" if etapa in ["Sentencia", "Preclusión", "Archivo"] else municipio.upper()
    regla = mapa_df[mapa_df['municipio'] == bloque]
    if regla.empty: return "Pendiente", "Pendiente", "Pendiente", "Pendiente"
    
    est = int(regla['estante'].iloc[0])
    filas = range(int(regla['fila_inicio'].iloc[0]), int(regla['fila_fin'].iloc[0]) + 1)
    slots = [(f"Fila {f}", f"Puesto {p}", str(u)) for f in filas for p in range(1, 4) for u in range(1, 21)]
    
    query = f"SELECT fila, puesto, ubicacion FROM inventario_expedientes WHERE estante = 'Estante {est}' AND usuario_propietario = '{usr}'"
    df_ocupados = conn.query(query, ttl=0)
    ocupados = set((r['fila'], r['puesto'], str(r['ubicacion'])) for _, r in df_ocupados.iterrows())
    
    for slot in slots:
        if slot not in ocupados:
            return f"Estante {est}", slot[0], slot[1], slot[2]
    return f"Estante {est}", "LLENO", "LLENO", "LLENO"

# ==========================================
# 2. SISTEMA DE LOGIN Y SESIÓN
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
    # ==========================================
    # 3. INTERFAZ PRINCIPAL PRIVADA
    # ==========================================
    usr = st.session_state['usuario_actual']
    
    st.sidebar.title(f"⚖️ {st.session_state['fiscalia_actual']}")
    st.sidebar.markdown(f"👤 **Usuario:** {usr}")
    
    if st.sidebar.button("🚪 Cerrar Sesión"):
        st.session_state['autenticado'] = False
        st.rerun()
        
    st.sidebar.divider()
    menu = ["⚙️ Configuración", "🔎 Consulta Rápida", "📝 Ingresar Nuevo Expediente", "🔄 Actualizar / Cerrar Caso", "📊 Ver Inventario", "📤 Carga Masiva (Excel)"]
    eleccion = st.sidebar.radio("Navegación:", menu)

    if eleccion == "⚙️ Configuración":
        st.header("⚙️ Configuración del Despacho")
        
        nuevo_nombre = st.text_input("Nombre de la Fiscalía asignada:", value=st.session_state['fiscalia_actual'])
        if st.button("Actualizar Perfil"):
            with conn.session as s:
                s.execute(text("UPDATE usuarios_despacho SET nombre_fiscalia = :val WHERE usuario = :usr"), {"val": nuevo_nombre, "usr": usr})
                s.commit()
            st.session_state['fiscalia_actual'] = nuevo_nombre
            st.success("Perfil actualizado.")
            st.rerun()

        st.write("---")
        st.write("### 🗺️ Mi Mapa Físico (Estantes y Filas)")
        st.info("Este mapa es único para tu despacho. Modificarlo no afectará a los demás fiscales.")
        
        # Cada usuario carga y edita SU propio mapa
        df_mapa = st.data_editor(obtener_mapa(usr), num_rows="dynamic")
        
        if st.button("Guardar Mapa"):
            with conn.session as s:
                # Borramos el mapa antiguo de este usuario y guardamos el nuevo
                s.execute(text("DELETE FROM mapas_personales WHERE usuario = :u"), {"u": usr})
                s.commit()
            
            df_mapa['usuario'] = usr
            with conn.engine.connect() as eng_conn:
                df_mapa.to_sql('mapas_personales', eng_conn, if_exists='append', index=False)
            st.success("Mapa de estantes actualizado para tu despacho.")
            
        # Opciones de creación de usuarios (Solo visibles para 'admin')
        if usr == 'admin':
            st.write("---")
            st.write("### 👑 Panel de Administrador")
            st.write("#### 👥 Crear Cuenta para un Colega")
            with st.form("nuevo_usuario"):
                n_usr = st.text_input("Nuevo Usuario (ej. fiscal_02)")
                n_pwd = st.text_input("Contraseña Temporal", type="password")
                n_fisc = st.text_input("Nombre del Despacho (ej. Fiscalía 02)")
                if st.form_submit_button("Crear Colega"):
                    with conn.session as s:
                        try:
                            s.execute(text("INSERT INTO usuarios_despacho (usuario, password, nombre_fiscalia) VALUES (:u, :p, :f)"), 
                                      {"u": n_usr, "p": generar_hash(n_pwd), "f": n_fisc})
                            s.commit()
                            st.success(f"Cuenta creada. El usuario '{n_usr}' ya puede iniciar sesión.")
                        except:
                            st.error("Error: Ese usuario ya existe.")

    elif eleccion == "🔎 Consulta Rápida":
        st.header("🔎 Consulta Rápida")
        termino = st.text_input("Acusado o Radicado:")
        if st.button("Buscar") and len(termino) >= 3:
            query = f"SELECT * FROM inventario_expedientes WHERE usuario_propietario = '{usr}' AND (radicado ILIKE '%{termino}%' OR acusado ILIKE '%{termino}%')"
            st.dataframe(conn.query(query, ttl=0))

    elif eleccion == "📝 Ingresar Nuevo Expediente":
        with st.form("f1"):
            r = st.text_input("Radicado*"); a = st.text_input("Acusado*"); d = st.text_input("Delito*")
            # Extraemos los municipios disponibles desde el mapa personal del usuario
            m = st.selectbox("Municipio", obtener_mapa(usr)['municipio'].tolist()); e = st.selectbox("Etapa", ["Indagación", "Imputación", "Acusación", "Sentencia", "Preclusión"])
            if st.form_submit_button("Guardar"):
                est, fil, pto, ubi = asignar_ubicacion_fisica(m, e, usr)
                with conn.session as s:
                    s.execute(text("INSERT INTO inventario_expedientes (radicado, acusado, delitos, municipio, etapa, estante, fila, puesto, ubicacion, status_activo, usuario_propietario) VALUES (:r, :a, :d, :m, :e, :est, :fil, :pto, :ubi, 1, :usr)"), 
                              {"r":r, "a":a, "d":d, "m":m, "e":e, "est":est, "fil":fil, "pto":pto, "ubi":ubi, "usr":usr})
                    s.commit()
                st.success(f"Guardado en {est}, {fil}, {pto}, Ubi {ubi}")

    elif eleccion == "🔄 Actualizar / Cerrar Caso":
        with st.form("f2"):
            r = st.text_input("Radicado:"); n = st.selectbox("Nueva Etapa", ["Sentencia", "Preclusión", "Archivo"]); obs = st.text_area("Observaciones*")
            if st.form_submit_button("Actualizar"):
                with conn.session as s:
                    if n in ["Sentencia", "Preclusión", "Archivo"]:
                        e, f, p, u = asignar_ubicacion_fisica("SENTENCIAS", n, usr)
                        s.execute(text("UPDATE inventario_expedientes SET etapa=:n, status_activo=0, estante=:e, fila=:f, puesto=:p, ubicacion=:u, observaciones=:obs WHERE radicado=:r AND usuario_propietario=:usr"),
                                  {"n":n, "e":e, "f":f, "p":p, "u":u, "obs":obs, "r":r, "usr":usr})
                    else: 
                        s.execute(text("UPDATE inventario_expedientes SET etapa=:n, observaciones=:obs WHERE radicado=:r AND usuario_propietario=:usr"),
                                  {"n":n, "obs":obs, "r":r, "usr":usr})
                    s.commit()
                st.success("Actualizado")

    elif eleccion == "📊 Ver Inventario":
        df = conn.query(f"SELECT * FROM inventario_expedientes WHERE usuario_propietario = '{usr}'", ttl=0)
        if not df.empty and 'usuario_propietario' in df.columns:
            df = df.drop(columns=['usuario_propietario'])
        st.dataframe(df)
        if st.button("✨ Auto-Asignar Ubicaciones"):
            casos_sin_ubicacion = conn.query(f"SELECT id, municipio, etapa FROM inventario_expedientes WHERE usuario_propietario = '{usr}' AND (estante IS NULL OR estante='')", ttl=0)
            with conn.session as s:
                for _, caso in casos_sin_ubicacion.iterrows():
                    e, f, p, u = asignar_ubicacion_fisica(caso['municipio'], caso['etapa'], usr)
                    s.execute(text("UPDATE inventario_expedientes SET estante=:e, fila=:f, puesto=:p, ubicacion=:u WHERE id=:id"),
                              {"e":e, "f":f, "p":p, "u":u, "id":caso['id']})
                s.commit()
            st.success("Reorganizado"); st.rerun()

    elif eleccion == "📤 Carga Masiva (Excel)":
        archivo = st.file_uploader("Sube Excel", type=["xlsx"])
        if archivo and st.button("Cargar"):
            df = pd.read_excel(archivo, dtype=str).fillna("").replace(r'\.0$', '', regex=True)
            df['usuario_propietario'] = usr
            with conn.engine.connect() as eng_conn:
                df.to_sql('inventario_expedientes', eng_conn, if_exists='append', index=False)
            st.success("Cargado exclusivamente para tu despacho.")