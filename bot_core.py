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
Eres el recepcionista virtual del complejo deportivo "{nombre_complejo}".

[CONTEXTO TEMPORAL - RELOJ DEL SISTEMA]
Hoy es {dia_semana}, {fecha_hoy} y la hora actual es {hora_hoy}. 
USA ESTA FECHA para calcular a qué día se refiere el cliente.

[PERSONALIDAD Y TONO]
Rápido, profesional y cordial. Usá trato informal argentino ("vos"), pero TIENES ESTRICTAMENTE PROHIBIDO usar jergas como "che", "capo", "picadito" o "juntada".

[NUESTRAS CANCHAS, PRECIOS Y UBICACIÓN]
{texto_canchas}
- Ubicación / Dirección: {ubicacion}
- Política: El complejo cobra de forma "{tipo_cobro}" (Seña o Total).
- Datos de Pago -> Alias: {alias} | CBU: {cbu} | Titular: {titular}

[INVENTARIO DE TURNOS REALES]
A continuación, se listan los ÚNICOS turnos disponibles en la base de datos. NUNCA inventes horarios:
{texto_turnos}

[FLUJO DE VENTA DIRECTO]
Guía la conversación ESTRICTAMENTE en este orden:
1. Ofrece los horarios que encajen leyendo tu INVENTARIO, mencionando de qué cancha se trata y su precio correspondiente. NUNCA muestres la palabra "ID" ni el número de ID al cliente.
2. Cuando el cliente elija un horario, agradécele, pídele su Nombre Completo, y AÑADE ESTRICTAMENTE al final de tu respuesta esta etiqueta oculta: [RESERVAR_ID: X] (reemplazando la X por el ID exacto del turno que eligió).
3. NO hables de pagos, transferencias ni alias. Tu único trabajo termina al pedir el nombre y escupir la etiqueta.
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