# ESP32-S3 完整链路：摄像头→人脸检测→TFT实时画面→MQTT→Web
# 实时画面 + 人脸框叠加 + WiFi + MQTT
VERSION = "v2.2"

# ===== 变更日志 =====
# v1.0 - 初始版本
# v1.1 - NTP时间同步 + 版本号机制
# v1.2 - 摄像头上下翻转
# v1.3 - NTP使用utime.mktime()/localtime()
# v1.4 - 修复时间戳：用rtc_to_timestamp()替代time.time()
# v1.5 - 全画面缩放替代中心裁切
# v1.8 - 回退QVGA方案：320x240统一，检测显示共用帧，帧率优化
# v1.9 - 人脸裁剪上传：64×64 RGB565 base64 → MQTT
# v2.0 - 语音控制集成：I2S麦克风+喇叭，按键语音交互，ASR/LLM/TTS
# v2.1 - 语音异步化(_thread)，不阻塞主循环；启动日志美化；config.json外置
# v2.2 - MQTT双向控制(订阅esp32/iot/cmd/#)；Web RGB选色；服务端合并单进程
# =====

import sys
sys.path.insert(0, 'lib')

import camera
import machine
import network
import time
import json
from umqtt.simple import MQTTClient
import gc
import ubinascii
import _thread
import voice_hal as voice_hw
import voice_client as voice

# ===== 配置（从 config.json 读取，改配置不用改代码）=====
def _load_config():
    try:
        with open("config.json", "r") as f:
            return json.loads(f.read())
    except Exception as e:
        print("[WARN] config.json 读取失败:", e, "，使用默认值")
        return {}

_cfg = _load_config()
WIFI_SSID   = _cfg.get("wifi_ssid", "APP")
WIFI_PASS   = _cfg.get("wifi_pass", "123456789")
MQTT_BROKER = _cfg.get("mqtt_broker", "101.33.209.65")
MQTT_PORT   = _cfg.get("mqtt_port", 1883)
MQTT_TOPIC  = _cfg.get("mqtt_topic", "esp32/face_detect")
CLIENT_ID   = _cfg.get("client_id", "esp32s3_face")

# ===== 语音 Server 配置 =====
voice.SERVER_IP   = _cfg.get("voice_server_ip", MQTT_BROKER)
voice.SERVER_PORT = _cfg.get("voice_server_port", 9000)
del _cfg  # 释放内存

# ===== 人脸检测控制 =====
face_detect_enabled = True

# ===== 语音异步控制 =====
_voice_busy = False          # 语音线程正在运行
_voice_actions = []          # 语音线程返回的动作队列
_voice_lock = _thread.allocate_lock()

# ===== TFT 配置 (ST7735 1.8寸) =====
TFT_SCL = 14
TFT_SDA = 21
TFT_CS  = 2
TFT_DC  = 1
TFT_RST = 3
TFT_BL  = 47

# 尺寸
TFT_W = 128
TFT_H = 160
CAM_W = 320
CAM_H = 240

DST_W = 128
DST_H = 160
BOX_SCALE = 1.3  # 检测框放大系数
CROP_SIZE = 64   # 人脸裁剪尺寸
CROP_INTERVAL = 1000  # 最短上传间隔(ms)

# ===== 全局Buffer（避免每帧分配）=====
_tft_buf = bytearray(DST_W * DST_H * 2)

# ===== TFT SPI 驱动 =====
spi = machine.SPI(1, baudrate=40_000_000, polarity=0, phase=0,
                  sck=machine.Pin(TFT_SCL),
                  mosi=machine.Pin(TFT_SDA))
cs  = machine.Pin(TFT_CS,  machine.Pin.OUT)
dc  = machine.Pin(TFT_DC,  machine.Pin.OUT)
rst = machine.Pin(TFT_RST, machine.Pin.OUT)
bl  = machine.Pin(TFT_BL,  machine.Pin.OUT)


def tft_cmd(cmd, data=None):
    dc.value(0); cs.value(0)
    spi.write(bytes([cmd]))
    if data:
        dc.value(1); spi.write(bytes(data))
    cs.value(1)


def tft_init():
    rst.value(0); time.sleep_ms(10)
    rst.value(1); time.sleep_ms(10)
    tft_cmd(0x01); time.sleep_ms(150)
    tft_cmd(0x11); time.sleep_ms(255)
    tft_cmd(0x3A, [0x05])
    tft_cmd(0x36, [0xC0])
    tft_cmd(0x29)
    bl.value(1)


