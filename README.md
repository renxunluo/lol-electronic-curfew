# lol-daily-limit

给管不住自己开黑的人：**前 N 把随便玩，打满之后赢一把才能收工。**

> 一个 Windows 小工具。只做三件事：读对局日志计数、看进程、结束进程。
> 不注入、不读内存、不改游戏文件、不访问客户端接口。

---

## 它是怎么工作的

每天打了几把，不靠猜，靠客户端自己留的痕迹：

```
D:\WeGameApps\英雄联盟\Game\Logs\GameLogs\
  └─ 2026-11-15T14-55-03\     ← 每进一局必建一个时间戳目录
  └─ 2026-11-15T15-13-43\
  └─ ...
```

数这个目录，就是当天真实的对局数。再配一路"守护自己看见过的游戏进程"做双保险，
**就算你把当天的日志目录删了，也不会清零**。

满额之后：

- **在峡谷里** → 绝不打断（硬断等于秒退，掉分还吃排队惩罚）
- **不在对局、客户端开着** → 留 10 秒看结算，然后关掉客户端
- **客户端没开** → 不管

---

## 功能

| 功能 | 说明 |
|---|---|
| 前 5 把不打扰 | 可改，见配置 |
| **「赢一把就睡觉」** | 满额后每打完一局弹窗问一句：赢了收工，输了放行继续 |
| 局内绝不打断 | 避免秒退 |
| 双路计数 | 删日志目录也不清零 |
| 开机自启 | 无窗口后台运行 |
| 改配置立即生效 | 守护自己重载，不用重启 |

---

## 安装

需要 **Windows 10/11** + **Python 3.8 或更高**。

1. 装 Python：<https://www.python.org/downloads/> ，安装时**务必勾选 `Add Python to PATH`**
2. 下载本仓库
3. 双击 **`install.bat`**

装好后守护会自动启动，并且以后每次开机都在。

---

## 日常使用

| 双击 | 作用 |
|---|---|
| `status.bat` | 看今天打了几把、还剩几把 |
| `install.bat` | 装开机自启 / 修好它 |
| `uninstall.bat` | 卸掉 |

命令行也行：

```bash
python lol_limit.py status
python lol_limit.py config
python lol_limit.py set win_to_sleep off
python lol_limit.py uninstall
```

---

## 配置

`%LOCALAPPDATA%\LolLimit\config.json`（守护每秒检查，改了立即生效）：

```json
{
 "limit": 5,
 "win_to_sleep": true,
 "grace_seconds": 10,
 "reset_hour": 0,
 "hard_cap": 0,
 "lol_root": ""
}
```

| 键 | 含义 |
|---|---|
| `limit` | 前几把不拦 |
| `win_to_sleep` | 「赢一把就睡觉」开关。`false` = 满额直接收工，不问输赢 |
| `grace_seconds` | 局末给几秒看结算再关 |
| `reset_hour` | 几点换日（熬夜党可以设 4） |
| `hard_cap` | 绝对上限，`0` = 不设限。设 10 就是再输也不许超 10 把 |
| `lol_root` | 游戏安装目录，留空自动探测 |

也可以命令行改：

```bash
python lol_limit.py set limit 3
python lol_limit.py set win_to_sleep off
python lol_limit.py set reset_hour 4
```

---

## 已知限制

**国服客户端的本地接口被反作弊锁死**：`lockfile` 恒为 0 字节、进程命令行读不到、
LCU 端口认得出但拿不到令牌。所以没法自动判断输赢，只能在打满额度后弹窗问你一句。

不想被问的话，把 `win_to_sleep` 关掉就是纯"满额即收工"模式。

---

## 它挡不住什么

- **任务管理器结束守护进程** —— 结束之后当天就不数也不拦了。这是普通用户权限下的极限。
  补救很便宜：双击 `status.bat`，它发现守护没在跑会自动拉起来。
- 直接删掉整个目录。

---

## 免责声明

- 本项目为**非官方**工具，与腾讯、拳头游戏（Riot Games）无任何关联
- 不修改游戏文件、不读取游戏内存、不注入进程、不访问游戏客户端接口
- 只在本机读取日志目录名、枚举进程、结束进程
- 仅供个人自律使用，使用风险自负

---

## License

[MIT](LICENSE)
