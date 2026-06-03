import re
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
import asyncio
from typing import Optional


from database import obtener_configuracion_complejo, liberar_turnos_vencidos, obtener_turnos_disponibles, pre_reservar_turno, confirmar_turno, obtener_datos_turno, rechazar_turno
from memory import obtener_usuario, actualizar_estado_usuario, limpiar_usuario, guardar_dato_temporal
from bot_core import generar_respuesta

app = FastAPI()

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
        
        if accion == "APROBAR":
            confirmar_turno(turno_id)
            limpiar_usuario(telefono_jugador) # Reseteamos la memoria del jugador
            return {
                "reply": f"✅ Turno {turno_id} aprobado.",
                "notify_owner": { # Usamos este canal paralelo para avisarle al jugador
                    "phone": telefono_jugador,
                    "message": f"¡Comprobante validado! ✅ Tu turno para {datos_turno['cancha_nombre']} está confirmado oficialmente. ¡Te esperamos!"
                }
            }
        else:
            rechazar_turno(turno_id)
            limpiar_usuario(telefono_jugador)
            return {
                "reply": f"❌ Turno {turno_id} rechazado y liberado.",
                "notify_owner": {
                    "phone": telefono_jugador,
                    "message": "❌ Tu pago fue rechazado por administración. El turno ha sido liberado. Escribí 'Hola' si deseás intentar nuevamente."
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
        datos_settings = config_complejo.get("settings", {})
        settings = datos_settings[0] if isinstance(datos_settings, list) else datos_settings
        turno_id = usuario["datos_temporales"].get("turno_id")
        datos_cliente = mensaje 
        
        exito = pre_reservar_turno(turno_id, datos_cliente, telefono_cliente)
        
        if exito:
            actualizar_estado_usuario(telefono_cliente, "ESPERANDO_PAGO")
            datos_settings = config_complejo.get("settings", {})
            settings = datos_settings[0] if isinstance(datos_settings, list) else datos_settings
            precio = settings.get("price_per_hour", 25000)
            alias = settings.get("payment_alias", "NO_CONFIGURADO")
            
            respuesta = f"¡Perfecto! Ya bloqueé la cancha a tu nombre. ⏳\n\nPor favor, transferí ${precio} al alias *{alias}* y enviame la *FOTO del comprobante* por acá.\n\nTenés 15 minutos exactos."
            return {"reply": respuesta}
        else:
            limpiar_usuario(telefono_cliente)
            return {"reply": "Uy, disculpá. Alguien más reservó ese horario. 😔 Escribime 'Hola' para buscar otra opción."}

    elif estado_actual == "ESPERANDO_PAGO":
        if payload.has_media:
            turno_id = usuario["datos_temporales"].get("turno_id")
            actualizar_estado_usuario(telefono_cliente, "EN_REVISION")
            
            # Buscamos el número del dueño para alertarlo
            datos_settings = config_complejo.get("settings", {})
            settings = datos_settings[0] if isinstance(datos_settings, list) else datos_settings
            numero_dueno = settings.get("notification_number", "549297XXXXXXX@c.us") # Por defecto, si falla la BD
            
            return {
                "reply": "Recibimos tu comprobante. ⏳ Está en revisión por administración. Te confirmamos a la brevedad.",
                "notify_owner": {
                    "phone": numero_dueno,
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
        return {"reply": "Tu comprobante sigue en revisión. ⏳ Por favor, aguardá unos minutos más."}

if __name__ == "__main__":
    print("🚀 Iniciando SaaS Core en puerto 8000...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)