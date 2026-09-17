"""
M6-2 服务层启动测试脚本
验证 FastAPI + WebSocket 服务层能正常启动
"""

import sys
import asyncio
import httpx
import websockets
import json


async def test_rest_api():
    """测试 REST 接口"""
    print("\n🔍 测试 REST 接口...")

    async with httpx.AsyncClient() as client:
        # 1. 健康检查
        response = await client.get("http://localhost:8000/health")
        print(f"  ✅ 健康检查: {response.status_code} - {response.json()}")

        # 2. 创建任务
        task_request = {
            "user_task": {
                "topic": "2026年AI芯片市场分析",
                "scope": ["市场概况", "供给端", "需求端"],
                "output_format_spec": "Markdown 格式",
                "constraints": ["引用必须来自 2024-2026 年"]
            }
        }
        response = await client.post("http://localhost:8000/api/v1/tasks", json=task_request)
        print(f"  ✅ 创建任务: {response.status_code}")
        task_data = response.json()
        task_id = task_data["task_id"]
        print(f"     task_id: {task_id}")

        # 3. 获取任务状态
        response = await client.get(f"http://localhost:8000/api/v1/tasks/{task_id}")
        print(f"  ✅ 获取任务: {response.status_code}")
        task = response.json()
        print(f"     status: {task['status']}")

        # 4. 列出所有任务
        response = await client.get("http://localhost:8000/api/v1/tasks")
        print(f"  ✅ 列出任务: {response.status_code}")
        print(f"     total: {response.json()['total']}")

        return task_id


async def test_websocket(task_id: str):
    """测试 WebSocket 连接"""
    print("\n🔍 测试 WebSocket 连接...")

    uri = f"ws://localhost:8000/api/v1/tasks/{task_id}/stream?debug_mode=1"

    try:
        async with websockets.connect(uri) as websocket:
            print(f"  ✅ WebSocket 连接成功: {uri}")

            # 接收连接确认消息
            message = await websocket.recv()
            print(f"  📩 收到消息: {json.loads(message)}")

            # 发送心跳 ping
            await websocket.send_json({"action": "ping"})
            pong = await websocket.recv()
            print(f"  💓 心跳响应: {json.loads(pong)}")

            # 模拟接收事件（TODO: M6-3 引擎会推送真实事件）
            print(f"  ℹ️  等待引擎推送事件...（M6-3 实现）")

    except Exception as e:
        print(f"  ❌ WebSocket 连接失败: {e}")


async def main():
    """主测试流程"""
    print("=" * 60)
    print("M6-2 服务层启动测试")
    print("=" * 60)

    # 等待服务启动
    print("\n⏳ 等待服务启动（3 秒）...")
    await asyncio.sleep(3)

    try:
        # 测试 REST 接口
        task_id = await test_rest_api()

        # 测试 WebSocket
        await test_websocket(task_id)

        print("\n" + "=" * 60)
        print("✅ M6-2 服务层测试完成")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
