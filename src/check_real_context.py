"""真实上下文实验。默认仅预览；--run 后仍需分别确认两次请求。"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse

import httpx
from openai import OpenAI, APIStatusError, APITimeoutError, APIConnectionError
from tokenizers import Tokenizer

from assistant import BEIJING, PROJECT_DIR, read_settings
from project_paths import RESOURCE_DIR


def prepare_input():
    tokenizer_path = RESOURCE_DIR / "deepseek_v4_tokenizer" / "tokenizer.json"
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    tokenizer.no_truncation()
    text = "a " * 1_200_000
    count = len(tokenizer.encode(text, add_special_tokens=False).ids)
    if not 1_100_000 <= count <= 1_300_000:
        raise ValueError("本地正文token数不在预定范围内，请先核对分词文件，不发送请求。")
    metadata = {
        "generator": "'a ' * 1200000",
        "characters": len(text),
        "utf8_bytes": len(text.encode("utf-8")),
        "local_content_tokens": count,
        "token_count_note": "仅离线正文计数，不含消息包装，不等于服务端计费量。",
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "tokenizer_sha256": hashlib.sha256(tokenizer_path.read_bytes()).hexdigest(),
        "tokenizer_source": "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip",
        "spec_checked_on": "2026-10-11",
        "documented_context": "1M tokens",
        "pricing_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
        "uncached_input_cny_per_million_off_peak_peak": [1, 2],
        "output_cny_per_million_off_peak_peak": [4, 8],
        "price_note": "估算不是费用上限；拒绝请求是否计费未确认。价格变动后应重新核对。",
    }
    return text, metadata


def validate_settings(base_url, model):
    address = urlparse(base_url)
    if (address.scheme != "https" or address.hostname != "api.deepseek.com"
            or address.port not in (None, 443) or address.username or address.password
            or address.query or address.fragment or address.path.rstrip("/") not in ("", "/v1")
            or model != "deepseek-flash"):
        raise ValueError("本实验仅允许官方HTTPS接口和deepseek-flash，其他配置需重新核对规格及费用。")


def make_client(api_key, base_url, http_client=None):
    return OpenAI(api_key=api_key, base_url=base_url, max_retries=0,
                  timeout=httpx.Timeout(60.0, connect=15.0), http_client=http_client)


def call_once(client, model, text, label):
    messages = [
        {"role": "system", "content": "This is a context-length test. Reply only OK."},
        {"role": "user", "content": text},
    ]
    record = {"label": label, "started_at": datetime.now(BEIJING).isoformat(),
              "http_status": None, "request_id": None}
    start = time.perf_counter()
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=model,
            messages=messages,
            temperature=0.3,
            stream=False,
            max_tokens=32,
            extra_body={"thinking": {"type": "disabled"}},
        )
        record["http_status"] = raw.status_code
        record["request_id"] = raw.headers.get("x-request-id") or raw.headers.get("request-id")
        response = raw.parse()
        choice = response.choices[0] if response.choices else None
        record.update(
            outcome="response_received", response_id=response.id, returned_model=response.model,
            content=choice.message.content if choice else None,
            finish_reason=choice.finish_reason if choice else None,
            usage=response.usage.model_dump() if response.usage else None,
        )
    except APIStatusError as error:
        body = error.response.text.replace(client.api_key, "[REDACTED]")
        record.update(outcome="api_rejection", http_status=error.status_code,
                      request_id=error.response.headers.get("x-request-id")
                      or error.response.headers.get("request-id"),
                      error_type=type(error).__name__, error_body=body[:12000],
                      error_body_truncated=len(body) > 12000)
    except APITimeoutError:
        record.update(outcome="timeout", error_type="APITimeoutError")
    except APIConnectionError:
        record.update(outcome="connection_error", error_type="APIConnectionError")
    except Exception as error:
        record.update(outcome="local_or_parse_error", error_type=type(error).__name__,
                      detail=str(error).replace(client.api_key, "[REDACTED]")[:1000])
    record["elapsed_seconds"] = round(time.perf_counter() - start, 3)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return record


def run_sequence(client, model, long_text, report_path, metadata, confirm=input):
    report = {
        "started_at": datetime.now(BEIJING).isoformat(),
        "experiment": "real-context-probe", "metadata": metadata,
        "parameters": {"model": model, "base_url": str(client.base_url),
                       "stream": False, "temperature": 0.3, "max_tokens": 32,
                       "thinking": "disabled", "max_retries": 0},
        "requests": [], "state": "awaiting_short_confirmation",
        "interpretation": "需人工核对原始响应，不能把所有错误或length结束都称为上下文超限。",
    }

    def save(state):
        report["state"] = state
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save("awaiting_short_confirmation")
    try:
        if confirm("输入 SHORT 发送一次短文本真实请求；其他输入取消：").strip() != "SHORT":
            save("cancelled_before_short")
            return
        save("short_in_flight")
        print("\n短文本对照请求：")
        short = call_once(client, model, "Reply OK.", "short_control")
        report["requests"].append(short)
        if (short["outcome"] != "response_received" or short.get("finish_reason") != "stop"
                or not short.get("content")):
            save("short_failed")
            print("短请求未完整成功，停止实验，不发送超长请求。")
            return
        save("awaiting_long_confirmation")
        print("\n下一次请求正文约120万tokens；按未命中输入估算约1.20/2.40元（非高峰/高峰）。")
        print("另有少量消息包装与最多32个输出tokens；不是硬性费用上限，不保证失败免费。")
        print("确认前可检查平台余额；本脚本不会自动查询或充值。")
        if confirm("接受费用并发送一次超长请求，请输入 LONG；其他输入取消：").strip() != "LONG":
            save("cancelled_before_long")
            return
        save("long_in_flight")
        print("\n超长真实请求（不重试，请等待结果）：")
        report["requests"].append(call_once(client, model, long_text, "oversized_input"))
        save("finished_needs_review")
        print("\n两次请求已结束。请核对错误正文是否明确说明上下文超限。")
        print("超时、413、普通400或正常返回，都不能直接写成已观察到上下文超限；不要连续重跑。")
    except (EOFError, KeyboardInterrupt):
        save("interrupted")
        print("\n已停止。若请求已经发出，停止本地等待不保证服务器停止处理或免于计费。")
    finally:
        print("记录位置：", report_path.resolve())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="进入真实请求模式，仍需交互确认")
    args = parser.parse_args(argv)
    text, metadata = prepare_input()
    print("真实上下文实验准备信息：")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    if not args.run:
        print("\n当前仅本地预览，未读取密钥，未发送模型请求。真实实验须添加 --run。")
        return 0
    api_key, base_url, model = read_settings()
    validate_settings(base_url, model)
    print("\n模型：", model, "；接口：", base_url)
    stamp = datetime.now(BEIJING).strftime("%Y%m%d-%H%M%S-%f")
    report_path = PROJECT_DIR / "experiment_results" / f"real-context-{stamp}" / "record.json"
    with make_client(api_key, base_url) as client:
        run_sequence(client, model, text, report_path, metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
