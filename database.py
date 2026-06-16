import os
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import bcrypt

load_dotenv()

URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_KEY")

# Inicializamos el cliente de Supabase
supabase: Client = create_client(URL, KEY)

def obtener_configuracion_complejo(bot_phone_number: str) -> dict:
    print(f"\n🕵️‍♂️ DEBUG: Buscando complejo con el número: '{bot_phone_number}'")
    try:
        # 1. Buscamos el negocio en la nueva tabla 'complejos'
        res = supabase.table('complejos').select('*').eq('numero_whatsapp', bot_phone_number).execute()
        
        if res.data:
            complejo = res.data[0]
            
            # 2. Le inyectamos su inventario de canchas activas
            canchas_res = supabase.table('canchas').select('*').eq('complejo_id', complejo['id']).eq('activa', True).execute()
            complejo['canchas_inventario'] = canchas_res.data if canchas_res.data else []
            
            print(f"✅ DEBUG: ¡Complejo '{complejo['nombre_negocio']}' encontrado!")
            return complejo
        else:
            print("⚠️ DEBUG: No se encontró ningún negocio con ese número de WhatsApp.")
            return None
            
    except Exception as e:
        print(f"❌ DEBUG Error consultando Supabase: {e}")
        return None

# --- INICIO LÓGICA DE TURNOS ---
def liberar_turnos_vencidos():
    """Libera los turnos pre-reservados que superaron los 15 minutos sin pago."""
    try:
        # 1. Calculamos el límite de tiempo exacto (hace 15 minutos en UTC)
        ahora_utc = datetime.now(timezone.utc)
        limite_tiempo = ahora_utc - timedelta(minutes=15)
        
        # Supabase requiere el formato ISO 8601 para las fechas
        limite_iso = limite_tiempo.isoformat()
        
        # 2. Ejecutamos la guillotina en Supabase
        # Actualiza el estado y borra los datos del cliente si se cumple la condición
        response = supabase.table('turnos').update({
            'estado': 'disponible',
            'cliente_nombre': None,
            'cliente_dni': None,
            'telefono_cliente': None
        }).eq('estado', 'pre-reservado').lt('updated_at', limite_iso).execute()
        
        # Si la lista de datos devuelta tiene elementos, significa que limpió turnos
        if response.data:
            print(f"♻️ [DATABASE] Se liberaron automáticamente {len(response.data)} turnos vencidos.")
            
    except Exception as e:
        print(f"❌ [DATABASE] Error ejecutando la guillotina de turnos: {e}")
# --- FIN LÓGICA DE TURNOS ---

# --- INICIO LECTOR DE TURNOS MULTI-TENANT ---
def obtener_turnos_disponibles(complex_id: int) -> list:
    """Extrae de Supabase todos los turnos libres desde este momento en adelante para un complejo específico."""
    try:
        ahora_utc = datetime.now(timezone.utc).isoformat()
        
        # Busca turnos 'disponibles' cuya fecha_hora sea mayor o igual a AHORA, ordenados cronológicamente
        response = supabase.table('turnos').select('*') \
            .eq('complex_id', complex_id) \
            .eq('estado', 'disponible') \
            .gte('fecha_hora', ahora_utc) \
            .order('fecha_hora') \
            .execute()
            
        return response.data if response.data else []
        
    except Exception as e:
        print(f"❌ [DATABASE] Error buscando turnos reales para complejo {complex_id}: {e}")
        return []
# --- FIN LECTOR DE TURNOS ---

# --- INICIO DE TRANSACCIONES DE VENTA ---
def pre_reservar_turno(turno_id: int, datos_cliente: str, telefono: str) -> bool:
    """Bloquea la cancha. Devuelve True si tuvo éxito, False si alguien más la ganó antes."""
    try:
        # 1. Verificamos por seguridad que siga 'disponible'
        check = supabase.table('turnos').select('estado').eq('id', turno_id).execute()
        if not check.data or check.data[0]['estado'] != 'disponible':
            return False
            
        # 2. Ejecutamos el bloqueo (UPDATE)
        response = supabase.table('turnos').update({
            'estado': 'pre-reservado',
            'cliente_nombre': datos_cliente, # Guardamos el Nombre y DNI en crudo
            'telefono_cliente': telefono
        }).eq('id', turno_id).execute()
        
        return bool(response.data)
    except Exception as e:
        print(f"❌ [DATABASE] Error bloqueando la cancha: {e}")
        return False

