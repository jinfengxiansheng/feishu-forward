#!/usr/bin/env python3
"""
飞书多群消息自动转发 - Railway 云端版
使用 lark-cli 读取消息（借内置应用权限）+ Webhook 发送消息

依赖: Python 3 stdlib + lark-cli (npm)
环境: Docker (Python + Node.js + lark-cli)

Auth: 首次部署使用设备授权码流程（无需浏览器）
  1. 脚本启动 → 检测未授权 → 获取设备码
  2. 用户在手机上打开 URL 输入设备码授权
  3. 脚本自动检测完成，进入转发循环

用法:
  python feishu-forward-railway.py
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

# ===== 配置 =====
SOURCE_CHATS = [
    {"chat_id": "oc_ac240bdc00dddadf3258c5a3cf1f70ae", "name": "先知研报", "label": "先知研报"},
    {"chat_id": "oc_1c2978ebfa0b79361cdb5c98de9c6a49", "name": "WU2198", "label": "WU2198"},
]
WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/983f893a-5a99-42cb-b3a8-a93b49ae5c07"
POLL_INTERVAL = 30
PAGE_SIZE = 5

# 数据目录（Docker 中使用 /data 并挂载 Volume 持久化）
DATA_DIR = os.environ.get("DATA_DIR", "/data")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(DATA_DIR, "feishu-forward-state.json")

# lark-cli 命令（Docker 中全局安装后在 PATH 中）
LARK_CLI = os.environ.get("LARK_CLI_PATH", "lark-cli")


# ===== 工具函数 =====
def _run_lark(cmd_args, timeout=30):
    """运行 lark-cli 命令并返回 JSON"""
    full_cmd = [LARK_CLI] + cmd_args
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    try:
        proc = subprocess.run(
            full_cmd, capture_output=True, text=True,
            env=env, timeout=timeout
        )
        if proc.returncode != 0:
            err = proc.stderr.strip() or "unknown error"
            return {"ok": False, "error": err}
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"Invalid JSON: {proc.stdout[:200]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except FileNotFoundError:
        return {"ok": False, "error": f"lark-cli not found: {LARK_CLI}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===== 鉴权管理 =====
def check_auth():
    """检查 lark-cli 是否已授权"""
    result = _run_lark(["auth", "status", "--json"], timeout=10)
    if result.get("ok"):
        identities = result.get("identities", {})
        user = identities.get("user", {})
        if user.get("status") == "ready":
            expires = user.get("expiresAt", "unknown")
            print(f"✅ lark-cli 已授权 (过期: {expires})")
            return True
    print("⚠️ lark-cli 未授权")
    return False


def run_device_auth():
    """执行设备授权码流程"""
    print("\n" + "=" * 60)
    print("  飞书设备授权")
    print("=" * 60)

    # Step 1: 发起设备授权
    print("\n[1/3] 获取设备授权码...")
    result = _run_lark([
        "auth", "login", "--no-wait", "--json", "--domain", "im"
    ], timeout=15)

    if not result.get("ok"):
        print(f"❌ 获取失败: {result.get('error', 'unknown')}")
        return False

    device_code = result.get("device_code", "")
    verification_url = result.get("verification_url", "")

    # user_code 嵌入在 verification_url 的查询参数中
    user_code = ""
    if "user_code=" in verification_url:
        import urllib.parse as _up
        qs = _up.parse_qs(_up.urlparse(verification_url).query)
        user_code = qs.get("user_code", [""])[0]

    if not device_code:
        print(f"❌ 未获取到 device_code: {json.dumps(result, ensure_ascii=False)}")
        return False

    # Step 2: 显示授权信息
    print(f"\n[2/3] 请在手机/电脑上完成授权：")
    print(f"       👉 打开: {verification_url}")
    if user_code:
        print(f"       📱 或者手动输入验证码: {user_code}")
    print(f"\n       ⏳ 等待授权完成 (最多 10 分钟)...\n")

    # Step 3: 轮询完成
    for i in range(120):  # 每 5 秒一次，共 10 分钟
        time.sleep(5)
        poll = _run_lark([
            "auth", "login", "--device-code", device_code, "--json"
        ], timeout=15)

        if poll.get("ok"):
            print(f"\n[3/3] ✅ 授权成功！")
            return True

        error = poll.get("error", "").lower()
        if "authorization_pending" in error or "pending" in error:
            if i % 6 == 0:  # 每 30 秒提示一次
                print(f"       ... 等待中 ({i * 5}s) ...")
        elif "expired" in error or "timeout" in error:
            print(f"\n❌ 授权码已过期，请重新部署")
            return False
        else:
            print(f"\n❌ 授权异常: {error}")
            return False

    print(f"\n❌ 授权超时 (10 分钟)")
    return False


def ensure_auth():
    """确保 lark-cli 已授权，否则执行设备授权"""
    if check_auth():
        return True

    print("需要完成飞书授权...")
    for attempt in range(3):
        print(f"\n--- 第 {attempt + 1}/3 次授权尝试 ---")
        if run_device_auth():
            return True
        time.sleep(5)

    print("\n❌ 授权失败，请检查后重新部署")
    return False


# ===== 状态管理 =====
def load_state():
    """加载转发状态"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ 状态文件损坏: {e}")
    return {}


