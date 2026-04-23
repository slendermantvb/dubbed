import os
import zlib
import base64
import time
from flask import Flask, request, jsonify
from collections import deque, defaultdict

app = Flask(__name__)

# --- CONFIGURACIÓN DE FLUJO ---
# Cada ruta es una red independiente en RAM
# maxlen evita que la memoria sature si una red no 'limpia' sus datos
MAX_PACKETS_PER_ROUTE = 500 
TRAFFIC_BUS = defaultdict(lambda: deque(maxlen=MAX_PACKETS_PER_ROUTE))

# Seguridad: Cambia esto en las variables de entorno de Render
ACCESS_TOKEN = os.environ.get("RELAY_KEY", "SINGULARIDAD_2024_KEY")

def is_authorized():
    return request.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"

@app.route("/")
def info():
    """Monitor de pulso del relay"""
    return {
        "engine": "Singularidad-Relay-V3",
        "mode": "Zero-Persistence / Pure-Transport",
        "active_networks": len(TRAFFIC_BUS),
        "status": "online"
    }, 200

@app.route("/push/<route>", methods=["POST"])
def push(route):
    """Recibe bytes crudos, los comprime y los pone en la tubería"""
    if not is_authorized(): return "Unauthorized", 401
    
    # Captura de flujo binario (cifrado o plano)
    raw_payload = request.get_data()
    if not raw_payload: return "empty", 400

    try:
        # Compresión rápida (Balance CPU/Ancho de banda)
        compressed = zlib.compress(raw_payload, 5)
        encoded = base64.b64encode(compressed).decode('utf-8')
        
        # Inserción en la cola de la red específica
        TRAFFIC_BUS[route].append({
            "d": encoded,
            "t": time.time()
        })
        return "1", 200
    except:
        return "error", 500

@app.route("/pull/<route>")
def pull(route):
    """Extrae todo el tráfico de una ruta y lo ELIMINA del servidor"""
    if not is_authorized(): return "Unauthorized", 401
    
    if route not in TRAFFIC_BUS or len(TRAFFIC_BUS[route]) == 0:
        return jsonify([]), 200

    # DRAIN: Sacamos los datos y limpiamos la memoria en un solo paso
    packets = list(TRAFFIC_BUS[route])
    TRAFFIC_BUS[route].clear() 
    
    return jsonify(packets), 200

@app.route("/flush/<route>")
def flush(route):
    """Cierre de emergencia de una red"""
    if not is_authorized(): return "Unauthorized", 401
    TRAFFIC_BUS.pop(route, None)
    return "flushed", 200

if __name__ == "__main__":
    # Configuración optimizada para Render y entornos de alta concurrencia
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)
