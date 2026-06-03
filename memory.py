usuarios_estado = {}

def obtener_usuario(telefono: str) -> dict:
    if telefono not in usuarios_estado:
        # Ahora la memoria incluye el estado del embudo y un cajón temporal de datos
        usuarios_estado[telefono] = {
            "estado": "BUSCANDO_TURNO", 
            "historial": [],
            "datos_temporales": {}
        }
    return usuarios_estado[telefono]

def actualizar_estado_usuario(telefono: str, nuevo_estado: str):
    if telefono in usuarios_estado:
        usuarios_estado[telefono]["estado"] = nuevo_estado

def guardar_dato_temporal(telefono: str, clave: str, valor):
    """Guarda información en tránsito (ej: el ID del turno elegido)."""
    if telefono in usuarios_estado:
        usuarios_estado[telefono]["datos_temporales"][clave] = valor

def limpiar_usuario(telefono: str):
    """Resetea al usuario cuando termina o cancela una compra."""
    if telefono in usuarios_estado:
        usuarios_estado[telefono] = {
            "estado": "BUSCANDO_TURNO", 
            "historial": [],
            "datos_temporales": {}
        }