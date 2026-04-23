import os
import zlib
import base64
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURACIÓN DE ALTO RENDIMIENTO ---
# 50,000 bloques comprimidos pueden representar millones de eventos de juego/chat
TRAFFIC_STACK = []
MAX_BLOCKS = 50000 

@app.route("/")
def index():
    return {
        "engine": "Singularidad-Relay",
        "status": "online",
        "memory_usage": f"{(len(TRAFFIC_STACK) / MAX_BLOCKS) * 100:.2f}%"
    }, 200

@app.route("/push", methods=["POST"])
def push():
    """Recibe datos y los 'aplasta' instantáneamente"""
    raw_data = request.get_data()
    if not raw_data: return "0", 400
    
    try:
        # Extraer ruta desde los headers para no parsear el cuerpo (Gana velocidad)
        route = request.headers.get("route", "general")
        
        # Compresión Máxima Nivel 9
        compressed = zlib.compress(raw_data, 9)
        c = base64.b64encode(compressed).decode('utf-8')
        
        # Inserción rápida en el stack
        TRAFFIC_STACK.append({"r": route, "c": c})
        
        # Limpieza por bloques para mantener fluidez de CPU
        if len(TRAFFIC_STACK) > MAX_BLOCKS:
            TRAFFIC_STACK.pop(0)
            
        return "1", 200 # Respuesta de 1 byte para ahorrar ancho de banda
    except:
        return "error", 500

@app.route("/pull/<route>")
def pull(route):
    """Entrega los últimos 50 paquetes comprimidos de una ruta específica"""
    # Usamos reversed para que el cliente reciba lo más nuevo primero (Fluidez)
    relevant = [p["c"] for p in reversed(TRAFFIC_STACK) if p["r"] == route][:50]
    return jsonify(relevant), 200

if __name__ == "__main__":
    # Configuración optimizada para Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)
