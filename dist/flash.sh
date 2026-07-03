#!/bin/bash
# ESP32-S3 — 烧录固件
# 用法: bash flash.sh [COM端口]
# 示例: bash flash.sh COM9
#
# 注意: 烧录前需让设备进入下载模式
#       按住 BOOT → 按 RST → 松开 BOOT

set -e
cd "$(dirname "$0")"

PORT=${1:-COM9}
FW="firmware_v2.1.bin"

if [ ! -f "$FW" ]; then
    echo "❌ 固件文件不存在: $FW"
    exit 1
fi

echo "╔══════════════════════════════════════╗"
echo "║  ESP32-S3 · 烧录固件                 ║"
echo "╚══════════════════════════════════════╝"
echo "固件: $FW ($(du -h $FW | cut -f1))"
echo "端口: $PORT"
echo

echo "⏳ 擦除 Flash..."
python3 -m esptool --chip esp32s3 --port $PORT erase-flash

echo
echo "⏳ 烧录固件..."
python3 -m esptool --chip esp32s3 --port $PORT -b 460800 \
    write-flash --flash-mode dio --flash-size 16MB --flash-freq 80m \
    0x0 $FW

echo
echo "✅ 烧录完成！设备将自动重启"
