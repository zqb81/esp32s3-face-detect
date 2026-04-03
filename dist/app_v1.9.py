"""
人脸检测 Web 服务
- MQTT 订阅接收数据
- SQLite 存储历史
- WebSocket 实时推送
- Web 界面显示
"""

import json
import time
import sqlite3
import threading
import base64
from datetime import datetime
from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO, emit
import paho.mqtt.client as mqtt
from PIL import Image
import io

# ===== 配置 =====
MQTT_BROKER = "127.0.0.1"  # 本地 MQTT
MQTT_PORT = 1883
MQTT_TOPIC = "esp32/face_detect"
DB_PATH = "face_detect.db"
FACE_TOPIC = "esp32/face_detect"
CROP_TOPIC = "esp32/face_detect/crop"
HOST = "0.0.0.0"
PORT = 8080

# ===== Flask 应用 =====
app = Flask(__name__)
app.config['SECRET_KEY'] = 'face_detect_secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# ===== 数据库 =====
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
        "score": r[3], "box": [r[4], r[5], r[6], r[7]]
    } for r in rows]

# ===== MQTT 回调 =====
mqtt_client = None

def on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✅ MQTT 已连接: {MQTT_BROKER}")
        client.subscribe(FACE_TOPIC)
        client.subscribe(CROP_TOPIC)
        print(f"📡 订阅: {FACE_TOPIC}, {CROP_TOPIC}")
    else:
        print(f"❌ MQTT 连接失败: {rc}")

def on_mqtt_message(client, userdata, msg):
    try:
        topic = msg.topic
        raw = json.loads(msg.payload.decode())
        
        if topic == CROP_TOPIC:
            # 人脸裁剪图片
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
        else:
            # 检测数据
            data = {
                "device": raw.get("device", "unknown"),
                "timestamp": raw.get("ts", raw.get("timestamp", time.time())),
                "frame": raw.get("frame", 0),
                "face_count": raw.get("count", raw.get("face_count", 0)),
                "faces": raw.get("faces", []),
            }
            save_detection(data)
            socketio.emit('new_detection', data, namespace='/')
            print(f"📨 收到: {data['device']} 帧{data['frame']} {data['face_count']}张人脸")
        
    except Exception as e:
        print(f"处理消息错误: {e}")

def start_mqtt():
    global mqtt_client
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
    return render_template('index.html')

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
