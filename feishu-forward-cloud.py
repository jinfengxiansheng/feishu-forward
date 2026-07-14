#!/usr/bin/env python3
"""
飞书多群消息自动转发 - 云端版（纯飞书API，不依赖 lark-cli）
源群: 先知研报 + WU2198
目标群: 学习交流群 (Webhook)

依赖: pip install requests
环境变量:
  FEISHU_APP_ID          - 飞书应用 App ID
  FEISHU_APP_SECRET      - 飞书应用 App Secret
  FEISHU_REFRESH_TOKEN   - 飞书 OAuth 2.0 refresh_token（首次需手动获取）

用法:
  python feishu-forward-cloud.py --once      # 单次运行
  python feishu-forward-cloud.py             # 持续轮询
  python feishu-forward-cloud.py --auth      # 首次授权（获取 refresh_token）
"""

import json
import os
import re
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "feishu-forward-state.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "feishu-token.json")

# ===== Token 管理 =====
def load_token():
    """尝试从环境变量或文件加载 token"""
    # 优先环境变量
    refresh_token = os.environ.get("FEISHU_REFRESH_TOKEN", "")
    if not refresh_token and os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            refresh_token = data.get("refresh_token", "")
    return refresh_token

def save_token(refresh_token, user_access_token=""):
    """保存 token 到文件"""
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "refresh_token": refresh_token,
            "user_access_token": user_access_token,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }, f, ensure_ascii=False, indent=2)

def get_user_access_token():
    """获取有效的 user_access_token"""
    refresh_token = load_token()
    if not refresh_token:
        print("❌ 未找到 refresh_token，请先运行: python feishu-forward-cloud.py --auth")
        sys.exit(1)

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")

    url = "https://open.feishu.cn/open-apis/authen/v1/refresh_access_token"
    headers = {"Content-Type": "application/json"}
    data = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "app_id": app_id,
        "app_secret": app_secret,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"❌ Token 刷新失败: {e}")
        sys.exit(1)

    code = result.get("code", -1)
    if code != 0:
        print(f"❌ Token 刷新错误: {result.get('msg', 'unknown')}")
        print(f"   可能需要重新授权: python feishu-forward-cloud.py --auth")
        sys.exit(1)

    data_obj = result.get("data", {})
    new_refresh = data_obj.get("refresh_token", "")
    access_token = data_obj.get("access_token", "")

    if new_refresh:
        save_token(new_refresh, access_token)

    return access_token

