# UKMFolio MCP Server

**🌐 语言 / Language:** **中文** · [English](./README.md)

一个 [MCP](https://modelcontextprotocol.io)（Model Context Protocol）服务器，让 AI 助手（Claude Desktop / Claude Code 等）能够直接访问 **UKM Folio**（马来西亚国民大学 UKM 的 Moodle 教学平台）上当前登录学生的内容：

- 📚 已选课程
- 📝 作业 / 测验的截止日期
- 📢 论坛公告与讨论
- 📄 课程文档（PDF / PPTX / DOCX / XLSX …，**自动下载并提取正文文字**）
- 🖼️ 老师发的图片（公告海报、二维码、时间表 …）——**在各工具结果中标出，并能以真实图片返回给 AI 读取**

登录与数据访问逻辑沿用经过长期实战验证的 [`UKMFolioPuller`](./UKMFolioPuller)：先走 `sso.ukm.my` 的 **SAML 2.0 单点登录**，再调用 Moodle 的 AJAX 接口 + 定向 HTML 抓取（UKM Folio 关闭了大部分列表级 Web Service 函数，这套混合方案是实测可用的方式）。

> ⚠️ 只读工具，不会替你提交作业、发帖或改成绩。

---

## 目录

- [功能与工具一览](#功能与工具一览)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置 config.json](#配置-configjson)
- [快速开始：自检](#快速开始自检)
- [运行服务器](#运行服务器)
- [接入 AI 客户端](#接入-ai-客户端)
- [工具详解](#工具详解)
- [典型使用场景](#典型使用场景)
- [部署到服务器（长期运行）](#部署到服务器长期运行)
- [故障排查](#故障排查)
- [项目结构与原理](#项目结构与原理)
- [安全与限制](#安全与限制)

---

## 功能与工具一览

服务器对外暴露 11 个工具：

| 工具 | 作用 |
|------|------|
| `list_courses` | 列出已选课程（id、全名、课程代码、分类、进度、链接） |
| `list_deadlines` | 列出作业/测验截止日期，可按课程、未来天数、是否含已过期筛选 |
| `get_submission_status` | 查作业的提交状态：是否已提交 / 已评分 / 已逾期（按课程，或按 cmid 查单个） |
| `list_announcements` | 列出论坛公告/讨论，按时间倒序 |
| `get_discussion` | 拉取某条讨论的完整帖子串（含正文 HTML 与纯文本） |
| `list_documents` | 列出某课程的文档（resource/folder/url/page/book），给出 `cmid` 和 `type` |
| `read_document` | 下载文档并**提取正文文字**（PDF/DOCX/PPTX/XLSX/TXT/HTML） |
| `list_modules` | 列出课程里所有类型的活动模块（用来发现 cmid / 看课程结构） |
| `list_images` | 列出课程里老师发的所有图片（论坛帖、标签、章节说明、page、图片文件），每张带一个 `image_url` |
| `view_image` | 按 `image_url` 取回一张图片，**直接返回图片本身**给 AI 读取（自动缩放） |
| `whoami` | 诊断：确认登录是否成功，返回站点、时区、课程数 |

**通用约定：**

- 大多数工具有一个可选的 `course` 过滤参数，可填 **课程 id**、**课程代码**（如 `TTTN2423`）或**课程名的任意子串**；不填则覆盖所有已选课程。
- 时间字段同时返回 **unix 秒**（如 `deadline`）和 **ISO-8601 本地时间字符串**（如 `deadline_local`，默认马来西亚时区 `Asia/Kuala_Lumpur`）。
- 读文档的标准流程：先 `list_documents` 拿到 `cmid` 和 `type`，再 `read_document(cmid, type)`。
- **图片以 URL 作为唯一标识。** 凡是出现图片的地方，结果里都有 `images` 列表（每项带 `image_url`），帖子正文在原位置插入 `[image: <alt> | <image_url>]` 标记，图片类文件带 `is_image: true`。把 URL 传给 `view_image` 即可看图。

---

## 环境要求

- **Python ≥ 3.11**（本机用的是 `/home/alanwine/PyVenv`，Python 3.14）
- 能访问 `ukmfoliov2.ukm.my` 和 `sso.ukm.my` 的网络
- 一个有效的 UKM Folio 账号（学号 + 密码）

依赖（已写入 `requirements.txt` / `pyproject.toml`）：

```
mcp            # 官方 MCP Python SDK（FastMCP）
requests       # HTTP / 会话
pypdf          # 提取 PDF 文字
python-docx    # 提取 .docx
python-pptx    # 提取 .pptx
openpyxl       # 提取 .xlsx
beautifulsoup4 # 解析 HTML 页面
lxml           # bs4 的解析后端
pillow         # 为 view_image 解码 / 缩放 / 转换图片
```

> `mcp` 限定为 `<2`：mcp 2.x 把本服务用到的 `FastMCP` 改名了。

---

## 安装

```bash
# 1) 进入项目目录
cd /home/alanwine/Documents/CodeProjects/Python/UKMFolioMCP

# 2) 安装依赖（任选其一）
/home/alanwine/PyVenv/bin/pip install -r requirements.txt
#   或者以「可编辑包」方式安装，会额外提供 ukmfolio-mcp 命令行入口
/home/alanwine/PyVenv/bin/pip install -e .

# 3) 准备配置文件
cp config.example.json config.json
# 然后编辑 config.json，填上你的学号和密码
```

> 用 `pip install -e .` 之后，下文中的 `python -m ukmfolio_mcp` 都可以换成更短的 `ukmfolio-mcp`。

---

## 配置 config.json

`config.json` 与 `UKMFolioPuller` 同一套字段，但这里只有登录相关字段是必填的（即使带着 Telegram 字段也会被忽略）。

```json
{
    "username": "a207421",
    "password": "你的密码",
    "base_url": "https://ukmfoliov2.ukm.my",
    "sso_url": "https://sso.ukm.my",
    "timezone": "Asia/Kuala_Lumpur",
    "host": "127.0.0.1",
    "port": 8000
}
```

| 字段 | 必填 | 说明 |
|------|:---:|------|
| `username` | ✅ | UKM 学号 |
| `password` | ✅ | 登录密码 |
| `base_url` | | UKM Folio 站点，默认 `https://ukmfoliov2.ukm.my`（旧地址 `ukmfolio.ukm.my` 现在会跳转过去） |
| `sso_url` | | SSO 站点，默认 `https://sso.ukm.my` |
| `timezone` | | 时间字段使用的时区，默认 `Asia/Kuala_Lumpur` |
| `host` | | HTTP 模式默认监听地址，默认 `127.0.0.1`（可被 `--host` 覆盖） |
| `port` | | HTTP 模式默认端口，默认 `8000`（可被 `--port` 覆盖） |

**凭据 / 配置文件的查找顺序：**

- 账号密码：环境变量 `UKMFOLIO_USERNAME` / `UKMFOLIO_PASSWORD` 优先于文件（适合服务器部署，避免明文落盘）。
- 配置文件路径：`--config <路径>` → 环境变量 `UKMFOLIO_CONFIG` → 项目根目录的 `config.json` → 当前工作目录的 `config.json`。

> 🔒 `config.json` 已在 `.gitignore` 中，不会被提交。

---

## 快速开始：自检

第一次用先跑 `--check`，它会真正登录一次并打印课程和近期截止数量，确认账号和网络都没问题（不会启动服务，跑完就退出）：

```bash
/home/alanwine/PyVenv/bin/python -m ukmfolio_mcp --check
```

正常输出类似：

```
[*] Logging in to UKM Folio …
[*] Authenticated. base_url=https://ukmfoliov2.ukm.my sesskey=LvH2dF34SK tz=Asia/Kuala_Lumpur
[*] 6 enrolled courses:
        15292  TTTN2423   Keperluan Pensuisan, Penghalaan dan Tanpa Wayar
        23521  TTTM2213   PENGATURCARAAN APLIKASI MUDAH ALIH
        ...
[*] 9 deadlines in next 14 days.
[*] OK
```

---

## 运行服务器

```bash
# 本地 stdio 传输（默认，给 Claude Desktop / Claude Code 用）
/home/alanwine/PyVenv/bin/python -m ukmfolio_mcp --stdio

# 远程 streamable HTTP 传输（部署到服务器、多客户端连接）
/home/alanwine/PyVenv/bin/python -m ukmfolio_mcp --http-server --host 0.0.0.0 --port 8000
#   接入端点： http://<host>:<port>/mcp
```

| 参数 | 说明 |
|------|------|
| `--stdio` | 用标准输入输出通信（默认）。MCP 客户端会自己拉起这个进程。 |
| `--http-server` | 启动一个 streamable-HTTP（SSE）服务，端点在 `/mcp`。 |
| `--host` / `--port` | 覆盖 config.json 里的监听地址 / 端口（仅 HTTP 模式有意义）。 |
| `--config <路径>` | 指定配置文件路径。 |
| `--check` | 仅测试登录并打印统计，然后退出。 |

> stdio 模式不要手动「跑起来等着」——它没有界面，是给客户端用管道驱动的。你平时只需配置好客户端，由客户端按需启动它。

---

## 接入 AI 客户端

### Claude Code

```bash
claude mcp add ukmfolio -- /home/alanwine/PyVenv/bin/python -m ukmfolio_mcp \
  --stdio --config /home/alanwine/Documents/CodeProjects/Python/UKMFolioMCP/config.json
```

加好后在 Claude Code 里用 `/mcp` 可以看到 `ukmfolio` 及其工具。

### Claude Desktop

编辑 `claude_desktop_config.json`（macOS 在 `~/Library/Application Support/Claude/`，Windows 在 `%APPDATA%\Claude\`），加入：

```json
{
  "mcpServers": {
    "ukmfolio": {
      "command": "/home/alanwine/PyVenv/bin/python",
      "args": [
        "-m", "ukmfolio_mcp", "--stdio",
        "--config", "/home/alanwine/Documents/CodeProjects/Python/UKMFolioMCP/config.json"
      ],
      "cwd": "/home/alanwine/Documents/CodeProjects/Python/UKMFolioMCP"
    }
  }
}
```

保存后重启 Claude Desktop。

> 💡 强烈建议传**绝对路径**的 `--config`，因为客户端启动子进程时的工作目录不一定是项目目录，否则可能找不到 `config.json`。

### 远程 HTTP 客户端

先在服务器上以 `--http-server` 启动（见上文），然后让支持 streamable-HTTP 的 MCP 客户端连到 `http://<host>:<port>/mcp` 即可。

---

## 工具详解

下面每个工具给出**参数**和**真实返回示例**（字段名与实际一致，数值为示意）。返回都是 JSON。

### 1. `list_courses`

列出已选课程。无参数。

```jsonc
[
  {
    "course_id": 15292,
    "course_name": "Keperluan Pensuisan, Penghalaan dan Tanpa Wayar",
    "course_shortname": "TTTN2423",
    "course_category": "Fakulti ...",
    "course_url": "https://ukmfolio.ukm.my/course/view.php?id=15292",
    "progress": null
  }
]
```

### 2. `list_deadlines`

列出作业/测验截止日期。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `course` | string? | 全部 | 课程 id / 代码 / 名称子串 |
| `days_ahead` | int? | 不限 | 只看未来这么多天内的 |
| `include_past` | bool | `true` | 是否包含已过期的；只看未来填 `false` |

```jsonc
[
  {
    "item_id": 123456,
    "item_type": "assign",
    "item_title": "[Group 2IT2] Lab 8: WLAN Configurations",
    "deadline": 1782489540,
    "item_url": "https://ukmfolio.ukm.my/mod/assign/view.php?id=...",
    "course_id": 15292,
    "course_shortname": "TTTN2423",
    "course_name": "Keperluan Pensuisan, ...",
    "deadline_local": "2026-06-18T23:59:00+08:00",
    "days_left": 4.3
  }
]
```

### 3. `get_submission_status`

查作业是否已提交、是否已评分、是否逾期。UKM Folio 禁用了作业的 Web Service 接口，所以这里抓取每个作业页面的"Submission status"表格——**每个作业一次 HTTP 请求**，建议带 `course` 过滤或直接查单个 `cmid`。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `course` | string? | 全部 | 课程过滤（id / 代码 / 名称子串），会遍历该课程下所有作业 |
| `cmid` | int? | — | 按课程模块 id 查单个作业（来自 `list_deadlines` 里作业的 `item_url`，或 `list_modules`）。优先于 `course`。 |

```jsonc
[
  {
    "cmid": 781045,
    "name": "[Group 2AKIT1] Lab 1: Basic Switch and Router Configurations",
    "item_type": "assign",
    "item_url": "https://ukmfolio.ukm.my/mod/assign/view.php?id=781045",
    "submission_status": "Submitted for grading",
    "submitted": true,
    "grading_status": "Not graded",
    "graded": false,
    "time_remaining": "Assignment was submitted 6 hours 59 mins early",
    "is_overdue": false,
    "last_modified": null,
    "grade": null,
    "status_found": true,
    "section": "Topic 1",
    "course_id": 15292,
    "course_shortname": "TTTN2423",
    "course_name": "Keperluan Pensuisan, ..."
  }
]
```

字段说明：

- `submission_status` / `grading_status` / `time_remaining` 是 **Moodle 原始字符串**（如 `"No submissions have been made yet"`、`"Not graded"`、`"4 days 4 hours remaining"`）。
- `submitted`、`graded`、`is_overdue` 是从原始字符串归一化出来的布尔值，方便筛选。当页面格式无法识别时它们为 `null`，此时 `status_found` 为 `false`。
- 作业列表来自 `core_courseformat_get_state`，所以包含课程里**所有**可见的作业模块（比 `list_deadlines` 更全，后者只列出有日历截止日期的项）。

### 4. `list_announcements`

列出论坛公告/讨论（按发布时间倒序）。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `course` | string? | 全部 | 课程过滤 |
| `limit` | int | `20` | 最多返回几条 |
| `with_body` | bool | `true` | 是否带上根帖纯文本正文 |

```jsonc
[
  {
    "item_id": 317111,
    "item_type": "forum",
    "item_title": "Week 11 Lecture and Lab Challenge 4",
    "author": "DR. WAN FARIZA BINTI PAIZI @ FAUZI",
    "posted_at": 1780000026,
    "posted_at_local": "2026-06-04T10:00:26+08:00",
    "reply_count": 0,
    "item_url": "https://ukmfolio.ukm.my/mod/forum/discuss.php?d=317111",
    "item_body": "Dear Students, Below is the link to today's lecture ...",
    "images": [],
    "course_id": 15292,
    "course_shortname": "TTTN2423",
    "course_name": "Keperluan Pensuisan, ..."
  }
]
```

> `item_id` 就是讨论 id，传给 `get_discussion` 可看完整帖串。

`images` 汇总了整个帖串里出现的所有图片（嵌入的和附件），`item_body` 里也会在原位置标出。很多公告**只有一张海报**，光看文字可能是空的或不完整：

```jsonc
{
  "item_title": "Welcome & Important Notice: No Tutorial/Lab This Week",
  "item_body": "[image: Announcement for no tutorial and lab for this week. | https://ukmfoliov2.ukm.my/pluginfile.php/14955/mod_forum/post/287/Gemini_Generated_Image_lfv2fwlfv2fwlfv2.jpg]",
  "images": [
    {
      "image_url": "https://ukmfoliov2.ukm.my/pluginfile.php/14955/mod_forum/post/287/Gemini_Generated_Image_lfv2fwlfv2fwlfv2.jpg",
      "filename": "Gemini_Generated_Image_lfv2fwlfv2fwlfv2.jpg",
      "alt": "Announcement for no tutorial and lab for this week.",
      "post_id": 287
    }
  ]
}
```

### 5. `get_discussion`

拉取某条讨论的全部帖子。

| 参数 | 类型 | 说明 |
|------|------|------|
| `discussion_id` | int | 讨论 id（来自 `list_announcements` 的 `item_id`） |

```jsonc
{
  "discussion_id": 317111,
  "title": "Week 11 Lecture and Lab Challenge 4",
  "url": "https://ukmfolio.ukm.my/mod/forum/discuss.php?d=317111",
  "post_count": 1,
  "posts": [
    {
      "post_id": 998877,
      "subject": "Week 11 Lecture and Lab Challenge 4",
      "author": "DR. WAN FARIZA BINTI PAIZI @ FAUZI",
      "timecreated": 1780000026,
      "created_local": "2026-06-04T10:00:26+08:00",
      "timemodified": 1780000026,
      "modified_local": "2026-06-04T10:00:26+08:00",
      "message_text": "Dear Students, ...",
      "message_html": "<p>Dear Students, ...</p>",
      "images": [],
      "parent_id": 0
    }
  ]
}
```

### 6. `list_documents`

列出某课程的文档类模块。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `course` | string? | 全部 | 课程过滤（建议指定，遍历全部课程较慢） |

```jsonc
[
  {
    "cmid": 275824,
    "name": "Course Proforma",
    "type": "resource",
    "url": "https://ukmfolio.ukm.my/mod/resource/view.php?id=275824",
    "section": "Course Information",
    "section_number": 1,
    "visible": true,
    "course_id": 15292,
    "course_shortname": "TTTN2423",
    "course_name": "Keperluan Pensuisan, ..."
  }
]
```

`type` 取值：`resource`（单个文件）、`folder`（文件夹/多文件）、`url`（外链）、`page`（站内网页）、`book`（多章节）。

### 7. `read_document`

下载文档并提取文字。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `cmid` | int | — | 来自 `list_documents` 的 `cmid` |
| `type` | string | `"resource"` | 来自 `list_documents` 的 `type` |
| `extract` | bool | `true` | 是否下载并提取文字；`false` 只返回文件链接 |
| `max_chars` | int | `50000` | 每段提取文本的截断长度 |

```jsonc
{
  "cmid": 275824,
  "type": "resource",
  "external_url": null,
  "files": [
    {
      "filename": "TTTN2423 ... (CCNA2).pdf",
      "file_url": "https://ukmfolio.ukm.my/pluginfile.php/2430601/mod_resource/content/3/....pdf",
      "content_type": "application/pdf",
      "size_bytes": 207508,
      "text": "Proforma Kursus  1) Kod Kursus : TTTN2423 ...",
      "text_truncated": true
    }
  ]
}
```

不同 `type` 的返回差异：

- `url` 模块：返回 `external_url`（外部链接），不下载。
- `page` / `book` 模块：返回 `page_text`（站内页面正文）。
- 无法识别的二进制类型：会下载但 `text` 为 `null`，并带一条 `note` 说明没有对应的文本提取器。
- 图片文件（PNG/JPG/…）在这里**不下载**：条目带 `is_image: true` 和一条 `note`，把 `file_url` 传给 `view_image` 即可。
- `page` / `book` 正文里嵌入的图片会额外返回 `images` 列表。

### 8. `list_modules`

列出课程里**所有类型**的模块（不只是文档），用来发现某个活动的 `cmid` 或浏览课程结构。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `course` | string? | 全部 | 课程过滤 |

返回每个模块的 `cmid`、`name`、`type`、`url`、`section`、`course_id`。

### 9. `list_images`

列出老师发过的所有图片，每张带一个 `image_url`，传给 `view_image` 即可查看。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `course` | string? | 全部 | 课程过滤（建议填写——每条讨论、每个模块各要一次请求） |
| `include_forums` | bool | `true` | 扫描论坛帖子（嵌入图片 + 附件，帖串里每一帖都扫） |
| `include_course_content` | bool | `true` | 扫描章节说明、标签 / 活动描述、page/book 正文，以及 resource/folder 里的图片文件 |

```jsonc
[
  {
    "image_url": "https://ukmfoliov2.ukm.my/pluginfile.php/14980/mod_forum/post/118/WhatsApp%20Image%202026-09-25%20at%204.18.18%20PM.jpeg",
    "filename": "WhatsApp Image 2026-09-25 at 4.18.18 PM.jpeg",
    "alt": "TTTK2233",
    "source": "forum_post",
    "source_title": "2026/2027: Lecture whatsApp group",
    "source_url": "https://ukmfoliov2.ukm.my/mod/forum/discuss.php?d=116",
    "discussion_id": 116,
    "post_id": 118,
    "posted_at_local": "2026-09-25T16:23:37+08:00",
    "course_id": 3476,
    "course_shortname": "TTTK2233",
    "course_name": "TTTK2233 CYBER SECURITY"
  }
]
```

`source` 取值：`forum_post`、`section`、`label`、`page`、`book`、`resource`、`folder`，或其他在课程页显示描述的活动类型（如 `assign`）。非论坛来源带 `cmid` 和 `section`，而不是 `discussion_id`/`post_id`。主题图标、站点 logo、侧边栏 block 里的图片都会被过滤掉。

### 10. `view_image`

取回一张图片，以 MCP 图片内容返回，让 AI 真正"看到"它（海报上的文字、二维码、时间表 …）。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `image_url` | string | — | 上面任一工具给出的 `image_url`，或 `read_document` 标了 `is_image` 的 `file_url` |
| `max_edge` | int | `1568` | 缩放到长边不超过这么多像素；只有小字看不清时才调大 |

返回两个内容块：一段 JSON 元数据文本，然后是图片本身。

```jsonc
{"image_url": "https://ukmfoliov2.ukm.my/pluginfile.php/14955/mod_forum/post/287/Gemini_Generated_Image_lfv2fwlfv2fwlfv2.jpg",
 "filename": "Gemini_Generated_Image_lfv2fwlfv2fwlfv2.jpg",
 "original_format": "JPEG", "original_size": [1536, 2752], "original_bytes": 3504647,
 "size": [875, 1568], "bytes": 361833, "resized": true}
```

- UKM Folio 上的图片用已登录会话下载（会话过期会自动重登）。
- 外站图片**不带** cookie 下载，并拒绝回环/内网地址。
- 尺寸和大小都在限制内的 JPEG/PNG/GIF/WEBP 原样返回；过大的会缩放，其他格式（BMP、TIFF …）转成 JPEG（有透明通道则转 PNG）。不支持 SVG。

### 11. `whoami`

诊断工具，无参数。

```jsonc
{
  "base_url": "https://ukmfoliov2.ukm.my",
  "sesskey": "LvH2dF34SK",
  "timezone": "Asia/Kuala_Lumpur",
  "course_count": 6
}
```

---

## 典型使用场景

接好客户端后，你可以直接用自然语言提问，AI 会自动调用上面的工具。例如：

- **「我未来一周有哪些作业要交？」** → `list_deadlines(days_ahead=7, include_past=false)`
- **「TTTN2423 还有哪些作业我没交？」** → `get_submission_status(course="TTTN2423")` → 筛 `submitted == false`
- **「我交的作业有被批改打分的吗？」** → `get_submission_status()` → 筛 `graded == true`
- **「TTTN2423 最近有什么公告？」** → `list_announcements(course="TTTN2423", limit=5)`
- **「把 TTTN2423 的 Course Proforma 读出来，总结课程评分占比。」** → `list_documents(course="TTTN2423")` 找到 cmid → `read_document(cmid, "resource")` → 基于提取的正文总结
- **「这周哪门课的 PPT 更新了？讲了什么？」** → `list_documents` + `read_document`（folder/resource）
- **「把这条公告的完整内容和后续回复给我。」** → `get_discussion(discussion_id)`
- **「TTTC3213 老师发的欢迎海报写了什么？」** → `list_announcements(course="TTTC3213")` → 取该条的 `images[0].image_url` → `view_image(image_url)`
- **「有没有老师发 WhatsApp/Telegram 群二维码？」** → `list_images()` → 对可疑的几张调用 `view_image`

---

## 部署到服务器（长期运行）

以 HTTP 模式 + systemd 为例，让它常驻后台。

**1) 用环境变量传凭据，避免明文配置文件：**

新建 `/etc/ukmfolio-mcp.env`（权限设 `600`）：

```
UKMFOLIO_USERNAME=a207421
UKMFOLIO_PASSWORD=你的密码
```

**2) systemd 单元** `/etc/systemd/system/ukmfolio-mcp.service`：

```ini
[Unit]
Description=UKMFolio MCP Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=alanwine
WorkingDirectory=/home/alanwine/Documents/CodeProjects/Python/UKMFolioMCP
EnvironmentFile=/etc/ukmfolio-mcp.env
ExecStart=/home/alanwine/PyVenv/bin/python -m ukmfolio_mcp --http-server --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**3) 启用并启动：**

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ukmfolio-mcp
sudo systemctl status ukmfolio-mcp
```

> 对外暴露时建议放在反向代理（Nginx/Caddy）后面，加上 TLS 和访问控制——这个服务本身不做鉴权，等于谁能连上谁就能读你的 Folio。

---

## 故障排查

| 现象 | 可能原因 / 处理 |
|------|----------------|
| `--check` 报登录失败 / `incorrect username or password` | `config.json` 学号或密码错误；或环境变量覆盖了错误的值 |
| `No config.json found` | 用 `--config` 指定绝对路径，或设置 `UKMFOLIO_CONFIG` |
| 客户端里看不到工具 | 检查 `command` 是否为 venv 里的 python；`--config` 用绝对路径；看客户端的 MCP 日志 |
| 拉数据中途偶发失败 | Moodle 会话过期时会自动重登一次并重试；偶发网络问题重试即可 |
| `read_document` 文字为空 | 可能是扫描版图片 PDF（无文字层，暂不做 OCR），或不支持的文件类型 |
| 遍历「全部课程」很慢 | 文档/公告按课程逐个发请求，尽量传 `course` 缩小范围 |
| `File exceeds 25 MB cap` | 单文件超过 25MB 上限；用返回的 `file_url` 自行下载 |
| `view_image` 报 `not a decodable image` | 该 URL 不是 Pillow 能解码的位图（如 SVG），或指向的是网页而不是文件 |
| `Step 1 failed: could not reach the SSO IdP` | 站点登录入口变了；确认 `base_url` 是 `https://ukmfoliov2.ukm.my` |

---

## 项目结构与原理

```
UKMFolioMCP/
├── ukmfolio_mcp/
│   ├── __main__.py     python -m ukmfolio_mcp 入口
│   ├── config.py       读取 config.json + 环境变量覆盖
│   ├── auth.py         SAML 2.0 SSO 登录 → (session, sesskey)
│   ├── moodle.py       Moodle AJAX 调用 + HTML 抓取（课程/截止/提交状态/论坛/文档）
│   ├── documents.py    cmid → 文件链接 → 下载 → 文本提取
│   ├── images.py       从 HTML/文件列表中找出老师发的图片；为 view_image 下载 + 缩放
│   ├── client.py       UKMFolioClient：会话缓存、自动重登、整理成 AI 友好结构
│   └── server.py       FastMCP 工具定义 + 命令行（--stdio / --http-server）
├── tests/              离线 pytest 测试（不联网）
├── config.example.json 配置模板
├── config.json         真实凭据（git 忽略）
├── requirements.txt
├── pyproject.toml      可装为 ukmfolio-mcp 命令
├── README.md           英文（主）
└── README.zh-CN.md     中文（辅）
```

关键技术点：

- **认证**：UKM Folio 用的是 SAML SSO（SimpleSAMLphp IdP），**不是** Moodle 的 Web Service token。UKMFolio v2 的 `/login/index.php` 显示的是本地登录表单，因此改从 `/login/?saml=on` 发起 SAML。登录拿到 `session` + `sesskey` 后缓存复用；当 Moodle 报会话过期错误时自动重新登录并重试。
- **截止日期**：调 `core_calendar_get_action_events_by_courses`，并对同一活动的多个日历事件去重，每个活动只保留最权威的一条。
- **提交状态**：作业的 `mod_assign_*` Web Service 函数在本站被禁用，所以抓取每个作业页面（`/mod/assign/view.php?id=<cmid>`）的"Submission status"表格。作业列表用 `core_courseformat_get_state` 枚举，原始状态字符串再归一化成 `submitted` / `graded` / `is_overdue` 布尔值。
- **公告/论坛**：站点关闭了列表级论坛接口，所以先抓课程页/论坛页发现讨论，再用 `mod_forum_get_discussion_posts` 拉根帖内容。
- **文档**：用 `core_courseformat_get_state` 枚举模块、按 URL 判断类型。`resource` 会 303 跳到 `pluginfile.php` 的真实文件；`folder` 页面里每个文件一个链接。文本提取：`pypdf`（PDF）、`python-docx`（DOCX）、`python-pptx`（PPTX）、`openpyxl`（XLSX），外加纯文本和 HTML。单文件 25MB 上限，提取文本按 `max_chars` 截断（默认 5 万字符）。
- **图片**：论坛图片来自每个帖子的 `message` HTML 以及 `attachments` / `messageinlinefiles`。课程主页的章节是懒加载的，HTML 不完整，所以标签和活动卡片通过 `core_course_get_module` 逐个渲染；章节说明从 `course/section.php` 读取（只读 `hassummary` 为真的章节）；page/book/resource/folder 走文档解析流程。`view_image` 返回 MCP `ImageContent` 且不输出结构化结果，避免 base64 重复一份。

---

## 安全与限制

- **只读**：不提交作业、不发帖、不改成绩。
- SSO 证书（`CN=sso.ukm.my`）是自签且已过期但仍在使用，登录流程对此做了容错。
- 不做 OCR，扫描版图片 PDF 提不出文字。（单独的图片可以让 AI 通过 `view_image` 直接读；PDF/PPTX 里的图片不会被提取。）
- `book` 模块目前只取首页 HTML。
- 服务本身不带鉴权，HTTP 模式对外暴露务必加反向代理 + TLS + 访问控制。
- 凭据请用环境变量或保护好 `config.json`（已默认 git 忽略）。
