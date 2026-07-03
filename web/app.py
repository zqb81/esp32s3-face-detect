"""
ESP32-S3 人脸检测 — 统一服务端
  HTTP  :8080  Web 看板 + 文字聊天
  TCP   :9000  ESP32 语音交互 (PCM → ASR → LLM → TTS → PCM)
  MQTT  :1883  订阅人脸检测 / 裁剪图 / 传感器

运行: python app.py
依赖: pip install -r requirements.txt
"""

import os
import io
import re
import json
import time
import wave
import struct
import socket
import sqlite3
import base64
import threading
import logging

from datetime import datetime
from flask import Flask, send_from_directory, jsonify, request, Response
from flask_socketio import SocketIO, emit
import paho.mqtt.client as mqtt
from PIL import Image

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

# ===== 日志 =====
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("server")

# ===== 配置 =====
MQTT_BROKER = os.environ.get("MQTT_BROKER", "127.0.0.1")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", 1883))
DB_PATH     = os.environ.get("DB_PATH", "face_detect.db")
WEB_HOST    = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT    = int(os.environ.get("WEB_PORT", 8080))
TCP_HOST    = os.environ.get("TCP_HOST", "0.0.0.0")
TCP_PORT    = int(os.environ.get("TCP_PORT", 9000))

FACE_TOPIC  = "esp32/face_detect"
CROP_TOPIC  = "esp32/face_detect/crop"

# ===== MiMo LLM =====
XIAOMI_API_KEY = os.environ.get("XIAOMI_API_KEY", "").strip()
_llm_client = None

def _get_llm():
    global _llm_client
    if _llm_client is None:
        from openai import OpenAI
        _llm_client = OpenAI(api_key=XIAOMI_API_KEY, base_url="https://api.xiaomimimo.com/v1")
    return _llm_client

# ===== 共享状态 =====
_face_context  = {"count": 0, "faces": []}
_sensor_cache  = {}
_db_lock       = threading.Lock()

# LLM 对话历史（Web 文字聊天 + TCP 语音共享）
SYSTEM_PROMPT = (
    "你是一个智能门禁+安防助手，运行在 ESP32-S3 人脸检测设备上。"
    "用简洁口语回复，每次不超过3句话。\n"
    "你可以控制: 风扇、LED灯、RGB灯带、蜂鸣器、人脸检测开关。\n"
    "当用户要求控制设备时，在回复末尾用 [ACTION] 标记添加指令。\n"
    "支持的指令:\n"
    '[ACTION]{"action":"fan","state":"on"/"off"}[/ACTION]\n'
    '[ACTION]{"action":"led","state":"on"/"off"}[/ACTION]\n'
    '[ACTION]{"action":"buzzer","state":"on"/"off","times":3}[/ACTION]\n'
    '[ACTION]{"action":"face_detect","state":"on"/"off"}[/ACTION]\n'
    '[ACTION]{"action":"rgb","color":"red/green/blue/yellow/purple/white/off"}[/ACTION]\n'
    '[ACTION]{"action":"rgb","r":0,"g":0,"b":255}[/ACTION]\n'
)

_chat_history = [{"role": "system", "content": SYSTEM_PROMPT}]
_history_lock = threading.Lock()
MAX_HISTORY  = 10  # 保留轮数


def parse_actions(text):
    """从 LLM 回复中提取 [ACTION]...[/ACTION] 指令"""
    actions = []
    for m in re.finditer(r"\[ACTION\](.*?)\[/ACTION\]", text, re.DOTALL):
        try:
            actions.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass
    clean = re.sub(r"\[ACTION\].*?\[/ACTION\]", "", text, flags=re.DOTALL).strip()
    return clean, actions


def chat_with_context(user_text):
    """LLM 对话（注入人脸检测上下文，维护历史）"""
    ctx = f"\n[当前人脸检测: 检测到 {_face_context['count']} 张人脸]" \
        if _face_context["count"] > 0 else "\n[当前人脸检测: 未检测到人脸]"

    with _history_lock:
        _chat_history.append({"role": "user", "content": user_text + ctx})
        resp = _get_llm().chat.completions.create(
            model="mimo-v2.5",
            messages=_chat_history,
        )
        reply = resp.choices[0].message.content or ""
        _chat_history.append({"role": "assistant", "content": reply})
        # 裁剪历史
        if len(_chat_history) > 1 + MAX_HISTORY * 2:
            _chat_history[1:] = _chat_history[-(MAX_HISTORY * 2):]

    return parse_actions(reply)


