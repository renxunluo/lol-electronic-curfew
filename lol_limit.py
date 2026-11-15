# -*- coding: utf-8 -*-
"""
每日限额守护 —— 给管不住自己开黑的你

规则（默认）：
  前 5 把随便打，不打扰。
  满 5 把之后，每打完一局弹窗问一句：
    赢了 -> 今天收工，关客户端
    输了 -> 放行，接着打，直到赢一把为止
  自然日 0 点自动重置。

计数依据（双保险，取大者）：
  1) Game\\Logs\\GameLogs\\ 下当天新建的时间戳目录（每进一局必建一个）
  2) 守护自己"看见"过的 League of Legends.exe 启动记录（state.json）
     —— 防止删目录把当天清零

这个脚本只做三件事：读目录名、看进程、结束进程。
不注入、不读内存、不改游戏文件、不访问客户端接口、不碰对局内数据。

快速开始：
  双击 install.bat   —— 装开机自启并启动守护
  双击 status.bat    —— 看今天打了几把
  双击 uninstall.bat —— 卸掉

命令行：
  python lol_limit.py install    装开机自启 + 启动
  python lol_limit.py status     看进度（会顺手拉起守护）
  python lol_limit.py config     看/改配置
  python lol_limit.py set win_to_sleep off
  python lol_limit.py uninstall  卸载
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import json
import os
import subprocess
import sys
import threading
import time
import traceback

# ---------------------------------------------------------------- 可调参数
# 这些是默认值；实际以 %LOCALAPPDATA%\LolLimit\config.json 为准。
LIMIT = 5                      # 前几把不拦
WIN_TO_SLEEP = True            # 选项「赢一把就睡觉」：满额后赢一把才关，输了可继续
HARD_CAP = 0                   # 绝对上限（0 = 不设限）
RESET_HOUR = 0                 # 几点换日（0 = 自然日 0 点）
GRACE_SECONDS = 10             # 局末留几秒看结算，再关客户端
POLL_PROC = 1.0                # 主循环间隔（秒）
ASK_WAIT = 90                  # 弹窗最多等多久，超时按"赢了"处理
LOG_MAX_BYTES = 2 * 1024 * 1024

LOL_ROOT = ""                  # 运行时由 find_lol_root() 填


def find_lol_root():
    """找英雄联盟安装目录：配置 > 注册表 > 常见路径。"""
    p = read_config().get("lol_root") or ""
    if p and os.path.isdir(p):
        return p
    try:
        import winreg
        for key in (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\英雄联盟",
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\英雄联盟",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\League of Legends",
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\League of Legends"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                    v, _ = winreg.QueryValueEx(k, "InstallSource")
                    if v and os.path.isdir(v):
                        return v
            except OSError:
                continue
    except Exception:
        pass
    for p in (r"D:\WeGameApps\英雄联盟", r"C:\WeGameApps\英雄联盟",
              r"D:\Riot Games\League of Legends", r"C:\Riot Games\League of Legends",
              r"E:\WeGameApps\英雄联盟"):
        if os.path.isdir(p):
            return p
    return r"D:\WeGameApps\英雄联盟"


def game_log_dir():
    return os.path.join(LOL_ROOT, r"Game\Logs\GameLogs")

# 满额后要关掉的客户端进程（只认 LOL 目录下的，不碰 WeGame 本体）
CLIENT_PROC_NAMES = {
    "leagueclient.exe",
    "leagueclientux.exe",
    "leagueclientuxrender.exe",
    "client.exe",              # TCLS\client.exe 国服登录器
    "launcher.exe",            # WeGameLauncher\launcher.exe
}
# 这些名字太通用（别的软件也有 client.exe / launcher.exe），必须落在 LOL 目录里
COMMON_NAMES = {"client.exe", "launcher.exe"}
GAME_PROC_NAME = "league of legends.exe"

DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LolLimit")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LOG_FILE = os.path.join(DATA_DIR, "guard.log")
PID_FILE = os.path.join(DATA_DIR, "guard.pid")
MUTEX_NAME = "Local\\LolLimitGuard_v1"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "LolLimitGuard"

# ---------------------------------------------------------------- Win32 底层
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
k32.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
k32.CreateMutexW.restype = wt.HANDLE
k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
k32.GetStdHandle.restype = wt.HANDLE
k32.GetStdHandle.argtypes = [wt.DWORD]
k32.GetConsoleMode.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.WriteConsoleW.restype = wt.BOOL
k32.WriteConsoleW.argtypes = [wt.HANDLE, wt.LPCWSTR, wt.DWORD,
                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p]


class Proc(object):
    __slots__ = ("pid", "name", "path", "start")

    def __init__(self, pid, name, path, start):
        self.pid = pid
        self.name = name
        self.path = path
        self.start = start

    @property
    def is_game(self):
        return self.name == GAME_PROC_NAME

    @property
    def in_lol_dir(self):
        return bool(self.path) and self.path.lower().startswith(LOL_ROOT.lower())

    @property
    def is_client(self):
        if self.name not in CLIENT_PROC_NAMES:
            return False
        if self.name in COMMON_NAMES:          # 名字太通用，必须落在 LOL 目录里
            return self.in_lol_dir
        return True


def _proc_path(h):
    buf = ctypes.create_unicode_buffer(32768)
    size = wt.DWORD(len(buf))
    if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
        return buf.value
    return ""


def _proc_start(h):
    ct, et, kt, ut = (wt.FILETIME() for _ in range(4))
    if k32.GetProcessTimes(h, ctypes.byref(ct), ctypes.byref(et),
                           ctypes.byref(kt), ctypes.byref(ut)):
        return (ct.dwHighDateTime << 32) | ct.dwLowDateTime
    return 0


def list_procs():
    """列出当前所有进程（pid / 名字 / 路径 / 启动时间）。"""
    out = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            pid = e.th32ProcessID
            if pid:
                name = e.szExeFile.lower()
                h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if h:
                    try:
                        out.append(Proc(pid, name, _proc_path(h), _proc_start(h)))
                    finally:
                        k32.CloseHandle(h)
                else:
                    out.append(Proc(pid, name, "", 0))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def kill_pid(pid):
    """先 TerminateProcess，失败再用 taskkill 兜底。返回是否成功。"""
    h = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if h:
        try:
            if k32.TerminateProcess(h, 1):
                return True
        finally:
            k32.CloseHandle(h)
    try:
        r = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def pid_alive(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wt.DWORD()
        if k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return code.value == 259        # STILL_ACTIVE
        return False
    finally:
        k32.CloseHandle(h)


# ---------------------------------------------------------------- 工具
def log(msg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            if os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
                os.replace(LOG_FILE, LOG_FILE + ".1")
        except OSError:
            pass
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def day_key(dt=None):
    dt = dt or datetime.datetime.now()
    return (dt - datetime.timedelta(hours=RESET_HOUR)).date().isoformat()


def count_game_logs(target_day):
    """GameLogs 下当天的新目录数 —— 每进一局必建一个。"""
    n = 0
    try:
        with os.scandir(game_log_dir()) as it:
            for e in it:
                name = e.name
                if len(name) < 10 or name[4] != "-" or name[7] != "-":
                    continue
                if RESET_HOUR == 0:
                    if name[:10] == target_day:
                        n += 1
                else:
                    try:
                        dt = datetime.datetime.strptime(name[:19], "%Y-%m-%dT%H-%M-%S")
                    except ValueError:
                        continue
                    if day_key(dt) == target_day:
                        n += 1
    except OSError:
        pass
    return n


def fresh_state(prev=None):
    prev = prev or {}
    return {
        "day": day_key(),
        "seen": [],                       # 当天看见过的游戏进程 "pid:starttime"
        "giveups": int(prev.get("giveups", 0)),   # 只是"求过解锁"的次数
        "grace_until": 0.0,
        "last_kill": 0.0,
        "last_win": None,                 # True=赢了 False=输了 None=还不知道
        "awaiting": False,                # 刚打完，正在等本人确认
        "await_deadline": 0.0,
        "was_in_game": False,
    }


def load_state():
    st = fresh_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        st.update(data)
        st["day"] = st.get("day") or day_key()
        st["seen"] = list(st.get("seen") or [])
        st["giveups"] = int(st.get("giveups", 0))
        st.pop("unlocked", None)          # 老版本留下的解锁标记一律作废
    except Exception:
        pass
    return st


def save_state(st):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log("save_state failed: %r" % (e,))


# ---------------------------------------------------------------- 配置
_DEFAULTS = {
    "limit": 5,
    "win_to_sleep": True,
    "grace_seconds": 10,
    "reset_hour": 0,
    "hard_cap": 0,
    "lol_root": "",          # 留空 = 自动探测
}
_CFG_MTIME = {"v": -1.0}


def read_config():
    cfg = dict(_DEFAULTS)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in _DEFAULTS:
            if k in data:
                cfg[k] = data[k]
    except Exception:
        pass
    return cfg


def write_config(cfg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CONFIG_FILE)
    except Exception as e:
        log("write_config failed: %r" % (e,))


def apply_config(cfg):
    """把配置套到全局变量上；返回是否真的有变化。"""
    global LIMIT, WIN_TO_SLEEP, GRACE_SECONDS, RESET_HOUR, HARD_CAP, LOL_ROOT
    old = (LIMIT, WIN_TO_SLEEP, GRACE_SECONDS, RESET_HOUR, HARD_CAP, LOL_ROOT)
    LIMIT = max(1, int(cfg.get("limit", 5)))
    WIN_TO_SLEEP = bool(cfg.get("win_to_sleep", True))
    GRACE_SECONDS = max(0, int(cfg.get("grace_seconds", 10)))
    RESET_HOUR = int(cfg.get("reset_hour", 0)) % 24
    HARD_CAP = max(0, int(cfg.get("hard_cap", 0)))
    root = cfg.get("lol_root") or ""
    if root and os.path.isdir(root):
        LOL_ROOT = root
    elif not LOL_ROOT:
        LOL_ROOT = find_lol_root()
    return old != (LIMIT, WIN_TO_SLEEP, GRACE_SECONDS, RESET_HOUR, HARD_CAP, LOL_ROOT)


def ensure_config():
    if not os.path.exists(CONFIG_FILE):
        write_config(dict(_DEFAULTS))


def reload_if_changed():
    """配置文件一改，守护下一轮自动生效，不用重启。"""
    try:
        m = os.path.getmtime(CONFIG_FILE)
    except OSError:
        m = -1.0
    if m != _CFG_MTIME["v"]:
        _CFG_MTIME["v"] = m
        if apply_config(read_config()):
            log("配置: 前%d把不拦 / 赢一把就睡觉=%s / 宽限%d秒 / 换日%02d:00"
                % (LIMIT, WIN_TO_SLEEP, GRACE_SECONDS, RESET_HOUR))


_POPUP_TS = {}


def popup(text, title="英雄联盟 · 每日限额"):
    """非阻塞提示弹窗，同类消息 60 秒内只弹一次。"""
    now = time.time()
    key = text[:40]
    if now - _POPUP_TS.get(key, 0) < 60:
        return
    _POPUP_TS[key] = now

    def _show():
        try:
            ctypes.windll.user32.MessageBoxW(
                None, text, title, 0x00000040 | 0x00040000)   # ICONINFO | TOPMOST
        except Exception:
            pass
    threading.Thread(target=_show, daemon=True).start()


# --------------------------------------------- 战后确认：赢了吗？
# 国服客户端把本地接口锁死了（lockfile 恒为 0 字节、命令行读不到），
# 读不到自动胜负，只能在打满额度后问一句。
_ASK = {"pending": False, "result": None}


def ask_result_async():
    """非阻塞弹窗：赢了吗？60 秒不点按"赢了"处理（保守）。"""
    if _ASK["pending"]:
        return
    _ASK["pending"] = True
    _ASK["result"] = None

    def _run():
        try:
            r = ctypes.windll.user32.MessageBoxTimeoutW(
                0,
                "刚才这把赢了吗？\n\n"
                "【是】今天收工，关掉客户端\n"
                "【否】放你继续打，直到赢一把\n\n"
                "60 秒不选，按【是】处理。",
                "英雄联盟 · 战果确认",
                0x00000004 | 0x00000040 | 0x00040000,   # YESNO | ICONQUESTION | TOPMOST
                0, 60000)
            _ASK["result"] = (r == 6)                   # IDYES
        except Exception:
            _ASK["result"] = True
        finally:
            _ASK["pending"] = False

    threading.Thread(target=_run, daemon=True).start()


def evaluate(st):
    """返回 (count, finished, games, clients, hard, seen_n)。"""
    today = day_key()
    procs = list_procs()
    lol = [p for p in procs if p.is_game or p.is_client]
    games = [p for p in lol if p.is_game]
    clients = [p for p in lol if p.is_client]

    changed = False
    for p in games:                       # 记录看见过的对局启动，防删日志清零
        k = "%d:%d" % (p.pid, p.start)
        if k not in st["seen"]:
            st["seen"].append(k)
            changed = True
    if changed:
        save_state(st)

    hard = count_game_logs(today)
    count = max(hard, len(st["seen"]))
    finished = count - (1 if games else 0)      # 正在跑的那局算还没结束
    return count, max(finished, 0), games, clients, hard, len(st["seen"])


def shutdown_clients(clients, reason):
    killed = []
    for p in clients:
        if kill_pid(p.pid):
            killed.append(p.name)
        time.sleep(0.05)
    if killed:
        log("拦截(%s): 关闭客户端 %s" % (reason, ",".join(killed)))
    return killed


# ---------------------------------------------------------------- 守护主循环
def mutex_acquire():
    h = k32.CreateMutexW(None, False, MUTEX_NAME)
    if not h:
        return None
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        k32.CloseHandle(h)
        return None
    return h


def run_daemon():
    mh = mutex_acquire()
    if mh is None:
        log("已有守护在运行，本进程退出")
        return 0
    ensure_config()
    reload_if_changed()
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    st = load_state()
    if st["day"] != day_key():
        st = fresh_state(st)
        save_state(st)

    log("守护启动 pid=%d 前%d把不拦 换日=%02d:00 今日=%s"
        % (os.getpid(), LIMIT, RESET_HOUR, day_key()))

    while True:
        try:
            tick(st)
        except Exception:
            log("循环异常:\n" + traceback.format_exc())
        time.sleep(POLL_PROC)


def tick(st):
    """单次判断 + 执行拦截。st 可变字典，跨天时原地重置。"""
    reload_if_changed()                   # 配置改了这里自动生效
    if st.get("day") != day_key():        # 跨天清零
        n = fresh_state(st)
        st.clear()
        st.update(n)
        save_state(st)
        log("换日，额度重置为 %d 把" % LIMIT)

    count, finished, games, clients, hard, seen_n = evaluate(st)

    now = time.time()
    in_game = bool(games)

    # 进/出对局的状态机：出局后要问一句结果
    was = bool(st.get("was_in_game"))
    if in_game and not was:
        st["was_in_game"] = True
        save_state(st)
    elif (not in_game) and was:
        st["was_in_game"] = False
        st["awaiting"] = True
        st["await_deadline"] = now + ASK_WAIT
        save_state(st)
        log("一局结束，等确认胜负（最多 %d 秒）" % ASK_WAIT)
        if WIN_TO_SLEEP and count >= LIMIT:
            ask_result_async()            # 只有打满额度才打扰

    if in_game:
        # 局内一律不打断：硬断算秒退，掉分还吃排队惩罚
        return

    if count < LIMIT:                     # 前 N 把不拦
        if st.get("grace_until"):
            st["grace_until"] = 0.0
            save_state(st)
        return

    # ---------------- 满额之后 ----------------
    if not (HARD_CAP and count >= HARD_CAP):   # 兜底上限优先
        if WIN_TO_SLEEP:                       # 选项「赢一把就睡觉」
            if st.get("awaiting"):
                if _ASK["result"] is not None:          # 本人点过弹窗
                    st["last_win"] = _ASK["result"]
                    _ASK["result"] = None
                    st["awaiting"] = False
                    save_state(st)
                    log("人工确认 win=%s" % st["last_win"])
                elif now < (st.get("await_deadline") or 0):
                    return                              # 还在等，先别动
                else:
                    st["last_win"] = True               # 超时按赢了处理（保守）
                    st["awaiting"] = False
                    save_state(st)
                    log("超时没确认，按赢了处理")
            if st.get("last_win") is False:
                # 输了 -> 放行，让它再打一把，直到赢为止
                if st.get("grace_until"):
                    st["grace_until"] = 0.0
                    save_state(st)
                return

    if not clients:
        return

    # 给 25 秒看结算，然后清场
    if not st.get("grace_until"):
        st["grace_until"] = now + GRACE_SECONDS
        save_state(st)
        return
    if now >= st["grace_until"]:
        if now - (st.get("last_kill") or 0) < 20:      # 被反复拉起时别死循环
            return
        if shutdown_clients(clients, "满额"):
            st["last_kill"] = now
            save_state(st)
            log("拦截成功，最近一局赢=%s" % st.get("last_win"))
            popup("赢了，今天到这里。\n\n英雄联盟已关闭，明天 0 点自动恢复。")


# ---------------------------------------------------------------- 命令
def _daemon_pid():
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
    except Exception:
        return None
    return pid if pid_alive(pid) else None


def _autostart_value():
    """读 HKCU Run 里的自启命令，没有就返回 None。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, RUN_VALUE)
        return v if v else None
    except Exception:
        return None


