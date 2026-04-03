# ESP32-S3 人脸检测 + WiFi + MQTT
# 固件：mp_esp_dl_models (espdl 模块)

import camera
import network
import time
import json
from umqtt.simple import MQTTClient
from espdl import FaceDetector, RGB565

# ===== 配置 =====
WIFI_SSID = "APP"
WIFI_PASS = "123456789"
MQTT_BROKER = "101.33.209.65"
MQTT_PORT = 1883
MQTT_TOPIC = "esp32/face_detect"
CLIENT_ID = "esp32s3_face"

# ===== 连接 WiFi =====
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print(f"连接 WiFi: {WIFI_SSID}")
        wlan.connect(WIFI_SSID, WIFI_PASS)
        timeout = 10
        while not wlan.isconnected() and timeout > 0:
            time.sleep(1)
            timeout -= 1
    if wlan.isconnected():
        print(f"WiFi 已连接: {wlan.ifconfig()}")
        return True
    else:
        print("WiFi 连接失败")
        return False

# ===== 连接 MQTT =====
def connect_mqtt():
    client = MQTTClient(CLIENT_ID, MQTT_BROKER, port=MQTT_PORT)
    try:
        client.connect()
        print(f"MQTT 已连接: {MQTT_BROKER}")
        return client
    except Exception as e:
        print(f"MQTT 连接失败: {e}")
        return None

# ===== 主程序 =====
def main():
    # 1. 连接网络
    if not connect_wifi():
        return
    
    # 2. 连接 MQTT
    mqtt = connect_mqtt()
    if not mqtt:
        return
    
    # 3. 初始化摄像头
    print("初始化摄像头...")
    cam = camera.Camera(
        pixel_format=camera.RGB565,
        frame_size=camera.QVGA,
    )
    cam.init()
    
    # 4. 创建人脸检测器
    print("创建人脸检测器...")
    detector = FaceDetector(width=320, height=240, pixel_format=RGB565)
    
    print("开始人脸检测 + MQTT 发送...")
    frame_count = 0
    send_count = 0
    
    try:
        while True:
            # 捕获一帧
            img = cam.capture()
            if img is None:
                time.sleep_ms(100)
                continue
            
            # 检测人脸
            faces = detector.detect(img)
            frame_count += 1
            
            # 有人脸时发送
            if faces:
                # 构建消息
                result = {
                    "device": CLIENT_ID,
                    "frame": frame_count,
                    "timestamp": time.time(),
                    "face_count": len(faces),
                    "faces": []
                }
                
                for face in faces:
                    face_data = {
                        "score": round(face["score"], 3),
                        "box": list(face["box"]),  # [x1, y1, x2, y2]
                    }
                    if face["features"]:
                        face_data["keypoints"] = {
                            "left_eye":  list(face["features"][0:2]),
                            "right_eye": list(face["features"][2:4]),
                            "nose":      list(face["features"][4:6]),
                            "left_mouth":  list(face["features"][6:8]),
                            "right_mouth": list(face["features"][8:10]),
                        }
                    result["faces"].append(face_data)
                
                # 发送 MQTT
                try:
                    msg = json.dumps(result)
                    mqtt.publish(MQTT_TOPIC, msg)
                    send_count += 1
                    print(f"[帧 {frame_count}] 发送: {len(faces)} 张人脸 (总计 {send_count})")
                except Exception as e:
                    print(f"MQTT 发送失败: {e}")
                    # 重连
                    try:
                        mqtt.connect()
                    except:
                        pass
            else:
                if frame_count % 60 == 0:
                    print(f"[帧 {frame_count}] 未检测到人脸")
            
            # 简单节流，避免 CPU 100%
            time.sleep_ms(50)
            
    except KeyboardInterrupt:
        print(f"\n停止 (共 {frame_count} 帧, 发送 {send_count} 次)")
    finally:
        try:
            mqtt.disconnect()
        except:
            pass
        cam.deinit()

if __name__ == "__main__":
    main()
