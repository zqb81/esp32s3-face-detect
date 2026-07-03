# ESP32-S3 AI 智能安防终端

基于 ESP32-S3 的端侧 AI 安防设备，**实时人脸检测** + **语音交互控制**，全部在设备端完成推理，无需云端。

## ✨ 核心能力

### 🧠 端侧人脸检测
- 基于 **ESP-DL** 深度学习框架，模型运行在 ESP32-S3 芯片上
- QVGA 320×240 实时检测，约 **4.5 FPS**
- 检测结果：人脸坐标、置信度评分、5 个关键点（眼/鼻/嘴）
- 置信度颜色标注：🟢 >80% / 🟡 >60% / 🔴 <60%
- 人脸裁剪上传：自动截取 64×64 人脸图，MQTT 推送到服务端存档

### 🎙️ 语音交互
- 按键触发录音，**I2S 麦克风**采集 16KHz 16bit PCM
- 语音链路：**ASR 语音识别 → LLM 大模型对话 → TTS 语音合成**
- 服务端调用 **MiMo 大模型** API（小米 MiMo-v2.5）
- I2S 功放 **实时播放** TTS 回复
- 语音控制设备：*"把灯变成红色"*、*"关闭人脸检测"*、*"响一下蜂鸣器"*
- **异步执行**：语音跑在第二核，不阻塞摄像头和 TFT 画面

### 📊 Web 看板
- 实时推送检测结果（WebSocket）
- 人脸图片时间线，历史记录查询
- 文字聊天（共享 LLM 对话历史）
- 设备远程控制面板（RGB 灯 / 蜂鸣器 / 检测开关）

## 架构

```
ESP32-S3 (MicroPython, 双核, 8MB PSRAM)
┌─────────────────────────────────────────────────┐
│ 核心0 — 主循环                                    │
│   OV5640 摄像头 → QVGA 320×240                    │
│       │                                           │
│       ├─ ESP-DL 人脸检测（每 5 帧）                 │
│       │     ├→ MQTT 上报检测结果 + 人脸裁剪图       │
│       │     └→ TFT 叠加人脸框 + 关键点              │
│       │                                           │
│       ├─ MQTT 接收 Web 控制指令                     │
│       └─ ST7735 TFT 128×160 实时显示               │
│                                                    │
│ 核心1 — 语音线程 (_thread)                          │
│   按键 → INMP441 录音 → TCP 上传                    │
│                    ↓                               │
│              ASR → LLM → TTS (服务端)               │
│                    ↓                               │
│          MAX98357A 播放 ← PCM 回传                  │
│          执行动作 ← RGB / 蜂鸣器 / 检测开关          │
└─────────────────────────────────────────────────┘

服务端 (单进程 app.py)
┌─────────────────────────────────────────────────┐
│ HTTP :8080  Web 看板 + 文字聊天 + 设备控制         │
│ TCP  :9000  语音桥接 (PCM → ASR → LLM → TTS)     │
│ MQTT :1883  双向通信（检测数据 ↑ / 控制指令 ↓）     │
└─────────────────────────────────────────────────┘
```

## 硬件

| 组件 | 型号 | 用途 |
|------|------|------|
| 主控 | ESP32-S3-WROOM-1 **N16R8** | 16MB Flash + 8MB PSRAM，AI 推理 |
| 摄像头 | OV5640 (DVP) | QVGA 实时采集 |
| 屏幕 | ST7735 1.8寸 TFT | 128×160 实时画面 + 人脸框 |
| 麦克风 | INMP441 (I2S) | 语音采集 16KHz |
| 功放 | MAX98357A (I2S) | TTS 语音播放 |
| RGB 灯 | WS2812 × 5 | 状态指示 + 语音控制 |
| 蜂鸣器 | 有源蜂鸣器 (PWM) | 报警 + 语音控制 |

## 引脚接线

> **N16R8 限制：** GPIO 22-25 未引出，GPIO 26-37 被 Flash/PSRAM 占用。可用 GPIO：0、38、46、48。

<details>
<summary>点击展开完整引脚表</summary>

**摄像头 (OV5640 DVP)：**
| 信号 | GPIO |
|------|------|
| D0-D7 | 11, 9, 8, 10, 12, 18, 17, 16 |
| XCLK / PCLK | 15 / 13 |
| VSYNC / HREF | 6 / 7 |
| SDA / SCL | 4 / 5 |

**TFT (ST7735 SPI)：**
| 信号 | GPIO |
|------|------|
| SCK / MOSI | 14 / 21 |
| CS / DC / RST | 2 / 1 / 3 |
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

</details>

## 快速开始

### 1. 烧录固件

```bash
# 进入下载模式：按住 BOOT → 按 RST → 松开 BOOT
cd dist
bash flash.sh COM9
```

### 2. 上传设备代码

编辑 `main/config.json`：

```json
{
    "wifi_ssid": "你的WiFi",
    "wifi_pass": "密码",
    "mqtt_broker": "服务器IP",
    "voice_server_ip": "服务器IP"
}
```

```bash
cd main
bash upload.sh COM9
```

### 3. 启动服务端

编辑 `web/.env` 填入 `XIAOMI_API_KEY`（[MiMo 开放平台](https://dev.mi.com/)获取）：

```bash
cd web
bash start.sh
```

打开 `http://服务器IP:8080` 查看 Web 看板。

## MQTT 通信

| Topic | 方向 | 内容 |
|-------|------|------|
| `esp32/face_detect` | ESP32 → 服务端 | 检测结果 JSON（帧号、人脸数、坐标、置信度） |
| `esp32/face_detect/crop` | ESP32 → 服务端 | 人脸裁剪图（64×64 RGB565 base64） |
| `esp32/iot/cmd/rgb` | 服务端 → ESP32 | RGB 灯颜色 |
| `esp32/iot/cmd/buzzer` | 服务端 → ESP32 | 蜂鸣器开关 |
| `esp32/iot/cmd/facedetect` | 服务端 → ESP32 | 人脸检测开关 |

## 目录结构

```
├── main/                    # ESP32 设备端
│   ├── main.py              # 主程序 (v2.2)
│   ├── config.json          # 设备配置（WiFi/MQTT/语音服务器）
│   ├── config.json.example
│   ├── upload.sh            # 一键上传脚本
│   └── lib/
│       ├── voice_hal.py     # 硬件抽象层（I2S/RGB/蜂鸣器/按键）
│       └── voice_client.py  # TCP 语音客户端
│
├── web/                     # 服务端（单进程统一服务）
│   ├── app.py               # HTTP + TCP + MQTT + MiMo AI
│   ├── .env / .env.example  # 服务端配置
│   ├── requirements.txt
│   ├── start.sh             # 一键部署脚本
│   └── static/              # Web 前端
│
├── dist/
│   ├── firmware_v2.1.bin    # 预编译固件（ESP-DL + camera + jpeg）
│   └── flash.sh             # 一键烧录脚本
│
└── README.md
```

## 固件编译

预编译固件已在 `dist/` 中，通常无需自行编译。如需定制：

<details>
<summary>点击展开编译步骤（WSL 推荐）</summary>

### 依赖

- ESP-IDF >= 5.3.0（推荐 5.4.x）
- MicroPython >= 1.26.0
- [mp_esp_dl_models](https://github.com/cnadler86/mp_esp_dl_models)
- [micropython-camera-API](https://github.com/cnadler86/micropython-camera-API)
- [mp_jpeg](https://github.com/cnadler86/mp_jpeg)

### 步骤

```bash
# 克隆
git clone --recursive https://github.com/cnadler86/mp_esp_dl_models.git
git clone --recursive https://github.com/cnadler86/micropython-camera-API.git
git clone https://github.com/cnadler86/mp_jpeg.git
git clone https://github.com/micropython/micropython.git
cd micropython && git checkout v1.26.0 && cd ..

# Patch: IDF 5.4.x WiFi 兼容 + cmake 路径
sed -i 's/WIFI_AUTH_MAX == 16/WIFI_AUTH_MAX >= 16/' micropython/ports/esp32/network_wlan.c
sed -i 's|${MP_CAMERA_DIR}/src/micropython.cmake|${MP_CAMERA_DIR}/micropython.cmake|g' mp_esp_dl_models/src/micropython.cmake
sed -i 's|${MP_JPEG_DIR}/src/micropython.cmake|${MP_JPEG_DIR}/micropython.cmake|g' mp_esp_dl_models/src/micropython.cmake

# 编译（N16R8 = SPIRAM_OCT）
cd mp_esp_dl_models/boards
idf.py \
  -D MICROPY_DIR=$HOME/esp/mp \
  -D MICROPY_BOARD=ESP32_GENERIC_S3 \
  -D MICROPY_BOARD_VARIANT=SPIRAM_OCT \
  -D MP_DL_FACE_DETECTOR_ENABLED=1 \
  -D MP_DL_FACE_RECOGNITION_ENABLED=1 \
  -D MP_DL_PEDESTRIAN_DETECTOR_ENABLED=1 \
  -D MP_CAMERA_DIR=$HOME/esp/cam \
  -D MP_JPEG_DIR=$HOME/esp/jpg \
  -D EXTRA_COMPONENT_DIRS=$HOME/esp/cam \
  -B build-n16r8 build

# 打包固件
cd build-n16r8
python $HOME/esp/mp/ports/esp32/makeimg.py \
  sdkconfig bootloader/bootloader.bin \
  partition_table/partition-table.bin \
  micropython.bin firmware.bin micropython.uf2
```

> **Windows 注意：** 命令行长度限制可能导致 qstr 预处理失败，推荐使用 WSL 编译。

</details>

## 版本历史

| 版本 | 更新内容 |
|------|---------|
| v2.2 | MQTT 双向控制、Web RGB 选色器、服务端合并单进程 |
| v2.1 | 语音异步化（_thread 双核）、启动日志美化、config.json 外置 |
| v2.0 | 语音控制集成（I2S 麦克风 + 喇叭，ASR/LLM/TTS） |
| v1.9 | 人脸裁剪上传（64×64 base64 → MQTT） |
| v1.8 | 回退 QVGA 方案，帧率稳定至约 4.5 FPS |
| v1.0 | 初始版本：摄像头 + TFT 显示 |

## 许可

MIT
