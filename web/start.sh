#!/bin/bash
# ESP32-S3 人脸检测 — 服务端一键部署
# 用法: bash start.sh
#
# 服务:
#   HTTP :8080  Web 看板 + 文字聊天
#   TCP  :9000  ESP32 语音交互
#   MQTT :1883  需外部 Mosquitto

set -e
cd "$(dirname "$0")"

echo "╔══════════════════════════════════════╗"
echo "║  ESP32-S3 人脸检测 · 服务端部署      ║"
echo "╚══════════════════════════════════════╝"
echo

# 检查 .env
if [ ! -f .env ]; then
    echo "⚠️  未找到 .env，从模板创建..."
    cp .env.example .env
    echo "📝 请编辑 .env 填入实际配置（尤其是 XIAOMI_API_KEY）"
    echo "   vim .env"
    echo
fi

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "❌ 未找到 python3"
    exit 1
fi

# 虚拟环境
if [ ! -d venv ]; then
    echo "📦 创建虚拟环境..."
    python3 -m venv venv
fi
source venv/bin/activate

# 安装依赖
echo "📦 安装依赖..."
pip install -r requirements.txt -q

# 检查 MQTT
echo
source .env 2>/dev/null || true
BROKER=${MQTT_BROKER:-127.0.0.1}
if [ "$BROKER" = "127.0.0.1" ] || [ "$BROKER" = "localhost" ]; then
    if ! command -v mosquitto &>/dev/null; then
        echo "⚠️  本地 MQTT Broker (Mosquitto) 未安装"
        echo "   Ubuntu: sudo apt install mosquitto"
        echo "   Mac:    brew install mosquitto"
        echo
    fi
fi

# 启动
echo "🚀 启动服务..."
echo "   Web:  http://0.0.0.0:${WEB_PORT:-8080}"
echo "   TCP:  0.0.0.0:${TCP_PORT:-9000} (语音)"
echo "   MQTT: ${BROKER}:${MQTT_PORT:-1883}"
echo
python3 app.py
