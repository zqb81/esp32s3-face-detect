"""
voice_server.py — 语音桥接服务器
  TCP 9000: 接收 ESP32-S3 音频 → ASR → LLM(含人脸检测上下文) → TTS → 回传
  MQTT: 订阅人脸检测结果，注入 LLM 对话上下文

运行: python voice_server.py
依赖: pip install openai paho-mqtt python-dotenv
"""
import os
import io
import json
import struct
import socket
import base64
import wave
import re
import time
import threading
import logging

# ===== 日志 =====
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("voice-server")

# ===== MiMo API =====
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

XIAOMI_API_KEY = os.environ.get("XIAOMI_API_KEY", "").strip()
if not XIAOMI_API_KEY:
    logger.warning("未设置 XIAOMI_API_KEY，请在 .env 或环境变量中设置")

from openai import OpenAI
client = OpenAI(api_key=XIAOMI_API_KEY, base_url="https://api.xiaomimimo.com/v1")

# ===== MQTT =====
try:
    import paho.mqtt.client as paho_mqtt
    HAS_PAHO = True
except ImportError:
    HAS_PAHO = False
    logger.warning("paho-mqtt 未安装，MQTT 上下文融合不可用")

# ===== 配置（优先读 .env，fallback 默认值）=====
TCP_HOST = os.environ.get("TCP_HOST", "0.0.0.0")
TCP_PORT = int(os.environ.get("TCP_PORT", 9000))
MQTT_BROKER = os.environ.get("MQTT_BROKER", "127.0.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
SAMPLE_RATE = 16000
CHANNELS = 1

# 人脸检测 MQTT 主题
FACE_DETECT_TOPIC = "esp32/face_detect"
FACE_CROP_TOPIC = "esp32/face_detect/crop"

# ===== TCP 协议 =====
MSG_AUDIO = 0x01
MSG_CMD = 0x02
MSG_DONE = 0x03
_MAX_FRAME_LEN = 2 * 1024 * 1024


def send_frame(sock, frame_type, data):
    header = struct.pack(">BI", frame_type, len(data))
    sock.sendall(header)
    sock.sendall(data)


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 4096))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_frame(sock):
    header = recv_exact(sock, 5)
    if header is None:
        return None, None
    frame_type, length = struct.unpack(">BI", header)
    if length > _MAX_FRAME_LEN:
        return None, None
    data = recv_exact(sock, length) if length > 0 else b""
    return frame_type, data


