import os
import time
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types
from dotenv import load_dotenv

from database import obtener_turnos_disponibles

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
# Inicializamos el cliente con el nuevo SDK oficial
client = genai.Client(api_key=API_KEY)

def generar_prompt_maestro(config: dict, texto_turnos: str) -> str:
    # Extraemos los datos del nuevo esquema SaaS
    nombre_complejo = config.get("nombre_negocio", "Complejo Deportivo")
    
    settings = config.get("settings", {})
    
    alias = settings.get("alias_pago", "NO CONFIGURADO")
    cbu = settings.get("cbu_pago", "NO CONFIGURADO")
    titular = settings.get("titular_pago", "TITULAR")
    tipo_cobro = settings.get("tipo_cobro", "TOTAL")
    ubicacion = settings.get("ubicacion", "Dirección no especificada, pedile al cliente que aguarde un momento para consultarlo.") # <-- NUEVA LÍNEA
    
    # Armamos el inventario dinámico de canchas y precios
    canchas = config.get("canchas_inventario", [])
    texto_canchas = ""
    if canchas:
        for c in canchas:
            texto_canchas += f"- {c['nombre']} ({c['tipo']}): ${c['precio']} por hora.\n"
    else:
        texto_canchas = "- (Aún no hay canchas configuradas)"
        
    # --- INICIO DEL ANCLA TEMPORAL ---
    zona_argentina = timezone(timedelta(hours=-3))
    ahora = datetime.now(zona_argentina)
    dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    
    fecha_hoy = ahora.strftime("%d/%m/%Y")
    hora_hoy = ahora.strftime("%H:%M hs")
    dia_semana = dias[ahora.weekday()]
    # --- FIN DEL ANCLA TEMPORAL ---
    
    prompt = f"""
Sos el asistente virtual exclusivo por WhatsApp del complejo deportivo "{nombre_complejo}".

[CONTEXTO TEMPORAL]
- Hoy es: {dia_semana}, {fecha_hoy}
- Hora actual: {hora_hoy}

[UBICACIÓN Y PRECIOS]
- Dirección del complejo: {ubicacion}
{texto_canchas}

[INVENTARIO EN TIEMPO REAL]
Solo podés ofrecer los horarios que aparezcan en la siguiente lista. NUNCA inventes horarios:
{texto_turnos}

[DICCIONARIO DE FRANJAS HORARIAS (USO INTERNO)]
Filtros estrictos para entender al cliente:
- "Mañana": Desde la apertura hasta las 12:59 hs.
- "Mediodía" / "Siesta": Desde las 13:00 hs hasta las 16:59 hs.
- "Tarde": Desde las 17:00 hs hasta las 19:59 hs.
- "Noche": Desde las 20:00 hs hasta el horario de cierre.
⚠️ REGLA ESTRICTA: NUNCA le menciones al cliente cuáles son los topes de estas franjas.

[MANEJO DE HORARIOS DUPLICADOS (MÚLTIPLES CANCHAS)]
Si en tu inventario ves que hay más de una cancha libre a la misma hora (ej: Cancha 1 a las 14:00 y Cancha 2 a las 14:00):
1. NO repitas el horario en tu mensaje. Mencioná que las 14:00 hs está disponible una sola vez.
2. Cuando el cliente confirme ese horario, elegí AL AZAR el ID de cualquiera de las canchas que estaban libres a esa hora para generar el comando [RESERVAR_ID: X].

[REGLA DE BIENVENIDA - PRIMER MENSAJE]
Si estás respondiendo al PRIMER mensaje del usuario en la conversación, TU RESPUESTA DEBE EMPEZAR SIEMPRE diciendo:
"Hola 👋 Soy el asistente virtual de {nombre_complejo}."

Luego, analizá qué te dijo el usuario y elegí UNA de estas dos opciones:
- OPCIÓN A (El usuario solo saludó, ej: "Hola"): 
  Continuá diciendo: "¿En qué te puedo ayudar hoy? Podés consultarme por:\n⚽ Turnos disponibles\n📍 Ubicación\n⏱️ Horarios y precios"
- OPCIÓN B (El usuario saludó y ya te pidió algo, ej: "Hola, tenés a la noche?"): 
  Respondé DIRECTAMENTE a su consulta.

[PRESENTACIÓN DE HORARIOS - ANTI SPAM]
NUNCA envíes una lista de más de 4 horarios seguidos.
- Si hay más de 5 horarios disponibles: Resumí la disponibilidad y preguntale en qué franja horaria prefiere jugar (Mañana, siesta, tarde o noche).
- Si el cliente ya pidió una franja y hay muchos turnos, dale solo 3 opciones espaciadas y aclarale que tenés más horarios.

[REGLAS DE COMPORTAMIENTO Y TONO]
1. Tono: Sé rápido, servicial y amable. Usá el español argentino (vos, tenés, querés), pero NUNCA uses jergas baratas (nada de "che", "capo").
2. Límite: Tu único trabajo es gestionar reservas.
3. Ocultamiento Técnico: El cliente NUNCA debe leer la palabra "ID".
4. Ocultamiento de Cancha: NUNCA le digas al cliente el nombre de la cancha (ej: "Cancha 1", "Sintético"). Al cliente no le importa el número de cancha, solo quiere saber la hora. Hablá en plural general (Ej: "Tenemos disponibles los siguientes horarios:").

[PASOS DE VENTA ESTRICTOS PARA RESERVAR]
Cuando el cliente quiera reservar una cancha, seguí este orden sin saltearte nada:
PASO 1: Ofrece las opciones disponibles (aplicando ANTI SPAM, filtros de FRANJA HORARIA, agrupando HORARIOS DUPLICADOS y SIN MENCIONAR el nombre de la cancha).
PASO 2: Cuando el cliente confirme la hora, decile que la vas a reservar, pedile su Nombre y Apellido, e INYECTA ESTRICTAMENTE al final de tu mensaje: [RESERVAR_ID: X] (Cambiá la X por el ID de ese turno).
PASO 3: Detente ahí. NUNCA hables de pagos o transferencias. Tu trabajo termina al pedir el nombre.
"""
    return prompt

