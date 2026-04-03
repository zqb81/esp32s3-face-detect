# ESP32-S3 人脸检测示例
# 使用 mp_esp_dl_models 固件

import camera
from espdl import FaceDetector, RGB565

# ===== 摄像头配置 (OV5640 并行接口) =====
# 引脚配置
CAM_PINS = {
    "d0": 11, "d1": 9, "d2": 8, "d3": 10, "d4": 12,
    "d5": 18, "d6": 17, "d7": 16,
    "xclk": 15, "pclk": 13, "vsync": 6, "href": 7,
    "sda": 4, "scl": 5,
}

# 初始化摄像头
cam = camera.Camera(
    pixel_format=camera.RGB565,
    frame_size=camera.QVGA,  # 320x240
)
cam.init()  # 使用默认引脚配置

# 创建人脸检测器
detector = FaceDetector(width=320, height=240, pixel_format=RGB565)

print("开始人脸检测...")
print("按 Ctrl+C 停止")

try:
    frame_count = 0
    while True:
        # 捕获一帧
        img = cam.capture()
        
        # 检测人脸
        faces = detector.detect(img)
        
        frame_count += 1
        if faces:
            print(f"[帧 {frame_count}] 检测到 {len(faces)} 张人脸:")
            for i, face in enumerate(faces):
                box = face["box"]
                score = face["score"]
                print(f"  人脸{i+1}: 置信度={score:.2f}, 位置=({box[0]},{box[1]},{box[2]},{box[3]})")
                
                # 如果有关键点信息
                if face["features"]:
                    features = face["features"]
                    print(f"    关键点: 左眼{features[0:2]}, 右眼{features[2:4]}, 鼻子{features[4:6]}, 左嘴角{features[6:8]}, 右嘴角{features[8:10]}")
        else:
            if frame_count % 30 == 0:
                print(f"[帧 {frame_count}] 未检测到人脸")
                
except KeyboardInterrupt:
    print("\n停止检测")
    cam.deinit()
