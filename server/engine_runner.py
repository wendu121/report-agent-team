"""
引擎运行器（独立 Python 进程入口）
M6-3 产出：引擎进程启动脚本

职责：
- 从命令行参数读取任务输入
- 调用 orchestrator.run_report() 执行
- 通过文件输出最终状态
- 通过回调推送 WebSocket 事件
"""

import sys
import os
import json
import argparse
from pathlib import Path
from datetime import datetime

# 导入引擎
sys.path.insert(0, str(Path(__file__).parent.parent))
from orchestrator import run_report, NewApiLLMClient


# ============================================================================
# 引擎运行入口
# ============================================================================


def run_engine(input_file: str):
    """运行引擎任务"""
    # 读取输入
    with open(input_file, "r", encoding="utf-8") as f:
        input_data = json.load(f)

    # 多租户：子进程 ContextVar 不跨进程，必须由 input.json 透传的 account_id 绑定租户根，
    # 并写 RAT_ACCOUNT_ID 让 orchestrator/tools 等模块解析到正确的账号资源目录。
    # 必须在任何资源路径解析之前（如 load_model_interfaces_and_endpoints）完成。
    _account_id = input_data.get("account_id")
    if _account_id:
        os.environ["RAT_ACCOUNT_ID"] = _account_id
        try:
            from server import tenancy
            tenancy.bind_account(_account_id)
            print(f"🏷️ 引擎子进程绑定租户: {_account_id}")
        except Exception as _e:
            print(f"⚠️ 引擎子进程绑定租户失败: {_e}")

    task_id = input_data["task_id"]
    user_task = input_data["user_task"]
    max_rounds = input_data.get("max_rounds", 2)
    # M9-5 / ChatEntry 控制项：本次任务的数据源 + 模型接口（真·控制器透传）。
    task_plugins = input_data.get("plugins") or None
    task_model = input_data.get("model") or None
    # 方案 A：审核模型(Gate)覆盖（None/"auto"=沿用 model_mapping 异基座默认）。
    task_gate_model = input_data.get("gate_model") or None
    output_file = Path(input_data.get("output_file", f"./outputs/{task_id}_output.json"))

    print(f"📋 引擎任务启动: task_id={task_id}")

    # 确保输出目录存在
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # TD-002：实时事件落盘（JSONL，每行一条）。服务进程并发 tail 后广播到 WS。
    # state_dir = output_file 所在目录（= 服务进程 STATE_DIR），保证两端读同一文件。
    events_file = output_file.parent / f"{task_id}_events.jsonl"
    events_file.unlink(missing_ok=True)  # 每轮运行从头开始，避免残留旧事件

    def _on_event(ev: dict) -> None:
        """把引擎事件追加写入 JSONL（task_id + timestamp 信封补齐，对齐 WSEvent 契约）。"""
        try:
            payload = {
                "event_type": ev.get("event_type"),
                "task_id": task_id,
                "timestamp": datetime.utcnow().isoformat(),
            }
            for _k, _v in ev.items():
                if _k != "event_type":
                    payload[_k] = _v
            with open(events_file, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as _e:
            print(f"⚠️ 引擎事件写入失败: {_e}")

    # 初始化 LLM 客户端（通用 OpenAI 兼容，多端点；DESIGN_OPENAI_ENDPOINTS.md）
    # endpoints + interfaces 从 config/models.yaml 读取（env 展开，密钥不落配置）。
    # 用户所选 interface 在 complete() 内动态解析到对应端点 + model_id。
    from orchestrator import load_model_interfaces_and_endpoints
    endpoints, interfaces, _, default_endpoint_id = load_model_interfaces_and_endpoints()
    llm = NewApiLLMClient(
        endpoints=endpoints,
        interfaces=interfaces,
        default_endpoint_id=default_endpoint_id,
    )

    # 运行引擎
    try:
        print(f"🚀 开始执行引擎: max_rounds={max_rounds}")
        final_state = run_report(
            user_task=user_task,
            llm=llm,
            max_rounds=max_rounds,
            agents=input_data.get("agents") or None,  # M8-5：子集编排；gates 由 GATE_NAME 推导
            plugins=task_plugins,                      # ChatEntry「插件」多选
            model=task_model,                          # ChatEntry「模型接口」下拉
            gate_model=task_gate_model,                # 方案 A：ChatEntry「审核模型」下拉
            tools=None,  # 使用默认工具配置
            on_event=_on_event  # TD-002：实时事件 → JSONL → 服务进程广播
        )

        # 写入输出文件
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(final_state, f, ensure_ascii=False, indent=2)

        routing_state = final_state.get("routing_state", {})
        print(f"✅ 引擎任务完成: task_id={task_id}, status={routing_state.get('status')}")

    except Exception as e:
        print(f"❌ 引擎任务失败: {e}")
        import traceback
        traceback.print_exc()

        # 写入错误信息
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({
                "error": str(e),
                "task_id": task_id
            }, f, ensure_ascii=False, indent=2)


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="引擎运行器")
    parser.add_argument("input_file", help="输入文件路径（JSON）")
    args = parser.parse_args()

    # 运行引擎
    run_engine(args.input_file)


if __name__ == "__main__":
    main()