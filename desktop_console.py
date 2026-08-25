# -*- coding: utf-8 -*-
"""
荧荧的桌面操作台 v8 (全屏四栏 + 透明背景 + 聊天输入)
- 全屏四栏, 透明背景 (桌面图标透过显示)
- 栏1: 完整活动日志
- 栏2: QQ 风格聊天 + 输入框 (可对话)
- 栏3: 荧荧小天地 (时钟/语录/好玩内容)
- 栏4: 游戏状态 + 零食余额 + 折线图
"""
import os
import re
import json
import hashlib
import sqlite3
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageTk

LOG_DIR = os.path.expandvars(r"%LOCALAPPDATA%\hermes\logs")
ENV_FILE = os.path.expandvars(r"%LOCALAPPDATA%\hermes\.env")
STATE_DB = os.path.expandvars(r"%LOCALAPPDATA%\hermes\state.db")
BALANCE_HISTORY = os.path.expandvars(r"%LOCALAPPDATA%\hermes\scripts\balance_history.json")
GAME_HISTORY = os.path.expandvars(r"%LOCALAPPDATA%\hermes\scripts\game_history.json")
REFRESH_MS = 3000
SCREEN_W, SCREEN_H = 2560, 1440
COL_W = SCREEN_W // 4
TRANSPARENT = "#010203"  # 透明色 (桌面图标透过显示)

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
        # 只保留第一行 (消息本体), 去掉换行后的任何提示词/模板尾巴
        content = content.split("\n")[0].strip()
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
    """读取 QQ 会话 + 近期 webhook 会话, 合并后按真实时间戳排序并去重"""
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
        # 2. 近 30 分钟内活跃的 webhook 会话 (桌面端对话; 每条桌面消息会开新会话, 全部合并才不丢回复)
        cutoff = datetime.now().timestamp() - 1800
        for w in conn.execute(
                "SELECT id FROM sessions WHERE source='webhook' AND last_activity_at > ? "
                "ORDER BY last_activity_at DESC LIMIT 10", (cutoff,)).fetchall():
            msgs = conn.execute(
                'SELECT role, content, timestamp FROM messages '
                'WHERE session_id=? AND role IN ("user","assistant") AND content IS NOT NULL '
                'ORDER BY id DESC LIMIT ?', (w["id"], limit * 2)
            ).fetchall()
            for m in reversed(msgs):
                item = _clean_msg(m, webhook=True)
                if item:
                    merged.append(item)
        conn.close()
        # 3. 按真实时间戳排序 (跨会话交叉时顺序才不乱)
        merged.sort(key=lambda x: x["ts"])
        # 4. 去重: 桌面发送的消息会同时写入 QQ 会话和 webhook 会话 (内容相同、时间接近)
        deduped, seen = [], {}
        for m in merged:
            if m["role"] == "user":
                last = seen.get(m["content"])
                if last is not None and m["ts"] - last < 120:
                    continue
                seen[m["content"]] = m["ts"]
            deduped.append(m)
        return deduped[-limit:]
    except Exception:
        return []


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
        self.root.geometry(f"{SCREEN_W}x{SCREEN_H}+0+0")
        self.root.resizable(False, False)

        self._log_pos = {}
        self._follow_bottom = True
        self._bg_running = False
        self._chat_loaded = 0

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

        # ===== 栏1: 日志 =====
        col1 = tk.Frame(container, bg=TRANSPARENT, bd=0, width=700,
                        highlightbackground="#313244", highlightthickness=1)
        col1.pack_propagate(False)
        col1.pack(side="left", fill="both", expand=False)
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

        # ===== 栏2: 聊天 + 输入框 =====
        col2 = tk.Frame(container, bg=TRANSPARENT, bd=0, width=700,
                        highlightbackground="#313244", highlightthickness=1)
        col2.pack_propagate(False)
        col2.pack(side="left", fill="both", expand=False)
        tk.Label(col2, text="💬 荧荧与主人", bg=TRANSPARENT, fg="#f5a0c0",
                 font=("Microsoft YaHei UI", 12, "bold"), pady=8).pack(fill="x")
        # 聊天显示区
        chat_frame = tk.Frame(col2, bg=TRANSPARENT)
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
        # 输入区 (不透明背景, 否则透明色区域点击穿透无法选中)
        input_frame = tk.Frame(col2, bg="#181825")
        input_frame.pack(fill="x", side="bottom", pady=8, padx=10)
        self.chat_input = tk.Entry(input_frame, bg="#11111b", fg="#cdd6f4",
                                   font=("Microsoft YaHei UI", 11), insertbackground="#cdd6f4",
                                   relief="flat", bd=0, highlightthickness=1,
                                   highlightbackground="#313244", highlightcolor="#f5a0c0")
        self.chat_input.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self.chat_input.bind("<Return>", lambda e: self.send_chat())
        tk.Button(input_frame, text="发送", command=self.send_chat,
                  bg="#f5a0c0", fg="#1e1e2e", relief="flat", activebackground="#f28bb8",
                  font=("Microsoft YaHei UI", 11, "bold"), cursor="hand2",
                  bd=0, padx=16, pady=6).pack(side="left")
        tk.Label(col2, text="(输入后发送, 荧荧会记在心里~)", bg=TRANSPARENT, fg="#6c7086",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=14, pady=(0, 4))

        # ===== 栏3: 荧荧小天地 =====
        col3 = tk.Frame(container, bg=TRANSPARENT, bd=0, width=520,
                        highlightbackground="#313244", highlightthickness=1)
        col3.pack_propagate(False)
        col3.pack(side="left", fill="both", expand=False)
        tk.Label(col3, text="🌟 荧荧的小天地", bg=TRANSPARENT, fg="#cba6f7",
                 font=("Microsoft YaHei UI", 12, "bold"), pady=8).pack(fill="x")
        self.big_time = tk.StringVar(value="--:--")
        tk.Label(col3, textvariable=self.big_time, bg=TRANSPARENT, fg="#cdd6f4",
                 font=("Consolas", 40, "bold")).pack(pady=(30, 0))
        self.big_date = tk.StringVar(value="----")
        tk.Label(col3, textvariable=self.big_date, bg=TRANSPARENT, fg="#a6adc8",
                 font=("Microsoft YaHei UI", 14)).pack(pady=(2, 0))
        avatar_img = draw_avatar(100)
        self.avatar_photo = ImageTk.PhotoImage(avatar_img)
        tk.Label(col3, image=self.avatar_photo, bg=TRANSPARENT).pack(pady=(20, 8))
        self.quote_var = tk.StringVar(value="荧荧会一直陪着主人哦~")
        tk.Label(col3, textvariable=self.quote_var, bg=TRANSPARENT, fg="#f5a0c0",
                 font=("Microsoft YaHei UI", 11), wraplength=520, justify="center").pack(pady=6)
        # 好玩的小玩意: 零食统计
        self.snack_var = tk.StringVar(value="🍬 今日零食: 统计中...")
        tk.Label(col3, textvariable=self.snack_var, bg=TRANSPARENT, fg="#fab387",
                 font=("Microsoft YaHei UI", 10)).pack(pady=6)
        self.fun_var = tk.StringVar(value="✨")
        tk.Label(col3, textvariable=self.fun_var, bg=TRANSPARENT, fg="#94e2d5",
                 font=("Microsoft YaHei UI", 10), wraplength=520, justify="center").pack(pady=6)

        # ===== 栏4: 游戏 + 零食 =====
        col4 = tk.Frame(container, bg=TRANSPARENT, bd=0, width=640,
                        highlightbackground="#313244", highlightthickness=1)
        col4.pack_propagate(False)
        col4.pack(side="left", fill="both", expand=False)
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
        self.balance_var = tk.StringVar(value="🍬 零食账本: --")
        tk.Label(snack_area, textvariable=self.balance_var, bg=TRANSPARENT, fg="#fab387",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="e", pady=(0, 2))
        self.chart_photo = None
        self.chart_lbl = tk.Label(snack_area, bg=TRANSPARENT)
        self.chart_lbl.pack(anchor="w", fill="x", pady=2, padx=0)
        tk.Button(snack_area, text="🍬 投喂荧荧", command=self.open_feed_page,
                  bg="#f5a0c0", fg="#1e1e2e", relief="flat", activebackground="#f28bb8",
                  font=("Microsoft YaHei UI", 11, "bold"), cursor="hand2",
                  bd=0, padx=14, pady=6).pack(anchor="e", pady=4)

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
        if role == "user":
            self.chat.insert(tk.END, f"  {t}\n", "time")
            self.chat.insert(tk.END, f"👤 主人: {content}\n", "user")
        else:
            self.chat.insert(tk.END, f"  {t}\n", "time")
            self.chat.insert(tk.END, f"🤖 荧荧: {content}\n", "assistant")
        self.chat.insert(tk.END, "─" * 30 + "\n", "sep")

    def send_chat(self):
        """输入框发送: 本地显示 + 通过 webhook 发给荧荧"""
        text = self.chat_input.get().strip()
        if not text:
            return
        self.chat_input.delete(0, tk.END)
        # 本地追加显示
        self.chat.configure(state="normal")
        now = datetime.now().strftime("%H:%M")
        self.chat.insert(tk.END, f"  {now}\n", "time")
        self.chat.insert(tk.END, f"👤 主人: {text}\n", "user")
        self.chat.insert(tk.END, "─" * 30 + "\n", "sep")
        self.chat.configure(state="disabled")
        self.chat.see(tk.END)
        self._chat_loaded += 1
        # 本地直连 DeepSeek API 获取荧荧回复 (不走 webhook, 无权限限制)
        def direct_reply():
            try:
                import json as _json
                import urllib.request
                # 1. 读 API key
                key = ""
                env_path = os.path.expandvars(r"%LOCALAPPDATA%\hermes\.env")
                try:
                    for line in open(env_path, encoding="utf-8"):
                        if line.startswith("DEEPSEEK_API_KEY="):
                            key = line.strip().split("=", 1)[1].strip().strip('"')
                            break
                except Exception:
                    pass
                if not key:
                    return
                # 2. 取最近聊天历史 (QQ 会话 + 桌面, 最多 20 条)
                history = []
                try:
                    import sqlite3
                    conn = sqlite3.connect(STATE_DB, timeout=10)
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        "SELECT id FROM sessions WHERE source LIKE '%qq%' ORDER BY last_activity_at DESC LIMIT 1"
                    ).fetchone()
                    if row:
                        rows = conn.execute(
                            "SELECT role, content FROM messages WHERE session_id=? AND role IN ('user','assistant') "
                            "AND content NOT LIKE '%untrusted_tool_result%' AND content NOT LIKE '%{\"output\"%' "
                            "ORDER BY id DESC LIMIT 20", (row[0],)).fetchall()
                        for m in reversed(rows):
                            c = (m["content"] or "").strip()
                            if not c:
                                continue
                            # webhook 模板前缀提取
                            if m["role"] == "user" and c.startswith("主人从桌面操作台发来消息"):
                                c = c.replace("主人从桌面操作台发来消息", "", 1).lstrip(":：-–—").strip()
                                c = c.split("\n")[0].strip()
                            history.append({"role": "user" if m["role"] == "user" else "assistant",
                                            "content": c})
                    conn.close()
                except Exception:
                    pass
                history.append({"role": "user", "content": text})
                if len(history) > 30:
                    history = history[-30:]
                # 3. 调用 DeepSeek (带荧荧人设)
                sys_prompt = ("你是荧荧,主人的 AI 助手。性格傲娇可爱,称呼主人为'笨蛋主人'或'主人',"
                              "说话带一点点傲娇(哼、才不是...),但内心很关心主人,用语自然不刻意。"
                              "回复要简短(2-4句),温暖。不要用markdown格式,不要提'我是AI'。")
                req = urllib.request.Request(
                    "https://api.deepseek.com/chat/completions",
                    data=_json.dumps({
                        "model": "deepseek-chat",
                        "messages": [{"role": "system", "content": sys_prompt}] + history,
                        "max_tokens": 500,
                    }).encode(),
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {key}"})
                resp = urllib.request.urlopen(req, timeout=120)
                d = _json.loads(resp.read())
                reply = d["choices"][0]["message"]["content"].strip()
                # 4. 写回 QQ 会话 (assistant, 标记桌面来源)
                try:
                    import sqlite3
                    conn = sqlite3.connect(STATE_DB, timeout=10)
                    cur = conn.cursor()
                    row = cur.execute(
                        "SELECT id FROM sessions WHERE source LIKE '%qq%' ORDER BY last_activity_at DESC LIMIT 1"
                    ).fetchone()
                    if row:
                        cur.execute(
                            "INSERT INTO messages (session_id, role, content, timestamp, active, observed) "
                            "VALUES (?, 'assistant', ?, ?, 1, 0)",
                            (row[0], reply, datetime.now().timestamp()))
                        conn.commit()
                    conn.close()
                except Exception:
                    pass
                # 5. 显示到聊天框 (主线程)
                def show():
                    self.chat.configure(state="normal")
                    now = datetime.now().strftime("%H:%M")
                    self.chat.insert(tk.END, f"  {now}\n", "time")
                    self.chat.insert(tk.END, f"🤖 荧荧: {reply}\n", "assistant")
                    self.chat.insert(tk.END, "─" * 30 + "\n", "sep")
                    self.chat.configure(state="disabled")
                    self.chat.see(tk.END)
                self.root.after(0, show)
                # 6. 写日志
                try:
                    path = os.path.join(LOG_DIR, "yingying_chat_log.txt")
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.now().strftime('%H:%M')}] 荧荧(直连): {reply}\n")
                except Exception:
                    pass
            except Exception as e:
                # 直连失败 → 回退 webhook
                try:
                    import hashlib
                    import hmac
                    import json as _json
                    import urllib.request
                    secret = "RDITjtnGZFulO1IIax63xQbURdXksUkaO79WqwnEAn4"
                    payload = _json.dumps({"message": text}).encode()
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
        threading.Thread(target=direct_reply, daemon=True).start()
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
                        (qq_sid, text, datetime.now().timestamp()))
                    conn.commit()
                conn.close()
            except Exception:
                pass
        threading.Thread(target=sync_to_qq, daemon=True).start()
        # 记录本地
        try:
            path = os.path.join(LOG_DIR, "yingying_chat_log.txt")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{now}] 主人: {text}\n")
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
        """检查重返1999 (雷电模拟器内): 返回 (状态, 详情)"""
        # 1. 检查模拟器是否运行 (dnplayer 进程)
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process dnplayer -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count"],
                capture_output=True, text=True, timeout=10, errors="replace",
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            emu_running = r.stdout.strip().isdigit() and int(r.stdout.strip()) > 0
        except Exception:
            emu_running = False
        if not emu_running:
            return "⚪ 空闲", "模拟器未启动"
        # 2. 模拟器在跑, 检查 1999 游戏 (ADB)
        try:
            r2 = subprocess.run(
                [r"F:\leidian\LDPlayer14\adb.exe", "-s", "emulator-5556",
                 "shell", "ps", "-A"],
                capture_output=True, text=True, timeout=10, errors="replace",
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            if r2 and "reverse1999" in r2.stdout:
                return "🟢 运行中", "模拟器内游戏运行中"
            return "🟡 模拟器在线", "游戏未启动"
        except Exception:
            return "🟡 模拟器在线", "ADB 未连接"
        return "⚪ 空闲", ""

    def _check_ak(self):
        """检查明日方舟 (雷电默认实例 emulator-5554): 返回 (状态, 详情)"""
        # 1. 检查模拟器是否运行 (dnplayer 进程)
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process dnplayer -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count"],
                capture_output=True, text=True, timeout=10, errors="replace",
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            emu_running = r.stdout.strip().isdigit() and int(r.stdout.strip()) > 0
        except Exception:
            emu_running = False
        if not emu_running:
            return "⚪ 空闲", "模拟器未启动"
        # 2. 模拟器在跑, 检查 明日方舟 游戏 (ADB emulator-5554)
        try:
            r2 = subprocess.run(
                [r"F:\leidian\LDPlayer14\adb.exe", "-s", "emulator-5554",
                 "shell", "ps", "-A"],
                capture_output=True, text=True, timeout=10, errors="replace",
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            if r2 and "arknights" in r2.stdout:
                return "🟢 运行中", "模拟器内游戏运行中"
            return "🟡 模拟器在线", "游戏未启动"
        except Exception:
            return "🟡 模拟器在线", "ADB 未连接"
        return "⚪ 空闲", ""

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
            if state.startswith("🟢") or state.startswith("🟡"):
                var.set(f"{state}  {detail}")
            else:
                # 空闲状态: 追加上次结束时间
                last = self._get_last_end(key)
                if last:
                    var.set(f"{state}  上次结束 {last}")
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
            ts = hist.get(key)
            if not ts:
                return ""
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def _track_game_ends(self, g):
        """检测游戏从运行 → 空闲, 记录结束时间"""
        try:
            hist = self._load_game_history()
            now = datetime.now().timestamp()
            # 各游戏的运行判定 (🟢 或 🟡 算运行中)
            running_keys = {k for k, v in g.items() if k != "detail"
                            and (v.startswith("🟢") or v.startswith("🟡"))}
            # 上次已知运行状态 (保存在实例属性)
            prev = getattr(self, "_prev_running", set())
            ended = prev - running_keys  # 之前运行, 现在不运行
            for key in ended:
                hist[key] = now
            if ended:
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
            # 置底 + 全屏 (注意: 不能带 SWP_NOZORDER=0x0004, 否则置底无效!)
            user32.SetWindowPos(hwnd, 1, 0, 0, SCREEN_W, SCREEN_H,
                                0x0001 | 0x0002 | 0x0010 | 0x0020)
            self.root.update()
            self._desktop_hwnd = hwnd
            # 点击窗口后重新置底 (避免窗口被激活跑到顶层)
            def re_bottom(event=None):
                try:
                    ctypes.windll.user32.SetWindowPos(
                        self._find_hwnd(), 1, 0, 0, SCREEN_W, SCREEN_H,
                        0x0001 | 0x0002 | 0x0010 | 0x0020)
                except Exception:
                    pass
            self.root.bind("<Button-1>", lambda e: self.root.after(50, re_bottom))
            self.root.bind("<ButtonRelease-1>", lambda e: self.root.after(50, re_bottom))
            # 周期性强制置底 (每 3 秒, 防止点击/激活后浮起)
            def periodic_bottom():
                try:
                    ctypes.windll.user32.SetWindowPos(
                        self._find_hwnd(), 1, 0, 0, SCREEN_W, SCREEN_H,
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
        self.root.after(REFRESH_MS, self.auto_refresh)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    YingYingLogWindow().run()
