# 智数学院助理

基于 Python、OpenAI 风格 SDK 与 Gradio 的校园助理课程项目。面向对外经济贸易大学人工智能与数据科学学院的课程、教师和学习事务提供本地交互服务，助理昵称为“小智”。

这是实验二项目，包含命令行和网页两种入口。知识资料是保留核对日期的本地快照，学校政策、教师任课和考试安排应按适用学期及官方通知确认。

## 功能

- 课程咨询与连续学习辅导，推荐已收录的课程学习网站。
- 流式与非流式回答，支持对话上下文开关和字符预算裁剪。
- 教师资料按系所浏览、搜索，公开教师邮箱可快捷填入邮件。
- 本机历史会话的新建、切换、重命名和删除；支持编辑已发送消息及分支版本切换。
- 考试通知或考试总表导入、按学号和学期查询，以及邮件提醒。
- 邮件草稿预览和确认发送、日志记录与常见接口异常处理。

## 安装与启动

项目曾在 Windows、Python 3.14 环境验证。请先安装 Python，再打开 PowerShell：

```powershell
git clone https://github.com/CenMunn/Assistant-at-the-School-of-Artificial-Intelligence-and-Data-Science.git
Set-Location Assistant-at-the-School-of-Artificial-Intelligence-and-Data-Science
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

在 `.env` 中填写自己的 `DEEPSEEK_API_KEY`、`DEEPSEEK_API_BASE` 和实际可用的 `DEEPSEEK_MODEL`。模型服务应兼容 OpenAI Chat Completions 接口，Base URL 不要填写到 `/chat/completions` 路径。

保存后启动网页：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\src\web_app.py
```

浏览器打开 **http://127.0.0.1:7860/**。终端需要持续运行；停止时在该终端按 **Ctrl+C**。之后再次启动只需进入项目文件夹并运行上述启动命令，无需重新安装依赖。

命令行入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\src\assistant.py
```

命令行支持 `/stream on`、`/stream off`、`/history on` 和 `/history off`。

## 邮件与考试示例

邮件为可选功能。使用前在 `.env` 配置自己的 `SMTP_HOST`、`SMTP_PORT`、`SMTP_SECURITY`、`SMTP_USERNAME` 和 `SMTP_PASSWORD`；邮箱服务可能要求专用授权码。未配置邮件时可先使用聊天和资料查询。实际发送需在网页预览并确认。

`texts/示例数据/` 提供匿名考试总表和通知，可用于体验导入功能。真实学生考试总表、个人考试通知、历史聊天、日志和已有提醒任务不随公开仓库上传。首次运行会按功能需要创建本地数据库目录。

提醒依赖本地程序持续运行、电脑保持唤醒和网络可用；服务器接受邮件不等于收件人已经阅读。

## 文件结构

| 路径 | 用途 |
| --- | --- |
| `src/web_app.py` | Gradio 网页入口 |
| `src/assistant.py` | SDK 调用、上下文、日志和命令行入口 |
| `src/conversation_*.py` | 会话保存、版本树和历史列表 |
| `src/exam_*.py` | 考试导入、查询和提醒 |
| `texts/prompts/` | 独立维护的 Prompt |
| `texts/college_knowledge.txt` | 学院知识和资料范围 |
| `texts/teachers.json` | 已核对的教师资料 |
| `texts/learning_resources.json` | 学习网站目录 |
| `texts/示例数据/` | 匿名演示数据 |
| `resources/` | 页面样式、交互脚本、培养方案、规则及分词资源 |
| `images/` | 校徽和聊天头像 |
| `.env.example` | 不含凭据的配置模板 |

## 使用范围

本项目是本地课程原型，自填学号不等于身份认证，历史会话也不构成正式多用户权限隔离。页面中的历史方案和公开教师资料不代表当前班级实际安排。系统的字符预算不是模型官方 token 上下文长度。

模型调用会产生所配置服务的用量。请勿提交 `.env`、邮箱凭据、个人数据库和日志；`.gitignore` 已排除常见本地文件。学校校徽、公开政策、培养方案等来源材料的权利归原权利人，本仓库不另行授权其商标或内容。