def confirmar_turno(turno_id: int) -> bool:
    """Marca el turno oficialmente como pagado."""
    try:
        response = supabase.table('turnos').update({
            'estado': 'confirmado'
        }).eq('id', turno_id).execute()
        return bool(response.data)
    except Exception as e:
        print(f"❌ [DATABASE] Error confirmando el pago: {e}")
        return False
# --- FIN DE TRANSACCIONES DE VENTA ---

def obtener_datos_turno(turno_id: int) -> dict:
    """Busca los datos de un turno para saber a quién notificarle la aprobación."""
    try:
        # ⚠️ MODIFICACIÓN: Agregamos 'fecha_hora' a la consulta SQL
        res = supabase.table('turnos').select('cancha_nombre, cliente_nombre, telefono_cliente, fecha_hora').eq('id', turno_id).execute()
        if res.data:
            return res.data[0]
        return None
    except Exception as e:
        print(f"❌ [DATABASE] Error buscando datos del turno: {e}")
        return None

def rechazar_turno(turno_id: int) -> bool:
    """Libera la cancha si el dueño rechaza el pago."""
    try:
        res = supabase.table('turnos').update({
            'estado': 'disponible',
            'cliente_nombre': None,
            'telefono_cliente': None
        }).eq('id', turno_id).execute()
        return bool(res.data)
    except Exception as e:
        print(f"❌ [DATABASE] Error rechazando el turno: {e}")
        return False

def crear_complejo(nombre: str, email: str, password_plana: str) -> bool:
    """Crea un nuevo cliente en la base de datos con contraseña encriptada."""
    try:
        # Encriptamos la contraseña para que ni siquiera vos la veas en texto plano en Supabase
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password_plana.encode('utf-8'), salt).decode('utf-8')
        
        res = supabase.table('complejos').insert({
            'nombre_negocio': nombre,
            'email_login': email,
            'password_hash': password_hash
        }).execute()
        
        return bool(res.data)
    except Exception as e:
        print(f"❌ [DATABASE] Error creando complejo: {e}")
        return False

def obtener_todos_los_complejos() -> list:
    """Extrae la lista de clientes registrados para el panel de Superadmin."""
    try:
        # Solo traemos los datos esenciales, no mandamos las contraseñas por red
        res = supabase.table('complejos').select('id, nombre_negocio, email_login, wa_session_status, bot_encendido').order('id').execute()
        return res.data if res.data else []
    except Exception as e:
        print(f"❌ [DATABASE] Error obteniendo lista de complejos: {e}")
        return []

def autenticar_complejo(email: str, password_plana: str) -> dict:
    """Verifica el email y la contraseña del cliente."""
    try:
        # Buscamos al usuario por su email
        res = supabase.table('complejos').select('id, nombre_negocio, password_hash').eq('email_login', email).execute()
        
        if not res.data:
            return None # El email no existe
            
        complejo = res.data[0]
        
        # Comparamos la contraseña plana con el hash de Supabase
        if bcrypt.checkpw(password_plana.encode('utf-8'), complejo['password_hash'].encode('utf-8')):
            return {"id": complejo['id'], "nombre": complejo['nombre_negocio']}
            
        return None # La contraseña es incorrecta
    except Exception as e:
        print(f"❌ [DATABASE] Error en autenticación: {e}")
        return None

def obtener_settings_complejo(complejo_id: int) -> dict:
    """Extrae el JSON de configuraciones de un negocio."""
    try:
        res = supabase.table('complejos').select('settings').eq('id', complejo_id).execute()
        if res.data and res.data[0]['settings']:
            return res.data[0]['settings']
        return {}
    except Exception as e:
        print(f"❌ [DATABASE] Error obteniendo settings: {e}")
        return {}

