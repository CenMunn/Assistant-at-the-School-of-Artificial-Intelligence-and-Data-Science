"""检查 SMTP 登录；不发送邮件，不读取收件箱。"""

import argparse
import getpass
import os
from pathlib import Path
import smtplib
import ssl
import sys

from dotenv import load_dotenv


from project_paths import PROJECT_DIR


def check_login(host, port, username, password):
    """使用证书校验的 SSL 连接验证登录，返回状态及说明。"""
    client = None
    try:
        client = smtplib.SMTP_SSL(
            host, port, timeout=15, context=ssl.create_default_context()
        )
        client.login(username, password)
        return 0, "SMTP 登录成功，服务器已接受账号凭据。本次未发送邮件；发信能力仍需后续测试。"
    except smtplib.SMTPAuthenticationError:
        return 1, "SMTP 认证失败。请核对完整邮箱地址、邮箱密码及客户端服务权限；不要连续重试。"
    except ssl.SSLCertVerificationError:
        return 1, "服务器证书校验失败。请检查系统时间及网络环境，保留证书校验。"
    except (TimeoutError, smtplib.SMTPServerDisconnected):
        return 1, "连接超时或服务器断开连接。请检查网络，稍后再试。"
    except smtplib.SMTPException:
        return 1, "SMTP 服务器未完成登录，请向邮箱管理员确认客户端登录要求。"
    except OSError:
        return 1, "无法建立连接。请检查服务器地址、端口和网络。"
    finally:
        if client is not None:
            try:
                client.close()
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="连接服务器并验证一次登录")
    args = parser.parse_args(argv)
    load_dotenv(PROJECT_DIR / ".env", override=False)
    host = os.getenv("SMTP_HOST", "smtp.exmail.qq.com").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    try:
        port = int(os.getenv("SMTP_PORT", "465"))
        if not host or not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        print("配置错误：请检查 SMTP_HOST 和 SMTP_PORT。")
        return 2
    if not username or "@" not in username or any(c.isspace() for c in username):
        print("请在本目录 .env 中设置 SMTP_USERNAME，填写完整邮箱地址。")
        return 2

    print(f"发信服务器：{host}:{port}；SSL/TLS，校验服务器证书。")
    print("只验证登录，不发送邮件、不访问收件箱、不自动重试。")
    if not args.run:
        print("配置检查完成。加 --run 才会连接服务器。")
        return 0

    password = os.getenv("SMTP_PASSWORD", "")
    if not password:
        if not sys.stdin.isatty():
            print("请在交互式 PowerShell 终端运行，便于隐藏输入密码。")
            return 2
        try:
            password = getpass.getpass("请输入邮箱密码（输入时不显示字符，按 Enter 确认）：")
        except (KeyboardInterrupt, EOFError):
            print("\n已取消，尚未连接服务器。")
            return 2
    if not password:
        print("密码为空，已取消。")
        return 2
    code, message = check_login(host, port, username, password)
    print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
