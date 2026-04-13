from flask import Flask, request, jsonify
import time

app = Flask(__name__)

PEERS = {}  # "ip:port" -> last_seen
MESSAGES = []
MAX_MESSAGES = 50

@app.route("/join", methods=["POST"])
def join():
    data = request.json
    # Registramos al par usando su IP pública
    peer_ip = request.remote_addr
    peer_port = data.get("port")
    PEERS[f"{peer_ip}:{peer_port}"] = time.time()
    
    # Limpieza de nodos caídos (> 60s)
    ahora = time.time()
    for p in list(PEERS.keys()):
        if ahora - PEERS[p] > 60:
            del PEERS[p]
            
    # Devolvemos la lista de todos los pares conocidos para que el cliente los guarde
    return jsonify({
        "status": "ok", 
        "peers": list(PEERS.keys()),
        "count": len(PEERS)
    })

@app.route("/send", methods=["POST"])
def send():
    data = request.json
    if not data or "id" not in data: return {"err": "invalid"}, 400
    
    if not any(m["id"] == data["id"] for m in MESSAGES):
        MESSAGES.append(data)
        if len(MESSAGES) > MAX_MESSAGES: MESSAGES.pop(0)
    return {"status": "sent"}

@app.route("/get")
def get_messages():
    return jsonify({"messages": MESSAGES})

@app.route("/")
def index():
    return f"🟢 Relay Activo | Nodos: {len(PEERS)} | Buffer: {len(MESSAGES)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
