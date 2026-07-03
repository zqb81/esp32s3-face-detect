# ESP32-S3 人脸检测系统

基于 ESP32-S3 + MicroPython 的端侧实时人脸检测，集成摄像头采集、TFT 显示、MQTT 通信、语音交互和 Web 可视化。

## 硬件

| 组件 | 型号 |
|------|------|
| 主控 | ESP32-S3-WROOM-1 N16R8 |
| 摄像头 | OV5640 |
| 屏幕 | ST7735 1.8寸 TFT (128×160) |
| 麦克风 | INMP441 (I2S) |
| 功放 | MAX98357A (I2S) |
| RGB 灯 | WS2812 × 5 |
| 蜂鸣器 | 有源蜂鸣器 |

## 引脚接线

> **N16R8 引脚限制：** GPIO 22-25 模块未引出，GPIO 26 = PSRAM CS，GPIO 27-32 = Flash SPI，GPIO 33-37 = Octal PSRAM 数据线，均不可用于外部硬件。空闲 GPIO：0、38、46、48。

**摄像头 (OV5640 DVP)：**
| 信号 | GPIO |
|------|------|
| D0-D7 | 11, 9, 8, 10, 12, 18, 17, 16 |
| XCLK | 15 |
| PCLK | 13 |
| VSYNC | 6 |
| HREF | 7 |
| SDA / SCL | 4 / 5 |

**TFT (ST7735 SPI)：**
| 信号 | GPIO |
|------|------|
| SCK | 14 |
| MOSI | 21 |
| CS | 2 |
| DC | 1 |
| RST | 3 |
| BL | 47 |

**语音 (I2S)：**
| 信号 | GPIO |
|------|------|
| 麦克风 BCLK / WS / SD | 39 / 40 / 41 |
| 喇叭 BCLK / WS / DIN | 42 / 43 / 44 |
| 语音按键 | 45 |

**设备控制：**
| 设备 | GPIO |
|------|------|
| WS2812 RGB | 38 |
| 蜂鸣器 PWM | 48 |

## 固件编译

