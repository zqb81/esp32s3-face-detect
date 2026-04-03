# ESP32-S3 人脸检测 + TFT 显示
# 使用 mp_esp_dl_models 固件

import camera
import machine
from espdl import FaceDetector, RGB565
import time

# ===== 摄像头配置 (OV5640 并行接口) =====
# 引脚已在固件中配置，直接初始化即可

# ===== TFT 配置 (ST7735 1.8寸) =====
TFT_SCL = 14
TFT_SDA = 21  # MOSI
TFT_CS  = 2
TFT_DC  = 1
TFT_RST = 3
TFT_BL  = 47

# TFT 尺寸
TFT_WIDTH = 128
TFT_HEIGHT = 160

# 显示缩放 (QVGA 320x240 -> TFT 128x160)
SCALE_X = 320 // TFT_WIDTH   # 2.5 -> 取整 2
SCALE_Y = 240 // TFT_HEIGHT  # 1.5 -> 取整 1

# ===== 初始化 TFT (SPI) =====
spi = machine.SPI(1, baudrate=40000000, polarity=0, phase=0,
                  sck=machine.Pin(TFT_SCL),
                  mosi=machine.Pin(TFT_SDA))

cs  = machine.Pin(TFT_CS,  machine.Pin.OUT)
dc  = machine.Pin(TFT_DC,  machine.Pin.OUT)
rst = machine.Pin(TFT_RST, machine.Pin.OUT)
bl  = machine.Pin(TFT_BL,  machine.Pin.OUT)

# ST7735 初始化序列
def tft_cmd(cmd, data=None):
    dc.value(0)
    cs.value(0)
    spi.write(bytes([cmd]))
    if data:
        dc.value(1)
        spi.write(bytes(data))
    cs.value(1)

def tft_init():
    rst.value(0)
    time.sleep_ms(10)
    rst.value(1)
    time.sleep_ms(10)
    
    tft_cmd(0x01)  # Software reset
    time.sleep_ms(150)
    tft_cmd(0x11)  # Sleep out
    time.sleep_ms(255)
    tft_cmd(0x3A, [0x05])  # 16-bit color
    tft_cmd(0x36, [0xC0])  # MADCTL: RGB, row/col exchange
    tft_cmd(0x29)  # Display on
    
    # 设置显示窗口
    tft_cmd(0x2A, [0x00, 0x00, 0x00, TFT_WIDTH-1])   # Column addr set
    tft_cmd(0x2B, [0x00, 0x00, 0x00, TFT_HEIGHT-1])  # Row addr set
    
    bl.value(1)  # 打开背光

def tft_pixel(x, y, color):
    """画一个像素 (RGB565)"""
    if 0 <= x < TFT_WIDTH and 0 <= y < TFT_HEIGHT:
        tft_cmd(0x2A, [0, x, 0, x])
        tft_cmd(0x2B, [0, y, 0, y])
        tft_cmd(0x2C)
        dc.value(1)
        cs.value(0)
        spi.write(color.to_bytes(2, 'big'))
        cs.value(1)

def tft_rect(x, y, w, h, color):
    """画一个矩形框"""
    for i in range(w):
        tft_pixel(x + i, y, color)
        tft_pixel(x + i, y + h - 1, color)
    for i in range(h):
        tft_pixel(x, y + i, color)
        tft_pixel(x + w - 1, y + i, color)

def tft_fill_rect(x, y, w, h, color):
    """填充一个矩形"""
    tft_cmd(0x2A, [0, x, 0, x+w-1])
    tft_cmd(0x2B, [0, y, 0, y+h-1])
    tft_cmd(0x2C)
    dc.value(1)
    cs.value(0)
    pixels = bytes([color >> 8, color & 0xFF] * w * h)
    spi.write(pixels)
    cs.value(1)

def tft_clear():
    """清屏"""
    tft_fill_rect(0, 0, TFT_WIDTH, TFT_HEIGHT, 0x0000)

def rgb565_to_rgb565_be(r, g, b):
    """RGB888 转 RGB565 (big-endian)"""
    color = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return color

# ===== 初始化硬件 =====
print("初始化 TFT...")
tft_init()
tft_clear()

print("初始化摄像头...")
cam = camera.Camera(
    pixel_format=camera.RGB565,
    frame_size=camera.QVGA,  # 320x240
)
cam.init()

print("创建人脸检测器...")
detector = FaceDetector(width=320, height=240, pixel_format=RGB565)

# 颜色定义
COLOR_GREEN  = rgb565_to_rgb565_be(0, 255, 0)
COLOR_RED    = rgb565_to_rgb565_be(255, 0, 0)
COLOR_BLUE   = rgb565_to_rgb565_be(0, 0, 255)
COLOR_YELLOW = rgb565_to_rgb565_be(255, 255, 0)
COLOR_WHITE  = 0xFFFF
COLOR_BLACK  = 0x0000

print("开始人脸检测...")
print("按 Ctrl+C 停止")

try:
    frame_count = 0
    while True:
        # 捕获一帧
        img = cam.capture()
        
        # 检测人脸
        faces = detector.detect(img)
        
        # 清屏
        tft_clear()
        
        # 显示帧数
        # (简化版：不显示文字，只画框)
        
        if faces:
            print(f"[帧 {frame_count}] 检测到 {len(faces)} 张人脸")
            for i, face in enumerate(faces):
                box = face["box"]
                score = face["score"]
                
                # 坐标缩放 (320x240 -> 128x160)
                x1 = box[0] * TFT_WIDTH // 320
                y1 = box[1] * TFT_HEIGHT // 240
                x2 = box[2] * TFT_WIDTH // 320
                y2 = box[3] * TFT_HEIGHT // 240
                
                w = x2 - x1
                h = y2 - y1
                
                # 根据置信度选择颜色
                if score > 0.8:
                    color = COLOR_GREEN
                elif score > 0.6:
                    color = COLOR_YELLOW
                else:
                    color = COLOR_RED
                
                # 画人脸框
                tft_rect(x1, y1, w, h, color)
                
                # 画关键点（如果有）
                if face["features"]:
                    features = face["features"]
                    for j in range(0, 10, 2):
                        px = features[j] * TFT_WIDTH // 320
                        py = features[j+1] * TFT_HEIGHT // 240
                        tft_pixel(px, py, COLOR_BLUE)
                        tft_pixel(px+1, py, COLOR_BLUE)
                        tft_pixel(px, py+1, COLOR_BLUE)
                        tft_pixel(px+1, py+1, COLOR_BLUE)
                
                print(f"  人脸{i+1}: 置信度={score:.2f}, 位置=({x1},{y1},{x2},{y2})")
        else:
            if frame_count % 30 == 0:
                print(f"[帧 {frame_count}] 未检测到人脸")
        
        frame_count += 1
                
except KeyboardInterrupt:
    print("\n停止检测")
    tft_clear()
    bl.value(0)  # 关闭背光
    cam.deinit()