# ══════════════════════════════════════════════════════════════
#  数据库
# ══════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS detections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device TEXT, timestamp REAL, datetime TEXT,
        frame INTEGER, face_count INTEGER, faces_json TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS faces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        detection_id INTEGER, score REAL,
        x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
        keypoints_json TEXT,
        FOREIGN KEY (detection_id) REFERENCES detections(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS face_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device TEXT, timestamp REAL, datetime TEXT, score REAL,
        x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
        img_jpeg BLOB)''')
    conn.commit()
    conn.close()


def save_detection(data):
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        dt = datetime.fromtimestamp(data["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        c.execute('INSERT INTO detections VALUES (NULL,?,?,?,?,?,?)', (
            data["device"], data["timestamp"], dt,
            data["frame"], data["face_count"], json.dumps(data["faces"])))
        did = c.lastrowid
        for f in data["faces"]:
            b = f["box"]
            c.execute('INSERT INTO faces VALUES (NULL,?,?,?,?,?,?,?)', (
                did, f["score"], b[0], b[1], b[2], b[3],
                json.dumps(f.get("keypoints", {}))))
        conn.commit()
        conn.close()
        return did


def save_face_image(data):
    img_b64 = data.get("img_data", "")
    if not img_b64:
        return None
    rgb565 = base64.b64decode(img_b64)
    w, h = data.get("img_w", 64), data.get("img_h", 64)
    pixels = bytearray(w * h * 3)
    for i in range(w * h):
        val = (rgb565[i*2] << 8) | rgb565[i*2+1]
        pixels[i*3]   = ((val >> 11) & 0x1F) << 3
        pixels[i*3+1] = ((val >> 5)  & 0x3F) << 2
        pixels[i*3+2] = (val & 0x1F) << 3
    img = Image.frombytes("RGB", (w, h), bytes(pixels))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    box = data.get("box", [0,0,0,0])
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('INSERT INTO face_images VALUES (NULL,?,?,?,?,?,?,?,?,?)', (
            data.get("device","unknown"), data.get("ts",0), data.get("time",""),
            data.get("score",0), box[0], box[1], box[2], box[3], buf.getvalue()))
        fid = c.lastrowid
        conn.commit()
        conn.close()
        return fid


def get_recent_detections(limit=100):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        'SELECT id,device,datetime,frame,face_count,faces_json FROM detections ORDER BY id DESC LIMIT ?',
        (limit,)).fetchall()
    conn.close()
    return [{"id":r[0],"device":r[1],"datetime":r[2],"frame":r[3],
             "face_count":r[4],"faces":json.loads(r[5])} for r in rows]


def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(face_count) FROM detections")
    r = c.fetchone()
    c.execute("SELECT COUNT(DISTINCT device) FROM detections")
    devs = c.fetchone()[0] or 0
    c.execute("SELECT datetime FROM detections ORDER BY id DESC LIMIT 1")
    last = c.fetchone()
    conn.close()
    return {"total_detections": r[0] or 0, "total_faces": r[1] or 0,
            "devices": devs, "last_detection": last[0] if last else "无"}


def get_recent_face_images(limit=20):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        'SELECT id,device,datetime,score,x1,y1,x2,y2 FROM face_images ORDER BY id DESC LIMIT ?',
        (limit,)).fetchall()
    conn.close()
    return [{"id":r[0],"device":r[1],"datetime":r[2],"score":r[3],
             "box":[r[4],r[5],r[6],r[7]],"img_url":f"/api/face_image/{r[0]}"} for r in rows]


# ══════════════════════════════════════════════════════════════
#  MQTT
# ══════════════════════════════════════════════════════════════

mqtt_client = None

def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    rc_val = rc.value if hasattr(rc, 'value') else rc
    if rc_val == 0:
        client.subscribe(FACE_TOPIC)
        client.subscribe(CROP_TOPIC)
        client.subscribe("esp32/iot/sensor")
        log.info("MQTT 已连接 %s，订阅 %s", MQTT_BROKER, FACE_TOPIC)
    else:
        log.error("MQTT 连接失败: rc=%s", rc)


def on_mqtt_message(client, userdata, msg):
    try:
        topic = msg.topic
        raw = json.loads(msg.payload.decode())

        if topic == CROP_TOPIC:
            fid = save_face_image(raw)
            if fid:
                socketio.emit('new_face_image', {
                    "id": fid, "device": raw.get("device","unknown"),
                    "datetime": raw.get("time",""), "score": raw.get("score",0),
                    "box": raw.get("box",[]), "img_url": f"/api/face_image/{fid}"
                }, namespace='/')
                log.info("👤 人脸图片 ID=%d score=%.2f", fid, raw.get("score",0))

        elif topic == "esp32/iot/sensor":
            _sensor_cache.update(raw)

        else:
            data = {
                "device": raw.get("device","unknown"),
                "timestamp": raw.get("ts", raw.get("timestamp", time.time())),
                "frame": raw.get("frame", 0),
                "face_count": raw.get("count", raw.get("face_count", 0)),
                "faces": raw.get("faces", []),
            }
            save_detection(data)
            _face_context["count"] = data["face_count"]
            _face_context["faces"] = data["faces"]
            socketio.emit('new_detection', data, namespace='/')

    except Exception as e:
        log.error("MQTT 消息处理: %s", e)


def start_mqtt():
    global mqtt_client
    try:
        mqtt_client = mqtt.Client(client_id="face_server",
                                  callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    except (AttributeError, TypeError):
        mqtt_client = mqtt.Client(client_id="face_server")
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_forever()
    except Exception as e:
        log.error("MQTT 错误: %s", e)


# ══════════════════════════════════════════════════════════════
#  TCP 语音服务 (ESP32 ← PCM → ASR → LLM → TTS → PCM)
# ══════════════════════════════════════════════════════════════

SAMPLE_RATE = 16000
MSG_AUDIO = 0x01
MSG_CMD   = 0x02
MSG_DONE  = 0x03
_MAX_FRAME = 2 * 1024 * 1024


def _tcp_send(sock, ftype, data):
    sock.sendall(struct.pack(">BI", ftype, len(data)))
    sock.sendall(data)


def _tcp_recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 4096))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _tcp_recv_frame(sock):
    header = _tcp_recv_exact(sock, 5)
    if header is None:
        return None, None
    ftype, length = struct.unpack(">BI", header)
    if length > _MAX_FRAME:
        return None, None
    data = _tcp_recv_exact(sock, length) if length > 0 else b""
    return ftype, data


def _pcm_to_wav(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


def _wav_to_pcm(wav_data):
    buf = io.BytesIO(wav_data)
    with wave.open(buf, "rb") as wf:
        return wf.readframes(wf.getnframes())


def _asr(wav_bytes):
    """语音识别"""
    b64 = base64.b64encode(wav_bytes).decode()
    resp = _get_llm().chat.completions.create(
        model="mimo-v2.5-asr",
        messages=[{"role": "user", "content": [
            {"type": "input_audio",
             "input_audio": {"data": f"data:audio/wav;base64,{b64}"}}
        ]}],
        extra_body={"asr_options": {"language": "zh"}}
    )
    return (resp.choices[0].message.content or "").strip()


def _tts(text):
    """语音合成"""
    resp = _get_llm().chat.completions.create(
        model="mimo-v2.5-tts",
        messages=[
            {"role": "user", "content": "Calm, natural, relaxed tone."},
            {"role": "assistant", "content": text}
        ],
        audio={"format": "wav", "voice": "Chloe"}
    )
    return base64.b64decode(resp.choices[0].message.audio.data)


def _handle_voice_client(conn, addr):
    """处理一个 ESP32 语音连接"""
    log.info("[TCP] 连接: %s", addr)
    audio_chunks = []
    try:
        while True:
            ftype, data = _tcp_recv_frame(conn)
            if ftype is None:
                break

            if ftype == MSG_AUDIO:
                audio_chunks.append(data)

            elif ftype == MSG_CMD:
                cmd = json.loads(data.decode())
                action = cmd.get("action", "")

                if action == "start_recording":
                    audio_chunks = []
                    log.info("[TCP] 录音中...")

                elif action == "stop_recording":
                    total = sum(len(c) for c in audio_chunks)
                    log.info("[TCP] 录音结束 %d 字节", total)
                    if not audio_chunks:
                        break

                    pcm = b"".join(audio_chunks)
                    wav = _pcm_to_wav(pcm)

                    # ASR
                    log.info("[ASR] 识别中...")
                    user_text = _asr(wav)
                    log.info("[ASR] %s", user_text)
                    if not user_text:
                        _tcp_send(conn, MSG_CMD, json.dumps(
                            {"action": "info", "msg": "未识别到内容"}).encode())
                        _tcp_send(conn, MSG_DONE, b"")
                        break

                    # LLM（共享历史和上下文）
                    log.info("[LLM] 思考中...")
                    clean_text, actions = chat_with_context(user_text)
                    log.info("[LLM] %s", clean_text)

                    # TTS
                    log.info("[TTS] 合成中...")
                    tts_wav = _tts(clean_text)
                    tts_pcm = _wav_to_pcm(tts_wav)
                    log.info("[TTS] %d 字节 PCM", len(tts_pcm))

                    # 发送音频
                    for i in range(0, len(tts_pcm), 4096):
                        _tcp_send(conn, MSG_AUDIO, tts_pcm[i:i+4096])

                    # 发送动作指令
                    for act in actions:
                        _tcp_send(conn, MSG_CMD, json.dumps(act).encode())

                    _tcp_send(conn, MSG_CMD, json.dumps(
                        {"action": "reply", "text": clean_text}).encode())
                    _tcp_send(conn, MSG_DONE, b"")
                    break

    except Exception as e:
        log.error("[TCP] %s: %s", addr, e)
    finally:
        conn.close()
        log.info("[TCP] 断开: %s", addr)


def start_tcp_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((TCP_HOST, TCP_PORT))
    server.listen(5)
    log.info("🎙️  TCP 语音服务: %s:%d", TCP_HOST, TCP_PORT)
    while True:
        conn, addr = server.accept()
        threading.Thread(target=_handle_voice_client, args=(conn, addr), daemon=True).start()


# ══════════════════════════════════════════════════════════════
#  Flask Web
# ══════════════════════════════════════════════════════════════

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("WEB_SECRET_KEY", "face_detect_secret")
socketio = SocketIO(app, cors_allowed_origins="*")


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/detections')
def api_detections():
    return jsonify(get_recent_detections(request.args.get('limit', 100, type=int)))


@app.route('/api/stats')
def api_stats():
    return jsonify(get_stats())


@app.route('/api/face_image/<int:fid>')
def api_face_image(fid):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute('SELECT img_jpeg FROM face_images WHERE id=?', (fid,)).fetchone()
    conn.close()
    if row and row[0]:
        return Response(row[0], mimetype='image/jpeg')
    return 'Not found', 404


@app.route('/api/face_images')
def api_face_images():
    return jsonify(get_recent_face_images(request.args.get('limit', 20, type=int)))


@app.route('/api/voice_chat', methods=['POST'])
def api_voice_chat():
    """Web 文字聊天（和 TCP 语音共享 LLM 历史）"""
    text = (request.get_json() or {}).get('text', '').strip()
    if not text:
        return jsonify({"reply": "请输入内容", "actions": []})
    if not XIAOMI_API_KEY:
        return jsonify({"reply": "API Key 未配置", "actions": []})
    try:
        clean, actions = chat_with_context(text)
        return jsonify({"reply": clean, "actions": actions})
    except Exception as e:
        return jsonify({"reply": f"处理出错: {e}", "actions": []})


@app.route('/api/device', methods=['POST'])
def api_device():
    data = request.get_json()
    action = data.get('action', '')
    state = data.get('state', 'on')
    if not mqtt_client:
        return jsonify({"ok": False, "msg": "MQTT not connected"})

    topic_map = {
        'fan':         ('esp32/iot/cmd/fan', state),
        'led':         ('esp32/iot/cmd/led', '100' if state == 'on' else '0'),
        'buzzer':      ('esp32/iot/cmd/buzzer', state),
        'face_detect': ('esp32/iot/cmd/facedetect', state),
        'rgb':         ('esp32/iot/cmd/rgb', data.get('color', 'off')),
    }
    if action not in topic_map:
        return jsonify({"ok": False, "msg": "unknown device"})

    topic, payload = topic_map[action]
    mqtt_client.publish(topic, payload)
    return jsonify({"ok": True})


@app.route('/api/sensors')
def api_sensors():
    return jsonify({
        "temperature": _sensor_cache.get("temperature"),
        "humidity":    _sensor_cache.get("humidity"),
        "light":       _sensor_cache.get("light"),
    })


@socketio.on('connect')
def handle_connect():
    log.info("🔗 WebSocket 客户端连接")
    emit('connected', {'status': 'ok'})


# ══════════════════════════════════════════════════════════════
#  启动
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    init_db()

    # MQTT 线程
    threading.Thread(target=start_mqtt, daemon=True).start()

    # TCP 语音线程
    threading.Thread(target=start_tcp_server, daemon=True).start()

    log.info("=" * 50)
    log.info("  ESP32-S3 人脸检测 统一服务端")
    log.info("  Web:   http://%s:%d", WEB_HOST, WEB_PORT)
    log.info("  TCP:   %s:%d (语音)", TCP_HOST, TCP_PORT)
    log.info("  MQTT:  %s:%d", MQTT_BROKER, MQTT_PORT)
    log.info("  LLM:   %s", "MiMo ✓" if XIAOMI_API_KEY else "未配置")
    log.info("=" * 50)

    socketio.run(app, host=WEB_HOST, port=WEB_PORT, debug=False, allow_unsafe_werkzeug=True)