# ===== 首次授权 =====
def run_auth_flow():
    """
    引导用户完成飞书 OAuth 2.0 授权流程。
    注意：这一步需要用到飞书开放平台的应用配置。
    """
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")

    if not app_id or not app_secret:
        print("=" * 60)
        print("  首次授权需要以下信息：")
        print("=" * 60)
        print()
        print("1. 去 https://open.feishu.cn/ 创建自建应用")
        print("2. 在「凭证与基础信息」页面获取 App ID 和 App Secret")
        print("3. 在「安全设置」→「重定向URL」添加: https://open.feishu.cn/")
        print("4. 在「权限管理」中开通以下权限：")
        print("   - im:message:readonly (获取消息)")
        print("   - im:message.group_msg:get_as_user (以用户身份获取群聊消息)")
        print()
        print("5. 设置环境变量后重新运行:")
        print("   set FEISHU_APP_ID=cli_xxxxxxxx")
        print("   set FEISHU_APP_SECRET=xxxxxxxx")
        print("   python feishu-forward-cloud.py --auth")
        print()
        print("=" * 60)
        return

    # 构造授权 URL
    redirect_uri = urllib.parse.quote("https://open.feishu.cn/", safe="")
    auth_url = (
        f"https://open.feishu.cn/open-apis/authen/v1/authorize?"
        f"app_id={app_id}&redirect_uri={redirect_uri}"
    )

    print("=" * 60)
    print("  请用浏览器打开以下链接并授权：")
    print("=" * 60)
    print(f"\n  {auth_url}\n")
    print("授权后，浏览器地址栏会包含 code=xxx")
    print("请将完整的 code 参数值粘贴到这里：")
    print()
    code = input("code: ").strip()

    if not code:
        print("❌ 未输入授权码，已取消")
        return

    # 用 code 换 token
    url = "https://open.feishu.cn/open-apis/authen/v1/access_token"
    headers = {"Content-Type": "application/json"}
    data = json.dumps({
        "grant_type": "authorization_code",
        "code": code,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"❌ 换取 token 失败: {e}")
        return

    code_ret = result.get("code", -1)
    if code_ret != 0:
        print(f"❌ 换取 token 失败: {result.get('msg', 'unknown')}")
        return

    data_obj = result.get("data", {})
    refresh_token = data_obj.get("refresh_token", "")
    access_token = data_obj.get("access_token", "")

    if refresh_token:
        save_token(refresh_token, access_token)
        print(f"✅ 授权成功！refresh_token 已保存到 {TOKEN_FILE}")
        print(f"   access_token 有效期: {data_obj.get('expires_in', '?')} 秒")
        print(f"   refresh_token 有效期: {data_obj.get('refresh_expires_in', '?')} 秒")
        print()
        print("现在可以运行: python feishu-forward-cloud.py --once")
    else:
        print("❌ 未获取到 refresh_token")

# ===== 状态管理 =====
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def get_chat_state(state, chat_id):
    return state.get(chat_id, {"last_message_id": "", "last_message_position": 0})

def set_chat_state(state, chat_id, last_id, last_pos):
    state[chat_id] = {"last_message_id": last_id, "last_message_position": last_pos}

# ===== 数据获取（纯飞书API） =====
def fetch_messages(chat_id, user_access_token):
    """通过飞书 API 获取群消息列表"""
    url = (
        f"https://open.feishu.cn/open-apis/im/v1/messages?"
        f"container_id_type=chat&container_id={chat_id}"
        f"&page_size={PAGE_SIZE}&sort_type=ByCreateTimeDesc"
    )
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {user_access_token}",
        "Content-Type": "application/json; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code", -1) != 0:
                print(f"    ❌ API 错误: {data.get('msg', 'unknown')}")
                return []
            items = data.get("data", {}).get("items", [])
            # 飞书 API 返回的字段名可能是 message_position 或 meta_position
            # 部分版本用 create_time 排序
            for item in items:
                if "body" in item and "content" in item["body"]:
                    item["content"] = item["body"]["content"]
                    item["msg_type"] = item.get("msg_type", "text")
            return items
    except Exception as e:
        print(f"    ❌ API 请求失败: {e}")
        return []

# ===== 消息转换 =====
IMAGE_KEY_RE = re.compile(r"img_v[0-9a-zA-Z_\-]+")

def build_webhook_payloads(msg, chat_label):
    """将消息转为 webhook payload 列表"""
    msg_type = msg.get("msg_type", "text")
    content = msg.get("content", "")
    sender_type = msg.get("sender", {}).get("sender_type", "")
    header = f"【{chat_label}·{'机器人' if sender_type == 'app' else '用户'}】"

    payloads = []

    if msg_type == "text":
        payloads.append(("text", {"text": f"{header}\n{content}"}))

    elif msg_type == "post":
        if content.startswith("{"):
            try:
                post_data = json.loads(content)
                text_lines, image_keys = _parse_post_for_webhook(post_data, header)
                text = "\n".join(text_lines) if text_lines else ""
                if text:
                    payloads.append(("text", {"text": text}))
                for img_key in image_keys:
                    payloads.append(("image", {"image_key": img_key}))
                if not payloads:
                    payloads.append(("text", {"text": f"{header}\n[富文本消息]"}))
                return payloads
            except json.JSONDecodeError:
                pass
        text = content
        image_keys = IMAGE_KEY_RE.findall(content)
        if image_keys:
            text = IMAGE_KEY_RE.sub("", content).replace("![Image]()", "").strip()
            if text:
                payloads.append(("text", {"text": f"{header}\n{text}"}))
            for img_key in image_keys:
                payloads.append(("image", {"image_key": img_key}))
        else:
            payloads.append(("text", {"text": f"{header}\n{text}" if text else f"{header}\n[富文本消息]"}))

    elif msg_type in ("image", "media"):
        try:
            data = json.loads(content) if content.startswith("{") else {}
            img_key = data.get("image_key", "")
        except json.JSONDecodeError:
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

