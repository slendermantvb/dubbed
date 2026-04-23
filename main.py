import os
from flask import Flask, request, jsonify

app = Flask(__name__)

# Memoria volátil para el tráfico en tránsito (se limpia sola)
TRAFFIC_STACK = []
MAX_CAPACITY = 100

@app.route("/push", methods=["POST"])
def push_traffic():
    """Recibe CUALQUIER cosa y la pone en el stack de transporte"""
    data = request.get_json()
    if not data:
        return {"status": "empty"}, 400

    # Metadatos mínimos de transporte
    packet = {
        "origin": request.remote_addr,
        "content": data, # Aquí va el 'lo que sea'
        "route": data.get("route", "general") # Opcional para filtrar apps
    }
    
    TRAFFIC_STACK.append(packet)
    
    # Mantener el stack ligero
    if len(TRAFFIC_STACK) > MAX_CAPACITY:
        TRAFFIC_STACK.pop(0)
        
    return {"status": "relayed"}

@app.route("/pull/<route>")
def pull_traffic(route):
    """Las apps piden el tráfico filtrado por su ruta"""
    # Filtramos el tráfico que pertenezca a esa app (chat, juego, etc)
    relevant_traffic = [p for p in TRAFFIC_STACK if p["route"] == route]
    return jsonify({"data": relevant_traffic})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
