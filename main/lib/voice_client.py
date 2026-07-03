# voice_client.py — TCP 语音客户端
# 连接 Server，录音上传，接收 TTS 播放
import gc
import json
import struct
import socket
import time
import voice_hal as hw

# ===== 消息协议 =====
MSG_AUDIO = 0x01
MSG_CMD   = 0x02
MSG_DONE  = 0x03
_MAX_FRAME_LEN = 256 * 1024

# ===== Server 配置（由 main.py 从 config.json 覆盖）=====
SERVER_IP = ""
SERVER_PORT = 9000

# ===== TCP 帧收发 =====

def _send_frame(sock, frame_type, data):
    header = struct.pack(">BI", frame_type, len(data))
    sock.sendall(header)
    sock.sendall(data)


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 4096))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _recv_frame(sock):
    header = _recv_exact(sock, 5)
    if header is None:
        return None, None
    frame_type, length = struct.unpack(">BI", header)
    if length > _MAX_FRAME_LEN:
        return None, None
    data = _recv_exact(sock, length) if length > 0 else b""
    return frame_type, data


# ===== 录音并上传 =====

def record_and_upload(sock):
    """按键录音，流式上传到 Server"""
    buf = bytearray(hw.BUF_SIZE)
    hw.clear_button_state()
    hw.init_mic()
    time.sleep_ms(100)

    print("[REC] 开始录音，松开按键结束...")
    _send_frame(sock, MSG_CMD, json.dumps({"action": "start_recording"}).encode())

    total_bytes = 0
    max_bytes = hw.SAMPLE_RATE * 2 * hw.MAX_RECORD_SEC

    try:
        while not hw.consume_button_release() and total_bytes < max_bytes:
            n = hw.mic_read(buf)
            if n > 0:
                _send_frame(sock, MSG_AUDIO, bytes(buf[:n]))
                total_bytes += n
                if total_bytes % (hw.BUF_SIZE * 10) == 0:
                    gc.collect()
            time.sleep_ms(1)
    finally:
        hw.deinit_mic()

    _send_frame(sock, MSG_CMD, json.dumps({"action": "stop_recording"}).encode())
    print(f"[REC] 录音结束，共 {total_bytes} 字节")
    gc.collect()
    return total_bytes


# ===== 接收 TTS 并播放 =====

def receive_and_play(sock):
    """接收 Server 返回的 TTS 音频并播放，返回动作列表"""
    actions = []
    hw.init_speaker()
    print("[PLAY] 接收回复...")

    try:
        while True:
            ftype, data = _recv_frame(sock)
            if ftype is None or ftype == MSG_DONE:
                break

            if ftype == MSG_AUDIO:
                hw.speaker_write(data)
                time.sleep_ms(1)

            elif ftype == MSG_CMD:
                try:
                    cmd = json.loads(data.decode())
                    print(f"[CMD] 收到指令: {cmd}")
                    actions.append(cmd)
                except Exception as e:
                    print(f"[CMD] 解析失败: {e}")
    finally:
        hw.deinit_speaker()

    print("[PLAY] 播放完毕")
    gc.collect()
    return actions


# ===== 完整语音交互流程 =====

def voice_interaction():
    """
    完整语音交互：连接 → 录音 → 等待 ASR+LLM+TTS → 播放回复
    返回 (success, actions)
    """
    sock = None
    try:
        print(f"[TCP] 连接 {SERVER_IP}:{SERVER_PORT} ...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect((SERVER_IP, SERVER_PORT))
        print("[TCP] 连接成功")

        # 录音
        n = record_and_upload(sock)
        if n == 0:
            print("[VOICE] 未录到音频")
            return False, []

        # 接收回复
        sock.settimeout(60)  # ASR+LLM+TTS 可能需要较长时间
        actions = receive_and_play(sock)
        return True, actions

    except Exception as e:
        print(f"[VOICE ERR] {e}")
        return False, []
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        gc.collect()
