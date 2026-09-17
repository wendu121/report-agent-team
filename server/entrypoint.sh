#!/bin/sh
# TD-006 后端入口脚本（D4：建表后启动 uvicorn，不引入 Alembic）
set -e

echo "🚀 [entrypoint] 初始化数据库表（Base.metadata.create_all）..."
python -c "from server.database import init_database; init_database()"
echo "✅ [entrypoint] 数据库表就绪，启动 uvicorn..."

# 生产模式：无 --reload
exec python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
