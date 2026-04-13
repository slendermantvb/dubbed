from flask import Flask, request, jsonify
import os
import time

app = Flask(__name__)

# peers: { (ip, port): last_seen }
PEERS = {}

TIMEOUT = 60  # segundos (vida del nodo)

# ----------- LIMPIAR NODOS MUERTOS -----------
def limpiar_peers():
    ahora = time.time()
    muertos = []

    for peer, last_seen in PEERS.items():
        if ahora - last_seen > TIMEOUT:
            muertos.append(peer)

    for m in muertos:
        del PEERS[m]

# ----------- JOIN -----------
@app.route("/join", methods=["POST"])
def join():
    data = request.json

    ip = data.get("ip")
    port = data.get("port")

    if not ip or not port:
        return {"error": "faltan datos"}, 400

    peer = (ip, int(port))

    # actualizar timestamp
    PEERS[peer] = time.time()

    limpiar_peers()

    return jsonify({
        "peers": list(PEERS.keys())
    })

# ----------- HEARTBEAT -----------
@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    data = request.json

    ip = data.get("ip")
    port = data.get("port")

    peer = (ip, int(port))

    if peer in PEERS:
        PEERS[peer] = time.time()

    return {"status": "ok"}

# ----------- VER PEERS -----------
@app.route("/peers")
def peers():
    limpiar_peers()
    return jsonify(list(PEERS.keys()))

# ----------- HOME -----------
@app.route("/")
def home():
    return f"🟢 bootstrap activo | peers: {len(PEERS)}"

# ----------- RUN -----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