def _pythonw():
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return cand if os.path.exists(cand) else sys.executable


def _start_daemon():
    """拉起守护，返回新 pid；失败返回 None。"""
    try:
        os.remove(PID_FILE)          # 清掉旧 pid，免得把死进程当成新的
    except OSError:
        pass
    subprocess.Popen([_pythonw(), os.path.abspath(__file__), "run"],
                     creationflags=CREATE_NO_WINDOW, close_fds=True)
    for _ in range(40):
        time.sleep(0.2)
        p = _daemon_pid()
        if p:
            return p
    return None


def cmd_status():
    st = load_state()
    if st["day"] != day_key():
        st = fresh_state(st)
    count, finished, games, clients, hard, seen_n = evaluate(st)

    print("=" * 50)
    print("  英雄联盟每日限额   %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 50)
    print("  规则            前 %d 把不拦 + %s"
          % (LIMIT, "「赢一把就睡觉」" if WIN_TO_SLEEP else "满额即收工"))
    print("  今日已开        %d 把" % count)
    print("  其中已结束      %d 把" % finished)
    if count < LIMIT:
        print("  还能再开        %d 把" % (LIMIT - count))
    else:
        win = st.get("last_win")
        if win is True:
            print("  额度状态        ★ 赢了，该收工（再开会被关）")
        elif win is False:
            print("  额度状态        最后一把输了，可以继续（直到赢）")
        else:
            print("  额度状态        还不知道输赢，下一局打完会问你")
    if games:
        print("  当前状态        正在对局中（这是第 %d 把）" % count)
    elif clients:
        print("  当前状态        客户端开着，还没进对局")
    else:
        print("  当前状态        客户端没开")
    print("-" * 50)
    print("  计数来源        对局日志 %d 个 / 守护记录 %d 个（取大）" % (hard, seen_n))
    print("  游戏目录        %s" % (LOL_ROOT or "（没找到）"))
    if st.get("giveups"):
        print("  求过解锁        %d 次，一次也没给" % st["giveups"])
    print("-" * 50)
    print("  最近 7 天：")
    for i in range(6, -1, -1):
        d = (datetime.datetime.now() - datetime.timedelta(days=i)).date().isoformat()
        n = count_game_logs(d)
        bar = "#" * min(n, 30)
        flag = "   <- 今天" if d == day_key() else ""
        print("    %s  %2d 把  %s%s" % (d, n, bar, flag))
    print("-" * 50)
    run = _daemon_pid()
    if run:
        print("  守护进程        运行中 (pid %s)" % run)
    else:
        print("  守护进程        没在跑，正在自动拉起 ...")
        run = _start_daemon()
        print("  守护进程        %s" % ("已自动启动 (pid %s)" % run if run else "拉起失败"))
    print("-" * 50)
    auto = _autostart_value()
    if auto:
        ok = os.path.exists(os.path.abspath(__file__))
        print("  开机自启        已配置%s" % ("，目标文件正常" if ok else "，但脚本路径不见了"))
    else:
        print("  开机自启        未配置（双击 安装守护 图标）")
    print("=" * 50)


def cmd_once():
    st = load_state()
    if st["day"] != day_key():
        st = fresh_state(st)
    count, finished, games, clients, hard, seen_n = evaluate(st)
    print("今日已开 %d / 已结束 %d / 前 %d 把不拦 / 还能开 %d"
          % (count, finished, LIMIT, max(0, LIMIT - count)))
    print("对局日志 %d 个，守护记录 %d 个" % (hard, seen_n))
    print("游戏进程 %d 个，客户端进程 %d 个" % (len(games), len(clients)))
    if count < LIMIT:
        print("结论: 还没到额度 -> 放行")
    elif st.get("last_win") is False:
        print("结论: 上一把输了 -> 放行继续")
    else:
        print("结论: 额度已用完 -> 启动会被拦截")


def cmd_install():
    import winreg
    script = os.path.abspath(__file__)
    cmd = '"%s" "%s" run' % (_pythonw(), script)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
    log("已写入开机自启: %s" % cmd)
    print("已写入开机自启: HKCU\\...\\Run\\%s" % RUN_VALUE)

    if _daemon_pid():
        print("守护已经在跑了 (pid %s)，不用重复启动。" % _daemon_pid())
        return
    pid = _start_daemon()
    print("守护已启动 (pid %s)" % pid if pid else "守护好像没起来，检查日志 %s" % LOG_FILE)


def cmd_uninstall():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_VALUE)
        print("已删除开机自启")
    except FileNotFoundError:
        print("开机自启本来就没装")
    pid = _daemon_pid()
    if pid:
        kill_pid(pid)
        print("已停掉守护 (pid %s)" % pid)
    else:
        print("守护没在运行")