需要带 `espdl` 模块的 MicroPython 固件，使用 [mp_esp_dl_models](https://github.com/cnadler86/mp_esp_dl_models) 构建。

### 依赖

- ESP-IDF >= 5.3.0（推荐 5.4.x）
- MicroPython >= 1.26.0
- [mp_esp_dl_models](https://github.com/cnadler86/mp_esp_dl_models)（含 esp-dl 子模块）
- [micropython-camera-API](https://github.com/cnadler86/micropython-camera-API)
- [mp_jpeg](https://github.com/cnadler86/mp_jpeg)

### 编译步骤

```bash
# 克隆依赖
git clone --recursive https://github.com/cnadler86/mp_esp_dl_models.git
git clone https://github.com/cnadler86/micropython-camera-API.git
git clone https://github.com/cnadler86/mp_jpeg.git
git clone https://github.com/micropython/micropython.git
cd micropython && git checkout v1.26.0 && cd ..

# 激活 IDF 环境后编译（N16R8 = SPIRAM_OCT 变体）
cd mp_esp_dl_models/boards
idf.py \
  -D MICROPY_DIR=/path/to/micropython \
  -D MICROPY_BOARD=ESP32_GENERIC_S3 \
  -D MICROPY_BOARD_VARIANT=SPIRAM_OCT \
  -D MP_DL_FACE_DETECTOR_ENABLED=1 \
  -B build-n16r8 build

# 生成固件
cd build-n16r8
python /path/to/micropython/ports/esp32/makeimg.py \
  sdkconfig \
  bootloader/bootloader.bin \
  partition_table/partition-table.bin \
  micropython.bin \
  firmware.bin \
  micropython.uf2
```

### 烧录固件

```bash
esptool.py --chip esp32s3 --port COM3 write_flash 0x0 firmware.bin
```

## 部署

### 1. 上传设备端代码

将 `main/` 目录下的文件上传到 ESP32：

```
main/main.py        → 设备根目录 /main.py
main/lib/           → 设备 /lib/ 目录
```

用 Thonny 或 mpremote：

```bash
mpremote connect COM3 cp main/main.py :main.py
mpremote connect COM3 mkdir :lib
mpremote connect COM3 cp main/lib/voice_hal.py :lib/voice_hal.py
mpremote connect COM3 cp main/lib/voice_client.py :lib/voice_client.py
```

### 2. 修改配置

编辑 `main/main.py` 顶部：

```python
WIFI_SSID   = "你的WiFi名"
WIFI_PASS   = "你的WiFi密码"
MQTT_BROKER = "你的服务器IP"
```

### 3. 启动服务端

```bash
cd web
pip install -r requirements.txt
python app.py        # MQTT + WebSocket + Web UI，端口 8080
python voice_server.py   # 语音 ASR/LLM/TTS，端口 9000
```

Web 页面：`http://服务器IP:8080`

## 架构

```
ESP32-S3
  摄像头 QVGA 320×240
      │
      ├─ espdl 人脸检测（每 5 帧）
      │       ├─ MQTT → esp32/face_detect      (检测结果 JSON)
      │       └─ MQTT → esp32/face_detect/crop (64×64 人脸裁剪 base64)
      │
      ├─ 下采样 128×160 → TFT 显示 + 人脸框叠加
      │
      └─ I2S 按键 → 录音 → TCP → voice_server
                               ASR + LLM + TTS
                          ← 播放回复 + 执行动作
                            (RGB / 蜂鸣器 / 人脸检测开关)

服务端
  MQTT Broker (Mosquitto :1883)
  Flask + SocketIO → WebSocket 实时推送 → Web 看板 (:8080)
  voice_server.py → ASR / LLM / TTS (:9000)
```

## 功能

- 实时摄像头画面 → TFT 显示
- 端侧人脸检测（espdl，约 4.5 FPS QVGA）
- 检测框颜色：绿 (>80%) / 黄 (>60%) / 红 (<60%)
- 5 个关键点标记
- NTP 时间同步（北京时间）
- MQTT 上报检测结果 + 人脸裁剪图
- 语音交互：按键录音 → ASR → LLM → TTS 播放
- 语音控制：RGB 灯、蜂鸣器、人脸检测开关

## MQTT Topic

| Topic | 内容 |
|-------|------|
| `esp32/face_detect` | 检测结果 JSON（时间戳、人脸数、坐标、置信度） |
| `esp32/face_detect/crop` | 最优人脸裁剪图（64×64 RGB565 base64） |

## 目录结构

```
├── main/                   # 烧录到 ESP32 的文件
│   ├── main.py             # 主程序
│   └── lib/
│       ├── voice_hal.py    # 硬件抽象层（I2S / RGB / 蜂鸣器 / 按键）
│       └── voice_client.py # 语音交互 TCP 客户端
│
├── web/                    # 服务端
│   ├── app.py              # Flask + MQTT + WebSocket
│   ├── voice_server.py     # ASR / LLM / TTS
│   ├── templates/index.html
│   └── requirements.txt
│
├── dist/                   # 历史发布版本
├── legacy/                 # 早期原型
└── README.md
```

## 版本历史

| 版本 | 更新内容 |
|------|---------|
| v2.0 | 语音控制集成（I2S 麦克风 + 喇叭，ASR/LLM/TTS） |
| v1.9 | 人脸裁剪上传（64×64 base64 → MQTT） |
| v1.8 | 回退 QVGA 方案，帧率稳定至约 4.5 FPS |
| v1.7 | 帧率优化：buffer 复用 + GC |
| v1.6 | VGA 640×480 + 检测下采样（实验） |
| v1.5 | 全画面缩放替代中心裁切 |
| v1.4 | 修复时间戳（rtc_to_timestamp） |
| v1.3 | NTP 使用 utime.localtime() |
| v1.2 | 摄像头上下翻转 |
| v1.1 | NTP 时间同步 |
| v1.0 | 初始版本 |

## 许可

MIT
