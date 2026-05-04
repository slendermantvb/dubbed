import os
import re
import zlib
import time
import base64
import hmac
import hashlib
import threading
from collections import deque, defaultdict
from flask import Flask, request, jsonify, g

app = Flask(__name__)

# ============================================================
# SINGULARIDAD RELAY V5 P2P BOOTSTRAP
# Zero-Persistence / Pure Transport / In-Memory Relay
# ============================================================

ENGINE_NAME = "Singularidad-Relay-V5-P2P"

ACCESS_TOKEN = os.environ.get("RELAY_KEY", "CAMBIA_ESTA_KEY_EN_ENV")
PORT = int(os.environ.get("PORT", 10000))

MAX_PACKETS_PER_ROUTE = int(os.environ.get("MAX_PACKETS_PER_ROUTE", 500))
MAX_PAYLOAD_BYTES = int(os.environ.get("MAX_PAYLOAD_BYTES", 512 * 1024))
PACKET_TTL_SECONDS = int(os.environ.get("PACKET_TTL_SECONDS", 300))
MAX_PULL_PACKETS = int(os.environ.get("MAX_PULL_PACKETS", 200))
COMPRESSION_LEVEL = int(os.environ.get("COMPRESSION_LEVEL", 5))

RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", 10))
RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", 120))
CLEANER_INTERVAL = int(os.environ.get("CLEANER_INTERVAL", 30))

NODE_TTL_SECONDS = int(os.environ.get("NODE_TTL_SECONDS", 90))

ROUTE_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]{1,80}$")

# ---------------- MEMORIA ----------------

TRAFFIC_BUS = defaultdict(lambda: deque(maxlen=MAX_PACKETS_PER_ROUTE))
ROUTE_LOCKS = defaultdict(threading.RLock)
RATE_BUCKETS = defaultdict(deque)

NODES = {}
NODES_LOCK = threading.RLock()

GLOBAL_LOCK = threading.RLock()

STATS = {
    "started_at": time.time(),
    "push_total": 0,
    "pull_total": 0,
    "flush_total": 0,
    "bytes_in_total": 0,
    "bytes_out_total": 0,
    "rejected_total": 0,
    "expired_total": 0,
    "rate_limited_total": 0,
    "errors_total": 0,
    "registered_nodes_total": 0,
    "relay_send_total": 0,
    "broadcast_total": 0,
}

# ============================================================
# UTILIDADES
# ============================================================

def now():
    return time.time()


def json_ok(data=None, status=200):
    if data is None:
        data = {}

    return jsonify({
        "ok": True,
        **data
    }), status


def json_error(message, status=400, code="error"):
    with GLOBAL_LOCK:
        STATS["rejected_total"] += 1

    return jsonify({
        "ok": False,
        "code": code,
        "error": message
    }), status


def constant_time_token_check(token_a, token_b):
    return hmac.compare_digest(
        str(token_a).encode("utf-8"),
        str(token_b).encode("utf-8")
    )


def is_authorized():
    auth = request.headers.get("Authorization", "")

    if not auth.startswith("Bearer "):
        return False

    token = auth.removeprefix("Bearer ").strip()
    return constant_time_token_check(token, ACCESS_TOKEN)


def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()

    return request.remote_addr or "unknown"


def is_valid_route(route):
    return bool(ROUTE_PATTERN.match(str(route)))


def packet_id(route, payload, timestamp):
    raw = f"{route}:{timestamp}:{len(payload)}".encode("utf-8") + payload[:64]
    return hashlib.sha256(raw).hexdigest()[:24]


def rate_limited():
    ip = get_client_ip()
    current = now()

    bucket = RATE_BUCKETS[ip]

    while bucket and current - bucket[0] > RATE_LIMIT_WINDOW:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT_REQUESTS:
        with GLOBAL_LOCK:
            STATS["rate_limited_total"] += 1
        return True

    bucket.append(current)
    return False


def require_auth_and_limits(route=None):
    if rate_limited():
        return json_error("Too many requests", status=429, code="rate_limited")

    if not is_authorized():
        return json_error("Unauthorized", status=401, code="unauthorized")

    if route is not None and not is_valid_route(route):
        return json_error(
            "Invalid route. Use only letters, numbers, _, -, . and max 80 chars.",
            status=400,
            code="invalid_route"
        )

    return None


