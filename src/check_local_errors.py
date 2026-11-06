"""演示本地错误处理。配置仅在测试进程内修改，模型客户端使用替身。"""

from contextlib import redirect_stdout
from datetime import datetime
import io
import os
from pathlib import Path
from unittest.mock import Mock, patch

import assistant


def run_case(title, arguments, environment, expected_code, expected_text):
    print(f"\n{title}")
    client = Mock()
    # 即使后续代码意外绕过检查，也不会发送真实API请求。
    client.chat.completions.create.side_effect = AssertionError("本实验不允许调用模型")
    output = io.StringIO()
    with patch.dict(os.environ, environment), \
            patch.object(assistant, "OpenAI", return_value=client), \
            patch("sys.argv", ["assistant.py", *arguments]), \
            redirect_stdout(output):
        exit_code = assistant.main()
    transcript = output.getvalue()
    print(transcript, end="")
    calls = client.chat.completions.create.call_count
    passed = exit_code == expected_code and expected_text in transcript and calls == 0
    print(f"程序退出码：{exit_code}；模型请求次数：{calls}；检查：{'通过' if passed else '未通过'}")
    return passed


def main():
    report = io.StringIO()
    with redirect_stdout(report):
        print("本地异常处理实验（受控测试，不连接模型服务）")
        print("时间：", datetime.now(assistant.BEIJING).isoformat(timespec="seconds"))
        print("预期错误时退出码1表示本次请求未完成，不表示测试脚本失败。")
        first = run_case(
            "实验A：API Key缺失",
            ["--question", "你好", "--test-id", "LOCAL-MISSING-KEY"],
            {"DEEPSEEK_API_KEY": "", "DEEPSEEK_API_BASE": "https://api.deepseek.com"},
            1, "请在 .env 中填写",
        )
        second = run_case(
            "实验B：系统资料加问题超过应用字符预算",
            ["--max-chars", "100", "--question", "数据库系统主要学什么？",
             "--test-id", "LOCAL-CHAR-LIMIT", "--no-history"],
            {"DEEPSEEK_API_KEY": "local-test-placeholder",
             "DEEPSEEK_API_BASE": "https://api.deepseek.com",
             "DEEPSEEK_MODEL": "deepseek-flash"},
            1, "已超过应用字符预算",
        )
        print("\n结论：A为本地配置校验，不是平台401；B为本地字符预算校验，不是模型上下文超限。")
        print("A在会话初始化前失败，当前程序不会把它写入问答JSONL；本报告保存其启动诊断。")
        print("B进入ask后被拦截，logs中以LOCAL-CHAR-LIMIT记录error，未产生模型输出。")
        print("两个测试均未修改.env。正常程序仍可读取原来的配置。")
    text = report.getvalue()
    print(text, end="")
    report_dir = assistant.PROJECT_DIR / "experiment_results"
    report_dir.mkdir(exist_ok=True)
    stamp = datetime.now(assistant.BEIJING).strftime("%Y%m%d-%H%M%S-%f")
    destination = report_dir / f"local-errors-{stamp}.txt"
    destination.write_text(text, encoding="utf-8")
    print(f"\n实验记录已保存：{destination}")
    return 0 if first and second else 1


if __name__ == "__main__":
    raise SystemExit(main())
