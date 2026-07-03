"""
人脸检测 Web 服务
- MQTT 订阅接收数据
- SQLite 存储历史
- WebSocket 实时推送
- Web 界面显示
"""

import os
import json
import time
import sqlite3
import threading
import base64
from datetime import datetime
from flask import Flask, send_from_directory, jsonify, request, Response
from flask_socketio import SocketIO, emit
import paho.mqtt.client as mqtt
from PIL import Image
import io

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

# ===== 配置（优先读 .env，fallback 默认值）=====
MQTT_BROKER = os.environ.get("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_TOPIC = "esp32/face_detect"
DB_PATH = os.environ.get("DB_PATH", "face_detect.db")
FACE_TOPIC = "esp32/face_detect"
CROP_TOPIC = "esp32/face_detect/crop"
HOST = os.environ.get("WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("WEB_PORT", 8080))

# ===== Flask 应用 =====
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("WEB_SECRET_KEY", "face_detect_secret")
socketio = SocketIO(app, cors_allowed_origins="*")

# ===== 数据库 =====
_db_lock = threading.Lock()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT,
            timestamp REAL,
            datetime TEXT,
            frame INTEGER,
            face_count INTEGER,
            faces_json TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_id INTEGER,
            score REAL,
            x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
            keypoints_json TEXT,
            FOREIGN KEY (detection_id) REFERENCES detections(id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS face_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT,
            timestamp REAL,
            datetime TEXT,
            score REAL,
            x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
            img_jpeg BLOB
        )
    ''')
    conn.commit()
    conn.close()

def save_detection(data):
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        dt = datetime.fromtimestamp(data["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")

        c.execute('''
            INSERT INTO detections (device, timestamp, datetime, frame, face_count, faces_json)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            data["device"],
            data["timestamp"],
            dt,
            data["frame"],
            data["face_count"],
            json.dumps(data["faces"])
        ))

        detection_id = c.lastrowid

        for face in data["faces"]:
            box = face["box"]
            kp_json = json.dumps(face.get("keypoints", {}))
            c.execute('''
                INSERT INTO faces (detection_id, score, x1, y1, x2, y2, keypoints_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (detection_id, face["score"], box[0], box[1], box[2], box[3], kp_json))

        conn.commit()
        conn.close()
        return detection_id

def get_recent_detections(limit=100):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT id, device, datetime, frame, face_count, faces_json
        FROM detections
        ORDER BY id DESC
        LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        results.append({
            "id": row[0],
            "device": row[1],
            "datetime": row[2],
            "frame": row[3],
            "face_count": row[4],
            "faces": json.loads(row[5])
        })
    return results

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*), SUM(face_count) FROM detections")
    row = c.fetchone()
    total_detections = row[0] or 0
    total_faces = row[1] or 0
    
    c.execute("SELECT COUNT(DISTINCT device) FROM detections")
    devices = c.fetchone()[0] or 0
    
    c.execute("SELECT datetime FROM detections ORDER BY id DESC LIMIT 1")
    last = c.fetchone()
    last_time = last[0] if last else "无"
    
    conn.close()
    
    return {
        "total_detections": total_detections,
        "total_faces": total_faces,
        "devices": devices,
        "last_detection": last_time
    }

def save_face_image(data):
    """RGB565 base64 → JPEG → SQLite 存储"""
    img_b64 = data.get("img_data", "")
    if not img_b64:
        return None
    
    rgb565 = base64.b64decode(img_b64)
    w = data.get("img_w", 64)
    h = data.get("img_h", 64)
    
    # RGB565 big-endian → RGB888
    pixels = bytearray(w * h * 3)
    for i in range(w * h):
        val = (rgb565[i * 2] << 8) | rgb565[i * 2 + 1]
        r = ((val >> 11) & 0x1F) << 3
        g = ((val >> 5) & 0x3F) << 2
        b = (val & 0x1F) << 3
        pixels[i * 3] = r
        pixels[i * 3 + 1] = g
        pixels[i * 3 + 2] = b
    
    img = Image.frombytes("RGB", (w, h), bytes(pixels))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    jpeg_bytes = buf.getvalue()
    
    box = data.get("box", [0, 0, 0, 0])
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO face_images (device, timestamp, datetime, score, x1, y1, x2, y2, img_jpeg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get("device", "unknown"),
            data.get("ts", 0),
            data.get("time", ""),
            data.get("score", 0),
            box[0], box[1], box[2], box[3],
            jpeg_bytes
        ))
        face_id = c.lastrowid
        conn.commit()
        conn.close()
        return face_id

def get_recent_face_images(limit=20):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT id, device, datetime, score, x1, y1, x2, y2
        FROM face_images
        ORDER BY id DESC
        LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    return [{
        "id": r[0], "device": r[1], "datetime": r[2],
        "score": r[3], "box": [r[4], r[5], r[6], r[7]],
        "img_url": f"/api/face_image/{r[0]}"
    } for r in rows]

# ===== MQTT 回调 =====
mqtt_client = None
_face_context = {"count": 0, "faces": []}
_sensor_cache = {}

def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    rc_val = rc.value if hasattr(rc, 'value') else rc
    if rc_val == 0:
        print(f"✅ MQTT 已连接: {MQTT_BROKER}")
        client.subscribe(FACE_TOPIC)
        client.subscribe(CROP_TOPIC)
        client.subscribe("esp32/iot/sensor")
        print(f"📡 订阅: {FACE_TOPIC}, {CROP_TOPIC}")
    else:
        print(f"❌ MQTT 连接失败: rc={rc}")

def on_mqtt_message(client, userdata, msg):
    try:
        topic = msg.topic
        raw = json.loads(msg.payload.decode())

        if topic == CROP_TOPIC:
            face_id = save_face_image(raw)
            if face_id:
                socketio.emit('new_face_image', {
                    "id": face_id,
                    "device": raw.get("device", "unknown"),
                    "datetime": raw.get("time", ""),
                    "score": raw.get("score", 0),
                    "box": raw.get("box", []),
                    "img_url": f"/api/face_image/{face_id}"
                }, namespace='/')
                print(f"👤 人脸图片: ID={face_id} 置信度={raw.get('score', 0)}")

        elif topic == "esp32/iot/sensor":
            _sensor_cache.update(raw)

        else:
            data = {
                "device": raw.get("device", "unknown"),
                "timestamp": raw.get("ts", raw.get("timestamp", time.time())),
                "frame": raw.get("frame", 0),
                "face_count": raw.get("count", raw.get("face_count", 0)),
                "faces": raw.get("faces", []),
            }
            save_detection(data)
            _face_context["count"] = data["face_count"]
            _face_context["faces"] = data["faces"]
            socketio.emit('new_detection', data, namespace='/')
            print(f"📨 收到: {data['device']} {data['face_count']}张人脸")

    except Exception as e:
        print(f"处理消息错误: {e}")

def start_mqtt():
    global mqtt_client
    try:
        mqtt_client = mqtt.Client(
            client_id="web_face_server",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
    except (AttributeError, TypeError):
        # paho-mqtt v1.x 没有 CallbackAPIVersion
        mqtt_client = mqtt.Client(client_id="web_face_server")
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_forever()
    except Exception as e:
        print(f"MQTT 错误: {e}")

# ===== Web 路由 =====
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/detections')
def api_detections():
    limit = request.args.get('limit', 100, type=int)
    return jsonify(get_recent_detections(limit))

@app.route('/api/stats')
def api_stats():
    return jsonify(get_stats())

@app.route('/api/face_image/<int:face_id>')
def api_face_image(face_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT img_jpeg FROM face_images WHERE id = ?', (face_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return Response(row[0], mimetype='image/jpeg')
    return 'Not found', 404

@app.route('/api/face_images')
def api_face_images():
    limit = request.args.get('limit', 20, type=int)
    return jsonify(get_recent_face_images(limit))

# ===== 语音聊天 API =====
@app.route('/api/voice_chat', methods=['POST'])
def api_voice_chat():
    data = request.get_json()
    text = data.get('text', '').strip()
    if not text:
        return jsonify({"reply": "请输入内容", "actions": []})

    try:
        from openai import OpenAI
        import os
        api_key = os.environ.get("XIAOMI_API_KEY", "")
        if not api_key:
            return jsonify({"reply": "API Key 未配置", "actions": []})

        client_ai = OpenAI(api_key=api_key, base_url="https://api.xiaomimimo.com/v1")

        ctx = ""
        if _face_context["count"] > 0:
            ctx = f"\n[当前人脸检测: 检测到 {_face_context['count']} 张人脸]"
        else:
            ctx = "\n[当前人脸检测: 未检测到人脸]"

        system_prompt = (
            "你是一个智能门禁+安防助手。用简洁口语回复，每次不超过3句话。"
            "你可以控制: 风扇、LED灯、RGB灯带、蜂鸣器、人脸检测开关。"
            "当用户要求控制设备时，在回复末尾用 [ACTION] 标记添加指令。\n"
            '支持: [ACTION]{"action":"fan","state":"on"/"off"}[/ACTION]\n'
            '[ACTION]{"action":"led","state":"on"/"off"}[/ACTION]\n'
            '[ACTION]{"action":"buzzer","state":"on"/"off"}[/ACTION]\n'
            '[ACTION]{"action":"face_detect","state":"on"/"off"}[/ACTION]\n'
            '[ACTION]{"action":"rgb","color":"red/green/blue/off"}[/ACTION]\n'
        )

        resp = client_ai.chat.completions.create(
            model="mimo-v2.5",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text + ctx}
            ]
        )
        reply = resp.choices[0].message.content or ""

        import re
        actions = []
        for m in re.finditer(r"\[ACTION\](.*?)\[/ACTION\]", reply, re.DOTALL):
            try:
                actions.append(json.loads(m.group(1)))
            except: pass
        clean = re.sub(r"\[ACTION\].*?\[/ACTION\]", "", reply, flags=re.DOTALL).strip()

        return jsonify({"reply": clean, "actions": actions})
    except Exception as e:
        return jsonify({"reply": f"处理出错: {e}", "actions": []})

@app.route('/api/device', methods=['POST'])
def api_device():
    data = request.get_json()
    action = data.get('action', '')
    state = data.get('state', 'on')

    if mqtt_client:
        if action == 'fan':
            topic = 'esp32/iot/cmd/fan'
            payload = state
        elif action == 'led':
            topic = 'esp32/iot/cmd/led'
            payload = '100' if state == 'on' else '0'
        elif action == 'buzzer':
            topic = 'esp32/iot/cmd/buzzer'
            payload = state
        elif action == 'face_detect':
            topic = 'esp32/iot/cmd/facedetect'
            payload = state
        elif action == 'rgb':
            topic = 'esp32/iot/cmd/rgb'
            payload = data.get('color', 'off')
        else:
            return jsonify({"ok": False, "msg": "unknown device"})

        mqtt_client.publish(topic, payload)
        return jsonify({"ok": True})

    return jsonify({"ok": False, "msg": "MQTT not connected"})

@app.route('/api/sensors')
def api_sensors():
    return jsonify({
        "temperature": _sensor_cache.get("temperature"),
        "humidity": _sensor_cache.get("humidity"),
        "light": _sensor_cache.get("light"),
    })

@socketio.on('connect')
def handle_connect():
    print("🔗 WebSocket 客户端连接")
    emit('connected', {'status': 'ok'})

# ===== 主程序 =====
if __name__ == '__main__':
    init_db()
    
    # MQTT 线程
    mqtt_thread = threading.Thread(target=start_mqtt, daemon=True)
    mqtt_thread.start()
    
    print(f"🌐 Web 服务启动: http://{HOST}:{PORT}")
    socketio.run(app, host=HOST, port=PORT, debug=False, allow_unsafe_werkzeug=True)
