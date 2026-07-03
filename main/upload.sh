#!/bin/bash
# ESP32-S3 — 上传应用代码到设备
# 用法: bash upload.sh [COM端口]
# 示例: bash upload.sh COM9
#       bash upload.sh /dev/ttyACM0

set -e
cd "$(dirname "$0")"

PORT=${1:-COM9}

echo "╔══════════════════════════════════════╗"
echo "║  ESP32-S3 · 上传应用代码             ║"
echo "╚══════════════════════════════════════╝"
echo "端口: $PORT"
echo

# 检查 config.json
if [ ! -f config.json ]; then
    echo "⚠️  未找到 config.json，从模板创建..."
    cp config.json.example config.json
    echo "📝 请编辑 config.json 填入 WiFi 和服务器地址"
    exit 1
fi

# 检查 mpremote
if ! command -v mpremote &>/dev/null; then
    echo "📦 安装 mpremote..."
    pip install mpremote
fi

echo "📤 上传文件..."
mpremote connect $PORT mkdir :lib 2>/dev/null || true
mpremote connect $PORT cp main.py :main.py
mpremote connect $PORT cp config.json :config.json
mpremote connect $PORT cp lib/voice_hal.py :lib/voice_hal.py
mpremote connect $PORT cp lib/voice_client.py :lib/voice_client.py

echo
echo "✅ 上传完成，重启设备..."
mpremote connect $PORT reset

echo "🎉 完成！设备将自动运行 main.py"