def save_state(state):
    """保存转发状态"""
    state["_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_chat_state(state, chat_id):
    return state.get(chat_id, {"last_message_position": 0})


def set_chat_state(state, chat_id, last_pos):
    state[chat_id] = {"last_message_position": last_pos}


# ===== 消息读取（lark-cli） =====
def fetch_messages(chat_id):
    """通过 lark-cli 读取群消息"""
    result = _run_lark([
        "im", "+chat-messages-list",
        "--as", "user",
        "--chat-id", chat_id,
        "--page-size", str(PAGE_SIZE),
        "--sort", "desc",
        "--json"
    ], timeout=20)

    if not result.get("ok"):
        err = result.get("error", "")
        print(f"    ❌ lark-cli 错误: {err}")
        return []

    data = result.get("data", {})
    msgs = data.get("messages", [])

    # 标准化字段
    for m in msgs:
        m["message_position"] = int(m.get("message_position", 0))
        m["content"] = m.get("content", "")
        m["msg_type"] = m.get("msg_type", "text")

    return msgs


# ===== 消息转换与发送 =====
IMAGE_KEY_RE = re.compile(r"img_[0-9a-zA-Z_\-]+")
POST_IMAGE_RE = re.compile(r"!\[Image\]\((img_[^)]+)\)")


def build_webhook_payloads(msg, chat_label):
    """将消息转为 webhook payload 列表"""
    msg_type = msg.get("msg_type", "text")
    content = msg.get("content", "")
    sender_name = msg.get("sender", {}).get("name", "")
    header = f"【{chat_label}·{sender_name}】" if sender_name else f"【{chat_label}】"

    payloads = []

    if msg_type == "text":
        payloads.append(("text", {"text": f"{header}\n{content}"}))

    elif msg_type == "post":
        # 解析 post 消息中的图片和文本
        image_matches = POST_IMAGE_RE.findall(content)
        if image_matches:
            # 去掉图片标记，保留文本
            text = POST_IMAGE_RE.sub("", content).strip()
            if text and text != content:
                payloads.append(("text", {"text": f"{header}\n{text}"}))
            for img_key in image_matches:
                payloads.append(("image", {"image_key": img_key}))
        else:
            payloads.append(("text", {"text": f"{header}\n{content}"}))

    elif msg_type in ("image", "media"):
        # 尝试从 content JSON 中提取 image_key
        try:
            data = json.loads(content) if content.startswith("{") else {}
            img_key = data.get("image_key", "")
        except (json.JSONDecodeError, AttributeError):
            img_key = ""
        if not img_key:
            m = IMAGE_KEY_RE.search(content)
            img_key = m.group(0) if m else ""
        if img_key:
            payloads.append(("image", {"image_key": img_key}))
        else:
            payloads.append(("text", {"text": f"{header}\n[图片消息]"}))

    elif msg_type == "file":
        payloads.append(("text", {"text": f"{header}\n[文件消息]"}))

    elif msg_type == "sticker":
        payloads.append(("text", {"text": f"{header}\n[表情消息]"}))

    else:
        payloads.append(("text", {"text": f"{header}\n[{msg_type}类型消息]"}))

    return payloads


def send_via_webhook(msg_type, content):
    """通过 Webhook 发送消息"""
    payload = json.dumps({
        "msg_type": msg_type,
        "content": content
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            code = result.get("code") or result.get("StatusCode")
            return code in (0, "0")
    except Exception as e:
        print(f"    ❌ Webhook 失败: {e}")
        return False


# ===== 消息处理 =====
def process_chat(chat_cfg, state):
    """处理单个源群的消息"""
    chat_id = chat_cfg["chat_id"]
    chat_label = chat_cfg["label"]
    cs = get_chat_state(state, chat_id)

    msgs = fetch_messages(chat_id)
    if not msgs:
        print(f"    无消息或拉取失败")
        return 0

    new_msgs = []
    for m in msgs:
        pos = m["message_position"]
        if pos > cs["last_message_position"]:
            new_msgs.append(m)

    if not new_msgs:
        print(f"    无新消息 (最大 position: {new_msgs[0] if new_msgs else msgs[0]['message_position']})")
        return 0

    print(f"    {len(new_msgs)} 条新消息")
    new_msgs.sort(key=lambda m: m.get("create_time", ""))

    count = 0
    max_pos = cs["last_message_position"]

    for m in new_msgs:
        payloads = build_webhook_payloads(m, chat_label)
        for ptype, pcontent in payloads:
            preview = json.dumps(pcontent, ensure_ascii=False)[:50]
            print(f"      [{ptype}] {preview} → ", end="", flush=True)
            if send_via_webhook(ptype, pcontent):
                print("✅")
                count += 1
            else:
                print("❌")
            time.sleep(0.5)

        pos = m["message_position"]
        if pos > max_pos:
            max_pos = pos

    set_chat_state(state, chat_id, max_pos)
    return count


def run_once():
    """单次检查所有源群"""
    state = load_state()
    total = 0

    for chat_cfg in SOURCE_CHATS:
        cs = get_chat_state(state, chat_cfg["chat_id"])
        print(f"  [{chat_cfg['name']}] last_pos={cs['last_message_position']}")
        total += process_chat(chat_cfg, state)

    if total > 0:
        save_state(state)
    return total


# ===== 启动补发 =====
def run_catchup():
    """启动时补发遗漏消息（翻页模式）"""
    print("\n[启动] 检查遗漏消息...")
    state = load_state()

    for chat_cfg in SOURCE_CHATS:
        chat_id = chat_cfg["chat_id"]
        cs = get_chat_state(state, chat_id)
        print(f"  {chat_cfg['name']} (last_pos={cs['last_message_position']}): ", end="", flush=True)

        # 翻页拉取，直到找到已处理的 position
        page_token = None
        catchup_msgs = []
        found = False

        for _ in range(20):  # 最多翻 20 页
            cmd = [
                "im", "+chat-messages-list",
                "--as", "user",
                "--chat-id", chat_id,
                "--page-size", "10",
                "--sort", "desc",
                "--json"
            ]
            if page_token:
                cmd += ["--page-token", page_token]

            result = _run_lark(cmd, timeout=20)
            if not result.get("ok"):
                print(f"拉取失败")
                break

            data = result.get("data", {})
            msgs = data.get("messages", [])
            has_more = data.get("has_more", False)
            page_token = data.get("page_token", "")

            for m in msgs:
                pos = int(m.get("message_position", 0))
                if pos > cs["last_message_position"]:
                    m["message_position"] = pos
                    catchup_msgs.append(m)
                else:
                    found = True
                    break

            if found or not has_more:
                break

        if not catchup_msgs:
            print("无遗漏消息")
            continue

        print(f"{len(catchup_msgs)} 条遗漏")

        # 按时间正序发送
        catchup_msgs.sort(key=lambda m: m.get("create_time", ""))
        count = 0
        max_pos = cs["last_message_position"]

        for m in catchup_msgs:
            payloads = build_webhook_payloads(m, chat_cfg["label"])
            for ptype, pcontent in payloads:
                if send_via_webhook(ptype, pcontent):
                    count += 1
                time.sleep(0.3)

            pos = m["message_position"]
            if pos > max_pos:
                max_pos = pos

        if max_pos > cs["last_message_position"]:
            set_chat_state(state, chat_id, max_pos)
            save_state(state)

        print(f"    → 补发了 {count} 条")

    print("[启动] 补发完成\n")


# ===== 主循环 =====
def main():
    print("🚀 飞书多群消息转发 - Railway 云端版")
    print(f"   源群:")
    for c in SOURCE_CHATS:
        print(f"     - {c['name']} ({c['chat_id']})")
    print(f"   目标: Webhook")
    print(f"   间隔: {POLL_INTERVAL}s\n")

    # 鉴权检查
    if not ensure_auth():
        print("❌ 鉴权失败，退出。Railway 会自动重启并重试。")
        sys.exit(1)

    # 启动时补发遗漏消息
    run_catchup()

    # 进入轮询循环
    print("🔄 进入持续监控...\n")
    error_streak = 0

    while True:
        try:
            now = time.strftime("%H:%M:%S")
            print(f"[{now}] 检查所有源群...")
            count = run_once()
            if count > 0:
                error_streak = 0
            else:
                error_streak = 0  # 0 条消息不是错误
        except KeyboardInterrupt:
            print("\n🛑 已停止")
            break
        except Exception as e:
            error_streak += 1
            print(f"  ⚠️ 轮询异常 (x{error_streak}): {e}")
            if error_streak > 10:
                print("❌ 连续异常过多，退出。Railway 会自动重启。")
                sys.exit(1)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
