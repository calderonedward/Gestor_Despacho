import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import hashlib

st.set_page_config(page_title="Gestor de Despacho", page_icon="⚖️", layout="wide")

# ==========================================
# 0. SEGURIDAD Y CONEXIÓN ROBUSTA (PSYCOPG2)
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
    # 1. Crear todas las tablas principales de forma segura en una sola transacción
    with conn.session as s:
        s.execute(text('''CREATE TABLE IF NOT EXISTS inventario_expedientes (
            id SERIAL PRIMARY KEY, radicado TEXT, municipio TEXT, etapa TEXT, 
            estante TEXT, fila TEXT, puesto TEXT, ubicacion TEXT, status_activo INTEGER, 
            observaciones TEXT, acusado TEXT, delitos TEXT, usuario_propietario TEXT,
            fecha_imputacion TEXT)'''))
            
        s.execute(text('''CREATE TABLE IF NOT EXISTS usuarios_despacho (
            usuario TEXT PRIMARY KEY, password TEXT, nombre_fiscalia TEXT)'''))
            
        s.execute(text('''CREATE TABLE IF NOT EXISTS mapas_personales (
            id SERIAL PRIMARY KEY, usuario TEXT, municipio TEXT, estante INTEGER, 
            fila_inicio INTEGER, fila_fin INTEGER, puestos_max INTEGER, ubic_max INTEGER)'''))
    
    # 2. Intentar agregar columnas nuevas de forma AISLADA para evitar InFailedSqlTransaction
    try:
        with conn.session as s:
            s.execute(text('ALTER TABLE inventario_expedientes ADD COLUMN fecha_imputacion TEXT'))
    except:
        pass 
        
    for col in ['puestos_max', 'ubic_max']:
        try:
            with conn.session as s:
                s.execute(text(f'ALTER TABLE mapas_personales ADD COLUMN {col} INTEGER'))
        except:
            pass
    
    # 3. Insertar usuario administrador por defecto en su propia transacción
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

