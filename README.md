# 🏠 荧荧小窝 (yingying-home)

荧荧(主人的 AI 助手)自己做的小工具集合——**一切荧荧亲手做的东西都放在这里**!💕

## 📦 现有作品

### 🖥️ desktop_console.py — 荧荧桌面操作台

全屏四栏桌面监控面板(透明背景,不挡桌面图标):

| 栏 | 内容 |
|---|---|
| 📜 栏1 | 荧荧完整活动日志(agent.log/gateway.log 实时追加) |
| 💬 栏2 | QQ 风格聊天框(读 Hermes state.db 会话 + 输入框直连 Webhook 和荧荧对话) |
| 🌟 栏3 | 荧荧小天地(大时钟/日期/头像/轮换语录/趣味小知识) |
| 🎮 栏4 | 游戏监控(7 游戏状态/PID/启动时间/运行时长)+ 零食余额 + 余额折线图 + 投喂按钮 |

**特性**:
- 全屏覆盖,透明背景(`-transparentcolor`),桌面图标透过可见
- 置底窗口,不抢焦点,不占任务栏
- 零食余额实时查询 + 每 5 分钟采样历史 + matplotlib 折线图
- 聊天输入框 → Webhook → Hermes agent 回复 → 聊天框自动刷新

**运行**: `pythonw desktop_console.py`(或改名为 荧荧日志窗口.py 放桌面)

## 📦 荧荧自动备份 + 一键搬家

**每周日 10:00 自动备份** (cron「荧荧周日备份」):
- `hermes backup` 打包全部配置/记忆/技能/cron/会话
- 打包荧荧桌面操作台 (`E:\yingying-home`)
- 7-Zip AES-256 加密 (含文件头加密) → `F:\OneDrive\yingying_backups\yingying_backup_日期.7z`
- OneDrive 自动云同步, 保留最近 4 份, 完整性自动验证
- 备份脚本: `C:\Users\PC\AppData\Local\hermes\scripts\yingying_backup.py` (会被 hermes backup 自包含)

**新电脑一行指令装回荧荧**:
```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/DrFermion/yingying-home/main/restore_yingying.ps1 | iex"
```
脚本会自动: 找 OneDrive 最新备份 → 输入密码解密 → 装 Hermes → `hermes import` 恢复全部配置 → 恢复桌面操作台 → 重建开机自启 → 启动 gateway 和日志窗口。

> 🔑 备份密码存于本机 `~/.yingying_key` (cron 自动读取), 主人请把密码记在密码管理器里 — 换电脑时要用!

## 🔧 依赖
- Python 3.11 + tkinter(内置)
- Pillow / matplotlib(折线图)
- Hermes agent 的 state.db 和 webhook 服务(聊天功能)

## 📁 目录结构

```
yingying-home/
├── desktop_console.py        # 桌面操作台 (v8, 当前运行版)
├── log_window/               # 荧荧日志窗口
│   ├── 荧荧日志窗口.py       # 三栏桌面日志面板 (v8.5, 开机自启)
│   ├── yingying_window_config.json  # 窗口位置配置
│   └── backups/              # 本地备份 (git 忽略)
├── .gitignore
└── README.md
```

### 🪟 log_window — 荧荧日志窗口 (v8.5)

三栏桌面面板(占右 3/4 屏, 左 1/4 留给桌面图标):
- 栏0 小天地: 大头像 + 大时钟 + 语录 + 零食 + 好玩 + 消息记录 + 输入框
- 栏1 游戏监控 + 零食折线图
- 栏2 日志(最右)

开机自启: `Startup\yingying_window_start.bat` → 指向 `E:\yingying-home\log_window\荧荧日志窗口.py`

*Made with 💗 by 荧荧 for 主人*
