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

## 快速开始

### 1. 烧录固件

```bash
# 进入下载模式：按住 BOOT → 按 RST → 松开 BOOT
cd dist
bash flash.sh COM9
```

### 2. 上传设备代码

编辑 `main/config.json` 填入 WiFi 和服务器地址：

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

编辑 `web/.env` 填入配置（尤其是 `XIAOMI_API_KEY`）：

```bash
cd web
bash start.sh
```

Web 页面：`http://服务器IP:8080`

## 固件编译

预编译固件在 `dist/firmware_v2.1.bin`，如需自行编译：

### 依赖

- ESP-IDF >= 5.3.0（推荐 5.4.x）
- MicroPython >= 1.26.0
- [mp_esp_dl_models](https://github.com/cnadler86/mp_esp_dl_models)（含 esp-dl 子模块）
- [micropython-camera-API](https://github.com/cnadler86/micropython-camera-API)
- [mp_jpeg](https://github.com/cnadler86/mp_jpeg)

### 编译步骤（WSL 推荐）

```bash
# 克隆依赖
git clone --recursive https://github.com/cnadler86/mp_esp_dl_models.git
git clone --recursive https://github.com/cnadler86/micropython-camera-API.git
git clone https://github.com/cnadler86/mp_jpeg.git
git clone https://github.com/micropython/micropython.git
cd micropython && git checkout v1.26.0 && cd ..

# 修复 IDF 5.4.x 兼容性
sed -i 's/WIFI_AUTH_MAX == 16/WIFI_AUTH_MAX >= 16/' micropython/ports/esp32/network_wlan.c

# 修复 cmake 路径（micropython.cmake 在仓库根目录，构建系统期望 src/ 下）
sed -i 's|${MP_CAMERA_DIR}/src/micropython.cmake|${MP_CAMERA_DIR}/micropython.cmake|g' mp_esp_dl_models/src/micropython.cmake
sed -i 's|${MP_JPEG_DIR}/src/micropython.cmake|${MP_JPEG_DIR}/micropython.cmake|g' mp_esp_dl_models/src/micropython.cmake

# 激活 IDF 环境后编译（N16R8 = SPIRAM_OCT 变体）
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
  -B build-n16r8 \
  build

# 生成固件
cd build-n16r8
python $HOME/esp/mp/ports/esp32/makeimg.py \
  sdkconfig bootloader/bootloader.bin \
  partition_table/partition-table.bin \
  micropython.bin firmware.bin micropython.uf2
```

> **Windows 注意：** 命令行长度限制可能导致 qstr 预处理失败，推荐使用 WSL 编译。

## 架构

```
ESP32-S3 (MicroPython, 双核)
  核心0 — 主循环
    摄像头 QVGA 320×240
        │
        ├─ espdl 人脸检测（每 5 帧）
        │       ├─ MQTT 发布 → esp32/face_detect      (检测结果 JSON)
        │       └─ MQTT 发布 → esp32/face_detect/crop  (64×64 人脸裁剪)
        │
        ├─ MQTT 订阅 ← esp32/iot/cmd/#  (Web 设备控制)
        │       → execute_voice_command() → RGB / 蜂鸣器 / 检测开关
        │
        └─ 下采样 128×160 → TFT 显示 + 人脸框叠加

  核心1 — 语音线程 (_thread，不阻塞主循环)
    按键 → I2S 录音 → TCP → 服务端
                         ASR + LLM + TTS
                    ← PCM 播放 + 执行动作

服务端 (单进程 app.py)
  HTTP  :8080  Flask + SocketIO → Web 看板 + 文字聊天 + 设备控制
  TCP   :9000  语音桥接 (PCM → ASR → LLM → TTS → PCM)
  MQTT  :1883  订阅检测数据 / 发布设备控制指令
```

## 功能

- 实时摄像头画面 → TFT 显示
- 端侧人脸检测（espdl，约 4.5 FPS QVGA）
- 检测框颜色：绿 (>80%) / 黄 (>60%) / 红 (<60%)
- 5 个关键点标记
- NTP 时间同步（北京时间）
- MQTT 上报检测结果 + 人脸裁剪图
- 语音交互：按键录音 → ASR → LLM → TTS 播放（异步，不阻塞画面）
- 语音控制：RGB 灯、蜂鸣器、人脸检测开关
- Web 看板：实时检测记录、人脸图片、统计、文字聊天、设备控制

## MQTT Topic

| Topic | 方向 | 内容 |
|-------|------|------|
| `esp32/face_detect` | ESP32 → 服务端 | 检测结果 JSON（时间戳、帧号、人脸数、坐标、置信度） |
| `esp32/face_detect/crop` | ESP32 → 服务端 | 最优人脸裁剪图（64×64 RGB565 base64） |
| `esp32/iot/cmd/rgb` | 服务端 → ESP32 | RGB 颜色（red/green/blue/off 等） |
| `esp32/iot/cmd/buzzer` | 服务端 → ESP32 | 蜂鸣器开关（on/off） |
| `esp32/iot/cmd/facedetect` | 服务端 → ESP32 | 人脸检测开关（on/off） |

## 目录结构

```
├── main/                    # 上传到 ESP32 的文件
│   ├── main.py              # 主程序 (v2.2)
│   ├── config.json          # 设备配置（不提交）
│   ├── config.json.example  # 配置模板
│   ├── upload.sh            # 上传脚本
│   └── lib/
│       ├── voice_hal.py     # 硬件抽象层（I2S / RGB / 蜂鸣器 / 按键）
│       └── voice_client.py  # 语音交互 TCP 客户端
│
├── web/                     # 服务端（单进程）
│   ├── app.py               # Flask + TCP + MQTT + MiMo API
│   ├── .env                 # 服务端配置（不提交）
│   ├── .env.example         # 配置模板
│   ├── requirements.txt     # Python 依赖
│   ├── start.sh             # 一键部署脚本
│   └── static/
│       ├── index.html       # Web 页面
│       ├── style.css        # 样式
│       └── app.js           # 前端逻辑
│
├── dist/
│   ├── firmware_v2.1.bin    # 预编译固件（espdl + camera + jpeg）
│   └── flash.sh             # 烧录脚本
│
├── .gitignore
└── README.md
```

## 版本历史

| 版本 | 更新内容 |
|------|---------|
| v2.2 | MQTT 双向控制、Web RGB 选色、服务端合并单进程、审查修复 |
| v2.1 | 语音异步化、启动日志美化、项目结构整理、config.json 外置配置 |
| v2.0 | 语音控制集成（I2S 麦克风 + 喇叭，ASR/LLM/TTS） |
| v1.9 | 人脸裁剪上传（64×64 base64 → MQTT） |
| v1.8 | 回退 QVGA 方案，帧率稳定至约 4.5 FPS |
| v1.5 | 全画面缩放替代中心裁切 |
| v1.0 | 初始版本 |

## 许可

MIT
