# ESP32-S3 人脸检测系统

基于 ESP32-S3 + MicroPython 的端侧实时人脸检测，集成摄像头采集、TFT 显示、MQTT 通信和 Web 可视化。

## 硬件

| 组件 | 型号 |
|------|------|
| 主控 | ESP32-S3-WROOM-1 N16R8 |
| 摄像头 | OV5640 |
| 屏幕 | ST7735 1.8寸 TFT (128×160) |

### 引脚接线

**摄像头：**
| 信号 | GPIO |
|------|------|
| D0-D7 | 11, 9, 8, 10, 12, 18, 17, 16 |
| XCLK | 15 |
| PCLK | 13 |
| VSYNC | 6 |
| HREF | 7 |
| SDA/SCL | 4, 5 |

**TFT (SPI)：**
| 信号 | GPIO |
|------|------|
| SCK | 14 |
| MOSI | 21 |
| CS | 2 |
| DC | 1 |
| RST | 3 |
| BL | 47 |

## 固件

需要编译带 `espdl` 模块的 MicroPython 固件，基于 [cnadler86/mp_esp_dl_models](https://github.com/cnadler86/mp_esp_dl_models)。

### 编译环境

```bash
# 使用 Docker
docker run --rm -v $PWD:/project -w /project espressif/idf:release-v5.4 idf.py build
```

- 启用模块：`FaceDetector`、`HumanDetector`、`FaceRecognizer`
- 模块名：`espdl`
- 输出：`firmware.bin` (~4.2MB) + `micropython.uf2` (~8.2MB)

## 架构

```
ESP32-S3                              服务器 (101.33.209.65)
┌─────────────────────┐               ┌──────────────────────┐
│ 摄像头 (QVGA RGB565) │               │ MQTT Broker          │
│       ↓              │               │ (Mosquitto :1883)    │
│ espdl FaceDetector   │──MQTT────────→│       ↓              │
│       ↓              │               │ Flask + SocketIO     │
│ TFT 显示 + 人脸框    │               │ SQLite 存储          │
│       ↓              │               │ WebSocket 实时推送   │
│ 裁剪人脸 64×64       │──MQTT crop───→│       ↓              │
└─────────────────────┘               │ Web 页面 (:8080)     │
                                      └──────────────────────┘
```

## 功能

### ESP32 端 (`camera_display.py`)
- 实时摄像头画面 → TFT 显示
- 端侧人脸检测（每 5 帧，espdl 模型）
- 检测框颜色编码：绿(>80%) / 黄(>60%) / 红(<60%)
- 5 个关键点标记
- NTP 时间同步
- MQTT 上报检测结果 + 人脸裁剪图
- 帧率 ~4.5 FPS (QVGA)

### 服务器端 (`web/`)
- Flask + SocketIO 后端
- SQLite 存储检测记录 + 人脸图片
- WebSocket 实时推送检测数据
- Web 仪表盘：检测统计、实时数据、人脸图片展示
- REST API：`/api/detections`、`/api/face_images`

## 版本历史

| 版本 | 日期 | 更新 |
|------|------|------|
| v1.0 | 2026-03-31 | 初始版本：摄像头→检测→TFT→MQTT→Web |
| v1.1 | 2026-03-31 | NTP 时间同步 + 版本号机制 |
| v1.2 | 2026-03-31 | 摄像头上下翻转 |
| v1.3 | 2026-03-31 | NTP 使用 utime.mktime() |
| v1.4 | 2026-03-31 | 修复时间戳：rtc_to_timestamp() |
| v1.5 | 2026-03-31 | 全画面缩放替代中心裁切 |
| v1.6 | 2026-04-01 | VGA 640×480 + 检测下采样 |
| v1.7 | 2026-04-01 | 帧率优化：buffer 复用 + GC |
| v1.8 | 2026-04-02 | 回退 QVGA，FPS ~4.5 |
| v1.9 | 2026-04-03 | 人脸裁剪上传 64×64 base64 |

## 快速开始

### 1. 烧录固件

```bash
esptool.py --chip esp32s3 --port /dev/ttyUSB0 write_flash 0x0 firmware.bin
```

### 2. 配置 WiFi

编辑 `camera_display.py` 顶部：
```python
WIFI_SSID = "你的WiFi名"
WIFI_PASS = "你的WiFi密码"
MQTT_BROKER = "你的服务器IP"
```

### 3. 上传代码

通过 Thonny 或 ampy 上传 `camera_display.py` 到 ESP32。

### 4. 启动服务器

```bash
cd web
pip install -r requirements.txt
python app.py
```

Web 页面：`http://服务器IP:8080`

## 目录结构

```
├── camera_display.py          # ESP32 主程序（v1.9）
├── face_detect_all.py         # 备用完整版（camera+espdl+TFT+MQTT）
├── face_detect_mqtt.py        # 精简版 MQTT 推送
├── main.py                    # 最小示例
├── main_with_tft.py           # TFT 显示版本
├── pc_receiver.py             # PC 端串口接收器
├── web/
│   ├── app.py                 # Flask 后端
│   ├── templates/index.html   # Web 前端
│   └── requirements.txt       # Python 依赖
└── dist/                      # 发布包（带版本号）
```

## MQTT Topic

| Topic | 方向 | 内容 |
|-------|------|------|
| `esp32/face_detect` | ESP32→服务器 | 检测结果 JSON |
| `esp32/face_detect/crop` | ESP32→服务器 | 人脸裁剪 base64 |

## 许可

MIT