# ===== 音频工具 =====
def pcm_to_wav_bytes(pcm_data):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def wav_to_pcm(wav_data):
    buf = io.BytesIO(wav_data)
    with wave.open(buf, "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    return pcm


# ===== MiMo API 调用 =====
def speech_to_text(wav_bytes):
    audio_b64 = base64.b64encode(wav_bytes).decode()
    resp = client.chat.completions.create(
        model="mimo-v2.5-asr",
        messages=[{
            "role": "user",
            "content": [{"type": "input_audio",
                         "input_audio": {"data": f"data:audio/wav;base64,{audio_b64}"}}]
        }],
        extra_body={"asr_options": {"language": "zh"}}
    )
    return (resp.choices[0].message.content or "").strip()


def text_to_speech(text):
    resp = client.chat.completions.create(
        model="mimo-v2.5-tts",
        messages=[
            {"role": "user",
             "content": "Calm, natural, and relaxed tone — like chatting casually with a close friend."},
            {"role": "assistant", "content": text}
        ],
        audio={"format": "wav", "voice": "Chloe"}
    )
    return base64.b64decode(resp.choices[0].message.audio.data)


def parse_actions(reply):
    actions = []
    for match in re.finditer(r"\[ACTION\](.*?)\[/ACTION\]", reply, re.DOTALL):
        try:
            actions.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass
    clean = re.sub(r"\[ACTION\].*?\[/ACTION\]", "", reply, flags=re.DOTALL).strip()
    return clean, actions


# ===== 人脸检测上下文 =====
_face_context = {"count": 0, "faces": [], "last_update": 0}
_face_lock = threading.Lock()

SYSTEM_PROMPT = (
    "你是一个智能门禁+安防助手，运行在 ESP32-S3 人脸检测设备上。"
    "用简洁口语回复，每次不超过3句话。"
    "你可以：\n"
    "1. 报告当前检测到的人脸数量和信息\n"
    "2. 控制检测模式和外设（风扇、LED、RGB灯带、蜂鸣器）\n"
    "3. 查询温湿度、光照等传感器数据\n"
    "4. 回答关于检测状态的问题\n"
    "当用户要求控制设备时，在回复末尾用 [ACTION] 标记添加指令。\n"
    "支持的指令格式:\n"
    '[ACTION]{"action":"face_detect","state":"on"/"off"}[/ACTION]\n'
    '[ACTION]{"action":"fan","state":"on"/"off","speed":0~1023}[/ACTION]\n'
    '[ACTION]{"action":"led","state":"on"/"off"}[/ACTION]\n'
    '[ACTION]{"action":"buzzer","state":"on"/"off","times":3}[/ACTION]\n'
    '[ACTION]{"action":"rgb","color":"red/green/blue/yellow/purple/white/off"}[/ACTION]\n'
    '[ACTION]{"action":"rgb","color":"rainbow"}[/ACTION]\n'
    '[ACTION]{"action":"rgb","r":0~255,"g":0~255,"b":0~255}[/ACTION]\n'
)

_shared_history = [{"role": "system", "content": SYSTEM_PROMPT}]
_history_lock = threading.Lock()
MAX_HISTORY_ROUNDS = 10


def chat_with_context(text):
    """带人脸检测上下文的 LLM 对话"""
    user_msg = text

    # 注入人脸检测上下文
    with _face_lock:
        ctx = dict(_face_context)
    if ctx["count"] > 0:
        user_msg += f"\n[当前人脸检测: 检测到 {ctx['count']} 张人脸]"
    else:
        user_msg += "\n[当前人脸检测: 未检测到人脸]"

    with _history_lock:
        _shared_history.append({"role": "user", "content": user_msg})

        resp = client.chat.completions.create(
            model="mimo-v2.5",
            messages=_shared_history,
        )
        reply = resp.choices[0].message.content or ""
        _shared_history.append({"role": "assistant", "content": reply})

        if len(_shared_history) > 1 + MAX_HISTORY_ROUNDS * 2:
            _shared_history[1:] = _shared_history[1 - MAX_HISTORY_ROUNDS * 2:]

    clean_text, actions = parse_actions(reply)
    return clean_text, actions


# ===== MQTT 订阅人脸检测结果 =====
def start_mqtt_subscriber():
    """订阅人脸检测 MQTT 主题，更新上下文"""
    if not HAS_PAHO:
        return

    mqtt_client = paho_mqtt.Client(
        client_id="voice_server_" + str(int(time.time())),
        callback_api_version=paho_mqtt.CallbackAPIVersion.VERSION2,
    )

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0 or (hasattr(rc, 'value') and rc.value == 0):
            client.subscribe(FACE_DETECT_TOPIC)
            logger.info(f"[MQTT] 已订阅 {FACE_DETECT_TOPIC}")
        else:
            logger.error(f"[MQTT] 连接失败: rc={rc}")

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            with _face_lock:
                _face_context["count"] = data.get("count", 0)
                _face_context["faces"] = data.get("faces", [])
                _face_context["last_update"] = time.time()
        except Exception as e:
            logger.error(f"[MQTT] 消息解析失败: {e}")

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_forever()
    except Exception as e:
        logger.error(f"[MQTT] 连接失败: {e}")


# ===== TCP 语音处理 =====
def handle_client(conn, addr):
    logger.info(f"[TCP] 新连接: {addr}")
    audio_chunks = []

    try:
        while True:
            ftype, data = recv_frame(conn)
            if ftype is None:
                break

            if ftype == MSG_AUDIO:
                audio_chunks.append(data)

            elif ftype == MSG_CMD:
                cmd = json.loads(data.decode())
                action = cmd.get("action", "")

                if action == "start_recording":
                    logger.info("[TCP] 开始接收音频...")
                    audio_chunks = []

                elif action == "stop_recording":
                    logger.info(f"[TCP] 音频接收完毕，共 {sum(len(c) for c in audio_chunks)} 字节")

                    if not audio_chunks:
                        logger.warning("[TCP] 无音频数据")
                        break

                    pcm_data = b"".join(audio_chunks)
                    wav_bytes = pcm_to_wav_bytes(pcm_data)

                    # ASR
                    logger.info("[ASR] 识别中...")
                    user_text = speech_to_text(wav_bytes)
                    logger.info(f"[ASR] 用户说: {user_text}")

                    if not user_text:
                        send_frame(conn, MSG_CMD, json.dumps(
                            {"action": "info", "msg": "未识别到内容"}).encode())
                        send_frame(conn, MSG_DONE, b"")
                        break

                    # LLM
                    logger.info("[LLM] 思考中...")
                    clean_text, actions = chat_with_context(user_text)
                    logger.info(f"[LLM] 回复: {clean_text}")
                    if actions:
                        logger.info(f"[LLM] 指令: {actions}")

                    # TTS
                    logger.info("[TTS] 合成中...")
                    tts_wav = text_to_speech(clean_text)
                    tts_pcm = wav_to_pcm(tts_wav)
                    logger.info(f"[TTS] 合成完成: {len(tts_pcm)} 字节 PCM")

                    # 发送 TTS 音频
                    chunk_size = 4096
                    for i in range(0, len(tts_pcm), chunk_size):
                        send_frame(conn, MSG_AUDIO, tts_pcm[i:i + chunk_size])

                    # 发送动作指令
                    for act in actions:
                        send_frame(conn, MSG_CMD, json.dumps(act).encode())

                    # 发送回复文本
                    send_frame(conn, MSG_CMD, json.dumps(
                        {"action": "reply", "text": clean_text}).encode())

                    send_frame(conn, MSG_DONE, b"")
                    break

    except Exception as e:
        logger.error(f"[TCP] 处理异常: {e}")
    finally:
        conn.close()
        logger.info(f"[TCP] 连接关闭: {addr}")


# ===== TCP 服务器 =====
def start_tcp_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((TCP_HOST, TCP_PORT))
    server.listen(5)
    logger.info(f"[TCP] 语音服务器启动: {TCP_HOST}:{TCP_PORT}")

    while True:
        conn, addr = server.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
        t.start()


# ===== 主程序 =====
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("  语音桥接服务器 (TCP + MQTT + MiMo API)")
    logger.info("=" * 50)

    # MQTT 订阅线程
    if HAS_PAHO:
        mqtt_thread = threading.Thread(target=start_mqtt_subscriber, daemon=True)
        mqtt_thread.start()

    # TCP 服务器（主线程）
    start_tcp_server()
