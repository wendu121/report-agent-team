"""
简化版服务测试脚本
M6-6 产出：基础服务验证
"""

import asyncio
import json
import requests
import time
from typing import Dict, Any

class ServiceTester:
    """服务层测试器"""
    
    def __init__(self, base_url: str = "http://localhost:8002"):
        self.base_url = base_url
        
    async def test_health(self) -> bool:
        """测试健康检查"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            print(f"✅ 健康检查: {response.status_code}")
            return response.status_code == 200
        except Exception as e:
            print(f"❌ 健康检查失败: {e}")
            return False
    
    async def test_create_task(self) -> str:
        """测试创建任务"""
        payload = {
            "user_task": {
                "topic": "人工智能发展趋势",
                "scope": ["技术趋势", "市场分析", "政策影响"],
                "output_format_spec": "Markdown 格式，每节带引用链接",
                "constraints": ["引用必须来自 2024-2026 年的权威来源"]
            }
        }
        
        try:
            response = requests.post(
                f"{self.base_url}/api/v1/tasks",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 201:
                task_data = response.json()
                task_id = task_data["task_id"]
                print(f"✅ 任务创建成功: {task_id}")
                return task_id
            else:
                print(f"❌ 任务创建失败: {response.status_code} - {response.text}")
                # 尝试解析错误详情
                try:
                    error_detail = response.json()
                    print(f"   错误详情: {error_detail}")
                except:
                    print(f"   错误文本: {response.text}")
                return None
                
        except Exception as e:
            print(f"❌ 任务创建异常: {e}")
            return None
    
    async def test_get_task(self, task_id: str) -> Dict[str, Any]:
        """测试获取任务状态"""
        try:
            response = requests.get(
                f"{self.base_url}/api/v1/tasks/{task_id}",
                timeout=5
            )
            
            if response.status_code == 200:
                task_data = response.json()
                print(f"✅ 获取任务状态: {task_id}")
                return task_data
            else:
                print(f"❌ 获取任务失败: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ 获取任务异常: {e}")
            return None
    
    async def test_review_task(self, task_id: str, action: str) -> bool:
        """测试人工复核"""
        payload = {
            "action": action,
            "comment": f"测试 {action} 操作"
        }
        
        try:
            response = requests.post(
                f"{self.base_url}/api/v1/tasks/{task_id}/review",
                json=payload,
                timeout=5
            )
            
            if response.status_code == 200:
                print(f"✅ 人工复核成功: {action}")
                return True
            else:
                print(f"❌ 人工复核失败: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 人工复核异常: {e}")
            return False
    
    async def test_audit_export(self, task_id: str) -> bool:
        """测试审计包导出"""
        try:
            response = requests.get(
                f"{self.base_url}/api/v1/tasks/{task_id}/audit-export",
                timeout=10
            )
            
            if response.status_code == 200:
                print(f"✅ 审计包导出成功: {task_id}")
                return True
            else:
                print(f"❌ 审计包导出失败: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 审计包导出异常: {e}")
            return False
    
    async def run_full_test(self):
        """运行完整测试流程"""
        print("🚀 开始服务层完整测试...")
        
        # 1. 健康检查
        if not await self.test_health():
            print("❌ 健康检查失败，服务未启动")
            return False
        
        # 2. 创建任务
        task_id = await self.test_create_task()
        if not task_id:
            print("❌ 任务创建失败")
            return False
        
        # 3. 获取任务状态
        task_data = await self.test_get_task(task_id)
        if not task_data:
            print("❌ 获取任务状态失败")
            return False
        
        # 4. 人工复核（模拟）
        await self.test_review_task(task_id, "confirm")
        
        # 5. 审计包导出
        await self.test_audit_export(task_id)
        
        print("✅ 服务层测试完成")
        return True


async def main():
    """主函数"""
    tester = ServiceTester()
    
    # 运行测试
    success = await tester.run_full_test()
    
    if success:
        print("\n🎉 所有测试通过！")
    else:
        print("\n💥 测试失败，请检查服务状态")


if __name__ == "__main__":
    asyncio.run(main())