def make_packet(route, raw_payload):
    timestamp = now()
    compressed = zlib.compress(raw_payload, COMPRESSION_LEVEL)
    encoded = base64.b64encode(compressed).decode("ascii")

    return {
        "id": packet_id(route, raw_payload, timestamp),
        "d": encoded,
        "t": timestamp,
        "raw": len(raw_payload),
        "zip": len(compressed)
    }


def push_packet_to_route(route, raw_payload):
    pkt = make_packet(route, raw_payload)

    lock = ROUTE_LOCKS[route]

    with lock:
        cleanup_route(route)
        TRAFFIC_BUS[route].append(pkt)
        queue_size = len(TRAFFIC_BUS[route])

    with GLOBAL_LOCK:
        STATS["push_total"] += 1
        STATS["bytes_in_total"] += len(raw_payload)

    return pkt, queue_size


def cleanup_route(route):
    current = now()
    removed = 0

    lock = ROUTE_LOCKS[route]

    with lock:
        q = TRAFFIC_BUS.get(route)

        if not q:
            return 0

        fresh = deque(maxlen=MAX_PACKETS_PER_ROUTE)

        while q:
            pkt = q.popleft()
            age = current - pkt.get("t", current)

            if age <= PACKET_TTL_SECONDS:
                fresh.append(pkt)
            else:
                removed += 1

        if fresh:
            TRAFFIC_BUS[route] = fresh
        else:
            TRAFFIC_BUS.pop(route, None)

    if removed:
        with GLOBAL_LOCK:
            STATS["expired_total"] += removed

    return removed


def cleanup_all_routes():
    routes = list(TRAFFIC_BUS.keys())
    total = 0

    for route in routes:
        total += cleanup_route(route)

    return total


def cleanup_nodes():
    current = now()
    removed = []

    with NODES_LOCK:
        for node_id in list(NODES.keys()):
            if current - NODES[node_id]["last_seen"] > NODE_TTL_SECONDS:
                removed.append(node_id)
                NODES.pop(node_id, None)

    return removed


def safe_int(value, default, min_value=None, max_value=None):
    try:
        num = int(value)
    except Exception:
        num = default

    if min_value is not None:
        num = max(min_value, num)

    if max_value is not None:
        num = min(max_value, num)

    return num


# ============================================================
# MIDDLEWARE
# ============================================================

@app.before_request
def before_request():
    g.request_started_at = now()


@app.after_request
def after_request(response):
    elapsed_ms = round((now() - g.request_started_at) * 1000, 2)
    response.headers["X-Relay-Engine"] = ENGINE_NAME
    response.headers["X-Response-Time-ms"] = str(elapsed_ms)
    response.headers["Cache-Control"] = "no-store"
    return response


# ============================================================
# INFO
# ============================================================

@app.route("/", methods=["GET"])
def info():
    uptime = round(now() - STATS["started_at"], 2)
    total_packets = sum(len(q) for q in TRAFFIC_BUS.values())

    cleanup_nodes()

    return json_ok({
        "engine": ENGINE_NAME,
        "mode": "Zero-Persistence / Pure-Transport / P2P-Bootstrap",
        "status": "online",
        "uptime_seconds": uptime,
        "active_routes": len(TRAFFIC_BUS),
        "queued_packets": total_packets,
        "active_nodes": len(NODES),
        "endpoints": {
            "push": "POST /push/<route>",
            "pull": "GET /pull/<route>",
            "peek": "GET /peek/<route>",
            "flush": "DELETE /flush/<route>",
            "register_node": "POST /register/<node_id>",
            "peers": "GET /peers",
            "send_to_node": "POST /send/<node_id>",
            "broadcast": "POST /broadcast",
            "decode": "POST /decode",
            "stats": "GET /stats"
        },
        "limits": {
            "max_packets_per_route": MAX_PACKETS_PER_ROUTE,
            "max_payload_bytes": MAX_PAYLOAD_BYTES,
            "packet_ttl_seconds": PACKET_TTL_SECONDS,
            "max_pull_packets": MAX_PULL_PACKETS,
            "compression_level": COMPRESSION_LEVEL,
            "node_ttl_seconds": NODE_TTL_SECONDS,
            "rate_limit_window": RATE_LIMIT_WINDOW,
            "rate_limit_requests": RATE_LIMIT_REQUESTS
        },
        "timestamp": now()
    })