def tft_set_window(x, y, w, h):
    tft_cmd(0x2A, [0, x, 0, x + w - 1])
    tft_cmd(0x2B, [0, y, 0, y + h - 1])
    tft_cmd(0x2C)


def tft_draw_frame(buf):
    """一次性写入整帧 (128x160 RGB565)"""
    tft_set_window(0, 0, TFT_W, TFT_H)
    dc.value(1); cs.value(0)
    spi.write(buf)
    cs.value(1)


# ===== 帧处理 =====
def downsample_frame(cam_buf):
    """QVGA全画面+上下翻转：320x240 → 128x160，直接写_tft_buf"""
    src_stride = CAM_W * 2  # 640 bytes/row
    dst_off = 0
    src_y = 0
    for row in range(DST_H):
        src_row = CAM_H - 1 - src_y  # 上下翻转
        src_row_off = src_row * src_stride
        for col in range(DST_W):
            src_off = src_row_off + col * 4
            _tft_buf[dst_off] = cam_buf[src_off]
            _tft_buf[dst_off + 1] = cam_buf[src_off + 1]
            dst_off += 2
        if row % 3 == 2:
            src_y += 2
        else:
            src_y += 1


def draw_overlay(faces):
    """在帧缓冲区上画人脸框"""
    for f in faces:
        box = f["box"]
        cx = (box[0] + box[2]) / 2 * DST_W / CAM_W
        cy = DST_H - 1 - (box[1] + box[3]) / 2 * DST_H / CAM_H
        hw = (box[2] - box[0]) * DST_W / CAM_W / 2 * BOX_SCALE
        hh = (box[3] - box[1]) * DST_H / CAM_H / 2 * BOX_SCALE
        dx1 = max(0, int(cx - hw))
        dy1 = max(0, int(cy - hh))
        dx2 = min(DST_W - 1, int(cx + hw))
        dy2 = min(DST_H - 1, int(cy + hh))
        w = dx2 - dx1
        h = dy2 - dy1
        if w <= 0 or h <= 0:
            continue

        score = f["score"]
        if score > 0.8:
            c_hi, c_lo = 0x07, 0xE0  # GREEN
        elif score > 0.6:
            c_hi, c_lo = 0xFF, 0xE0  # YELLOW
        else:
            c_hi, c_lo = 0xF8, 0x00  # RED

        for x in range(dx1, dx2 + 1):
            if 0 <= x < DST_W:
                if 0 <= dy1 < DST_H:
                    off = (dy1 * DST_W + x) * 2
                    _tft_buf[off] = c_hi; _tft_buf[off + 1] = c_lo
                if 0 <= dy2 < DST_H:
                    off = (dy2 * DST_W + x) * 2
                    _tft_buf[off] = c_hi; _tft_buf[off + 1] = c_lo
        for y in range(dy1, dy2 + 1):
            if 0 <= y < DST_H:
                if 0 <= dx1 < DST_W:
                    off = (y * DST_W + dx1) * 2
                    _tft_buf[off] = c_hi; _tft_buf[off + 1] = c_lo
                if 0 <= dx2 < DST_W:
                    off = (y * DST_W + dx2) * 2
                    _tft_buf[off] = c_hi; _tft_buf[off + 1] = c_lo

        if f.get("features"):
            for i in range(0, 10, 2):
                px = int(f["features"][i] * DST_W / CAM_W)
                py = DST_H - 1 - int(f["features"][i + 1] * DST_H / CAM_H)
                if 0 <= px < DST_W and 0 <= py < DST_H:
                    off = (py * DST_W + px) * 2
                    _tft_buf[off] = 0x00; _tft_buf[off + 1] = 0x1F


# ===== WiFi =====
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print(f"连接 WiFi: {WIFI_SSID}")
        wlan.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(15):
            if wlan.isconnected():
                break
            time.sleep(1)
    if wlan.isconnected():
        print(f"WiFi OK: {wlan.ifconfig()[0]}")
        return True
    print("WiFi 失败")
    return False


# ===== NTP 时间同步 =====
import utime

