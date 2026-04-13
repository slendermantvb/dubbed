from flask import Flask, request, jsonify
import time

app = Flask(__name__)

PEERS = {}  # (ip, port) -> last_seen
TIMEOUT = 60

# -------- VALIDACIÓN --------

def validar_ip(ip):
    try:
        parts = ip.split(".")
        return len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
    except:
        return False

def validar_port(port):
    return isinstance(port, int) and 0 < port < 65536

# -------- LIMPIEZA --------

def limpiar():
    ahora = time.time()
    muertos = []

    for peer, t in PEERS.items():
        if ahora - t > TIMEOUT:
            muertos.append(peer)

    for m in muertos:
        del PEERS[m]

# -------- JOIN --------

@app.route("/join", methods=["POST"])
def join():
    try:
        data = request.json
        ip = data.get("ip")
        port = data.get("port")

        if not validar_ip(ip) or not validar_port(port):
            return {"error": "datos inválidos"}, 400

        peer = (ip, int(port))
        PEERS[peer] = time.time()

        limpiar()

        return jsonify({
            "peers": list(PEERS.keys())
        })

    except Exception as e:
        return {"error": str(e)}, 500

# -------- HEARTBEAT --------

@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    try:
        data = request.json
        ip = data.get("ip")
        port = data.get("port")

        peer = (ip, int(port))

        if peer in PEERS:
            PEERS[peer] = time.time()

        return {"ok": True}

    except:
        return {"error": "fail"}, 500

# -------- GET PEERS --------

@app.route("/peers")
def peers():
    limpiar()
    return jsonify(list(PEERS.keys()))

# -------- HOME --------

@app.route("/")
def home():
    return f"🟢 Bootstrap activo | peers: {len(PEERS)}"
