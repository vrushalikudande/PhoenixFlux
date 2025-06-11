import os
import redis
from flask import Flask, request, jsonify
import gzip
import io

app = Flask(__name__)

REDIS_HOST = os.environ.get('REDIS_HOST', 'redis.logging.svc.cluster.local')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_DB = int(os.environ.get('REDIS_DB', 0))

rdb = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)

@app.route('/logs', methods=['POST'])
def receive_logs():
    raw_data = request.data
    # Try to decompress if Content-Encoding is gzip or if data looks like gzip
    if request.headers.get('Content-Encoding', '') == 'gzip' or (raw_data and raw_data[:2] == b'\x1f\x8b'):
        try:
            raw_data = gzip.decompress(raw_data)
        except Exception as e:
            print("Gzip decompress error:", e)
            print("Raw data:", request.data)
            return jsonify({'status': 'error', 'message': 'Gzip decompress error', 'error': str(e)}), 400
    try:
        import json
        logs = json.loads(raw_data)
    except Exception as e:
        print("JSON decode error:", e)
        print("Raw data:", raw_data)
        return jsonify({'status': 'error', 'message': 'Invalid JSON', 'error': str(e)}), 400

    if not logs:
        print("Empty or invalid payload. Raw data:", raw_data)
        return jsonify({'status': 'error', 'message': 'Empty payload'}), 400

    if isinstance(logs, list):
        log_batch = logs
    else:
        log_batch = [logs]

    for log in log_batch:
        k8s_info = log.get('kubernetes') or {}
        ns = k8s_info.get('namespace_name', 'unknown')
        pod = k8s_info.get('pod_name', 'unknown')
        pod_key = f"{ns}:{pod}"
        rdb.rpush(f"logs:{pod_key}", str(log))
        rdb.set(f"logs:{pod_key}:latest", str(log))

    print(f"Received and stored {len(log_batch)} logs.")
    return jsonify({'status': 'success', 'stored_logs': len(log_batch)}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)