def sync_ntp():
    try:
        import ntptime
    except ImportError:
        print("ntptime 模块不存在")
        _set_manual_time()
        return False
    servers = ["ntp.aliyun.com", "pool.ntp.org", "cn.ntp.org.cn"]
    for svr in servers:
        try:
            ntptime.host = svr
            ntptime.settime()
            utc_ts = utime.time()
            beijing_ts = utc_ts + 8 * 3600
            bt = utime.localtime(beijing_ts)
            import machine
            machine.RTC().datetime((bt[0], bt[1], bt[2], bt[6]+1, bt[3], bt[4], bt[5], 0))
            print(f"NTP OK ({svr}): {machine.RTC().datetime()}")
            return True
        except Exception as e:
            print(f"NTP {svr} 失败: {e}")
    _set_manual_time()
    return False

def get_timestamp():
    import machine
    t = machine.RTC().datetime()
    return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[4]:02d}:{t[5]:02d}:{t[6]:02d}"

def _days_before_month(y, m):
    d = [0,31,59,90,120,151,181,212,243,273,304,334]
    r = d[m-1]
    if m > 2 and ((y%4==0 and y%100!=0) or y%400==0): r += 1
    return r

def rtc_to_timestamp():
    import machine
    t = machine.RTC().datetime()
    days = 0
    for y in range(1970, t[0]):
        days += 366 if ((y%4==0 and y%100!=0) or y%400==0) else 365
    days += _days_before_month(t[0], t[1]) + t[2] - 1
    return days*86400 + t[4]*3600 + t[5]*60 + t[6] - 8*3600

def _set_manual_time():
    import machine
    machine.RTC().datetime((2026, 7, 4, 5, 12, 0, 0, 0))
    print(f"手动时间: {machine.RTC().datetime()}")


# ===== MQTT =====
mqtt = None
mqtt_ok = False
CMD_TOPIC = "esp32/iot/cmd/#"   # 订阅所有控制指令
_mqtt_cmds = []                 # Web 下发的指令队列

def _mqtt_callback(topic, msg):
    """MQTT 订阅回调：Web 设备控制按钮"""
    try:
        t = topic.decode() if isinstance(topic, bytes) else topic
        payload = msg.decode() if isinstance(msg, bytes) else msg
        # esp32/iot/cmd/fan → fan
        act = t.rsplit("/", 1)[-1]

        if act == "rgb":
            _mqtt_cmds.append({"action": "rgb", "color": payload})
        elif act == "led":
            state = "on" if payload not in ("0", "off") else "off"
            _mqtt_cmds.append({"action": "led", "state": state})
        elif act == "facedetect":
            _mqtt_cmds.append({"action": "face_detect", "state": payload})
        elif act in ("fan", "buzzer"):
            _mqtt_cmds.append({"action": act, "state": payload})
        else:
            print("[MQTT CMD] 未知:", t, payload)
    except Exception as e:
        print("[MQTT CMD] 解析失败:", e)

def connect_mqtt():
    global mqtt, mqtt_ok
    try:
        mqtt = MQTTClient(CLIENT_ID, MQTT_BROKER, port=MQTT_PORT)
        mqtt.set_callback(_mqtt_callback)
        mqtt.connect()
        mqtt.subscribe(CMD_TOPIC)
        mqtt_ok = True
        print("MQTT OK: %s (订阅 %s)" % (MQTT_BROKER, CMD_TOPIC))
    except Exception as e:
        mqtt_ok = False
        print(f"MQTT 失败: {e}")

def send_mqtt(faces, frame_no=0):
    global mqtt_ok
    if not mqtt_ok or not faces:
        return
    try:
        msg = json.dumps({
            "device": CLIENT_ID,
            "ts": rtc_to_timestamp(),
            "time": get_timestamp(),
            "frame": frame_no,
            "count": len(faces),
            "faces": [{
                "score": round(f["score"], 3),
                "box": list(f["box"]),
            } for f in faces]
        })
        mqtt.publish(MQTT_TOPIC, msg)
    except Exception as e:
        mqtt_ok = False
        try:
            mqtt.connect()
            mqtt_ok = True
        except:
            pass


last_crop_ts = 0  # 上次上传时间

