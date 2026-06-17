import re
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
import asyncio
from typing import Optional

from database import *
from memory import obtener_usuario, actualizar_estado_usuario, limpiar_usuario, guardar_dato_temporal
from bot_core import generar_respuesta
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# --- INICIO DEL ESCUDO CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ortigoza-apps.duckdns.org", 
        "http://localhost:8000", # Dejamos este por si necesitas testear algo localmente
        "http://127.0.0.1:8000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- FIN DEL ESCUDO CORS ---

# --- INICIO DEL WORKER: CRONÓMETRO DE 15 MINUTOS ---
async def verificar_turnos_vencidos():
    """Bucle infinito que revisa turnos vencidos sin congelar el servidor."""
    while True:
        try:
            # Llama a la función que anula los turnos pre-reservados hace más de 15 min
            liberar_turnos_vencidos()
            # print("⏱️ [WORKER] Revisión de turnos completada.") # Descomentar para debug
        except Exception as e:
            print(f"❌ [WORKER] Error verificando turnos: {e}")
        
        # Pausa el worker por 60 segundos antes de volver a revisar
        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    """Se ejecuta una sola vez cuando arranca FastAPI."""
    print("⚙️ [SISTEMA] Arrancando Cronómetro de Liberación en segundo plano...")
    asyncio.create_task(verificar_turnos_vencidos())
# --- FIN DEL WORKER ---

class WebhookPayload(BaseModel):
    bot_phone_number: str
    phone_number: str
    message_body: str
    has_media: bool
    media_data: Optional[str] = None
    mime_type: Optional[str] = None

# Agregá esto cerca de donde definiste tus otros modelos Pydantic
class NuevoCliente(BaseModel):
    nombre: str
    email: str
    password: str
    token_maestro: str

# Tu llave secreta (Cambiá esto por una contraseña difícil que solo vos sepas)
TOKEN_SUPERADMIN = "surya2016" 

class LoginCliente(BaseModel):
    email: str
    password: str

class NuevaCancha(BaseModel):
    nombre: str
    tipo: str
    precio: int

@app.post("/api/login")
async def login_cliente(datos: LoginCliente):
    """Endpoint para que los dueños de los complejos inicien sesión."""
    complejo = autenticar_complejo(datos.email, datos.password)
    
    if complejo:
        return {
            "mensaje": "✅ Acceso concedido.", 
            "complejo_id": complejo["id"], 
            "nombre": complejo["nombre"]
        }
    else:
        return {"error": "Email o contraseña incorrectos."}

@app.post("/api/superadmin/clientes")
async def registrar_cliente(datos: NuevoCliente):
    """Endpoint exclusivo para que el Superadmin registre nuevos negocios."""
    if datos.token_maestro != TOKEN_SUPERADMIN:
        return {"error": "Acceso denegado. Token maestro inválido."}
        
    exito = crear_complejo(datos.nombre, datos.email, datos.password)
    
    if exito:
        return {"mensaje": f"✅ Negocio '{datos.nombre}' registrado correctamente."}
    else:
        return {"error": "Hubo un error al registrar el cliente en Supabase."}

@app.get("/api/superadmin/clientes")
async def listar_clientes(token_maestro: str):
    """Endpoint para cargar la tabla principal del Superadmin."""
    if token_maestro != TOKEN_SUPERADMIN:
        return {"error": "Acceso denegado. Token maestro inválido."}
        
    clientes = obtener_todos_los_complejos()
    return {"clientes": clientes}