def cmd_restart():
    pid = _daemon_pid()
    if pid:
        kill_pid(pid)
        print("已停掉旧守护 (pid %s)" % pid)
    time.sleep(1)
    new = _start_daemon()
    print("新守护已启动 (pid %s)，改动已生效" % new if new
          else "守护没起来，检查日志 %s" % LOG_FILE)


def cmd_config():
    cfg = read_config()
    print("=" * 54)
    print("  当前配置   %s" % CONFIG_FILE)
    print("=" * 54)
    print("  limit         前几把不拦          %d 把" % cfg["limit"])
    print("  win_to_sleep  「赢一把就睡觉」    %s" % ("开（赢了收工，输了可继续）"
                                                    if cfg["win_to_sleep"] else "关（满额直接收工）"))
    print("  grace_seconds 局末宽限            %d 秒" % cfg["grace_seconds"])
    print("  reset_hour    几点换日            %02d:00" % (cfg["reset_hour"] % 24))
    print("  hard_cap      绝对上限            %s" % (cfg["hard_cap"] or "不设限"))
    print("=" * 54)
    print("  改法：")
    print("    python lol_limit.py set win_to_sleep off")
    print("    python lol_limit.py set limit 3")
    print("    python lol_limit.py set grace_seconds 10")
    print("  （守护每秒检查配置文件，改完立即生效，不用重启）")


