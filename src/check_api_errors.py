"""通过本地模拟传输测试SDK异常，不连接模型服务。"""

from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

import assistant


class ApiErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stamp = datetime.now(assistant.BEIJING).strftime("%Y%m%d-%H%M%S-%f")
        cls.result_dir = assistant.PROJECT_DIR / "experiment_results" / f"simulated-api-{stamp}"
        cls.result_dir.mkdir(parents=True)

    def run_case(self, case_id, title, error_type, status_code, expected_tip):
        calls = []
        records = []
        history_snapshots = []
        simulated_answer = "【模拟回复】程序可以继续处理下一次提问。"

        def transport(request):
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                if case_id == "SIM-401":
                    return httpx.Response(401, json={"error": {
                        "message": "Simulated invalid key", "type": "authentication_error",
                        "code": "invalid_api_key",
                    }})
                if case_id == "SIM-CONNECTION":
                    raise httpx.ConnectError("Simulated connection failure", request=request)
                raise httpx.ReadTimeout("Simulated timeout", request=request)
            return httpx.Response(200, json={
                "id": "simulated-completion", "object": "chat.completion",
                "created": 0, "model": "simulated-model",
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": simulated_answer,
                }, "finish_reason": "stop"}],
            })

        def client_factory(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(
                transport=httpx.MockTransport(transport), trust_env=False,
            ))

        original_save = assistant.save_log
        original_command = assistant.handle_command

        def save_simulated_log(record):
            record = {**record, "simulation": True, "test_source": "httpx.MockTransport"}
            records.append(record)
            # 仍使用程序的真实日志写入逻辑，只改变模拟日志的保存位置。
            with patch.object(assistant, "PROJECT_DIR", self.result_dir):
                return original_save(record)

        def command_with_snapshot(command, state):
            if command == "/show":
                history_snapshots.append([dict(message) for message in state["history"]])
            return original_command(command, state)

        inputs = iter(["第一次测试提问", "/show", "第二次测试提问", "/show", "exit"])

        def scripted_input(prompt):
            value = next(inputs)
            print(prompt + value)
            return value

        output = io.StringIO()
        with patch.object(assistant, "read_settings", return_value=(
            "local-simulation-only", "https://model.invalid/v1", "simulated-model",
        )), patch.object(assistant, "OpenAI", side_effect=client_factory), \
                patch.object(assistant, "save_log", side_effect=save_simulated_log), \
                patch.object(assistant, "handle_command", side_effect=command_with_snapshot), \
                patch("builtins.input", side_effect=scripted_input), \
                patch("sys.argv", ["assistant.py", "--test-id", case_id]), \
                redirect_stdout(output):
            exit_code = assistant.main()

        transcript = output.getvalue()
        (self.result_dir / f"{case_id}.txt").write_text(
            "受控模拟：未发送真实网络请求；回复为预设文本。\n" + transcript,
            encoding="utf-8",
        )
        print(f"\n{title}（受控模拟）")
        print(transcript[transcript.index("\n用户："):], end="")

        self.assertEqual(exit_code, 0, "错误不应终止交互循环")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["status"], "error")
        self.assertEqual(records[0]["error_type"], error_type)
        self.assertEqual(records[0]["http_status"], status_code)
        self.assertIn(expected_tip, records[0]["error_message"])
        self.assertIn(expected_tip, transcript)
        self.assertEqual(records[0]["output"], "")
        self.assertIsNone(records[0]["first_text_seconds"])
        self.assertEqual(history_snapshots[0], [], "失败问答不能进入历史")
        self.assertEqual(records[1]["status"], "success")
        self.assertEqual(history_snapshots[1], [
            {"role": "user", "content": "第二次测试提问"},
            {"role": "assistant", "content": simulated_answer},
        ])
        self.assertEqual(len(calls), 2, "每次提问只发起一次模拟请求，不自动重试")
        self.assertEqual([m["role"] for m in calls[1]["messages"]], ["system", "user"])
        self.assertEqual(calls[1]["messages"][-1]["content"], "第二次测试提问")

        saved = [json.loads(line) for path in (self.result_dir / "logs").glob("*.jsonl")
                 for line in path.read_text(encoding="utf-8").splitlines()]
        matching = [r for r in saved if r["test_id"] == case_id]
        self.assertEqual([r["status"] for r in matching], ["error", "success"])
        self.assertTrue(all(r["simulation"] for r in matching))
        self.assertNotIn("local-simulation-only", json.dumps(matching))
        print("检查通过：提示正确；失败不入历史；下一问可继续；模拟日志已保存。")
        print("模拟请求：2次；真实网络请求：0次。")

    def test_authentication_failure(self):
        self.run_case("SIM-401", "实验C：Key无效", "AuthenticationError", 401, "认证失败")

    def test_connection_failure(self):
        self.run_case("SIM-CONNECTION", "实验D：连接失败", "APIConnectionError", None, "连接失败")

    def test_timeout(self):
        self.run_case("SIM-TIMEOUT", "实验E：请求超时", "APITimeoutError", None, "请求超时")


if __name__ == "__main__":
    print("API异常处理实验：全部为本地模拟，不读取真实密钥，不连接模型服务。")
    print("超时直接注入异常，不实际等待45秒；后续成功回复也是预设文本。")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ApiErrorTests)
    summary = io.StringIO()
    result = unittest.TextTestRunner(stream=summary, verbosity=2).run(suite)
    text = summary.getvalue()
    print(text)
    (ApiErrorTests.result_dir / "检查结果.txt").write_text(text, encoding="utf-8")
    print(f"本次模拟记录：{ApiErrorTests.result_dir}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
