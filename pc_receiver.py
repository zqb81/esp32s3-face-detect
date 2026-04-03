"""
电脑端 MQTT 接收人脸检测结果
依赖：pip install paho-mqtt
"""

import json
import paho.mqtt.client as mqtt

# 配置
MQTT_BROKER = "101.33.209.65"
MQTT_PORT = 1883
MQTT_TOPIC = "esp32/face_detect"

# 统计
msg_count = 0
total_faces = 0

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✅ 已连接 MQTT: {MQTT_BROKER}")
        client.subscribe(MQTT_TOPIC)
        print(f"📡 订阅: {MQTT_TOPIC}")
    else:
        print(f"❌ 连接失败: {rc}")

def on_message(client, userdata, msg):
    global msg_count, total_faces
    try:
        data = json.loads(msg.payload.decode())
        msg_count += 1
        total_faces += data["face_count"]
        
        print(f"\n{'='*50}")
        print(f"📨 消息 #{msg_count} | 设备: {data['device']}")
        print(f"   帧号: {data['frame']} | 检测到 {data['face_count']} 张人脸")
        
        for i, face in enumerate(data["faces"]):
            score = face["score"]
            box = face["box"]
            print(f"   👤 人脸{i+1}: 置信度={score:.1%} 位置=({box[0]},{box[1]})-({box[2]},{box[3]})")
            
            if "keypoints" in face:
                kp = face["keypoints"]
                print(f"      关键点: 左眼{kp['left_eye']} 右眼{kp['right_eye']} 鼻子{kp['nose']}")
        
        print(f"{'='*50}")
        
    except Exception as e:
        print(f"解析错误: {e}")
        print(f"原始数据: {msg.payload[:200]}")

def on_disconnect(client, userdata, rc):
    print(f"⚠️ 断开连接: {rc}")

def main():
    print("🖥️  ESP32-S3 人脸检测接收端")
    print(f"   MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"   Topic: {MQTT_TOPIC}")
    print("   Ctrl+C 退出\n")
    
    client = mqtt.Client(client_id="pc_face_receiver")
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        print(f"\n\n📊 统计: 收到 {msg_count} 条消息, 共 {total_faces} 张人脸")
        client.disconnect()
    except Exception as e:
        print(f"错误: {e}")

if __name__ == "__main__":
    main()