@app.route("/health", methods=["GET"])
def health():
    return json_ok({
        "status": "healthy",
        "timestamp": now()
    })


@app.route("/stats", methods=["GET"])
def stats():
    auth_error = require_auth_and_limits()
    if auth_error:
        return auth_error

    cleanup_all_routes()
    cleanup_nodes()

    with GLOBAL_LOCK:
        stats_copy = dict(STATS)

    route_data = {}

    for route, q in TRAFFIC_BUS.items():
        route_data[route] = {
            "queued": len(q),
            "oldest_age_seconds": round(now() - q[0]["t"], 2) if q else None,
            "newest_age_seconds": round(now() - q[-1]["t"], 2) if q else None
        }

    with NODES_LOCK:
        nodes_copy = dict(NODES)

    return json_ok({
        "stats": stats_copy,
        "routes": route_data,
        "nodes": nodes_copy
    })


# ============================================================
# RELAY BÁSICO
# ============================================================

@app.route("/push/<route>", methods=["POST"])
def push(route):
    auth_error = require_auth_and_limits(route)
    if auth_error:
        return auth_error

    content_length = request.content_length

    if content_length is not None and content_length > MAX_PAYLOAD_BYTES:
        return json_error(
            f"Payload too large. Max allowed: {MAX_PAYLOAD_BYTES} bytes.",
            status=413,
            code="payload_too_large"
        )

    raw_payload = request.get_data(cache=False)

    if not raw_payload:
        return json_error("Empty payload", status=400, code="empty_payload")

    if len(raw_payload) > MAX_PAYLOAD_BYTES:
        return json_error(
            f"Payload too large. Max allowed: {MAX_PAYLOAD_BYTES} bytes.",
            status=413,
            code="payload_too_large"
        )

    try:
        pkt, queue_size = push_packet_to_route(route, raw_payload)

        return json_ok({
            "accepted": True,
            "packet_id": pkt["id"],
            "route": route,
            "raw_bytes": len(raw_payload),
            "compressed_bytes": pkt["zip"],
            "queue_size": queue_size
        })

    except Exception as e:
        with GLOBAL_LOCK:
            STATS["errors_total"] += 1

        return json_error(
            f"Internal push error: {str(e)}",
            status=500,
            code="push_error"
        )


@app.route("/pull/<route>", methods=["GET"])
def pull(route):
    auth_error = require_auth_and_limits(route)
    if auth_error:
        return auth_error

    try:
        limit = request.args.get("limit", default=MAX_PULL_PACKETS, type=int)
        limit = max(1, min(limit, MAX_PULL_PACKETS))

        cleanup_route(route)

        lock = ROUTE_LOCKS[route]

        with lock:
            if route not in TRAFFIC_BUS or len(TRAFFIC_BUS[route]) == 0:
                return json_ok({
                    "route": route,
                    "count": 0,
                    "packets": []
                })

            q = TRAFFIC_BUS[route]
            packets = []

            while q and len(packets) < limit:
                packets.append(q.popleft())

            if len(q) == 0:
                TRAFFIC_BUS.pop(route, None)

        bytes_out = sum(pkt.get("zip", 0) for pkt in packets)

        with GLOBAL_LOCK:
            STATS["pull_total"] += 1
            STATS["bytes_out_total"] += bytes_out

        return json_ok({
            "route": route,
            "count": len(packets),
            "packets": packets
        })

    except Exception as e:
        with GLOBAL_LOCK:
            STATS["errors_total"] += 1

        return json_error(
            f"Internal pull error: {str(e)}",
            status=500,
            code="pull_error"
        )