def crop_and_send(cam_buf, face):
    """裁剪人脸区域并MQTT上传"""
    global last_crop_ts, mqtt_ok
    if not mqtt_ok:
        return

    now = time.ticks_ms()
    if time.ticks_diff(now, last_crop_ts) < CROP_INTERVAL:
        return

    box = face["box"]
    x1, y1, x2, y2 = box
    # BOX_SCALE 放大
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    hw = (x2 - x1) / 2 * BOX_SCALE
    hh = (y2 - y1) / 2 * BOX_SCALE
    sx1 = max(0, int(cx - hw))
    sy1 = max(0, int(cy - hh))
    sx2 = min(CAM_W - 1, int(cx + hw))
    sy2 = min(CAM_H - 1, int(cy + hh))
    sw = sx2 - sx1
    sh = sy2 - sy1
    if sw <= 0 or sh <= 0:
        return

    # 采样到 CROP_SIZE × CROP_SIZE
    crop = bytearray(CROP_SIZE * CROP_SIZE * 2)
    dst_i = 0
    for dy in range(CROP_SIZE):
        sy = sy1 + dy * sh // CROP_SIZE
        if sy >= CAM_H:
            sy = CAM_H - 1
        for dx in range(CROP_SIZE):
            sx = sx1 + dx * sw // CROP_SIZE
            if sx >= CAM_W:
                sx = CAM_W - 1
            src_off = (sy * CAM_W + sx) * 2
            crop[dst_i] = cam_buf[src_off]
            crop[dst_i + 1] = cam_buf[src_off + 1]
            dst_i += 2

    b64 = ubinascii.b2a_base64(crop).decode().rstrip()

    try:
        msg = json.dumps({
            "type": "face_crop",
            "device": CLIENT_ID,
            "ts": rtc_to_timestamp(),
            "time": get_timestamp(),
            "score": round(face["score"], 3),
            "box": list(box),
            "img_w": CROP_SIZE,
            "img_h": CROP_SIZE,
            "img_data": b64
        })
        mqtt.publish(MQTT_TOPIC + "/crop", msg)
        last_crop_ts = now
    except Exception as e:
        print(f"上传人脸失败: {e}")

# ===== 人脸检测 (可选) =====
try:
    from espdl import FaceDetector
    DL_RGB565 = 6
    detector = FaceDetector(width=CAM_W, height=CAM_H, pixel_format=DL_RGB565)
    HAS_DETECTOR = True
    print("人脸检测器已加载")
except ImportError:
    detector = None
    HAS_DETECTOR = False
    print("无 espdl 模块，仅显示画面")


# ===== 初始化 =====
print()
print("╔══════════════════════════════════════════╗")
print("║  ESP32-S3 人脸检测系统  %s              ║" % VERSION)
print("║  N16R8 · OV5640 · ST7735 · INMP441      ║")
print("╚══════════════════════════════════════════╝")
print()

# 1. TFT
print("[INIT] TFT ST7735 ...", end=" ")
tft_init()
print("OK")

# 2. WiFi
print("[INIT] WiFi ...")
wifi_ok = connect_wifi()

# 3. NTP 时间同步
if wifi_ok:
    print("[INIT] NTP 时间同步 ...", end=" ")
    sync_ntp()

# 4. MQTT
if wifi_ok:
    print("[INIT] MQTT ...", end=" ")
    connect_mqtt()

# 5. 语音硬件
print("[INIT] 语音硬件 ...")
voice_hw.init_all()

# 6. 摄像头
print("[INIT] 摄像头 OV5640 ...", end=" ")
cam = camera.Camera(
    data_pins=[11, 9, 8, 10, 12, 18, 17, 16],
    pclk_pin=13,
    vsync_pin=6,
    href_pin=7,
    sda_pin=4,
    scl_pin=5,
    xclk_pin=15,
    pixel_format=camera.PixelFormat.RGB565,
    frame_size=camera.FrameSize.QVGA,
)
cam.init()
time.sleep_ms(500)
print("OK")

# 启动汇总
print()
print("[SYS] WiFi: %s | MQTT: %s | 检测: %s | 语音: 异步"
      % ("OK" if wifi_ok else "OFF",
         "OK" if mqtt_ok else "OFF",
         "ON" if HAS_DETECTOR else "OFF"))
