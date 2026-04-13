from flask import Flask, request, jsonify

app = Flask(__name__)
PEERS = set()

@app.route("/join", methods=["POST"])
def join():
    ip = request.json.get("ip")
    port = request.json.get("port")

    PEERS.add((ip, port))

    return jsonify({
        "peers": list(PEERS)
    })

@app.route("/")
def home():
    return "Bootstrap activo"

app.run(host="0.0.0.0", port=10000)