@app.route("/peek/<route>", methods=["GET"])
def peek(route):
    auth_error = require_auth_and_limits(route)
    if auth_error:
        return auth_error

    cleanup_route(route)

    limit = request.args.get("limit", default=50, type=int)
    limit = max(1, min(limit, MAX_PULL_PACKETS))

    lock = ROUTE_LOCKS[route]

    with lock:
        packets = list(TRAFFIC_BUS.get(route, []))[:limit]

    return json_ok({
        "route": route,
        "count": len(packets),
        "packets": packets
    })


@app.route("/flush/<route>", methods=["DELETE", "GET"])
def flush(route):
    auth_error = require_auth_and_limits(route)
    if auth_error:
        return auth_error

    lock = ROUTE_LOCKS[route]

    with lock:
        removed = len(TRAFFIC_BUS.get(route, []))
        TRAFFIC_BUS.pop(route, None)

    with GLOBAL_LOCK:
        STATS["flush_total"] += 1

    return json_ok({
        "route": route,
        "flushed": True,
        "removed_packets": removed
    })


@app.route("/flush-all", methods=["DELETE", "POST"])
def flush_all():
    auth_error = require_auth_and_limits()
    if auth_error:
        return auth_error

    with GLOBAL_LOCK:
        removed_routes = len(TRAFFIC_BUS)
        removed_packets = sum(len(q) for q in TRAFFIC_BUS.values())
        TRAFFIC_BUS.clear()
        STATS["flush_total"] += 1

    return json_ok({
        "flushed": True,
        "removed_routes": removed_routes,
        "removed_packets": removed_packets
    })


# ============================================================
# P2P BOOTSTRAP
# ============================================================

@app.route("/register/<node_id>", methods=["POST"])
def register_node(node_id):
    auth_error = require_auth_and_limits(node_id)
    if auth_error:
        return auth_error

    data = request.get_json(silent=True) or {}

    public_ip = get_client_ip()
    p2p_port = safe_int(data.get("p2p_port", 7777), 7777, 1, 65535)
    name = str(data.get("name", node_id))[:80]

    # opcional: IP local que manda el cliente
    local_ip = str(data.get("local_ip", ""))[:80]
    version = str(data.get("version", "unknown"))[:80]

    node_data = {
        "id": node_id,
        "name": name,
        "public_ip": public_ip,
        "local_ip": local_ip,
        "p2p_port": p2p_port,
        "last_seen": now(),
        "relay_route": f"node.{node_id}",
        "version": version
    }

    with NODES_LOCK:
        is_new = node_id not in NODES
        NODES[node_id] = node_data

    with GLOBAL_LOCK:
        if is_new:
            STATS["registered_nodes_total"] += 1

    return json_ok({
        "registered": True,
        "node": node_data
    })


@app.route("/peers", methods=["GET"])
def get_peers():
    auth_error = require_auth_and_limits()
    if auth_error:
        return auth_error

    cleanup_nodes()

    exclude = request.args.get("exclude", "").strip()

    with NODES_LOCK:
        peers = [
            node for node in NODES.values()
            if node["id"] != exclude
        ]

    return json_ok({
        "count": len(peers),
        "peers": peers
    })


@app.route("/send/<target_node>", methods=["POST"])
def send_to_node(target_node):
    auth_error = require_auth_and_limits(target_node)
    if auth_error:
        return auth_error

    raw_payload = request.get_data(cache=False)

    if not raw_payload:
        return json_error("Empty payload", 400, "empty_payload")

    if len(raw_payload) > MAX_PAYLOAD_BYTES:
        return json_error(
            f"Payload too large. Max allowed: {MAX_PAYLOAD_BYTES} bytes.",
            status=413,
            code="payload_too_large"
        )

    route = f"node.{target_node}"

    try:
        pkt, queue_size = push_packet_to_route(route, raw_payload)

        with GLOBAL_LOCK:
            STATS["relay_send_total"] += 1

        return json_ok({
            "sent": True,
            "target": target_node,
            "route": route,
            "packet_id": pkt["id"],
            "queue_size": queue_size
        })

    except Exception as e:
        with GLOBAL_LOCK:
            STATS["errors_total"] += 1

        return json_error(
            f"Send error: {str(e)}",
            status=500,
            code="send_error"
        )


