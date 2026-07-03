# ESP32-S3 人脸检测完整版
# 摄像头 → 人脸检测 → TFT 显示 → MQTT 发送

import camera
import machine
import network
import time
import json
from umqtt.simple import MQTTClient
from espdl import FaceDetector
# espdl.RGB565 = 6, espdl.RGB888 = 0, espdl.GRAYSCALE = 3
DL_RGB565 = 6

# ===== 配置 =====
WIFI_SSID = "APP"
WIFI_PASS = "123456789"
MQTT_BROKER = "101.33.209.65"
MQTT_PORT = 1883
MQTT_TOPIC = "esp32/face_detect"
CLIENT_ID = "esp32s3_face"

# TFT 引脚 (ST7735 1.8寸)
TFT_SCL = 14
TFT_SDA = 21
TFT_CS  = 2
TFT_DC  = 1
TFT_RST = 3
TFT_BL  = 47

# TFT 尺寸
TFT_W = 128
TFT_H = 160

# 摄像头尺寸
CAM_W = 320
CAM_H = 240

# 颜色 (RGB565 big-endian)
BLACK   = 0x0000
WHITE   = 0xFFFF
GREEN   = 0x07E0
RED     = 0xF800
YELLOW  = 0xFFE0
BLUE    = 0x001F

