import os
import google.generativeai as genai
from dotenv import load_dotenv

# Cargamos tu clave desde el .env
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

print("🔍 Buscando modelos disponibles para Generación de Texto...\n")

# Iteramos sobre todos los modelos que nos devuelve la API
for m in genai.list_models():
    # Filtramos solo los que sirven para chatear/generar contenido
    if 'generateContent' in m.supported_generation_methods:
        print(f"✅ {m.name}")