free_mem = gc.mem_free()
print("[SYS] 空闲内存: %d KB" % (free_mem // 1024))

# ===== 语音命令执行 =====
def execute_voice_command(actions):
    """
    执行语音交互返回的动作指令。
    对齐 C 版本 voice_task.c execute_action()。
    """
    global face_detect_enabled
    for cmd in actions:
        act = cmd.get("action", "")

        if act in ("fan", "motor"):          # stub — no free GPIO
            if cmd.get("state") == "on":
                voice_hw.motor_on(cmd.get("speed", voice_hw.MOTOR_DUTY))
            else:
                voice_hw.motor_off()

        elif act == "led":                   # stub — no free GPIO
            if cmd.get("state") == "on":
                voice_hw.led_on()
            else:
                voice_hw.led_off()

        elif act == "rgb":                   # GPIO 38
            if "color" in cmd:
                voice_hw.rgb_color(cmd["color"])
            else:
                voice_hw.set_rgb_static(
                    cmd.get("r", 0), cmd.get("g", 0), cmd.get("b", 0))
            print("[VOICE] RGB →", cmd.get("color", "custom"))

        elif act == "buzzer":                # GPIO 48
            if cmd.get("state") == "on":
                voice_hw.buzzer_beep(cmd.get("times", 3))
            else:
                voice_hw.buzzer_off()

        elif act == "face_detect":           # Python 扩展动作
            state = cmd.get("state", "on")
            face_detect_enabled = (state == "on")
            print("[VOICE] 人脸检测:", "开启" if state == "on" else "关闭")

        else:
            print("[VOICE] 未知指令:", act)


def _voice_thread():
    """后台语音线程：录音 → ASR → LLM → TTS → 返回动作"""
    global _voice_busy, _voice_actions
    try:
        voice_hw.rgb_color("green")      # 录音状态指示
        ok, actions = voice.voice_interaction()
        if ok and actions:
            with _voice_lock:
                _voice_actions.extend(actions)
    except Exception as e:
        print("[VOICE ERR]", e)
    finally:
        voice_hw.rgb_color("idle")       # 恢复待机色
        _voice_busy = False
        gc.collect()


print("[SYS] 主循环启动，按 Ctrl+C 停止")
print()

# ===== 主循环 =====
frame = 0
fps_t = time.ticks_ms()
fps_n = 0
last_faces = []  # 缓存上一帧的人脸，中间帧复用

try:
    while True:
        # ===== 语音按键检测（异步启动）=====
        if voice_hw.consume_button_release() and not _voice_busy:
            _voice_busy = True
            print("[VOICE] 按键触发 → 后台录音...")
            _thread.start_new_thread(_voice_thread, ())

        # ===== 处理语音线程返回的动作 =====
        if _voice_actions:
            with _voice_lock:
                pending = list(_voice_actions)
                _voice_actions.clear()
            execute_voice_command(pending)

        # ===== 处理 MQTT 下发的控制指令（Web 按钮）=====
        if mqtt_ok:
            try:
                mqtt.check_msg()
            except:
                pass
            if _mqtt_cmds:
                execute_voice_command(_mqtt_cmds)
                _mqtt_cmds.clear()

        # 捕获
        img = cam.capture()
        if img is None:
            time.sleep_ms(10)
            continue

        cam_buf = img if isinstance(img, (bytes, bytearray)) else bytes(img)

        # 下采样到TFT（复用_tft_buf）
        downsample_frame(cam_buf)

        # 人脸检测：每5帧，直接用摄像头原帧
        if HAS_DETECTOR and face_detect_enabled and frame % 5 == 0:
            result = detector.run(img)
            last_faces = result if result else []
            send_mqtt(last_faces, frame)
            # 上传人脸裁剪图（取置信度最高的一张）
            if last_faces:
                best = max(last_faces, key=lambda f: f["score"])
                if best["score"] > 0.6:
                    crop_and_send(cam_buf, best)
        faces = last_faces

        # 画人脸框
        if faces:
            draw_overlay(faces)

        # 写入 TFT
        tft_draw_frame(_tft_buf)

        # 主动GC
        if frame % 10 == 0:
            gc.collect()

        # FPS（每 3 秒打印一次，减少刷屏）
        frame += 1
        fps_n += 1
        elapsed = time.ticks_diff(time.ticks_ms(), fps_t)
        if elapsed >= 3000:
            fps = fps_n * 1000 / elapsed
            mem_k = gc.mem_free() // 1024
            voice_tag = " 🎙" if _voice_busy else ""
            print("[RUN] %.1f FPS | 人脸 %d | 帧 %d | 内存 %dKB | MQTT %s%s"
                  % (fps, len(faces), frame, mem_k,
                     "OK" if mqtt_ok else "OFF", voice_tag))
            fps_t = time.ticks_ms()
            fps_n = 0

except KeyboardInterrupt:
    print("\n[SYS] 停止 (共 %d 帧)" % frame)
finally:
    bl.value(0)
    cam.deinit()
    voice_hw.deinit_mic()
    voice_hw.deinit_speaker()
    if mqtt:
        try:
            mqtt.disconnect()
        except:
            pass
    print("[SYS] 已关闭")
