import os
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

load_dotenv()

URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_KEY")

# Inicializamos el cliente de Supabase
supabase: Client = create_client(URL, KEY)

def obtener_configuracion_complejo(bot_phone_number: str) -> dict:
    print(f"\n🕵️‍♂️ DEBUG: Buscando complejo con número exacto: '{bot_phone_number}'")
    try:
        response = supabase.table('complexes').select('*, settings(*)').eq('whatsapp_number', bot_phone_number).execute()
        
        print(f"📦 DEBUG: Respuesta cruda de Supabase: {response}")
        
        if response.data:
            print("✅ DEBUG: ¡Complejo encontrado!")
            return response.data[0]
        else:
            print("⚠️ DEBUG: Supabase respondió con éxito, pero la lista está vacía (Data=[]).")
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
        res = supabase.table('turnos').select('cancha_nombre, cliente_nombre, telefono_cliente').eq('id', turno_id).execute()
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