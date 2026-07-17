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
        # 1. Crear tabla si no existe
        s.execute(text('''CREATE TABLE IF NOT EXISTS inventario_expedientes (
            id SERIAL PRIMARY KEY, radicado TEXT, municipio TEXT, etapa TEXT, 
            estante TEXT, fila TEXT, puesto TEXT, ubicacion TEXT, status_activo INTEGER, 
            observaciones TEXT, acusado TEXT, delitos TEXT, usuario_propietario TEXT,
            fecha_imputacion TEXT)'''))
        
        # 2. Intentar agregar la columna por si ya existía sin ella
        try:
            s.execute(text('ALTER TABLE inventario_expedientes ADD COLUMN fecha_imputacion TEXT'))
            s.commit()
        except:
            s.rollback() 
            
        # 3. Resto de tablas
        s.execute(text('''CREATE TABLE IF NOT EXISTS usuarios_despacho (
            usuario TEXT PRIMARY KEY, password TEXT, nombre_fiscalia TEXT)'''))
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
    menu = ["⚙️ Configuración", "🔎 Consulta Rápida", "📝 Ingresar Nuevo Expediente", "🔄 Actualizar / Cerrar Caso", "📊 Ver Inventario", "📤 Carga Masiva (Excel)", "📥 Descargar Reporte (Excel)"]
    eleccion = st.sidebar.radio("Navegación:", menu)

    if eleccion == "⚙️ Configuración":
        st.header("⚙️ Configuración del Despacho")
        
        # --- Nombre de la fiscalía ---
        nuevo_nombre = st.text_input("Nombre de la Fiscalía asignada:", value=st.session_state['fiscalia_actual'])
        if st.button("Actualizar Perfil"):
            with conn.session as s:
                s.execute(text("UPDATE usuarios_despacho SET nombre_fiscalia = :val WHERE usuario = :usr"), {"val": nuevo_nombre, "usr": usr})
                s.commit()
            st.session_state['fiscalia_actual'] = nuevo_nombre
            st.success("Perfil actualizado.")
            st.rerun()

        st.write("---")
        # --- CAMBIO DE CONTRASEÑA PERSONAL ---
        st.write("### 🔑 Cambiar mi contraseña")
        with st.form("cambiar_pwd"):
            pwd_ant = st.text_input("Contraseña actual", type="password")
            pwd_nueva = st.text_input("Nueva contraseña", type="password")
            if st.form_submit_button("Actualizar mi contraseña"):
                hash_ant = generar_hash(pwd_ant)
                # Verificamos la contraseña actual
                df_check = conn.query(f"SELECT * FROM usuarios_despacho WHERE usuario='{usr}' AND password='{hash_ant}'", ttl=0)
                if not df_check.empty:
                    with conn.session as s:
                        s.execute(text("UPDATE usuarios_despacho SET password = :p WHERE usuario = :u"), 
                                  {"p": generar_hash(pwd_nueva), "u": usr})
                        s.commit()
                    st.success("Contraseña actualizada correctamente.")
                else:
                    st.error("La contraseña actual es incorrecta.")

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
            
        # Opciones de creación y gestión de usuarios (Solo visibles para 'admin')
        if usr == 'admin':
            st.write("---")
            st.write("### 👑 Panel de Administrador")
            
            # --- CREAR COLEGAS ---
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
                            st.success(f"Cuenta '{n_usr}' creada.")
                        except:
                            st.error("Error: Ese usuario ya existe.")

            # --- RESTABLECER CONTRASEÑAS ---
            st.write("#### 🔄 Restablecer contraseña de un colega")
            with st.form("reset_pwd"):
                lista_usuarios = conn.query("SELECT usuario FROM usuarios_despacho", ttl=0)['usuario'].tolist()
                r_usr = st.selectbox("Seleccionar usuario", lista_usuarios)
                r_pwd = st.text_input("Nueva contraseña para este colega", type="password")
                
                if st.form_submit_button("Restablecer Clave"):
                    with conn.session as s:
                        s.execute(text("UPDATE usuarios_despacho SET password = :p WHERE usuario = :u"), 
                                  {"p": generar_hash(r_pwd), "u": r_usr})
                        s.commit()
                    st.success(f"La contraseña de {r_usr} ha sido cambiada.")

    elif eleccion == "🔎 Consulta Rápida":
        st.header("🔎 Consulta Rápida")
        termino = st.text_input("Acusado o Radicado:")
        if st.button("Buscar") and len(termino) >= 3:
            query = f"SELECT * FROM inventario_expedientes WHERE usuario_propietario = '{usr}' AND (radicado ILIKE '%{termino}%' OR acusado ILIKE '%{termino}%')"
            df_resultado = conn.query(query, ttl=0)
            st.dataframe(df_resultado)
            
            # --- NUEVA SECCIÓN: EDICIÓN DE OBSERVACIONES ---
            if not df_resultado.empty:
                st.write("---")
                st.write("### 📝 Editar Observaciones")
                with st.form("form_editar_obs"):
                    # Tomamos el radicado del primer caso que aparezca en la búsqueda
                    radicado_actual = df_resultado.iloc[0]['radicado']
                    
                    # Tomamos la observación actual para que aparezca en el cuadro de texto
                    obs_actual = df_resultado.iloc[0]['observaciones']
                    if pd.isna(obs_actual) or obs_actual == "None" or obs_actual == None:
                        obs_actual = ""
                        
                    nueva_obs = st.text_area(f"Añadir o modificar observaciones para el radicado {radicado_actual}:", value=obs_actual)
                    
                    if st.form_submit_button("Guardar Observación"):
                        with conn.session as s:
                            s.execute(text("UPDATE inventario_expedientes SET observaciones = :obs WHERE radicado = :rad AND usuario_propietario = :usr"), 
                                      {"obs": nueva_obs, "rad": radicado_actual, "usr": usr})
                            s.commit()
                        st.success("¡Observaciones actualizadas correctamente!")

    elif eleccion == "📝 Ingresar Nuevo Expediente":
        with st.form("f1"):
            r = st.text_input("Radicado*")
            a = st.text_input("Acusado*")
            d = st.text_input("Delito*")
            # --- AGREGAMOS EL CALENDARIO DE FECHA ---
            f_imp = st.date_input("Fecha de Imputación")
            
            # Extraemos los municipios disponibles desde el mapa personal del usuario
            m = st.selectbox("Municipio", obtener_mapa(usr)['municipio'].tolist())
            e = st.selectbox("Etapa", ["Indagación", "Imputación", "Acusación", "Sentencia", "Preclusión"])
            
            if st.form_submit_button("Guardar"):
                est, fil, pto, ubi = asignar_ubicacion_fisica(m, e, usr)
                with conn.session as s:
                    # Convertimos la fecha a texto para guardarla
                    fecha_str = str(f_imp)
                    s.execute(text("""INSERT INTO inventario_expedientes 
                                      (radicado, acusado, delitos, municipio, etapa, estante, fila, puesto, ubicacion, status_activo, usuario_propietario, fecha_imputacion) 
                                      VALUES (:r, :a, :d, :m, :e, :est, :fil, :pto, :ubi, 1, :usr, :f_imp)"""), 
                              {"r":r, "a":a, "d":d, "m":m, "e":e, "est":est, "fil":fil, "pto":pto, "ubi":ubi, "usr":usr, "f_imp":fecha_str})
                    s.commit()
                st.success(f"Guardado en {est}, {fil}, {pto}, Ubi {ubi}")

    elif eleccion == "🔄 Actualizar / Cerrar Caso":
        st.header("🔄 Actualizar / Cerrar Caso")
        with st.form("f2"):
            r = st.text_input("Radicado del caso:")
            n = st.selectbox("Nueva Etapa", ["Indagación", "Imputación", "Acusación", "Sentencia", "Preclusión", "Archivo"])
            f_imp = st.date_input("Fecha de Imputación (si aplica):")
            obs = st.text_area("Observaciones:")
            
            if st.form_submit_button("Actualizar"):
                with conn.session as s:
                    # Convertimos la fecha a texto para guardarla en la BD
                    fecha_str = str(f_imp)
                    
                    if n in ["Sentencia", "Preclusión", "Archivo"]:
                        e, f, p, u = asignar_ubicacion_fisica("SENTENCIAS", n, usr)
                        s.execute(text("""UPDATE inventario_expedientes 
                                          SET etapa=:n, status_activo=0, estante=:e, fila=:f, puesto=:p, 
                                          ubicacion=:u, observaciones=:obs, fecha_imputacion=:f_imp 
                                          WHERE radicado=:r AND usuario_propietario=:usr"""),
                                  {"n":n, "e":e, "f":f, "p":p, "u":u, "obs":obs, "f_imp":fecha_str, "r":r, "usr":usr})
                    else: 
                        s.execute(text("""UPDATE inventario_expedientes 
                                          SET etapa=:n, observaciones=:obs, fecha_imputacion=:f_imp 
                                          WHERE radicado=:r AND usuario_propietario=:usr"""),
                                  {"n":n, "obs":obs, "f_imp":fecha_str, "r":r, "usr":usr})
                    s.commit()
                st.success("Caso actualizado exitosamente.")

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
    elif eleccion == "📥 Descargar Reporte (Excel)":
        st.header("📥 Reporte de Inventario")
        df_reporte = conn.query(f"SELECT * FROM inventario_expedientes WHERE usuario_propietario = '{usr}'", ttl=0)
        
        if not df_reporte.empty:
            st.dataframe(df_reporte)
            from io import BytesIO
            output = BytesIO()
            df_reporte.to_excel(output, index=False)
            
            st.download_button(
                label="📥 Descargar archivo Excel",
                data=output.getvalue(),
                file_name=f"Reporte_Despacho_{usr}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.warning("No hay expedientes para generar el reporte.")