@app.route("/broadcast", methods=["POST"])
def broadcast_to_nodes():
    auth_error = require_auth_and_limits()
    if auth_error:
        return auth_error

    cleanup_nodes()

    raw_payload = request.get_data(cache=False)

    if not raw_payload:
        return json_error("Empty payload", 400, "empty_payload")

    if len(raw_payload) > MAX_PAYLOAD_BYTES:
        return json_error(
            f"Payload too large. Max allowed: {MAX_PAYLOAD_BYTES} bytes.",
            status=413,
            code="payload_too_large"
        )

    exclude = request.args.get("exclude", "").strip()

    with NODES_LOCK:
        targets = [
            node_id for node_id in NODES.keys()
            if node_id != exclude
        ]

    sent = []

    try:
        for node_id in targets:
            route = f"node.{node_id}"
            push_packet_to_route(route, raw_payload)
            sent.append(node_id)

        with GLOBAL_LOCK:
            STATS["broadcast_total"] += 1

        return json_ok({
            "broadcast": True,
            "sent_to": sent,
            "count": len(sent)
        })

    except Exception as e:
        with GLOBAL_LOCK:
            STATS["errors_total"] += 1

        return json_error(
            f"Broadcast error: {str(e)}",
            status=500,
            code="broadcast_error"
        )


# ============================================================
# DECODE
# ============================================================

@app.route("/decode", methods=["POST"])
def decode_packet():
    auth_error = require_auth_and_limits()
    if auth_error:
        return auth_error

    data = request.get_json(silent=True)

    if not data or "d" not in data:
        return json_error("Missing field: d", status=400, code="missing_data")

    try:
        compressed = base64.b64decode(data["d"])
        raw = zlib.decompress(compressed)

        return json_ok({
            "raw_base64": base64.b64encode(raw).decode("ascii"),
            "raw_text_preview": raw[:500].decode("utf-8", errors="replace"),
            "raw_bytes": len(raw)
        })

    except Exception as e:
        return json_error(
            f"Decode error: {str(e)}",
            status=400,
            code="decode_error"
        )


# ============================================================
# CLEANER THREAD
# ============================================================

def cleaner_loop():
    while True:
        try:
            time.sleep(CLEANER_INTERVAL)
            cleanup_all_routes()
            cleanup_nodes()

            current = now()

            for ip in list(RATE_BUCKETS.keys()):
                bucket = RATE_BUCKETS[ip]

                while bucket and current - bucket[0] > RATE_LIMIT_WINDOW:
                    bucket.popleft()

                if not bucket:
                    RATE_BUCKETS.pop(ip, None)

        except Exception:
            with GLOBAL_LOCK:
                STATS["errors_total"] += 1


def start_cleaner():
    t = threading.Thread(target=cleaner_loop, daemon=True)
    t.start()


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(_):
    return json_error("Endpoint not found", status=404, code="not_found")


@app.errorhandler(405)
def method_not_allowed(_):
    return json_error("Method not allowed", status=405, code="method_not_allowed")


@app.errorhandler(413)
def request_entity_too_large(_):
    return json_error(
        "Request entity too large",
        status=413,
        code="request_too_large"
    )


@app.errorhandler(Exception)
def global_error(e):
    with GLOBAL_LOCK:
        STATS["errors_total"] += 1

    return json_error(
        f"Internal server error: {str(e)}",
        status=500,
        code="internal_error"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    if ACCESS_TOKEN == "CAMBIA_ESTA_KEY_EN_ENV":
        print("[WARN] Estás usando la key por defecto. Configura RELAY_KEY en producción.")

    start_cleaner()

    print(f"[BOOT] {ENGINE_NAME}")
    print(f"[BOOT] Port: {PORT}")
    print(f"[BOOT] Max payload: {MAX_PAYLOAD_BYTES} bytes")
    print(f"[BOOT] Packet TTL: {PACKET_TTL_SECONDS}s")
    print(f"[BOOT] Node TTL: {NODE_TTL_SECONDS}s")

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