def cmd_set(args):
    if len(args) < 2:
        print("用法：set <键> <值>")
        print("  键：limit / win_to_sleep / grace_seconds / reset_hour / hard_cap")
        print("  例：set win_to_sleep off")
        return
    key, raw = args[0].lower(), args[1].lower()
    if key not in _DEFAULTS:
        print("不认识的键：%s（可选：%s）" % (key, " / ".join(_DEFAULTS)))
        return
    cfg = read_config()
    if key == "win_to_sleep":
        cfg[key] = raw in ("on", "true", "1", "yes", "开", "是")
    else:
        try:
            cfg[key] = int(raw)
        except ValueError:
            print("「%s」要填数字" % key)
            return
    write_config(cfg)
    apply_config(cfg)
    reload_if_changed()
    shown = ("开" if cfg[key] else "关") if key == "win_to_sleep" else cfg[key]
    print("已更新：%s = %s" % (key, shown))
    if key == "win_to_sleep":
        print("「赢一把就睡觉」现在是%s状态。" % ("开" if cfg[key] else "关"))


def cmd_giveup():
    """不解锁。只问那两个问题，并把"求过几次"记下来。"""
    st = load_state()
    if st["day"] != day_key():
        st = fresh_state(st)
    st["giveups"] = int(st.get("giveups", 0)) + 1
    save_state(st)
    log("用户请求解锁，第 %d 次，已拒绝" % st["giveups"])

    print("")
    print("  不解锁。先回答我两个问题：")
    print("")
    print("    1. 今天搞学习了吗？")
    print("    2. 做了有意义的事情了吗？")
    print("")
    print("  答完再来。但你要清楚：这个脚本不会给你解锁，")
    print("  问多少次都一样。今天这一把的日子，过了就没了。")
    print("")
    print("  （这是第 %d 次问。每次都记着。）" % st["giveups"])