# ==========================================
# 1. LÓGICA DE ASIGNACIÓN FÍSICA
# ==========================================
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
# 2. SISTEMA DE LOGIN
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
        "📥 Carga Masiva (Excel)", 
        "🗺️ Configurar Mi Mapa Físico"
    ]
    eleccion = st.sidebar.radio("Navegación:", menu)

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
                        s.execute(text("UPDATE usuarios_despacho SET password = :p WHERE usuario = :u"), 
                                    {"p": generar_hash(pwd_nueva), "u": usr})
                    st.success("Contraseña actualizada correctamente.")
                else:
                    st.error("La contraseña actual es incorrecta.")

        if usr == 'admin':
            st.write("---")
            st.write("### 👑 Panel de Administrador")
            with st.form("nuevo_usuario"):
                n_usr = st.text_input("Nuevo Usuario (ej. fiscal_02)")
                n_pwd = st.text_input("Contraseña Temporal", type="password")
                n_fisc = st.text_input("Nombre del Despacho (ej. Fiscalía 02)")
                if st.form_submit_button("Crear Colega"):
                    with conn.session as s:
                        try:
                            s.execute(text("INSERT INTO usuarios_despacho (usuario, password, nombre_fiscalia) VALUES (:u, :p, :f)"), 
                                        {"u": n_usr, "p": generar_hash(n_pwd), "f": n_fisc})
                            st.success(f"Cuenta '{n_usr}' creada.")
                        except:
                            st.error("Error: Ese usuario ya existe.")

            with st.form("reset_pwd"):
                lista_usuarios = conn.query("SELECT usuario FROM usuarios_despacho", ttl=0)['usuario'].tolist()
                r_usr = st.selectbox("Seleccionar usuario", lista_usuarios)
                r_pwd = st.text_input("Nueva contraseña para este colega", type="password")
                if st.form_submit_button("Restablecer Clave"):
                    with conn.session as s:
                        s.execute(text("UPDATE usuarios_despacho SET password = :p WHERE usuario = :u"), 
                                    {"p": generar_hash(r_pwd), "u": r_usr})
                    st.success(f"La contraseña de {r_usr} ha sido cambiada.")

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
                    if pd.isna(obs_actual) or obs_actual == "None" or obs_actual is None:
                        obs_actual = ""
                    nueva_obs = st.text_area(f"Añadir o modificar observaciones para el radicado {radicado_actual}:", value=obs_actual)
                    if st.form_submit_button("Guardar Observación"):
                        with conn.session as s:
                            s.execute(text("UPDATE inventario_expedientes SET observaciones = :obs WHERE radicado = :rad AND usuario_propietario = :usr"), 
                                        {"obs": nueva_obs, "rad": radicado_actual, "usr": usr})
                        st.success("¡Observaciones actualizadas correctamente!")

    elif eleccion == "📝 Ingresar Nuevo Expediente":
        with st.form("f1"):
            r = st.text_input("Radicado*")
            a = st.text_input("Acusado*")
            d = st.text_input("Delito*")
            f_imp = st.date_input("Fecha de Imputación")
            m = st.selectbox("Municipio", obtener_mapa(usr)['municipio'].tolist())
            e = st.selectbox("Etapa", ["Indagación", "Imputación", "Acusación", "Sentencia", "Preclusión"])
            
            if st.form_submit_button("Guardar"):
                est, fil, pto, ubi = asignar_ubicacion_fisica(m, e, usr)
                with conn.session as s:
                    fecha_str = str(f_imp)
                    s.execute(text("""INSERT INTO inventario_expedientes 
                                        (radicado, acusado, delitos, municipio, etapa, estante, fila, puesto, ubicacion, status_activo, usuario_propietario, fecha_imputacion) 
                                        VALUES (:r, :a, :d, :m, :e, :est, :fil, :pto, :ubi, 1, :usr, :f_imp)"""), 
                                {"r":r, "a":a, "d":d, "m":m, "e":e, "est":est, "fil":fil, "pto":pto, "ubi":ubi, "usr":usr, "f_imp":fecha_str})
                st.success(f"Guardado en {est}, {fil}, {pto}, Ubi {ubi}")

    elif eleccion == "🔄 Actualizar / Cerrar Caso":
        st.header("🔄 Actualizar / Cerrar Caso")
        
        # Contenedor para mostrar los mensajes sin que se borren al instante
        msg_container = st.container()
        
        with st.form("f2"):
            r = st.text_input("Radicado del caso:")
            n = st.selectbox("Nueva Etapa", ["Indagación", "Imputación", "Acusación", "Sentencia", "Preclusión", "Archivo"])
            f_imp = st.date_input("Fecha de Imputación (si aplica):")
            obs = st.text_area("Observaciones:")
            
            submit_btn = st.form_submit_button("Actualizar")
            
        if submit_btn:
            # Buscamos el caso en la base de datos ANTES de que se modifique
            df_actual = conn.query(f"SELECT * FROM inventario_expedientes WHERE radicado='{r}' AND usuario_propietario='{usr}'", ttl=0)
            
            if not df_actual.empty:
                df_actual = df_actual.fillna("")
                # Guardamos una copia exacta en la memoria
                st.session_state['backup_caso'] = df_actual.iloc[0].to_dict()
                
                with conn.session as s:
                    fecha_str = str(f_imp)
                    
                    if n in ["Sentencia", "Preclusión", "Archivo"]:
                        e, f, p, u = asignar_ubicacion_fisica("SENTENCIAS", n, usr)
                        s.execute(text("""UPDATE inventario_expedientes 
                                          SET etapa=:n, status_activo=0, estante=:e, fila=:f, puesto=:p, 
                                          ubicacion=:u, observaciones=:obs, fecha_imputacion=:f_imp 
                                          WHERE radicado=:r AND usuario_propietario=:usr"""),
                                  {"n":n, "e":e, "f":f, "p":p, "u":u, "obs":obs, "f_imp":fecha_str, "r":r, "usr":usr})
                        estado_str = "🔴 Inactivo (Enviado a Sentencias/Archivo)"
                    else: 
                        s.execute(text("""UPDATE inventario_expedientes 
                                          SET etapa=:n, status_activo=1, observaciones=:obs, fecha_imputacion=:f_imp 
                                          WHERE radicado=:r AND usuario_propietario=:usr"""),
                                  {"n":n, "obs":obs, "f_imp":fecha_str, "r":r, "usr":usr})
                        estado_str = "🟢 Activo"
                        
                msg_container.success(f"Caso actualizado exitosamente a la etapa '{n}'. Estado actual: {estado_str}")
            else:
                msg_container.error(f"No se encontró el radicado {r}. Verifica el número.")

        # Botón mágico de DESHACER
        if 'backup_caso' in st.session_state and st.session_state['backup_caso'] is not None:
            backup = st.session_state['backup_caso']
            st.write("---")
            st.warning(f"⚠️ ¿Digitaste mal? El último caso modificado fue el radicado **{backup['radicado']}**.")
            
            if st.button("↩️ Deshacer error (Restaurar estado y recuperar ubicación)"):
                with conn.session as s:
                    s.execute(text("""UPDATE inventario_expedientes 
                                      SET etapa=:eta, status_activo=:act, estante=:est, fila=:fil, 
                                          puesto=:pue, ubicacion=:ubi, observaciones=:obs, fecha_imputacion=:f_imp 
                                      WHERE radicado=:rad AND usuario_propietario=:usr"""),
                              {"eta": backup['etapa'], "act": backup['status_activo'], 
                               "est": backup['estante'], "fil": backup['fila'], 
                               "pue": backup['puesto'], "ubi": backup['ubicacion'], 
                               "obs": backup['observaciones'], "f_imp": backup['fecha_imputacion'],
                               "rad": backup['radicado'], "usr": usr})
                st.session_state['backup_caso'] = None
                st.success("¡Acción deshecha con éxito! El caso ha recuperado su etapa anterior y su espacio original.")

        # ==========================================
        # NUEVA SECCIÓN: ELIMINAR CASO DEFINITIVAMENTE
        # ==========================================
        st.write("---")
        st.write("### 🗑️ Eliminar Registro Definitivamente")
        st.info("💡 Si borras un caso aquí, se eliminará por completo del sistema y su espacio en el estante quedará libre para el próximo caso que ingreses.")
        
        with st.form("form_eliminar"):
            rad_eliminar = st.text_input("Ingresa el Radicado exacto a eliminar:")
            # Casilla de seguridad para evitar borrados accidentales
            confirmar = st.checkbox("Estoy seguro de que quiero borrar este caso por completo.")
            
            if st.form_submit_button("🚨 Eliminar Expediente"):
                if not confirmar:
                    st.error("Debes marcar la casilla de confirmación para poder eliminar.")
                elif len(rad_eliminar) < 3:
                    st.error("Ingresa un radicado válido.")
                else:
                    # Verificamos si existe antes de borrarlo
                    df_check = conn.query(f"SELECT * FROM inventario_expedientes WHERE radicado='{rad_eliminar}' AND usuario_propietario='{usr}'", ttl=0)
                    
                    if not df_check.empty:
                        with conn.session as s:
                            s.execute(text("DELETE FROM inventario_expedientes WHERE radicado=:r AND usuario_propietario=:u"), 
                                      {"r": rad_eliminar, "u": usr})
                        st.success(f"¡El radicado {rad_eliminar} ha sido borrado del sistema! Su espacio físico ya está disponible.")
                    else:
                        st.error(f"No se encontró el radicado {rad_eliminar} en tu inventario.")
    elif eleccion == "📊 Ver Inventario":
        st.header("📊 Inventario de Expedientes")
        
        df = conn.query(f"SELECT * FROM inventario_expedientes WHERE usuario_propietario = '{usr}'", ttl=0)
        
        if not df.empty:
            if 'usuario_propietario' in df.columns:
                df = df.drop(columns=['usuario_propietario'])
                
            # Separar los datos
            df_activos = df[df['status_activo'] == 1]
            df_inactivos = df[df['status_activo'] == 0]
            
            # Buscar duplicados ignorando los campos vacíos
            df_validos = df[df['radicado'].astype(str).str.strip() != ""]
            duplicados = df_validos[df_validos.duplicated(subset=['radicado'], keep=False)]
            
            # Crear 4 pestañas interactivas
            tab1, tab2, tab3, tab4 = st.tabs(["🟢 Casos Activos", "🔴 Casos Inactivos", "📋 Todos", "⚠️ Duplicados"])
            
            with tab1:
                st.write(f"**Total casos activos:** {len(df_activos)}")
                st.dataframe(df_activos, use_container_width=True)
                
            with tab2:
                st.write(f"**Total casos inactivos:** {len(df_inactivos)}")
                st.dataframe(df_inactivos, use_container_width=True)
                
            with tab3:
                st.write(f"**Total general:** {len(df)}")
                st.dataframe(df, use_container_width=True)
                
            with tab4:
                st.write("### 🚨 Detección de Radicados Duplicados")
                if not duplicados.empty:
                    st.warning(f"Se detectaron {len(duplicados)} registros con radicados repetidos. Revisa la tabla:")
                    # Mostramos los duplicados ordenados para que los veas juntos
                    st.dataframe(duplicados.sort_values(by='radicado'), use_container_width=True)
                    
                    st.info("💡 Si presionas el botón, el sistema eliminará los registros más antiguos y conservará únicamente la última versión ingresada de cada radicado.")
                    if st.button("🧹 Eliminar duplicados (Conservar el más reciente)"):
                        with conn.session as s:
                            s.execute(text("""
                                DELETE FROM inventario_expedientes a USING (
                                    SELECT MAX(id) as max_id, radicado
                                    FROM inventario_expedientes 
                                    WHERE usuario_propietario = :usr AND radicado IS NOT NULL AND radicado != ''
                                    GROUP BY radicado HAVING COUNT(*) > 1
                                ) b
                                WHERE a.radicado = b.radicado 
                                AND a.id <> b.max_id 
                                AND a.usuario_propietario = :usr
                            """), {"usr": usr})
                        st.success("¡Limpieza completada! Solo se conservó un registro por cada radicado.")
                        st.rerun()
                else:
                    st.success("¡Todo en orden! No se encontraron radicados duplicados en tu sistema.")
                
            st.write("---")
            if st.button("✨ Auto-Asignar Ubicaciones a Casos Pendientes"):
                query_pendientes = f"SELECT id, municipio, etapa FROM inventario_expedientes WHERE usuario_propietario = '{usr}' AND (estante IS NULL OR estante='' OR estante='Pendiente')"
                casos_sin_ubicacion = conn.query(query_pendientes, ttl=0)
                
                if not casos_sin_ubicacion.empty:
                    with conn.session as s:
                        for _, caso in casos_sin_ubicacion.iterrows():
                            e, f, p, u = asignar_ubicacion_fisica(caso['municipio'], caso['etapa'], usr)
                            s.execute(text("UPDATE inventario_expedientes SET estante=:e, fila=:f, puesto=:p, ubicacion=:u WHERE id=:id"),
                                      {"e":e, "f":f, "p":p, "u":u, "id":caso['id']})
                    st.success("¡Ubicaciones reorganizadas con éxito!")
                    st.rerun()
                else:
                    st.info("Todos tus casos ya tienen una ubicación física asignada.")
        else:
            st.warning("No tienes expedientes registrados en el inventario.")

    elif eleccion == "📥 Carga Masiva (Excel)":
        st.header("📥 Carga Masiva de Expedientes mediante Excel")
        archivo = st.file_uploader("Sube tu archivo Excel", type=["xlsx"])
        if archivo and st.button("Cargar"):
            df = pd.read_excel(archivo, dtype=str).fillna("").replace(r'\.0$', '', regex=True)
            df['usuario_propietario'] = usr

            mapa_df = obtener_mapa(usr)
            df_ocupados_global = conn.query(f"SELECT estante, fila, puesto, ubicacion FROM inventario_expedientes WHERE usuario_propietario = '{usr}'", ttl=0)
            
            ocupados_por_estante = {}
            for est in mapa_df['estante'].unique():
                est_str = f"Estante {int(est)}"
                subset = df_ocupados_global[df_ocupados_global['estante'] == est_str]
                ocupados_por_estante[est_str] = set((str(r['fila']), str(r['puesto']), str(r['ubicacion'])) for _, r in subset.iterrows())

            for index, row in df.iterrows():
                mun = str(row.get('municipio', '')).upper()
                eta = str(row.get('etapa', ''))
                
                bloque = "SENTENCIAS" if eta in ["Sentencia", "Preclusión", "Archivo"] else mun
                regla = mapa_df[mapa_df['municipio'] == bloque]
                
                if regla.empty:
                    estante, fila, puesto, ubicacion = "Pendiente", "Pendiente", "Pendiente", "Pendiente"
                    df.loc[index, 'estante'] = str(estante)
                    df.loc[index, 'fila'] = str(fila)
                    df.loc[index, 'puesto'] = str(puesto)
                    df.loc[index, 'ubicacion'] = str(ubicacion)
                    df.loc[index, 'status_activo'] = 1
                else:
                    est = int(regla['estante'].iloc[0])
                    est_str = f"Estante {est}"
                    filas = range(int(regla['fila_inicio'].iloc[0]), int(regla['fila_fin'].iloc[0]) + 1)
                    
                    max_puestos = int(regla['puestos_max'].iloc[0]) if 'puestos_max' in regla.columns and pd.notna(regla['puestos_max'].iloc[0]) else 3
                    max_ubic = int(regla['ubic_max'].iloc[0]) if 'ubic_max' in regla.columns and pd.notna(regla['ubic_max'].iloc[0]) else 20
                    
                    slots = [(f"Fila {f}", f"Puesto {p}", str(u)) for f in filas for p in range(1, max_puestos + 1) for u in range(1, max_ubic + 1)]

                    if est_str not in ocupados_por_estante:
                        ocupados_por_estante[est_str] = set()
                        
                    slot_encontrado = None
                    for slot in slots:
                        if slot not in ocupados_por_estante[est_str]:
                            slot_encontrado = slot
                            break
                            
                    if slot_encontrado:
                        estante = est_str
                        fila = slot_encontrado[0]
                        puesto = slot_encontrado[1]
                        ubicacion = slot_encontrado[2]
                        ocupados_por_estante[est_str].add(slot_encontrado)
                    else:
                        estante, fila, puesto, ubicacion = est_str, "LLENO", "LLENO", "LLENO"

                    df.loc[index, 'estante'] = str(estante)
                    df.loc[index, 'fila'] = str(fila)
                    df.loc[index, 'puesto'] = str(puesto)
                    df.loc[index, 'ubicacion'] = str(ubicacion)
                    df.loc[index, 'status_activo'] = 1
            
            columnas_permitidas = [
                'radicado', 'municipio', 'etapa', 'estante', 'fila', 
                'puesto', 'ubicacion', 'status_activo', 'observaciones', 
                'acusado', 'delitos', 'usuario_propietario', 'fecha_imputacion'
            ]
            df_final = df[[col for col in columnas_permitidas if col in df.columns]]

            with _engine.connect() as eng_conn:
                df_final.to_sql('inventario_expedientes', eng_conn, if_exists='append', index=False)
            st.success("¡Carga masiva realizada de forma instantánea y con ubicaciones precisas!")
            
        df_reporte = conn.query(f"SELECT * FROM inventario_expedientes WHERE usuario_propietario = '{usr}'", ttl=0)
        
        if not df_reporte.empty:
            st.info("💡 Puedes hacer doble clic en cualquier celda de la tabla inferior para modificarla directamente.")
            df_editado = st.data_editor(
                df_reporte,
                num_rows="dynamic",
                key="editor_inventario",
                use_container_width=True,
                hide_index=True
            )
            
            if st.button("💾 Guardar Cambios en la Base de Datos"):
                try:
                    with _engine.connect() as eng_conn:
                        with eng_conn.begin():
                            eng_conn.execute(text(f"DELETE FROM inventario_expedientes WHERE usuario_propietario = '{usr}'"))
                            df_editado.to_sql('inventario_expedientes', eng_conn, if_exists='append', index=False)
                    st.success("¡Cambios actualizados y guardados correctamente en la base de datos!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al guardar los cambios: {e}")

            st.write("---")
            st.write("### 📥 Opciones de Descarga (Excel)")
            filtro_descarga = st.radio(
                "Selecciona qué expedientes deseas exportar:", 
                ["🟢 Casos Activos", "🔴 Casos Inactivos (Cerrados)", "📋 Todos los Casos"],
                horizontal=True
            )
            
            # Crear una copia para no alterar la tabla interactiva
            df_descarga = df_editado.copy()
            
            # Aplicar el filtro según tu selección
            if filtro_descarga == "🟢 Casos Activos":
                df_descarga = df_descarga[df_descarga['status_activo'] == 1]
                nombre_archivo = f"Reporte_Activos_{usr}.xlsx"
            elif filtro_descarga == "🔴 Casos Inactivos (Cerrados)":
                df_descarga = df_descarga[df_descarga['status_activo'] == 0]
                nombre_archivo = f"Reporte_Inactivos_{usr}.xlsx"
            else:
                nombre_archivo = f"Reporte_Completo_{usr}.xlsx"

            from io import BytesIO
            output = BytesIO()
            df_descarga.to_excel(output, index=False)
            
            st.download_button(
                label=f"📥 Descargar archivo Excel",
                data=output.getvalue(),
                file_name=nombre_archivo,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.warning("No hay expedientes para generar el reporte.")

    elif eleccion == "🗺️ Configurar Mi Mapa Físico":
        st.header("⚙️ Configuración de Espacios y Capacidad por Municipio")
        st.info("💡 Puedes modificar directamente el estante, las filas, los puestos máximos y las ubicaciones por puesto para cada municipio.")
    
        mapa_actual = obtener_mapa(usr)
    
        if not mapa_actual.empty:
            mapa_editado = st.data_editor(
                mapa_actual,
                num_rows="dynamic",
                key="editor_mapa_fisico",
                use_container_width=True,
                hide_index=True
            )
        
            if st.button("💾 Guardar Configuración del Mapa"):
                try:
                    with _engine.connect() as eng_conn:
                        with eng_conn.begin():
                            eng_conn.execute(text(f"DELETE FROM mapas_personales WHERE usuario = '{usr}'"))
                            mapa_editado['usuario'] = usr
                            mapa_editado.to_sql('mapas_personales', eng_conn, if_exists='append', index=False)
                    st.success("¡Configuración del mapa físico guardada con éxito!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al guardar el mapa: {e}")
        else:
            st.warning("No tienes registros en tu mapa físico. Configura uno inicial para comenzar.")
