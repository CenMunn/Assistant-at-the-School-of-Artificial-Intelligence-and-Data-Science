"""使用预设历史验证应用字符预算，不调用模型。"""

from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import assistant


HISTORY = [
    {"role": "user", "content": "[第1轮] 我的练习代号是Z7Q9，请记住它。"},
    {"role": "assistant", "content": "[第1轮] 已记住你的练习代号。"},
    {"role": "user", "content": "[第2轮] 我今天想练习Python条件判断。"},
    {"role": "assistant", "content": "[第2轮] 可以编写一个判断整数奇偶性的程序。"},
    {"role": "user", "content": "[第3轮] 我还想练习for循环。"},
    {"role": "assistant", "content": "[第3轮] 可以用循环计算1到10的和。"},
]
QUESTION = "我在第1轮提供的练习代号是什么？"


class HistoryBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.started = datetime.now(assistant.BEIJING)
        stamp = cls.started.strftime("%Y%m%d-%H%M%S-%f")
        cls.result_dir = assistant.PROJECT_DIR / "experiment_results" / f"history-budget-{stamp}"
        cls.result_dir.mkdir(parents=True)

    def check_budget(self, removed_rounds):
        system_prompt = assistant.load_system_prompt("v3")
        history = deepcopy(HISTORY)
        with patch.object(assistant, "datetime", wraps=datetime) as clock, \
                patch.object(assistant, "OpenAI", side_effect=AssertionError("本实验禁止调用模型")):
            clock.now.return_value = self.started
            before, _, _ = assistant.prepare_messages(system_prompt, history, QUESTION, True, 1_000_000)
            fixed_chars = len(before[0]["content"]) + len(QUESTION)
            expected_history = HISTORY[removed_rounds * 2:]
            budget = fixed_chars + sum(len(m["content"]) for m in expected_history)
            after, kept, dropped = assistant.prepare_messages(
                system_prompt, history, QUESTION, True, budget,
            )

        # 覆盖边界比较、从哪端裁剪、成对裁剪及不能改动原始历史等行为。
        self.assertEqual(dropped, removed_rounds)
        self.assertEqual(kept, expected_history)
        self.assertEqual(history, HISTORY)
        self.assertEqual(after[0], before[0])
        self.assertTrue(after[0]["content"].startswith(system_prompt))
        self.assertEqual(after[-1], {"role": "user", "content": QUESTION})
        self.assertEqual(after, [before[0], *expected_history, before[-1]])
        self.assertEqual([m["role"] for m in after],
                         ["system", *(["user", "assistant"] * (3 - removed_rounds)), "user"])
        self.assertEqual(sum(len(m["content"]) for m in after), budget)
        code_present = any("Z7Q9" in m["content"] for m in after)
        self.assertEqual(code_present, removed_rounds == 0)

        evidence = {
            "time": self.started.isoformat(), "test_kind": "local_message_preparation",
            "history_source": "预设练习对话，非真实模型输出", "api_calls": 0,
            "prompt_version": "v3", "history_enabled": True,
            "max_chars": budget, "fixed_chars": fixed_chars,
            "before_chars": sum(len(m["content"]) for m in before),
            "after_chars": sum(len(m["content"]) for m in after),
            "dropped_rounds": dropped, "old_code_in_messages": code_present,
            "before_messages": before, "after_messages": after,
            "status": "passed",
        }
        (self.result_dir / f"trim-{removed_rounds}.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"\n场景{removed_rounds + 1}：预算允许保留{3 - removed_rounds}轮历史")
        print(f"系统资料和当前问题：{fixed_chars}字符；本次预算：{budget}字符")
        print(f"裁剪前：{evidence['before_chars']}字符，{len(before)}条消息")
        print(f"裁剪后：{evidence['after_chars']}字符，{len(after)}条消息；裁剪{dropped}轮")
        print("裁剪后的messages（系统正文折叠展示，完整内容见JSON）：")
        print(f"  system：V3规则、学院资料和运行信息，共{len(after[0]['content'])}字符，完整保留")
        for message in after[1:]:
            print(f"  {message['role']}：{message['content']}")
        print(f"旧代号Z7Q9是否仍在待发送消息中：{'是' if code_present else '否'}")
        print("检查通过：顺序正确、整轮裁剪、系统和当前问题保留、总长度不超预算。")

    def test_01_exact_budget_keeps_all_rounds(self):
        self.check_budget(0)

    def test_02_remove_oldest_round(self):
        self.check_budget(1)

    def test_03_remove_two_oldest_rounds(self):
        self.check_budget(2)


if __name__ == "__main__":
    output = io.StringIO()
    with redirect_stdout(output):
        print("历史裁剪实验：使用预设的三轮历史，不读取密钥、不调用模型。")
        print("验证对象：prepare_messages；预算单位为字符，不是token。")
        print("裁剪前的三轮历史：")
        for message in HISTORY:
            print(f"  {message['role']}：{message['content']}")
        print(f"当前问题：{QUESTION}")
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(HistoryBudgetTests)
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
        print("\n说明：完整messages已保存为JSON，本实验没有发送它们。")
        print("删除早期问答后，其中的个人信息不再出现在本次待发送上下文中。")
        print("这说明裁剪会损失旧信息；未调用模型，不能据此声称观察到了模型的具体回答。")
        print("仅验证应用字符预算，不代表真实模型上下文超限实验。")
    transcript = output.getvalue()
    print(transcript, end="")
    (HistoryBudgetTests.result_dir / "运行记录.txt").write_text(transcript, encoding="utf-8")
    print(f"\n记录位置：{HistoryBudgetTests.result_dir}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
