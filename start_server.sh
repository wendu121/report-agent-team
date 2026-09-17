#!/bin/bash
# 启动服务层脚本（Linux/macOS）
# M6-6 产出：一键启动脚本

set -e

echo "🚀 启动 Report Agent Team 服务层"

# 检查 Python 版本
PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
echo "Python 版本: $PYTHON_VERSION"

# 检查依赖
echo "📦 检查依赖..."
if ! python -c "import fastapi" 2>/dev/null; then
    echo "安装依赖..."
    pip install -r requirements.txt
fi

# 初始化数据库（可选）
echo "🗄️  初始化数据库..."
if [ "$INIT_DATABASE" = "true" ]; then
    python -c "from server.database import init_database; init_database()"
fi

# 启动服务
echo "🌐 启动 FastAPI 服务..."
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload