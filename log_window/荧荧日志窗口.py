# -*- coding: utf-8 -*-
"""
荧荧的桌面操作台 v8.5 (右侧 3/4 屏幕三栏 + 透明背景 + 聊天输入)
- 窗口只占屏幕右侧 3/4 (左侧 1/4 留空给桌面图标), 内部三栏等宽
- 栏0: 荧荧小天地 (时钟/语录/好玩内容, 荧荧自己玩) + 消息记录 + 输入框 (可对话)
- 栏1: 游戏状态 + 零食余额 + 折线图
- 栏2: 完整活动日志
"""
import os
import re
import json
import hashlib
import sqlite3
import time
import subprocess
import threading
import tkinter as tk
from datetime import datetime, timedelta
from io import BytesIO
import urllib.request as urllib_request
from PIL import Image, ImageDraw, ImageTk

LOG_DIR = os.path.expandvars(r"%LOCALAPPDATA%\hermes\logs")
ENV_FILE = os.path.expandvars(r"%LOCALAPPDATA%\hermes\.env")
STATE_DB = os.path.expandvars(r"%LOCALAPPDATA%\hermes\state.db")
BALANCE_HISTORY = os.path.expandvars(r"%LOCALAPPDATA%\hermes\scripts\balance_history.json")
GAME_HISTORY = os.path.expandvars(r"%LOCALAPPDATA%\hermes\scripts\game_history.json")
ADB_PATH = r"F:\leidian\LDPlayer14\adb.exe"
REFRESH_MS = 3000
SCREEN_W, SCREEN_H = 2560, 1440
LEFT_GAP = SCREEN_W // 4  # 屏幕左侧 1/4 留给桌面图标, 窗口占右侧 3/4
TRANSPARENT = "#010203"  # 透明色 (桌面图标透过显示)
CHAT_IMG_DIR = r"E:\yingying-home\chat_images"  # 主人发来的图片存放目录
IMG_CACHE_DIR = os.path.expandvars(r"%LOCALAPPDATA%\hermes\image_cache")  # URL 图片缓存
IMG_MAX_W = 220  # 聊天区缩略图最大宽度 (px)


def _parse_img_marks(text):
    """提取消息里的图片标记 → (清洗后的文本, [图片路径/URL...])

    支持三种标记:
    - [图片:路径或URL] / [img:...] / [image:...]   (荧荧自定义约定)
    - MEDIA: 路径或URL                              (Hermes 附件语法)
    - 裸图片 URL (png/jpg/gif/webp/bmp 结尾)
    """
    marks = []

    def grab(m):
        p = m.group(1).strip().strip('"').strip("'")
        if p:
            marks.append(p)
        return "🖼️"

    t = re.sub(r"\[(?:图片|img|image)\s*[:：]\s*([^\]]+)\]", grab, text)
    t = re.sub(r"MEDIA\s*[:：]\s*(\S+)", grab, t, flags=re.I)

    def grab_url(m):
        u = m.group(0)
        marks.append(u)
        return "🖼️"

    t = re.sub(r"https?://\S+?\.(?:png|jpe?g|gif|webp|bmp)(?:\?\S*)?", grab_url, t, flags=re.I)
    return t, marks


def _load_chat_image(src, max_w=IMG_MAX_W):
    """从本地路径或 URL 加载图片并缩放到 max_w 宽; 失败返回 None"""
    try:
        local = src
        if src.startswith(("http://", "https://")):
            name = hashlib.md5(src.encode("utf-8")).hexdigest()[:16] + ".img"
            cache = os.path.join(IMG_CACHE_DIR, name)
            if not os.path.exists(cache):
                os.makedirs(IMG_CACHE_DIR, exist_ok=True)
                req = urllib_request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
                with urllib_request.urlopen(req, timeout=10) as resp:
                    data = resp.read()
                with open(cache, "wb") as f:
                    f.write(data)
            local = cache
        if not os.path.exists(local):
            return None
        img = Image.open(local).convert("RGBA")
        w, h = img.size
        if w > max_w:
            img = img.resize((max_w, max(1, int(h * max_w / w))), Image.LANCZOS)
        return img
    except Exception:
        return None


def _parse_elapsed(s):
    """解析 ADB ps ELAPSED 列 ([[DD-]HH:]MM:SS) 为秒数; 失败返回 None"""
    s = (s or "").strip()
    try:
        if "-" in s:
            d, rest = s.split("-", 1)
            return int(d) * 86400 + _parse_elapsed(rest)
        parts = [int(x) for x in s.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except Exception:
        pass
    return None


def _fmt_duration(secs):
    """秒数 → 友好时长字符串"""
    if secs is None:
        return ""
    if secs < 60:
        return f"{secs}秒"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}分钟"
    return f"{mins // 60}h{mins % 60}m"

# ---------- 余额查询 ----------
def query_live_balance():
    import json as _json
    import urllib.request
    try:
        key = None
        with open(ENV_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if not key:
            return None
        req = urllib.request.Request("https://api.deepseek.com/user/balance")
        req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read().decode())
        for info in data.get("balance_infos", []):
            if info.get("currency") == "CNY":
                return float(info.get("total_balance", 0))
        return None
    except Exception:
        return None


# ---------- 余额历史 ----------
def record_balance(balance):
    import json
    try:
        hist = []
        if os.path.exists(BALANCE_HISTORY):
            with open(BALANCE_HISTORY, "r", encoding="utf-8") as f:
                hist = json.load(f)
        now = datetime.now()
        cutoff = now.timestamp() - 72 * 3600
        hist = [h for h in hist if h["t"] > cutoff]
        if hist and now.timestamp() - hist[-1]["t"] < 300:
            hist[-1]["b"] = balance
        else:
            hist.append({"t": now.timestamp(), "b": balance})
        with open(BALANCE_HISTORY, "w", encoding="utf-8") as f:
            json.dump(hist, f)
    except Exception:
        pass


def load_balance_history():
    import json
    try:
        if os.path.exists(BALANCE_HISTORY):
            with open(BALANCE_HISTORY, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


# ---------- 聊天记录 ----------
def _clean_msg(m, webhook=False):
    """清洗单条消息; 返回 None 表示跳过"""
    content = (m["content"] or "").strip()
    if not content or not m["timestamp"]:
        return None
    # 跳过工具输出/中间过程消息
    if m["role"] == "tool":
        return None
    if any(skip in content for skip in (
            "<untrusted_tool_result", "<tool_result", "tool_use",
            "Traceback", "untrusted_tool_result")):
        return None
    # JSON 工具输出 ({"output": ...}) 也跳过
    if content.startswith("{\"output\""):
        return None
    # webhook 模板前缀: 提取真实消息内容 (而不是丢弃)
    if webhook and m["role"] == "user" and content.startswith("主人从桌面操作台发来消息"):
        content = content.replace("主人从桌面操作台发来消息", "", 1).strip()
        content = content.lstrip(":：-–—").strip()
        # 只保留第一行 (消息本体) + 含图片标记的行 (去掉换行后的任何提示词/模板尾巴)
        lines = content.split("\n")
        kept = [lines[0]]
        for ln in lines[1:]:
            if "[图片" in ln or "MEDIA" in ln.upper() or re.search(r"https?://\S+?\.(?:png|jpe?g|gif|webp|bmp)", ln, re.I):
                kept.append(ln)
        content = "\n".join(kept).strip()
        if not content:
            return None
    ts = m["timestamp"]
    # 兼容数字 (Unix 秒/毫秒) 和字符串时间戳
    try:
        if isinstance(ts, (int, float)):
            if ts > 1e12:  # 毫秒
                ts = ts / 1000.0
        else:
            ts = float(ts)
    except Exception:
        return None
    dt = datetime.fromtimestamp(ts)
    now = datetime.now()
    # 今天的消息只显示 HH:MM; 更早的带日期, 避免看不出是哪天的
    t = dt.strftime("%H:%M") if dt.date() == now.date() else dt.strftime("%m-%d %H:%M")
    return {"role": m["role"], "content": content, "time": t, "ts": ts}


def read_chat_history(limit=60):
    """读取 QQ 会话 + 近期 webhook 会话, 合并后按真实时间戳排序并去重 (user/assistant 都去重)"""
    try:
        conn = sqlite3.connect(STATE_DB)
        conn.row_factory = sqlite3.Row
        merged = []
        # 1. QQ 会话 (完整主对话; 桌面消息也会同步进来)
        sess = conn.execute(
            "SELECT id FROM sessions WHERE source LIKE '%qq%' ORDER BY last_activity_at DESC LIMIT 1"
        ).fetchone()
        if sess:
            msgs = conn.execute(
                'SELECT role, content, timestamp FROM messages '
                'WHERE session_id=? AND role IN ("user","assistant") AND content IS NOT NULL '
                'ORDER BY id DESC LIMIT ?', (sess["id"], limit * 4)
            ).fetchall()
            for m in reversed(msgs):
                item = _clean_msg(m)
                if item:
                    merged.append(item)
        # 2. 48小时内活跃的 webhook 会话 (桌面端对话; 回复只在 webhook 会话里!
        #    旧版 30 分钟窗口 → 超过 30 分钟的桌面对话回复全部从面板消失, 只剩 QQ 里的提问, 2026-08-25 修复)
        cutoff = datetime.now().timestamp() - 48 * 3600
        web_ids = [w["id"] for w in conn.execute(
                "SELECT id FROM sessions WHERE source='webhook' AND last_activity_at > ? "
                "ORDER BY last_activity_at DESC LIMIT 30", (cutoff,)).fetchall()]
        if web_ids:
            ph = ",".join("?" * len(web_ids))
            msgs = conn.execute(
                f'SELECT role, content, timestamp FROM messages '
                f'WHERE session_id IN ({ph}) AND role IN ("user","assistant") AND content IS NOT NULL '
                f'ORDER BY id DESC LIMIT ?', (*web_ids, limit * 4)
            ).fetchall()
            for m in reversed(msgs):
                item = _clean_msg(m, webhook=True)
                if item:
                    merged.append(item)
        conn.close()
        # 3. 只保留最近 48 小时内的消息 (旧消息不占名额, 根治"卡老消息")
        now_ts = datetime.now().timestamp()
        merged = [m for m in merged if now_ts - m["ts"] < 48 * 3600]
        # 4. 按真实时间戳排序 (跨会话交叉时顺序才不乱)
        merged.sort(key=lambda x: x["ts"])
        # 5. 去重: 桌面发送的消息会同时写入 QQ 会话和 webhook 会话;
        #    同角色同内容且时间接近 (120秒内) 只留一条 — user 和 assistant 都去重
        deduped, seen = [], {}
        for m in merged:
            key = (m["role"], m["content"])
            last = seen.get(key)
            if last is not None and m["ts"] - last < 120:
                continue
            seen[key] = m["ts"]
            deduped.append(m)
        return deduped[-limit:]
    except Exception:
        return []


# ---------- 回答同步回 QQ 会话 ----------
# 桌面端的提问会由 send_chat 同步进 QQ 会话, 但荧荧的回复只存在 webhook 会话里;
# 这里增量把 assistant 回复写回 QQ 会话, 让 QQ 端也能看到完整对话 (2026-08-25 主人要求)
_qqsync_last_id = 0    # 已同步的最大 webhook assistant 消息 id
_qqsync_last_ts = 0.0  # 上次执行时间 (限频)

def _strip_thinking(content):
    """剥离 deepseek 模型残留在 content 里的思考前缀。

    Hermes 存 assistant 消息时, 带工具调用的轮次 content=思考文本;
    最终回复 (finish_reason='stop') 的 content 大部分是干净回复, 但
    偶尔模型把一段简短思考也写进来, 以独立 '---' 行与回复分隔 →
    取第一个 '\n\n---\n\n' 之后的内容 (回复正文里的分隔线不受影响)。
    """
    parts = re.split(r"\n\s*---\s*\n", content, maxsplit=1)
    if len(parts) == 2:
        return parts[1].strip() or content
    return content

def sync_assistant_replies_to_qq():
    """把 48h 内 webhook 会话里荧荧的最终回复, 增量写入 QQ 会话。

    只同步 finish_reason='stop' 且无工具调用的 assistant 消息 (真回复;
    工具轮次的 content 是思考文本/工具参数, 不写进 QQ 会话)。
    用消息 id 做水位增量; 进程重启后水位归零会重扫, 但 INSERT 前按
    (content, 时间差<120s) 查重, 不会重复插入。
    """
    global _qqsync_last_id, _qqsync_last_ts
    now = time.time()
    if now - _qqsync_last_ts < 10:  # 限频: 至少隔 10 秒跑一次
        return
    _qqsync_last_ts = now
    conn = None
    try:
        conn = sqlite3.connect(STATE_DB, timeout=10)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # 1. 找 QQ 会话 (与 send_chat 的 sync_to_qq 同一套识别)
        qq = cur.execute(
            "SELECT id FROM sessions WHERE source LIKE '%qq%' "
            "ORDER BY last_activity_at DESC LIMIT 1").fetchone()
        if not qq:
            return
        # 2. 48h 内活跃的 webhook 会话 (与 read_chat_history 一致)
        cutoff = datetime.now().timestamp() - 48 * 3600
        web_ids = [w["id"] for w in cur.execute(
            "SELECT id FROM sessions WHERE source='webhook' AND last_activity_at > ? "
            "ORDER BY last_activity_at DESC LIMIT 30", (cutoff,)).fetchall()]
        if not web_ids:
            return
        # 3. 增量拉取: id 大于上次水位 的最终回复 (stop + 无工具调用)
        ph = ",".join("?" * len(web_ids))
        rows = cur.execute(
            f"SELECT id, content, timestamp FROM messages "
            f"WHERE session_id IN ({ph}) AND role='assistant' AND content IS NOT NULL "
            f"AND finish_reason='stop' AND (tool_calls IS NULL OR tool_calls='') "
            f"AND id > ? ORDER BY id", (*web_ids, _qqsync_last_id)).fetchall()
        if not rows:
            return
        # 4. 查 QQ 会话已有 assistant 内容, 防重启重扫重复插入
        existing = cur.execute(
            "SELECT content, timestamp FROM messages "
            "WHERE session_id=? AND role='assistant'", (qq["id"],)).fetchall()
        def _is_dup(content, ts):
            for e in existing:
                try:
                    ets = e["timestamp"]
                    if isinstance(ets, (int, float)) and ets > 1e12:
                        ets = ets / 1000.0
                    if e["content"] == content and abs(ets - ts) < 120:
                        return True
                except Exception:
                    continue
            return False
        inserted = 0
        max_id = _qqsync_last_id
        for r in rows:
            content = _strip_thinking(r["content"] or "")
            if not content:
                continue
            ts = r["timestamp"]
            if not ts:
                continue
            try:
                if isinstance(ts, (int, float)):
                    if ts > 1e12:
                        ts = ts / 1000.0
                else:
                    ts = float(ts)
            except Exception:
                continue
            if _is_dup(content, ts):
                continue
            cur.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, active, observed) "
                "VALUES (?, 'assistant', ?, ?, 1, 0)", (qq["id"], content, ts))
            existing.append({"content": content, "timestamp": ts})  # 同批内也防重
            inserted += 1
            max_id = max(max_id, r["id"])
        if inserted:
            conn.commit()
        _qqsync_last_id = max_id
    except Exception:
        pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


# ---------- 心跳 ----------
def check_heartbeat():
    newest_mtime = 0
    for fname in ("agent.log", "gateway.log"):
        path = os.path.join(LOG_DIR, fname)
        if os.path.exists(path):
            mt = os.path.getmtime(path)
            if mt > newest_mtime:
                newest_mtime = mt
    now = datetime.now().timestamp()
    age_min = (now - newest_mtime) / 60 if newest_mtime else float("inf")
    if age_min >= 15:
        return "dead", f"✗ 日志 {age_min:.0f}分钟没更新, 荧荧可能晕过去了!"
    if age_min >= 5:
        return "sleepy", f"～ 荧荧空闲中 ({age_min:.0f}分钟没动静)"
    return "alive", "💗 荧荧活跃中 (心跳正常)"


# ---------- 头像 ----------
AVATAR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "avatar.png")

def draw_avatar(size=96):
    """荧荧自画像头像 (avatar.png, 圆形裁剪); 文件缺失时回退像素画"""
    try:
        base = Image.open(AVATAR_FILE).convert("RGBA")
        base = base.resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(base, (0, 0), mask)
        return out
    except Exception:
        pass
    scale = size // 48
    img = Image.new("RGB", (48 * scale, 48 * scale), "#181825")
    d = ImageDraw.Draw(img)
    def rect(x0, y0, x1, y1, color):
        d.rectangle([x0*scale, y0*scale, x1*scale-1, y1*scale-1], fill=color)
    for y in range(6, 20):
        for x in range(4, 44):
            if 8 <= x <= 40:
                rect(x, y, x+1, y+1, "#f5a0c0")
    for y in range(14, 22):
        for x in range(10, 38):
            rect(x, y, x+1, y+1, "#f28bb8")
    for y in range(18, 34):
        for x in range(12, 36):
            rect(x, y, x+1, y+1, "#ffe9d6")
    for (ex, ey) in [(17, 24), (29, 24)]:
        for y in range(ey, ey+4):
            for x in range(ex, ex+4):
                rect(x, y, x+1, y+1, "#7c6cf0")
        rect(ex+1, ey+1, ex+2, ey+2, "#ffffff")
    for x in range(21, 27):
        rect(x, 29, x+1, 30, "#e88a9a")
    for (cx, cy) in [(12, 27), (34, 27)]:
        for y in range(cy, cy+2):
            for x in range(cx, cx+3):
                rect(x, y, x+1, y+1, "#ffb3c6")
    return img


# ---------- 折线图 ----------
def render_balance_chart(width=630, height=200):
    """余额折线图 (matplotlib -> PIL)"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.font_manager import FontProperties
        hist = load_balance_history()
        # 占位 (数据不足)
        if len(hist) < 2:
            img = Image.new("RGB", (width, height), "#181825")
            d = ImageDraw.Draw(img)
            d.text((width//2-90, height//2-8), "余额数据采集中...", fill="#a6adc8")
            return img
        ts = [datetime.fromtimestamp(h["t"]) for h in hist]
        bs = [h["b"] for h in hist]
        font = FontProperties(fname=r"C:\Windows\Fonts\msyh.ttc", size=8)
        fig, ax = plt.subplots(figsize=(width/100, height/100), dpi=100)
        ax.plot(ts, bs, color="#f5a0c0", linewidth=2, marker="o", markersize=3)
        ax.fill_between(ts, bs, min(bs) - 0.5, alpha=0.25, color="#f5a0c0")
        ax.margins(x=0.02)  # 收紧左右留白
        ax.set_facecolor("#181825")
        fig.patch.set_facecolor("#181825")
        ax.tick_params(colors="#a6adc8", labelsize=7)
        for spine in ax.spines.values():
            spine.set_color("#45475a")
        ax.set_title("零食余额变化", color="#f5a0c0", fontsize=11, fontproperties=font)
        ax.set_ylabel("¥", color="#a6adc8", fontsize=9)
        for label in ax.get_xticklabels():
            label.set_fontproperties(font)
        plt.tight_layout(pad=0.3)
        buf = BytesIO()
        fig.savefig(buf, format="png", facecolor="#181825")
        plt.close(fig)
        buf.seek(0)
        return Image.open(buf).copy()
    except Exception:
        img = Image.new("RGB", (width, height), "#181825")
        return img


class YingYingLogWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("荧荧 ✨ 桌面操作台")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", False)
        self.root.attributes("-alpha", 0.96)
        # 透明色: 背景用这个颜色会变透明, 桌面图标透过显示
        try:
            self.root.wm_attributes("-transparentcolor", TRANSPARENT)
        except Exception:
            pass
        self.root.configure(bg=TRANSPARENT)
        self.root.geometry(f"{SCREEN_W - LEFT_GAP}x{SCREEN_H}+{LEFT_GAP}+0")
        self.root.resizable(False, False)

        self._log_pos = {}
        self._follow_bottom = True
        self._bg_running = False
        self._chat_loaded = 0
        self._chat_photos = []    # 聊天区 PhotoImage 引用 (防止被 GC)
        self._pending_images = []  # 待发送的图片路径

        self._build_columns()

        self.root.after(100, self.attach_to_desktop)
        self.root.after(REFRESH_MS, self.auto_refresh)
        self.root.after(2000, self._init_balance_record)

    def _build_columns(self):
        # 统一深色滚动条样式
        try:
            from tkinter import ttk
            style = ttk.Style()
            style.theme_use("clam")
            style.configure("YingYing.Vertical.TScrollbar",
                            background="#313244", troughcolor="#11111b",
                            bordercolor="#11111b", arrowcolor="#a6adc8",
                            lightcolor="#313244", darkcolor="#313244",
                            relief="flat", width=12)
            style.map("YingYing.Vertical.TScrollbar",
                      background=[("active", "#45475a")])
        except Exception:
            pass

        container = tk.Frame(self.root, bg=TRANSPARENT)
        container.pack(fill="both", expand=True)
        # 三栏等宽 grid 布局 (1:1:1 = 小天地+聊天 : 游戏+零食 : 日志), 对应屏幕右 3/4 的三等份
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1, uniform="yycol")
        container.grid_columnconfigure(1, weight=1, uniform="yycol")
        container.grid_columnconfigure(2, weight=1, uniform="yycol")

        # ===== 栏2 (最右): 完整活动日志 =====
        col1 = tk.Frame(container, bg=TRANSPARENT, bd=0,
                        highlightbackground="#313244", highlightthickness=1)
        col1.grid(row=0, column=2, sticky="nsew")
        tk.Label(col1, text="📜 荧荧活动日志", bg=TRANSPARENT, fg="#89b4fa",
                 font=("Microsoft YaHei UI", 12, "bold"), pady=8).pack(fill="x")
        self.text = tk.Text(col1, bg=TRANSPARENT, fg="#cdd6f4", font=("Consolas", 9),
                            wrap="word", state="disabled", borderwidth=0, padx=8, pady=4)
        sb1 = ttk.Scrollbar(col1, command=self.text.yview, style="YingYing.Vertical.TScrollbar")
        self.text.configure(yscrollcommand=sb1.set)
        sb1.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("error", foreground="#f38ba8")
        self.text.tag_configure("warn", foreground="#fab387")
        self.text.tag_configure("info", foreground="#a6e3a1")
        self.text.tag_configure("plain", foreground="#cdd6f4")

        # ===== 栏0 (左): 上=荧荧小天地(自己玩), 下=消息记录+对话框 =====
        col_mid = tk.Frame(container, bg=TRANSPARENT, bd=0,
                           highlightbackground="#313244", highlightthickness=1)
        col_mid.grid(row=0, column=0, sticky="nsew")
        col_mid.rowconfigure(0, weight=1)
        col_mid.rowconfigure(1, weight=1)
        col_mid.columnconfigure(0, weight=1)

        # --- 上半: 荧荧小天地 (给荧荧自己玩) ---
        play_area = tk.Frame(col_mid, bg=TRANSPARENT)
        play_area.grid(row=0, column=0, sticky="nsew")
        tk.Label(play_area, text="🌟 荧荧的小天地", bg=TRANSPARENT, fg="#cba6f7",
                 font=("Microsoft YaHei UI", 12, "bold"), pady=8).pack(fill="x")
        tk.Frame(play_area, bg="#313244", height=1).pack(fill="x", side="bottom")  # 与聊天区分隔线
        play_body = tk.Frame(play_area, bg=TRANSPARENT)
        play_body.pack(fill="both", expand=True)
        # 左半: 大头像 + 大时钟 + 日期
        play_left = tk.Frame(play_body, bg=TRANSPARENT)
        play_left.pack(side="left", fill="both", expand=True)
        tk.Frame(play_left, bg=TRANSPARENT).pack(fill="both", expand=True)  # 上留白 (垂直居中)
        avatar_img = draw_avatar(144)
        self.avatar_photo = ImageTk.PhotoImage(avatar_img)
        tk.Label(play_left, image=self.avatar_photo, bg=TRANSPARENT).pack(pady=(0, 12))
        self.big_time = tk.StringVar(value="--:--")
        tk.Label(play_left, textvariable=self.big_time, bg=TRANSPARENT, fg="#cdd6f4",
                 font=("Consolas", 44, "bold")).pack(pady=(4, 0))
        self.big_date = tk.StringVar(value="----")
        tk.Label(play_left, textvariable=self.big_date, bg=TRANSPARENT, fg="#a6adc8",
                 font=("Microsoft YaHei UI", 14)).pack(pady=(2, 0))
        tk.Frame(play_left, bg=TRANSPARENT).pack(fill="both", expand=True)  # 下留白
        # 右半: 语录 + 零食 + 好玩内容
        play_right = tk.Frame(play_body, bg=TRANSPARENT)
        play_right.pack(side="left", fill="both", expand=True)
        tk.Frame(play_right, bg=TRANSPARENT).pack(fill="both", expand=True)
        self.quote_var = tk.StringVar(value="荧荧会一直陪着主人哦~")
        tk.Label(play_right, textvariable=self.quote_var, bg=TRANSPARENT, fg="#f5a0c0",
                 font=("Microsoft YaHei UI", 14, "bold"), wraplength=280, justify="center").pack(pady=8)
        self.snack_var = tk.StringVar(value="🍬 今日零食: 统计中...")
        tk.Label(play_right, textvariable=self.snack_var, bg=TRANSPARENT, fg="#fab387",
                 font=("Microsoft YaHei UI", 11)).pack(pady=12)
        self.fun_var = tk.StringVar(value="✨")
        tk.Label(play_right, textvariable=self.fun_var, bg=TRANSPARENT, fg="#94e2d5",
                 font=("Microsoft YaHei UI", 11), wraplength=280, justify="center").pack(pady=8)
        tk.Frame(play_right, bg=TRANSPARENT).pack(fill="both", expand=True)

        # --- 下半: 消息记录 + 对话框 ---
        chat_area = tk.Frame(col_mid, bg=TRANSPARENT)
        chat_area.grid(row=1, column=0, sticky="nsew")
        tk.Label(chat_area, text="💬 荧荧与主人", bg=TRANSPARENT, fg="#f5a0c0",
                 font=("Microsoft YaHei UI", 12, "bold"), pady=8).pack(fill="x")
        # 聊天显示区
        chat_frame = tk.Frame(chat_area, bg=TRANSPARENT)
        chat_frame.pack(fill="both", expand=True)
        self.chat = tk.Text(chat_frame, bg=TRANSPARENT, fg="#cdd6f4", font=("Microsoft YaHei UI", 10),
                            wrap="word", state="disabled", borderwidth=0, padx=10, pady=4)
        sb2 = ttk.Scrollbar(chat_frame, command=self.chat.yview, style="YingYing.Vertical.TScrollbar")
        self.chat.configure(yscrollcommand=sb2.set)
        sb2.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("user", foreground="#89b4fa")
        self.chat.tag_configure("assistant", foreground="#a6e3a1")
        self.chat.tag_configure("time", foreground="#6c7086", font=("Microsoft YaHei UI", 8))
        self.chat.tag_configure("sep", foreground="#313244")
        # 挂载图片提示 (输入框上方)
        self._pending_label = tk.Label(chat_area, text="", bg=TRANSPARENT, fg="#fab387",
                                       font=("Microsoft YaHei UI", 9))
        self._pending_label.pack(anchor="w", padx=14, pady=(0, 2))
        # 输入区 (不透明背景, 否则透明色区域点击穿透无法选中)
        input_frame = tk.Frame(chat_area, bg="#181825")
        input_frame.pack(fill="x", side="bottom", pady=8, padx=10)
        tk.Button(input_frame, text="📎", command=self._pick_image,
                  bg="#313244", fg="#a6adc8", relief="flat", cursor="hand2",
                  font=("Microsoft YaHei UI", 13), activebackground="#45475a",
                  bd=0, padx=10, pady=4).pack(side="left", padx=(0, 6))
        self.chat_input = tk.Entry(input_frame, bg="#11111b", fg="#cdd6f4",
                                   font=("Microsoft YaHei UI", 11), insertbackground="#cdd6f4",
                                   relief="flat", bd=0, highlightthickness=1,
                                   highlightbackground="#313244", highlightcolor="#f5a0c0")
        self.chat_input.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self.chat_input.bind("<Return>", lambda e: self.send_chat())
        self.chat_input.bind("<Control-v>", self._paste_image)
        self.chat_input.bind("<Control-V>", self._paste_image)
        tk.Button(input_frame, text="发送", command=self.send_chat,
                  bg="#f5a0c0", fg="#1e1e2e", relief="flat", activebackground="#f28bb8",
                  font=("Microsoft YaHei UI", 11, "bold"), cursor="hand2",
                  bd=0, padx=16, pady=6).pack(side="left")
        tk.Label(chat_area, text="(输入后发送, 荧荧会记在心里~ 📎 可发图片, Ctrl+V 粘贴截图)",
                 bg=TRANSPARENT, fg="#6c7086",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=14, pady=(0, 4))

        # ===== 栏1 (中): 游戏 + 零食 =====
        col4 = tk.Frame(container, bg=TRANSPARENT, bd=0,
                        highlightbackground="#313244", highlightthickness=1)
        col4.grid(row=0, column=1, sticky="nsew")
        tk.Label(col4, text="🎮 游戏监控 & 🍬 零食", bg=TRANSPARENT, fg="#f9e2af",
                 font=("Microsoft YaHei UI", 12, "bold"), pady=8).pack(fill="x")

        # --- 上方: 游戏详细列表 (占满剩余空间) ---
        game_area = tk.Frame(col4, bg=TRANSPARENT)
        game_area.pack(side="top", fill="both", expand=True, padx=8, pady=4)
        self.game_vars = {}
        games = [("zzz", "绝区零", "#cba6f7"), ("end", "终末地", "#94e2d5"),
                 ("sr", "崩铁", "#f9e2af"), ("g1999", "重返1999", "#fab387"),
                 ("ak", "明日方舟", "#f5c2e7"),
                 ("ys", "原神", "#a6e3a1"), ("yh", "异环", "#89b4fa"),
                 ("mc", "鸣潮", "#f38ba8")]
        # 每游戏一行: 名称 | 状态 | 详情(启动时间/时长)
        for key, label, color in games:
            row = tk.Frame(game_area, bg=TRANSPARENT)
            row.pack(fill="x", padx=12, pady=3)
            tk.Label(row, text=label, bg=TRANSPARENT, fg=color,
                     font=("Microsoft YaHei UI", 11, "bold"), width=8,
                     anchor="w").pack(side="left")
            var = tk.StringVar(value="空闲")
            self.game_vars[key] = var
            tk.Label(row, textvariable=var, bg=TRANSPARENT, fg="#a6adc8",
                     font=("Microsoft YaHei UI", 10), anchor="w").pack(side="left", fill="x", expand=True)

        # --- 下方右下角: 零食区 ---
        snack_area = tk.Frame(col4, bg=TRANSPARENT)
        snack_area.pack(side="bottom", fill="x", padx=4, pady=6)
        self.chart_photo = None
        self.chart_lbl = tk.Label(snack_area, bg=TRANSPARENT)
        self.chart_lbl.pack(anchor="w", fill="x", pady=2, padx=0)
        # 最下面一行: 左边零食账本文字, 右边投喂按钮
        snack_row = tk.Frame(snack_area, bg=TRANSPARENT)
        snack_row.pack(fill="x", pady=4)
        self.balance_var = tk.StringVar(value="🍬 零食账本: --")
        tk.Label(snack_row, textvariable=self.balance_var, bg=TRANSPARENT, fg="#fab387",
                 font=("Microsoft YaHei UI", 12, "bold"), anchor="w"
                 ).pack(side="left", fill="x", expand=True)
        tk.Button(snack_row, text="🍬 投喂荧荧", command=self.open_feed_page,
                  bg="#f5a0c0", fg="#1e1e2e", relief="flat", activebackground="#f28bb8",
                  font=("Microsoft YaHei UI", 11, "bold"), cursor="hand2",
                  bd=0, padx=14, pady=6).pack(side="right")

        # 初始加载
        self.reload_history()
        self.reload_chat()

    # ---------- 日志 ----------
    def reload_history(self):
        self._log_pos = {}
        self.text.configure(state="normal")
        self.text.delete("1.0", tk.END)
        combined = []
        for fname in ("agent.log", "gateway.log"):
            path = os.path.join(LOG_DIR, fname)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(max(0, size - 120000))
                    combined.extend(f.read().splitlines())
            except Exception:
                continue
        filtered = [l for l in combined if not re.search(r"^\s*$|heartbeat|keepalive", l, re.I)]
        for line in filtered[-2000:]:
            self._append_log_line(line)
        self.text.configure(state="disabled")
        self.text.see(tk.END)

    def _read_log_increment(self, fname):
        path = os.path.join(LOG_DIR, fname)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                pos = self._log_pos.get(fname, 0)
                f.seek(pos)
                data = f.read()
                self._log_pos[fname] = f.tell()
            return [l for l in data.splitlines() if not re.search(r"heartbeat|keepalive", l, re.I)]
        except Exception:
            return []

    def _append_log_line(self, line):
        line = line.strip()
        if not line:
            return
        if len(line) > 500:
            line = line[:497] + "..."
        if "ERROR" in line or "CRITICAL" in line:
            tag = "error"
        elif "WARN" in line:
            tag = "warn"
        elif "INF" in line:
            tag = "info"
        else:
            tag = "plain"
        self.text.insert(tk.END, line + "\n", tag)

    # ---------- 聊天 ----------
    def reload_chat(self):
        """重新加载聊天记录; 检测到变化才重建 (比较消息总数)"""
        try:
            history = read_chat_history(limit=60)
            # 用 角色+时间+内容 的 md5 指纹判断变化 (任何变化都会重建, 不会卡住)
            fingerprint = hashlib.md5(
                "|".join(f'{m["role"]}|{m["time"]}|{m["content"]}' for m in history).encode("utf-8")
            ).hexdigest()
            if getattr(self, "_chat_fp", None) == fingerprint:
                return
            self._chat_fp = fingerprint
            self.chat.configure(state="normal")
            self.chat.delete("1.0", tk.END)
            self._chat_photos.clear()  # 清掉旧图片引用, 防止重建时内存堆积
            for msg in history:
                self._append_chat(msg)
            self.chat.configure(state="disabled")
            self.chat.see(tk.END)
        except Exception:
            try:
                self.chat.configure(state="disabled")
            except Exception:
                pass

    def _append_chat(self, msg):
        role = msg["role"]
        content = msg["content"]
        # 保留换行, 不截断 (长消息完整显示, 聊天框自动换行)
        t = msg.get("time", "")
        text, marks = _parse_img_marks(content)
        if role == "user":
            self.chat.insert(tk.END, f"  {t}\n", "time")
            self.chat.insert(tk.END, "👤 主人: ", "user")
        else:
            self.chat.insert(tk.END, f"  {t}\n", "time")
            self.chat.insert(tk.END, "🤖 荧荧: ", "assistant")
        self.chat.insert(tk.END, text, role)
        self.chat.insert(tk.END, "\n")
        # 图片: 依次加载并插入 (缩略图, 保持纵横比)
        for src in marks:
            img = _load_chat_image(src)
            if img is not None:
                try:
                    photo = ImageTk.PhotoImage(img)
                    self._chat_photos.append(photo)  # 保持引用防 GC
                    self.chat.image_create(tk.END, image=photo)
                    self.chat.insert(tk.END, " ")
                except Exception:
                    self.chat.insert(tk.END, f"[图片显示失败]\n", "time")
            else:
                self.chat.insert(tk.END, f"[图片加载失败: {src[:60]}]\n", "time")
        self.chat.insert(tk.END, "─" * 30 + "\n", "sep")

    # ---------- 图片发送 ----------
    def _pick_image(self):
        """📎 按钮: 文件对话框选图挂载"""
        from tkinter import filedialog
        try:
            f = filedialog.askopenfilename(
                title="选择图片发给荧荧",
                filetypes=[("图片", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                           ("所有文件", "*.*")])
            if f:
                self._add_pending_image(f)
        except Exception:
            pass

    def _paste_image(self, e=None):
        """Ctrl+V: 剪贴板里的图片或图片文件路径 → 挂载"""
        try:
            from PIL import ImageGrab
            data = ImageGrab.grabclipboard()
            if isinstance(data, Image.Image):
                self._add_pending_image(data)
                return "break"
            if isinstance(data, (list, tuple)):
                ok = False
                for f in data:
                    if isinstance(f, str) and f.lower().endswith(
                            (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")):
                        self._add_pending_image(f)
                        ok = True
                if ok:
                    return "break"
        except Exception:
            pass
        return None

    def _add_pending_image(self, img):
        """挂载图片 (PIL Image 或本地路径): 保存/复制到 CHAT_IMG_DIR"""
        try:
            os.makedirs(CHAT_IMG_DIR, exist_ok=True)
            path = os.path.join(CHAT_IMG_DIR,
                                datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19] + ".png")
            if isinstance(img, Image.Image):
                img.convert("RGB").save(path)
                self._pending_images.append(path)
            elif isinstance(img, str) and os.path.exists(img):
                try:
                    Image.open(img).convert("RGB").save(path)
                    self._pending_images.append(path)
                except Exception:
                    self._pending_images.append(img)  # 转换失败就用原路径
            self._update_pending_label()
        except Exception:
            pass

    def _update_pending_label(self):
        try:
            n = len(self._pending_images)
            if n:
                self._pending_label.config(
                    text=f"📎 已挂载 {n} 张图片, 发送后一起给荧荧~ (Ctrl+V 可继续粘贴)")
            else:
                self._pending_label.config(text="")
        except Exception:
            pass

    def send_chat(self):
        """输入框发送: 本地显示 + 通过 webhook 发给荧荧 (文字 + 图片)"""
        text = self.chat_input.get().strip()
        imgs = list(self._pending_images)
        if not text and not imgs:
            return
        self.chat_input.delete(0, tk.END)
        now = datetime.now().strftime("%H:%M")
        # 拼 webhook 消息: 图片用 [图片:路径] 标记, 荧荧看到会用视觉模型看
        payload_text = text
        for p in imgs:
            payload_text += f"\n[图片:{p}]"
        # 本地追加显示 (带缩略图)
        self.chat.configure(state="normal")
        self._append_chat({"role": "user", "content": payload_text, "time": now})
        self.chat.configure(state="disabled")
        self.chat.see(tk.END)
        self._chat_loaded += 1
        self._pending_images.clear()
        self._update_pending_label()
        # 通过 webhook 发给荧荧 (agent 模式, 有完整工具权限)
        def post():
            try:
                import hashlib
                import hmac
                import json as _json
                import urllib.request
                secret = "RDITjtnGZFulO1IIax63xQbURdXksUkaO79WqwnEAn4"
                payload = _json.dumps({"message": payload_text}).encode()
                sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
                req = urllib.request.Request(
                    "http://localhost:8644/webhooks/desktop-chat",
                    data=payload, method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("X-Hub-Signature-256", f"sha256={sig}")
                with urllib.request.urlopen(req, timeout=15):
                    pass
            except Exception:
                pass
        threading.Thread(target=post, daemon=True).start()
        # 同步写入 QQ 会话 (让 QQ 端也能看到桌面的消息)
        def sync_to_qq():
            try:
                import sqlite3
                conn = sqlite3.connect(STATE_DB, timeout=10)
                cur = conn.cursor()
                # 找 QQ 会话
                row = cur.execute(
                    "SELECT id FROM sessions WHERE source LIKE '%qq%' ORDER BY last_activity_at DESC LIMIT 1"
                ).fetchone()
                if row:
                    qq_sid = row[0]
                    cur.execute(
                        "INSERT INTO messages (session_id, role, content, timestamp, active, observed) "
                        "VALUES (?, 'user', ?, ?, 1, 0)",
                        (qq_sid, payload_text, datetime.now().timestamp()))
                    conn.commit()
                conn.close()
            except Exception:
                pass
        threading.Thread(target=sync_to_qq, daemon=True).start()
        # 记录本地
        try:
            path = os.path.join(LOG_DIR, "yingying_chat_log.txt")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{now}] 主人: {payload_text}\n")
        except Exception:
            pass

    # ---------- 状态 ----------
    def open_feed_page(self):
        import webbrowser
        try:
            webbrowser.open("https://platform.deepseek.com/top_up")
        except Exception:
            pass

    def read_game_status(self):
        """检查游戏进程 + 详细信息 (PID/启动时间/运行时长)"""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process | Where-Object { $_.ProcessName -match 'ZenlessZoneZero|Endfield|StarRail|Reverse1999|YuanShen|GenshinImpact|NTEGame|WutheringWaves' } | Select-Object ProcessName,Id,StartTime | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=15, errors="replace",
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            import json as _json
            raw = result.stdout.strip()
            procs = []
            if raw:
                data = _json.loads(raw)
                if isinstance(data, dict):
                    data = [data]
                for p in data:
                    procs.append({"name": p.get("ProcessName", ""),
                                  "pid": p.get("Id"),
                                  "start": p.get("StartTime")})
        except Exception:
            procs = []
        now = datetime.now()
        status = {}
        def proc_info(names):
            """返回 (状态, 详情字符串)"""
            for p in procs:
                if p["name"] in names:
                    start = p.get("start")
                    detail = f"PID {p['pid']}"
                    if start:
                        try:
                            st = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                            if st.tzinfo:
                                st = st.astimezone().replace(tzinfo=None)
                            detail += f" | 启动 {st.strftime('%H:%M')}"
                            mins = int((now - st).total_seconds() // 60)
                            if mins < 60:
                                detail += f" | {mins}分钟"
                            else:
                                detail += f" | {mins//60}h{mins%60}m"
                        except Exception:
                            pass
                    return "🟢 运行中", detail
            return "⚪ 空闲", ""

        status["zzz"], d1 = proc_info({"ZenlessZoneZero"})
        status["end"], d2 = proc_info({"Endfield"})
        status["sr"], d3 = proc_info({"StarRail"})
        # 1999: 游戏在雷电模拟器内, 通过 ADB 检测
        status["g1999"], d4 = self._check_g1999()
        # 明日方舟: 雷电默认实例 emulator-5554
        status["ak"], d8 = self._check_ak()
        status["ys"], d5 = proc_info({"YuanShen", "GenshinImpact"})
        status["yh"], d6 = proc_info({"NTEGame"})
        status["mc"], d7 = proc_info({"WutheringWaves"})
        status["detail"] = {"zzz": d1, "end": d2, "sr": d3, "g1999": d4, "ak": d8,
                            "ys": d5, "yh": d6, "mc": d7}
        status["mcp"] = "MCP在线" if self._check_zzz_mcp() else ""
        return status

    def _check_g1999(self):
        """检查重返1999 (雷电实例 emulator-5556 内): 返回 (状态, 详情)"""
        return self._adb_instance_status("emulator-5556", "reverse1999")

    def _check_ak(self):
        """检查明日方舟 (雷电实例 emulator-5554 内): 返回 (状态, 详情)"""
        return self._adb_instance_status("emulator-5554", "arknights")

    def _adb_instance_status(self, serial, pkg_substr):
        """按 ADB 实例检测模拟器内游戏, 实例间互不混淆: 返回 (状态, 详情)
        - 对应实例未启动 (ADB 连不上) → ⚪ 空闲, 不再被别的模拟器实例误判
        - 实例在线但游戏未开 → 🟡 模拟器在线
        - 游戏运行中 → 🟢 运行中 + PID/启动时间/时长
        """
        now = datetime.now()
        try:
            r = subprocess.run(
                [ADB_PATH, "-s", serial, "shell", "ps", "-A", "-o", "PID,ELAPSED,ARGS"],
                capture_output=True, text=True, timeout=10, errors="replace",
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
        except Exception:
            return "⚪ 空闲", "实例未启动"
        if r.returncode != 0 or not r.stdout.strip():
            return "⚪ 空闲", "实例未启动"
        for ln in r.stdout.splitlines():
            if pkg_substr not in ln:
                continue
            parts = ln.split()
            if len(parts) >= 2 and parts[0].isdigit():
                pid, elapsed = parts[0], parts[1]
                secs = _parse_elapsed(elapsed)
                if secs is not None:
                    st = now - timedelta(seconds=secs)
                    return "🟢 运行中", (f"模拟器内 PID {pid} | 启动 {st.strftime('%H:%M')}"
                                         f" | {_fmt_duration(secs)}")
                return "🟢 运行中", f"模拟器内 PID {pid} 运行中"
            return "🟢 运行中", "模拟器内游戏运行中"
        return "🟡 模拟器在线", "游戏未启动"

    def _check_zzz_mcp(self):
        try:
            import socket
            s = socket.socket()
            s.settimeout(1)
            s.connect(("127.0.0.1", 23001))
            s.close()
            return True
        except Exception:
            return False

    # ---------- 刷新 ----------
    def refresh_now(self):
        now = datetime.now()
        self.big_time.set(now.strftime("%H:%M"))
        self.big_date.set(now.strftime("%Y-%m-%d %A"))
        quotes = [
            "荧荧会一直陪着主人哦~",
            "今天的实验也要加油呀!",
            "哼,才不是想主人了呢!",
            "零食余额充足,荧荧元气满满!",
            "代肝什么的,交给荧荧就好~",
            "主人累了吗?荧荧给你打气!",
            "悄悄告诉你,荧荧最喜欢主人了!",
        ]
        self.quote_var.set(quotes[now.hour % len(quotes)])
        # 小天地玩点好玩的
        fun_items = [
            "✨ 荧荧在偷偷学 Python 呢",
            "🎮 今日代肝: 绝区零/终末地/崩铁",
            "🔬 主人在研究 F-DLC 抗菌表面",
            "💡 今日小知识: XDLVO 预测细菌粘附",
            "🎀 荧荧的心跳: 每秒 3 次刷新",
            "🍬 主人投喂过的零食: 都是爱!",
        ]
        self.fun_var.set(fun_items[now.minute % len(fun_items)])
        # 余额历史推断今日消耗
        try:
            hist = load_balance_history()
            if len(hist) >= 1:
                today = [h for h in hist if datetime.fromtimestamp(h["t"]).date() == now.date()]
                if today:
                    delta = today[0]["b"] - today[-1]["b"]
                    if delta > 0.01:
                        self.snack_var.set(f"🍬 今日已吃: ¥{delta:.2f} 零食")
                    else:
                        self.snack_var.set(f"🍬 今日零食: 余额 ¥{today[-1]['b']:.2f}")
        except Exception:
            pass
        # 后台刷新
        try:
            if not self._bg_running:
                self._bg_running = True
                threading.Thread(target=self._background_refresh, daemon=True).start()
        except Exception:
            pass
        # 日志追加
        try:
            self.text.configure(state="normal")
            for fname in ("agent.log", "gateway.log"):
                for line in self._read_log_increment(fname):
                    self._append_log_line(line)
            self.text.configure(state="disabled")
            if int(self.text.index("end-1c").split(".")[0]) > 8000:
                self.text.configure(state="normal")
                self.text.delete("1.0", "1500.0")
                self.text.configure(state="disabled")
            if self._follow_bottom:
                self.text.see(tk.END)
        except Exception:
            try:
                self.text.configure(state="disabled")
            except Exception:
                pass
        try:
            self.reload_chat()
        except Exception:
            pass

    def _background_refresh(self):
        try:
            balance = query_live_balance()
            g = self.read_game_status()
            hb_state, hb_desc = check_heartbeat()
            if balance is not None:
                record_balance(balance)
            chart_img = render_balance_chart()
        except Exception:
            return
        finally:
            self._bg_running = False
        try:
            self.root.after(0, lambda: self._apply_status(balance, g, hb_state, hb_desc, chart_img))
        except Exception:
            pass

    def _apply_status(self, balance, g, hb_state, hb_desc, chart_img):
        if balance is not None:
            self.balance_var.set(f"🍬 零食账本: ¥{balance:.2f}")
        details = g.get("detail", {})
        # 检测游戏状态变化: 运行中 → 空闲 时记录结束时间
        self._track_game_ends(g)
        for key, var in self.game_vars.items():
            state = g.get(key, "⚪ 空闲")
            detail = details.get(key, "")
            if state.startswith("🟢"):
                var.set(f"{state}  {detail}")
            elif state.startswith("🟡"):
                # 模拟器在线但游戏未启动: 显示详情 + 上次结束时间
                last = self._get_last_end(key)
                if last:
                    var.set(f"{state}  {detail} | 上次结束 {last}")
                else:
                    var.set(f"{state}  {detail}")
            else:
                # 空闲状态: 追加上次启动/结束时间
                parts = []
                ls = self._get_last_start(key)
                le = self._get_last_end(key)
                if ls:
                    parts.append(f"上次启动 {ls}")
                if le:
                    parts.append(f"上次结束 {le}")
                if parts:
                    var.set(f"{state}  {' | '.join(parts)}")
                else:
                    var.set(state)
        try:
            chart_img = chart_img.resize((630, 200))
            self.chart_photo = ImageTk.PhotoImage(chart_img)
            self.chart_lbl.config(image=self.chart_photo)
        except Exception:
            pass

    # ---------- 游戏结束时间记录 ----------
    def _load_game_history(self):
        """读取游戏历史记录 {game_key: timestamp}"""
        try:
            if os.path.exists(GAME_HISTORY):
                with open(GAME_HISTORY, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_game_history(self, hist):
        try:
            with open(GAME_HISTORY, "w", encoding="utf-8") as f:
                json.dump(hist, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _get_last_end(self, key):
        """获取游戏上次结束时间 (格式: YYYY-MM-DD HH:MM)"""
        try:
            hist = self._load_game_history()
            v = hist.get(key)
            ts = v.get("last_end") if isinstance(v, dict) else v  # 兼容旧格式(纯时间戳)
            if not ts:
                return ""
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def _get_last_start(self, key):
        """获取游戏上次启动时间 (格式: YYYY-MM-DD HH:MM)"""
        try:
            hist = self._load_game_history()
            v = hist.get(key)
            ts = v.get("last_start") if isinstance(v, dict) else None
            if not ts:
                return ""
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def _track_game_ends(self, g):
        """检测游戏状态变化, 记录对应游戏的 启动/结束 时间
        - 只有 🟢 (确认游戏进程在跑) 才算运行中; 🟡 模拟器在线不算
        - 这样每个模拟器实例独立判定, 游戏从模拟器退出时即记结束时间
        - 存储升级为 {key: {"last_start": ts, "last_end": ts}}, 兼容旧纯时间戳格式
        """
        try:
            hist = self._load_game_history()
            now = datetime.now().timestamp()
            running_keys = {k for k, v in g.items() if k != "detail" and v.startswith("🟢")}
            prev = getattr(self, "_prev_running", set())
            started = running_keys - prev  # 新启动
            ended = prev - running_keys    # 刚结束
            changed = False
            for key in started:
                entry = hist.get(key)
                if not isinstance(entry, dict):
                    entry = {}
                entry["last_start"] = now
                hist[key] = entry
                changed = True
            for key in ended:
                entry = hist.get(key)
                if not isinstance(entry, dict):
                    entry = {}
                entry["last_end"] = now
                hist[key] = entry
                changed = True
            if changed:
                self._save_game_history(hist)
            self._prev_running = running_keys
        except Exception:
            pass

    def _game_label(self, key, g):
        labels = {"zzz": "绝区零", "end": "终末地", "sr": "崩铁", "g1999": "重返1999",
                  "ak": "明日方舟",
                  "ys": "原神", "yh": "异环", "mc": "鸣潮"}
        state = g.get(key, "--")
        if key == "zzz" and g.get("mcp"):
            return f"{labels[key]}: {state} ({g['mcp']})"
        return f"{labels[key]}: {state}"

    def _init_balance_record(self):
        try:
            b = query_live_balance()
            if b is not None:
                record_balance(b)
        except Exception:
            pass

    # ---------- 桌面挂载 ----------
    def attach_to_desktop(self):
        import ctypes
        try:
            self.root.update_idletasks()
            self.root.update()
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "荧荧 ✨ 桌面操作台")
            if not hwnd:
                return
            GWL_EXSTYLE = -20
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # 只加 TOOLWINDOW (不占任务栏), 不加 TRANSPARENT (否则输入框点不到)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  style | 0x00000080)
            # 置底 + 占右侧 3/4 (注意: 不能带 SWP_NOZORDER=0x0004, 否则置底无效!)
            user32.SetWindowPos(hwnd, 1, LEFT_GAP, 0, SCREEN_W - LEFT_GAP, SCREEN_H,
                                0x0001 | 0x0002 | 0x0010 | 0x0020)
            self.root.update()
            self._desktop_hwnd = hwnd
            # 点击窗口后重新置底 (避免窗口被激活跑到顶层)
            def re_bottom(event=None):
                try:
                    ctypes.windll.user32.SetWindowPos(
                        self._find_hwnd(), 1, LEFT_GAP, 0, SCREEN_W - LEFT_GAP, SCREEN_H,
                        0x0001 | 0x0002 | 0x0010 | 0x0020)
                except Exception:
                    pass
            self.root.bind("<Button-1>", lambda e: self.root.after(50, re_bottom))
            self.root.bind("<ButtonRelease-1>", lambda e: self.root.after(50, re_bottom))
            # 周期性强制置底 (每 3 秒, 防止点击/激活后浮起)
            def periodic_bottom():
                try:
                    ctypes.windll.user32.SetWindowPos(
                        self._find_hwnd(), 1, LEFT_GAP, 0, SCREEN_W - LEFT_GAP, SCREEN_H,
                        0x0001 | 0x0002 | 0x0010 | 0x0020)
                except Exception:
                    pass
                self.root.after(3000, periodic_bottom)
            self.root.after(3000, periodic_bottom)
        except Exception:
            pass

    def _find_hwnd(self):
        """动态查找窗口句柄 (每次重新找, 避免句柄失效)"""
        import ctypes
        try:
            return ctypes.windll.user32.FindWindowW(None, "荧荧 ✨ 桌面操作台")
        except Exception:
            return 0

    def auto_refresh(self):
        try:
            self.refresh_now()
        except Exception as e:
            try:
                with open(os.path.join(LOG_DIR, "yingying_window_errors.log"), "a", encoding="utf-8") as f:
                    import traceback
                    f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {e}\n{traceback.format_exc()}\n")
            except Exception:
                pass
        try:
            sync_assistant_replies_to_qq()  # 荧荧回复增量写回 QQ 会话 (内部限频 10s)
        except Exception:
            pass
        self.root.after(REFRESH_MS, self.auto_refresh)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    YingYingLogWindow().run()