# ===== TFT 驱动 (SPI) =====
spi = machine.SPI(1, baudrate=40000000, polarity=0, phase=0,
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
    tft_cmd(0x01); time.sleep_ms(150)  # SW reset
    tft_cmd(0x11); time.sleep_ms(255)  # Sleep out
    tft_cmd(0x3A, [0x05])  # 16-bit color
    tft_cmd(0x36, [0xC0])  # MADCTL
    tft_cmd(0x29)  # Display on
    bl.value(1)

def tft_set_window(x, y, w, h):
    tft_cmd(0x2A, [0, x, 0, x + w - 1])
    tft_cmd(0x2B, [0, y, 0, y + h - 1])
    tft_cmd(0x2C)

def tft_pixel(x, y, color):
    if 0 <= x < TFT_W and 0 <= y < TFT_H:
        tft_set_window(x, y, 1, 1)
        dc.value(1); cs.value(0)
        spi.write(color.to_bytes(2, 'big'))
        cs.value(1)

def tft_hline(x, y, w, color):
    if y < 0 or y >= TFT_H: return
    x0 = max(0, x)
    x1 = min(TFT_W, x + w)
    if x0 >= x1: return
    tft_set_window(x0, y, x1 - x0, 1)
    dc.value(1); cs.value(0)
    c = color.to_bytes(2, 'big')
    spi.write(c * (x1 - x0))
    cs.value(1)

def tft_vline(x, y, h, color):
    if x < 0 or x >= TFT_W: return
    y0 = max(0, y)
    y1 = min(TFT_H, y + h)
    if y0 >= y1: return
    tft_set_window(x, y0, 1, y1 - y0)
    dc.value(1); cs.value(0)
    c = color.to_bytes(2, 'big')
    spi.write(c * (y1 - y0))
    cs.value(1)

def tft_rect(x, y, w, h, color):
    tft_hline(x, y, w, color)
    tft_hline(x, y + h - 1, w, color)
    tft_vline(x, y, h, color)
    tft_vline(x + w - 1, y, h, color)

def tft_clear():
    tft_set_window(0, 0, TFT_W, TFT_H)
    dc.value(1); cs.value(0)
    spi.write(b'\x00\x00' * TFT_W * TFT_H)
    cs.value(1)

# 坐标缩放
def scale_x(cx): return cx * TFT_W // CAM_W
def scale_y(cy): return cy * TFT_H // CAM_H

# ===== WiFi =====
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print(f"连接 WiFi: {WIFI_SSID}")
        wlan.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(15):
            if wlan.isconnected(): break
            time.sleep(1)
    if wlan.isconnected():
        print(f"WiFi OK: {wlan.ifconfig()[0]}")
        return True
    print("WiFi 失败")
    return False

# ===== MQTT =====
mqtt = None
mqtt_ok = False

def connect_mqtt():
    global mqtt, mqtt_ok
    try:
        mqtt = MQTTClient(CLIENT_ID, MQTT_BROKER, port=MQTT_PORT)
        mqtt.connect()
        mqtt_ok = True
        print(f"MQTT OK: {MQTT_BROKER}")
    except Exception as e:
        mqtt_ok = False
        print(f"MQTT 失败: {e}")

def send_mqtt(faces):
    global mqtt_ok
    if not mqtt_ok or not faces:
        return
    try:
        msg = json.dumps({
            "device": CLIENT_ID,
            "ts": time.time(),
            "count": len(faces),
            "faces": [{
                "score": round(f["score"], 3),
                "box": list(f["box"]),
            } for f in faces]
        })
        mqtt.publish(MQTT_TOPIC, msg)
    except Exception as e:
        print(f"MQTT 发送失败: {e}")
        mqtt_ok = False
        try: mqtt.connect(); mqtt_ok = True
        except: pass

# ===== 主程序 =====
def main():
    print("=== ESP32-S3 人脸检测 ===")

    # 1. TFT
    print("初始化 TFT...")
    tft_init()
    tft_clear()
    tft_rect(0, 0, TFT_W, TFT_H, WHITE)
    tft_rect(2, 2, TFT_W-4, TFT_H-4, GREEN)

    # 2. WiFi
    tft_clear()
    wifi_ok = connect_wifi()
    status_color = GREEN if wifi_ok else RED

    # 3. MQTT
    if wifi_ok:
        connect_mqtt()

    # 4. 摄像头
    print("初始化摄像头...")
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

    # 5. 人脸检测器
    print("创建检测器...")
    detector = FaceDetector(width=CAM_W, height=CAM_H, pixel_format=DL_RGB565)

    print("开始检测...")
    frame = 0
    fps_t = time.ticks_ms()
    fps_n = 0

    try:
        while True:
            img = cam.capture()
            if img is None:
                time.sleep_ms(50)
                continue

            faces = detector.run(img)
            if faces is None:
                faces = []
            frame += 1
            fps_n += 1

            # 清屏
            tft_clear()

            # 画人脸框
            if faces:
                for f in faces:
                    box = f["box"]
                    x1 = scale_x(box[0])
                    y1 = scale_y(box[1])
                    x2 = scale_x(box[2])
                    y2 = scale_y(box[3])
                    w = x2 - x1
                    h = y2 - y1

                    # 置信度颜色
                    if f["score"] > 0.8:
                        c = GREEN
                    elif f["score"] > 0.6:
                        c = YELLOW
                    else:
                        c = RED

                    tft_rect(x1, y1, w, h, c)

                    # 关键点
                    if f["features"]:
                        for i in range(0, 10, 2):
                            px = scale_x(f["features"][i])
                            py = scale_y(f["features"][i+1])
                            tft_pixel(px, py, BLUE)
                            tft_pixel(px+1, py, BLUE)
                            tft_pixel(px, py+1, BLUE)

                # MQTT 发送
                send_mqtt(faces)

            # FPS 统计
            elapsed = time.ticks_diff(time.ticks_ms(), fps_t)
            if elapsed >= 1000:
                fps = fps_n * 1000 / elapsed
                print(f"FPS: {fps:.1f} | 人脸: {len(faces)} | 帧: {frame}")
                fps_t = time.ticks_ms()
                fps_n = 0

    except KeyboardInterrupt:
        print(f"\n停止 (共 {frame} 帧)")
    finally:
        tft_clear()
        bl.value(0)
        cam.deinit()
        if mqtt:
            try: mqtt.disconnect()
            except: pass

if __name__ == "__main__":
    main()