def actualizar_settings_complejo(complejo_id: int, nuevos_datos: dict) -> bool:
    """Actualiza campos específicos dentro del JSONB de settings."""
    try:
        # 1. Traemos los datos actuales para no borrar lo que ya estaba
        settings_actuales = obtener_settings_complejo(complejo_id)
        
        # 2. Mezclamos (actualizamos) los datos viejos con los nuevos
        settings_actuales.update(nuevos_datos)
        
        # 3. Guardamos el JSON completo de nuevo
        res = supabase.table('complejos').update({'settings': settings_actuales}).eq('id', complejo_id).execute()
        return bool(res.data)
    except Exception as e:
        print(f"❌ [DATABASE] Error actualizando settings: {e}")
        return False

def obtener_canchas_complejo(complejo_id: int) -> list:
    """Extrae el inventario de canchas de un complejo específico."""
    try:
        # Traemos todas las canchas de ese cliente ordenadas por ID
        res = supabase.table('canchas').select('*').eq('complejo_id', complejo_id).order('id').execute()
        return res.data if res.data else []
    except Exception as e:
        print(f"❌ [DATABASE] Error obteniendo canchas: {e}")
        return []

def crear_cancha(complejo_id: int, nombre: str, tipo: str, precio: int) -> bool:
    """Inserta una nueva cancha en el inventario del cliente."""
    try:
        res = supabase.table('canchas').insert({
            'complejo_id': complejo_id,
            'nombre': nombre,
            'tipo': tipo,
            'precio': precio
        }).execute()
        return bool(res.data)
    except Exception as e:
        print(f"❌ [DATABASE] Error creando cancha: {e}")
        return False
    
def actualizar_cancha(cancha_id: int, nombre: str, tipo: str, precio: int) -> bool:
    """Modifica los datos de una cancha existente."""
    try:
        res = supabase.table('canchas').update({
            'nombre': nombre,
            'tipo': tipo,
            'precio': precio
        }).eq('id', cancha_id).execute()
        return bool(res.data)
    except Exception as e:
        print(f"❌ [DATABASE] Error actualizando cancha: {e}")
        return False

def eliminar_cancha(cancha_id: int) -> bool:
    """Borra permanentemente una cancha del inventario."""
    try:
        res = supabase.table('canchas').delete().eq('id', cancha_id).execute()
        return bool(res.data)
    except Exception as e:
        print(f"❌ [DATABASE] Error eliminando cancha: {e}")
        return False

