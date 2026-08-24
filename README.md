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

## 🔧 依赖
- Python 3.11 + tkinter(内置)
- Pillow / matplotlib(折线图)
- Hermes agent 的 state.db 和 webhook 服务(聊天功能)

## 📁 目录规划(未来)
```
yingying-home/
├── desktop_console.py   # 桌面操作台 (当前)
├── scripts/             # 荧荧的小脚本
├── skills/              # 荧荧的技能笔记
└── README.md
```

*Made with 💗 by 荧荧 for 主人*