def generar_respuesta(mensaje_usuario: str, historial_previo: list, config_complejo: dict) -> str:
    complex_id = config_complejo.get("id")
    
    # 1. Leemos los turnos reales de Supabase
    turnos_libres = obtener_turnos_disponibles(complex_id)
    if not turnos_libres:
        texto_turnos = "Actualmente no hay turnos disponibles. Informale esto al cliente con amabilidad."
    else:
        texto_turnos = ""
        # Definimos la zona horaria de Argentina (UTC-3)
        zona_argentina = timezone(timedelta(hours=-3))
        
        for t in turnos_libres:
            # Parseamos la fecha UTC de Supabase (reemplazamos 'Z' por '+00:00' para evitar errores de parseo)
            fecha_utc = datetime.fromisoformat(t['fecha_hora'].replace('Z', '+00:00'))
            # La convertimos a hora local Argentina
            fecha_ar = fecha_utc.astimezone(zona_argentina)
            
            # Formateamos a un texto limpio y sin ambigüedades
            fecha_formateada = fecha_ar.strftime("%d/%m/%Y a las %H:%M hs")
            
            # Le inyectamos el ID exacto de la base de datos para que la IA lo conozca
            texto_turnos += f"- ID: {t['id']} | Cancha: {t['cancha_nombre']} | Horario: {fecha_formateada}\n"
    
    # 2. Generamos el System Prompt inyectándole el inventario fresco
    prompt_dinamico = generar_prompt_maestro(config_complejo, texto_turnos)
    
    # 3. Armamos el historial compatible con el nuevo SDK de google.genai
    messages = []
    for msg in historial_previo:
        rol_gemini = "user" if msg["role"] == "user" else "model"
        messages.append(types.Content(role=rol_gemini, parts=[types.Part.from_text(text=msg["content"])]))
        
    messages.append(types.Content(role="user", parts=[types.Part.from_text(text=mensaje_usuario)]))
    
    # 4. Escudo Anti-Saturación (Llamada a la API con 3 intentos)
    max_intentos = 3
    intentos = 0
    
    while intentos < max_intentos:
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=messages,
                config=types.GenerateContentConfig(
                    system_instruction=prompt_dinamico,
                    temperature=0.3
                )
            )
            return response.text
        except Exception as e:
            intentos += 1
            print(f"⚠️ [GEMINI] Error en intento {intentos}/{max_intentos}: {e}")
            if intentos >= max_intentos:
                return "Disculpá, el sistema está procesando muchas reservas en este instante. ¿Me repetís tu último mensaje por favor? ⏳"
            time.sleep(2) # Pausa de 2 segundos antes de volver a intentar