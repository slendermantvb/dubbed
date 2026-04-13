from flask import Flask, request, jsonify
import os
import time

app = Flask(__name__)

PEERS = {}
TIMEOUT = 60  # segundos

# ----------- LIMPIAR PEERS -----------
def limpiar():
    ahora = time.time()
    muertos = []

    for peer, t in PEERS.items():
        if ahora - t > TIMEOUT:
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
    PEERS[peer] = time.time()

    limpiar()

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

    return {"ok": True}

# ----------- VER PEERS -----------
@app.route("/peers")
def peers():
    limpiar()
    return jsonify(list(PEERS.keys()))

# ----------- HOME -----------
@app.route("/")
def home():
    return f"🟢 bootstrap activo | peers: {len(PEERS)}"

# ----------- RUN -----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