def _parse_post_for_webhook(post_data, header):
    lines = []
    image_keys = []
    title = post_data.get("title", "")
    if title:
        lines.append(f"📌 {title}")
    content_blocks = post_data.get("content", [])
    if isinstance(content_blocks, list):
        for paragraph in content_blocks:
            if isinstance(paragraph, list):
                line_parts = []
                for elem in paragraph:
                    if not isinstance(elem, dict):
                        continue
                    tag = elem.get("tag", "text")
                    if tag == "text":
                        line_parts.append(elem.get("text", ""))
                    elif tag == "a":
                        line_parts.append(elem.get("text", "[链接]"))
                    elif tag == "at":
                        uname = elem.get("user_name", "") or elem.get("user_id", "")
                        line_parts.append(f"@{uname}")
                    elif tag == "img":
                        ik = elem.get("image_key", "")
                        if ik:
                            image_keys.append(ik)
                    elif tag == "emotion":
                        line_parts.append("[表情]")
                    else:
                        line_parts.append(f"[{tag}]")
                if line_parts:
                    lines.append("".join(line_parts))
    if not lines:
        lines.append(header)
    return lines, image_keys

# ===== Webhook 发送 =====
def send_via_webhook(msg_type, content):
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

# ===== 主逻辑 =====
def process_chat(chat_cfg, state, user_access_token):
    chat_id = chat_cfg["chat_id"]
    chat_label = chat_cfg["label"]
    cs = get_chat_state(state, chat_id)
    msgs = fetch_messages(chat_id, user_access_token)

    if not msgs:
        print(f"    无消息或拉取失败")
        return 0

    new_msgs = []
    for m in msgs:
        pos = int(m.get("message_position", 0))
        if pos > cs["last_message_position"]:
            new_msgs.append(m)

    if not new_msgs:
        max_pos = sorted([int(m.get("message_position", 0)) for m in msgs], reverse=True)[0] if msgs else 0
        print(f"    无新消息 (最大 position: {max_pos})")
        return 0

    print(f"    {len(new_msgs)} 条新消息")
    new_msgs.sort(key=lambda m: m.get("create_time", ""))

    count = 0
    for m in new_msgs:
        msg_id = m.get("message_id", "?")[:20]
        payloads = build_webhook_payloads(m, chat_label)
        for ptype, pcontent in payloads:
            preview = json.dumps(pcontent, ensure_ascii=False)[:40]
            print(f"      [{ptype}] {preview} → ", end="", flush=True)
            if send_via_webhook(ptype, pcontent):
                print("✅")
                count += 1
            else:
                print("❌")
            time.sleep(0.5)
        pos = int(m.get("message_position", 0))
        if pos > cs["last_message_position"]:
            cs["last_message_position"] = pos
            cs["last_message_id"] = m.get("message_id", "")

    set_chat_state(state, chat_id, cs["last_message_id"], cs["last_message_position"])
    return count

def run_once():
    user_access_token = get_user_access_token()
    state = load_state()
    total = 0
    for chat_cfg in SOURCE_CHATS:
        cs = get_chat_state(state, chat_cfg["chat_id"])
        print(f"  [{chat_cfg['name']}] last_pos={cs['last_message_position']}")
        total += process_chat(chat_cfg, state, user_access_token)
    save_state(state)
    return total

def run_loop():
    print(f"🚀 飞书多群消息转发已启动 (云端版)")
    for c in SOURCE_CHATS:
        print(f"   源: {c['name']} ({c['chat_id']})")
    print(f"   目标: 学习交流群 (Webhook)")
    print(f"   间隔: {POLL_INTERVAL}秒\n")

    while True:
        try:
            now = time.strftime("%H:%M:%S")
            print(f"[{now}] 检查所有源群...")
            run_once()
        except KeyboardInterrupt:
            print("\n🛑 已停止")
            break
        except Exception as e:
            print(f"  ⚠️ 轮询异常: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    if "--auth" in sys.argv:
        run_auth_flow()
    elif "--once" in sys.argv:
        print("🔍 单次测试模式\n")
        run_once()
    else:
        run_loop()