@app.post("/webhook")
async def handle_whatsapp_message(payload: WebhookPayload):
    telefono_cliente = payload.phone_number
    numero_bot = payload.bot_phone_number
    mensaje = payload.message_body
    
    config_complejo = obtener_configuracion_complejo(numero_bot)
    if not config_complejo:
        return {"reply": "Disculpe, este número de bot no está registrado en el sistema."}

    # ==========================================
    # INTERCEPTOR DE COMANDOS DEL DUEÑO
    # ==========================================
    # Escanea si el mensaje es exactamente "APROBAR X" o "RECHAZAR X"
    match_comando = re.match(r'(?i)^\s*(aprobar|rechazar)\s+(\d+)\s*$', mensaje)
    
    if match_comando:
        accion = match_comando.group(1).upper()
        turno_id = int(match_comando.group(2))
        
        datos_turno = obtener_datos_turno(turno_id)
        if not datos_turno:
            return {"reply": f"⚠️ No encontré el turno ID {turno_id} en la base de datos."}
            
        telefono_jugador = datos_turno['telefono_cliente']
        
        # 🛡️ Pasamos el ID tal cual está en la base de datos (sea @c.us o @lid)
        telefono_limpio = str(telefono_jugador).strip()
        
        if accion == "APROBAR":
            confirmar_turno(turno_id)
            limpiar_usuario(telefono_jugador) 
            
            # 1. Extraemos los datos dinámicos del complejo
            settings = config_complejo.get("settings", {})
            ubicacion = settings.get("ubicacion", "Dirección no especificada")
            nombre_complejo = config_complejo.get("nombre_negocio", "Nuestro Complejo")
            nombre_jugador = datos_turno.get("cliente_nombre", "Jugador")
            cancha = datos_turno.get("cancha_nombre", "Cancha")
            
            # 2. Parseamos la fecha a Hora Argentina
            from datetime import datetime, timezone, timedelta
            zona_argentina = timezone(timedelta(hours=-3))
            try:
                # Reemplazamos la Z de Supabase para evitar errores de parseo
                fecha_utc = datetime.fromisoformat(datos_turno['fecha_hora'].replace('Z', '+00:00'))
                fecha_ar = fecha_utc.astimezone(zona_argentina)
                fecha_str = fecha_ar.strftime("%d/%m/%Y")
                hora_str = fecha_ar.strftime("%H:%M hs")
            except:
                fecha_str = "Fecha confirmada"
                hora_str = "Horario confirmado"
                
            # 3. Armamos el diseño del ticket
            ticket_msg = f"""✅ *¡TURNO CONFIRMADO!* ✅

Hola {nombre_jugador}, tu pago ha sido aprobado exitosamente. Acá tenés tu ticket de acceso:

🏟️ *Complejo:* {nombre_complejo}
⚽ *Cancha:* {cancha}
📅 *Fecha:* {fecha_str}
⏰ *Horario:* {hora_str}
📍 *Ubicación:* {ubicacion}
🎫 *Ticket ID:* #{turno_id}

¡Te esperamos! Llevá este mensaje por cualquier eventualidad."""

            return {
                "reply": f"✅ Turno {turno_id} aprobado exitosamente. Se le envió el ticket al cliente.",
                "notify_owner": { 
                    # Al ser de tipo TEXT en la base de datos, el teléfono viaja perfecto sin decimales
                    "phones": [telefono_jugador], 
                    "message": ticket_msg
                }
            }
        else:
            rechazar_turno(turno_id)
            limpiar_usuario(telefono_jugador)
            return {
                "reply": f"❌ Turno {turno_id} rechazado y liberado.",
                "notify_owner": {
                    "phones": [telefono_jugador],
                    "message": f"❌ Hola, te informamos que tu pago para el turno de {datos_turno['cancha_nombre']} no pudo ser validado y el turno fue liberado.\n\nPor favor, escribí 'Hola' para intentar generar una nueva reserva."
                }
            }

    # ==========================================
    # MÁQUINA DE ESTADOS: EL EMBUDO DE VENTAS
    # ==========================================
    usuario = obtener_usuario(telefono_cliente)
    estado_actual = usuario.get("estado", "BUSCANDO_TURNO")
    
    if estado_actual == "BUSCANDO_TURNO":
        respuesta_ia = generar_respuesta(mensaje, usuario["historial"], config_complejo)
        match = re.search(r'\[RESERVAR_ID:\s*(\d+)\]', respuesta_ia)
        
        if match:
            turno_id = int(match.group(1))
            respuesta_limpia = re.sub(r'\[RESERVAR_ID:\s*\d+\]', '', respuesta_ia).strip()
            guardar_dato_temporal(telefono_cliente, "turno_id", turno_id)
            actualizar_estado_usuario(telefono_cliente, "ESPERANDO_DATOS")
            
            usuario["historial"].extend([
                {"role": "user", "content": mensaje},
                {"role": "model", "content": respuesta_limpia}
            ])
            return {"reply": respuesta_limpia}
        
        usuario["historial"].extend([{"role": "user", "content": mensaje}, {"role": "model", "content": respuesta_ia}])
        return {"reply": respuesta_ia}

    elif estado_actual == "ESPERANDO_DATOS":
        turno_id = usuario["datos_temporales"].get("turno_id")
        datos_cliente = mensaje 
        
        exito = pre_reservar_turno(turno_id, datos_cliente, telefono_cliente)
        
        if exito:
            actualizar_estado_usuario(telefono_cliente, "ESPERANDO_PAGO")
            
            # Buscamos el precio real de la cancha en la BD
            precio_cancha = obtener_precio_por_turno(turno_id)
            
            # Leemos las reglas de cobro del panel web
            settings = config_complejo.get("settings", {})
            alias = settings.get("alias_pago", "NO_CONFIGURADO")
            titular = settings.get("titular_pago", "NO_CONFIGURADO") # Agregamos la lectura del titular
            cbu = settings.get("cbu_pago", "NO_CONFIGURADO")         # Agregamos la lectura del CBU
            tolerancia = settings.get("minutos_tolerancia_pago", 15)
            tipo_cobro = settings.get("tipo_cobro", "TOTAL")
            monto_sena = settings.get("monto_sena", 0)
            
            # Lógica matemática del mensaje
            if tipo_cobro == "SENA" and monto_sena > 0:
                texto_cobro = f"la seña de *${monto_sena}*"
            else:
                texto_cobro = f"el total de *${precio_cancha}*"
            
            # Armamos el nuevo mensaje estructurado
            respuesta = f"""¡Perfecto! Ya bloqueé la cancha a tu nombre. ⏳

El valor total de la cancha es de *${precio_cancha}*.
Por favor, transferí {texto_cobro} al alias *{alias}* y enviame la *FOTO del comprobante* por acá.

💳 *Nombre del titular de la cuenta:* {titular}
🏦 *CBU/CVU:* {cbu}

Tenés {tolerancia} minutos exactos antes de que el sistema libere el turno automáticamente. ¡Muchas gracias por elegirnos!"""
            
            return {"reply": respuesta}
        else:
            limpiar_usuario(telefono_cliente)
            return {"reply": "Uy, disculpá. Alguien más reservó ese horario recién. 😔 Escribime 'Hola' para buscar otra opción."}

    elif estado_actual == "ESPERANDO_PAGO":
        if payload.has_media:
            turno_id = usuario["datos_temporales"].get("turno_id")
            actualizar_estado_usuario(telefono_cliente, "EN_REVISION")
            
            settings = config_complejo.get("settings", {})
            numeros_alerta = settings.get("numeros_alerta", [])
            
            # Si el dueño aún no configuró números, mandamos al tuyo por defecto
            if not numeros_alerta:
                numeros_alerta = ["5492975949503"]
                
            numeros_formateados = []
            for num in numeros_alerta:
                num_str = str(num).strip()
                if "@" not in num_str:
                    num_str = f"{num_str}@c.us"
                numeros_formateados.append(num_str)
            
            return {
                "reply": "Recibimos tu comprobante. ⏳ Está en revisión por administración. Te confirmamos a la brevedad.",
                "notify_owner": {
                    "phones": numeros_formateados, # Pasamos la lista limpia
                    "message": f"🚨 *NUEVA SEÑA RECIBIDA* 🚨\n\n*Turno ID:* {turno_id}\n*Teléfono:* {telefono_cliente}\n\nPara aprobar respondé:\n*APROBAR {turno_id}*\n\nPara rechazar respondé:\n*RECHAZAR {turno_id}*",
                    "media_data": payload.media_data,
                    "mime_type": payload.mime_type
                }
            }
        else:
            if mensaje.strip().lower() == "cancelar":
                limpiar_usuario(telefono_cliente)
                return {"reply": "Reserva cancelada. Escribí 'Hola' para buscar de nuevo."}
            return {"reply": "Por favor, enviame la *foto del comprobante* para confirmar el turno. (O escribí 'cancelar')."}

    elif estado_actual == "EN_REVISION":
        mensaje_limpio = mensaje.strip().upper()
        
        # 1. La Válvula de Escape
        if mensaje_limpio == "CANCELAR":
            # Rescatamos el ID del turno de la memoria temporal
            turno_id = usuario.get("datos_temporales", {}).get("turno_id")
            
            if turno_id:
                # Liberamos la cancha en la base de datos
                cambiar_estado_turno_manual(turno_id, "disponible")
                
            limpiar_usuario(telefono_cliente) # Reseteamos la memoria del bot
            
            return {"reply": "✅ Cancelamos tu reserva anterior y liberamos la cancha.\n\nEscribime qué estás buscando ahora (Ej: '¿Qué turnos hay para hoy a las 14?')."}
            
        # 2. El Mensaje Instructivo
        else:
            return {"reply": "⏳ Tu comprobante sigue en revisión por administración.\n\n⚠️ *Si querés buscar otro horario o te equivocaste, escribí la palabra CANCELAR para anular tu solicitud actual.*"}

