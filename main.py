import os
import zlib
import base64
import time
from flask import Flask, request, jsonify
from collections import deque, defaultdict

app = Flask(__name__)

# --- CONFIGURACIÓN DE FLUJO ---
# maxlen evita que la memoria sature si una red no 'limpia' sus datos
MAX_PACKETS_PER_ROUTE = 500 
TRAFFIC_BUS = defaultdict(lambda: deque(maxlen=MAX_PACKETS_PER_ROUTE))

# Seguridad: Se recomienda configurar 'RELAY_KEY' en las variables de entorno
ACCESS_TOKEN = os.environ.get("RELAY_KEY", "SINGULARIDAD_2024_KEY")

def is_authorized():
    """Verifica el token de acceso en la cabecera Authorization"""
    return request.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"

@app.route("/")
def info():
    """Monitor de estado del relay"""
    return {
        "engine": "Singularidad-Relay-V3",
        "mode": "Zero-Persistence / Pure-Transport",
        "active_networks": len(TRAFFIC_BUS),
        "status": "online",
        "timestamp": time.time()
    }, 200

@app.route("/push/<route>", methods=["POST"])
def push(route):
    """
    Recibe bytes, los comprime y los encola.
    Uso: POST /push/mi_canal con Header 'Authorization: Bearer <KEY>'
    """
    if not is_authorized(): 
        return "Unauthorized", 401
    
    raw_payload = request.get_data()
    if not raw_payload: 
        return "empty", 400

    try:
        # Compresión nivel 5 (balance velocidad/tamaño)
        compressed = zlib.compress(raw_payload, 5)
        encoded = base64.b64encode(compressed).decode('utf-8')
        
        TRAFFIC_BUS[route].append({
            "d": encoded,
            "t": time.time()
        })
        return "1", 200
    except Exception as e:
        return f"error: {str(e)}", 500

@app.route("/pull/<route>")
def pull(route):
    """
    Extrae el tráfico y lo borra de la memoria (Destrucción tras lectura).
    """
    if not is_authorized(): 
        return "Unauthorized", 401
    
    if route not in TRAFFIC_BUS or len(TRAFFIC_BUS[route]) == 0:
        return jsonify([]), 200

    # DRAIN: Extrae los datos y limpia la cola en un solo paso
    packets = list(TRAFFIC_BUS[route])
    TRAFFIC_BUS[route].clear() 
    
    return jsonify(packets), 200

@app.route("/flush/<route>")
def flush(route):
    """Cierre de emergencia y limpieza total de una ruta específica"""
    if not is_authorized(): 
        return "Unauthorized", 401
    
    TRAFFIC_BUS.pop(route, None)
    return "flushed", 200

if __name__ == "__main__":
    # El puerto 10000 es el estándar para servicios web en Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)