class _ConsoleStream(object):
    """直接调 WriteConsoleW 输出。

    这台机器的 Python 是 UTF-8 模式（sys.stdout.encoding == 'utf-8'），
    而控制台代码页是 936，普通 print 写出去的 UTF-8 字节会被按 GBK 解码，
    中文就成了方块。WriteConsoleW 走的是 Unicode 通道，跟代码页无关。
    """

    def __init__(self, handle):
        self._h = handle

    def write(self, s):
        if not s:
            return 0
        n = wt.DWORD(0)
        buf = ctypes.create_unicode_buffer(s, len(s))
        k32.WriteConsoleW(self._h, buf, len(s), ctypes.byref(n), None)
        return n.value

    def flush(self):
        return None

    def isatty(self):
        return True

    @property
    def encoding(self):
        return "utf-8"


def _fix_console_encoding():
    """连真控制台就用 Unicode 通道；被重定向/管道时统一 UTF-8。"""
    try:
        h = k32.GetStdHandle(-11)
        mode = wt.DWORD(0)
        if h and h != INVALID_HANDLE_VALUE and \
                ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            sys.stdout = _ConsoleStream(h)
            return
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            try:
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    except Exception:
        pass


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    _fix_console_encoding()
    ensure_config()
    reload_if_changed()
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    if cmd == "run":
        return run_daemon()
    if cmd == "status":
        cmd_status()
    elif cmd == "once":
        cmd_once()
    elif cmd == "install":
        cmd_install()
    elif cmd == "uninstall":
        cmd_uninstall()
    elif cmd == "restart":
        cmd_restart()
    elif cmd == "giveup":
        cmd_giveup()
    elif cmd == "config":
        cmd_config()
    elif cmd == "set":
        cmd_set(sys.argv[2:])
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