@app.get("/api/complejos/{complejo_id}/settings")
async def get_settings(complejo_id: int):
    """Devuelve las configuraciones al frontend para rellenar los formularios."""
    settings = obtener_settings_complejo(complejo_id)
    return {"settings": settings}

@app.put("/api/complejos/{complejo_id}/settings")
async def update_settings(complejo_id: int, datos: dict):
    """Recibe datos desde el panel web y actualiza el JSON."""
    exito = actualizar_settings_complejo(complejo_id, datos)
    if exito:
        return {"mensaje": "✅ Configuraciones guardadas exitosamente."}
    return {"error": "Error al guardar en la base de datos."}

@app.put("/api/complejos/{complejo_id}/vincular_whatsapp")
async def vincular_whatsapp_complejo(complejo_id: int, datos: dict):
    """Guarda automáticamente en Supabase el número de WhatsApp que el cliente acaba de escanear."""
    numero = datos.get("numero_whatsapp")
    if not numero:
        return {"error": "Falta el número de WhatsApp en la solicitud."}
        
    try:
        # Aprovechamos el objeto 'supabase' importado globalmente
        supabase.table('complejos').update({"numero_whatsapp": numero}).eq('id', complejo_id).execute()
        print(f"💾 [BACKEND] Complejo ID {complejo_id} vinculado con éxito al WhatsApp: {numero}")
        return {"mensaje": "✅ Número de WhatsApp vinculado correctamente en la base de datos."}
    except Exception as e:
        print(f"❌ [BACKEND] Error vinculando WhatsApp: {e}")
        return {"error": f"No se pudo actualizar la base de datos: {str(e)}"}