def generar_agenda_complejo(complejo_id: int, dias_a_generar: int = 7) -> dict:
    """Genera turnos automáticamente basados en las reglas del complejo."""
    try:
        # 1. Obtener reglas del negocio
        settings = obtener_settings_complejo(complejo_id)
        canchas = obtener_canchas_complejo(complejo_id)

        if not canchas:
            return {"error": "Debes crear al menos una cancha antes de generar la agenda."}

        apertura_str = settings.get("horario_apertura", "14:00")
        cierre_str = settings.get("horario_cierre", "23:00")
        duracion_min = settings.get("duracion_turno_minutos", 60)

        # 2. Configurar zonas horarias (Hora Argentina)
        from datetime import datetime, timedelta, timezone
        zona_ar = timezone(timedelta(hours=-3))
        ahora_ar = datetime.now(zona_ar)
        hoy = ahora_ar.date()

        h_apertura, m_apertura = map(int, apertura_str.split(':'))
        h_cierre, m_cierre = map(int, cierre_str.split(':'))

        minutos_inicio = h_apertura * 60 + m_apertura
        minutos_fin = h_cierre * 60 + m_cierre
        
        if minutos_fin <= minutos_inicio:
            minutos_fin += 24 * 60 

        # 3. Buscar turnos ya existentes para NO DUPLICARLOS
        # Modificamos la fecha límite para evitar problemas de formato en la consulta SQL
        fecha_limite = (ahora_ar - timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        res_existentes = supabase.table('turnos').select('cancha_nombre, fecha_hora').eq('complex_id', complejo_id).gte('fecha_hora', fecha_limite).execute()
        
        existentes_set = set()
        if res_existentes.data:
            for t in res_existentes.data:
                # 🛡️ ESCUDO ANTI-DUPLICADOS MEJORADO: Normalizamos el formato de Supabase
                fecha_raw = t['fecha_hora'].replace('Z', '+00:00')
                try:
                    fecha_obj = datetime.fromisoformat(fecha_raw)
                    # Forzamos a que tenga exactamente nuestro molde
                    fecha_str_exacta = fecha_obj.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    existentes_set.add(f"{t['cancha_nombre']}_{fecha_str_exacta}")
                except:
                    # Fallback por si la fecha viene corrupta
                    existentes_set.add(f"{t['cancha_nombre']}_{t['fecha_hora']}")

        nuevos_turnos = []

        # 4. Generar la grilla matemática
        for i in range(dias_a_generar):
            fecha_bucle = hoy + timedelta(days=i) 
            
            tiempo_actual = minutos_inicio
            while tiempo_actual + duracion_min <= minutos_fin:
                hora = (tiempo_actual // 60) % 24
                minuto = tiempo_actual % 60
                
                dt_turno_ar = datetime(fecha_bucle.year, fecha_bucle.month, fecha_bucle.day, hora, minuto, tzinfo=zona_ar)
                
                if hora < h_apertura:
                    dt_turno_ar += timedelta(days=1)

                if dt_turno_ar < ahora_ar:
                    tiempo_actual += duracion_min
                    continue

                # Formatear a UTC exacto para Supabase (Este es "nuestro molde")
                dt_turno_utc = dt_turno_ar.astimezone(timezone.utc)
                fecha_hora_iso = dt_turno_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

                for cancha in canchas:
                    huella = f"{cancha['nombre']}_{fecha_hora_iso}"
                    
                    # Ahora la comparación de huellas nunca fallará
                    if huella not in existentes_set:
                        nuevos_turnos.append({
                            "complex_id": complejo_id,
                            "cancha_nombre": cancha["nombre"],
                            "fecha_hora": fecha_hora_iso,
                            "estado": "disponible"
                        })
                        existentes_set.add(huella) 
                
                tiempo_actual += duracion_min

        # 5. Insertar en bloque a la base de datos
        if nuevos_turnos:
            supabase.table('turnos').insert(nuevos_turnos).execute()
            return {"mensaje": f"✅ Agenda aprovisionada: {len(nuevos_turnos)} turnos nuevos generados."}
        else:
            return {"mensaje": "✅ La agenda ya estaba al día. No hizo falta generar turnos nuevos."}

    except Exception as e:
        print(f"❌ [GENERADOR] Error: {e}")
        return {"error": f"Error al generar turnos: {str(e)}"}

def obtener_turnos_hoy(complejo_id: int) -> list:
    """Busca los turnos generados desde hoy a las 00:00 hasta mañana para mostrarlos en el panel."""
    try:
        from datetime import datetime, timedelta, timezone
        zona_ar = timezone(timedelta(hours=-3))
        hoy_ar = datetime.now(zona_ar).replace(hour=0, minute=0, second=0, microsecond=0)
        pasado_manana = hoy_ar + timedelta(days=8) # Traemos 48hs de turnos
        
        res = supabase.table('turnos') \
            .select('*') \
            .eq('complex_id', complejo_id) \
            .gte('fecha_hora', hoy_ar.astimezone(timezone.utc).isoformat()) \
            .lt('fecha_hora', pasado_manana.astimezone(timezone.utc).isoformat()) \
            .order('fecha_hora') \
            .execute()
            
        return res.data if res.data else []
    except Exception as e:
        print(f"❌ [DATABASE] Error buscando turnos de hoy: {e}")
        return []

def cambiar_estado_turno_manual(turno_id: int, nuevo_estado: str) -> bool:
    """Permite al dueño bloquear o liberar un turno manualmente."""
    try:
        res = supabase.table('turnos').update({'estado': nuevo_estado}).eq('id', turno_id).execute()
        return bool(res.data)
    except Exception as e:
        print(f"❌ [DATABASE] Error cambiando estado del turno: {e}")
        return False