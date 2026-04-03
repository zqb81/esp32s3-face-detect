#!/bin/bash
# 启动人脸检测 Web 服务

cd "$(dirname "$0")"

echo "📦 安装依赖..."
pip3 install -r requirements.txt -q

echo "🚀 启动 Web 服务..."
python3 app.py