@app.get("/api/complejos/{complejo_id}/canchas")
async def listar_canchas(complejo_id: int):
    """Devuelve la lista de canchas al panel web del dueño."""
    canchas = obtener_canchas_complejo(complejo_id)
    return {"canchas": canchas}

@app.post("/api/complejos/{complejo_id}/generar_agenda")
async def generar_agenda_api(complejo_id: int):
    """Endpoint para que el dueño genere su bloque de turnos semanales."""
    # Por defecto genera turnos para los próximos 7 días
    resultado = generar_agenda_complejo(complejo_id, dias_a_generar=7)
    
    if "error" in resultado:
        return {"error": resultado["error"]}
    return {"mensaje": resultado["mensaje"]}

# Definimos el modelo para recibir el nuevo estado
class ActualizarEstadoTurno(BaseModel):
    estado: str

@app.get("/api/complejos/{complejo_id}/turnos_hoy")
async def get_turnos_hoy_api(complejo_id: int):
    """Devuelve los turnos de las próximas 48hs al panel del cliente."""
    turnos = obtener_turnos_hoy(complejo_id)
    return {"turnos": turnos}

@app.put("/api/turnos/{turno_id}/estado")
async def cambiar_estado_api(turno_id: int, datos: ActualizarEstadoTurno):
    """Recibe la orden del panel para bloquear o liberar un turno."""
    exito = cambiar_estado_turno_manual(turno_id, datos.estado)
    if exito:
        return {"mensaje": "✅ Estado actualizado correctamente."}
    return {"error": "Error al intentar actualizar el turno."}

@app.post("/api/complejos/{complejo_id}/canchas")
async def agregar_cancha(complejo_id: int, datos: NuevaCancha):
    """Recibe los datos del panel web y guarda la nueva cancha."""
    exito = crear_cancha(complejo_id, datos.nombre, datos.tipo, datos.precio)
    if exito:
        return {"mensaje": f"✅ Cancha '{datos.nombre}' agregada al inventario."}
    return {"error": "Error al guardar la cancha en la base de datos."}

@app.put("/api/canchas/{cancha_id}")
async def editar_cancha_api(cancha_id: int, datos: NuevaCancha):
    """Recibe los datos corregidos y los guarda en la base de datos."""
    exito = actualizar_cancha(cancha_id, datos.nombre, datos.tipo, datos.precio)
    if exito:
        return {"mensaje": "✅ Cancha actualizada correctamente."}
    return {"error": "Error al actualizar la cancha."}

@app.delete("/api/canchas/{cancha_id}")
async def borrar_cancha_api(cancha_id: int):
    """Elimina una cancha y hace una limpieza en cascada de sus turnos vacíos."""
    try:
        # 1. Buscamos los datos de la cancha ANTES de borrarla para saber su nombre
        res_cancha = supabase.table('canchas').select('nombre, complejo_id').eq('id', cancha_id).execute()
        
        if res_cancha.data:
            nombre_cancha = res_cancha.data[0]['nombre']
            complejo_id = res_cancha.data[0]['complejo_id']
            
            # 2. LIMPIEZA EN CASCADA SEGURA: Borramos solo los turnos que nadie compró aún
            supabase.table('turnos') \
                .delete() \
                .eq('complex_id', complejo_id) \
                .eq('cancha_nombre', nombre_cancha) \
                .eq('estado', 'disponible') \
                .execute()
            
            # 3. Finalmente, eliminamos la cancha del sistema
            supabase.table('canchas').delete().eq('id', cancha_id).execute()
            
            print(f"🗑️ [BACKEND] Cancha '{nombre_cancha}' eliminada junto con sus turnos disponibles.")
            return {"mensaje": f"✅ La cancha y sus horarios disponibles fueron eliminados correctamente."}
        else:
            return {"error": "La cancha que intentas eliminar no existe en la base de datos."}
            
    except Exception as e:
        print(f"❌ [BACKEND] Error al eliminar cancha: {e}")
        return {"error": "Hubo un problema interno al intentar eliminar la cancha."}

if __name__ == "__main__":
    print("🚀 Iniciando SaaS Core en puerto 8000...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)