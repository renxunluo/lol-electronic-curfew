# -*- coding: utf-8 -*-
"""
电子宵禁 · 界面

起一个本地小服务，用 Edge 的「应用窗口」模式打开，看起来就是个独立小软件。
只读状态，不改任何东西（守护进程该管的还是它管）。

用法：
    python lol_limit_ui.py         # 打开界面
"""
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lol_limit as L  # noqa: E402

PORT = 8765

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>英雄联盟电子宵禁</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    background: #14141c;
    color: #e8e8f0;
    padding: 22px 20px 18px;
    user-select: none;
    max-width: 470px;
    margin: 0 auto;
    min-height: 100vh;
  }
  .head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 18px; }
  .head h1 { font-size: 17px; font-weight: 600; letter-spacing: .5px; }
  .head .date { font-size: 12px; color: #6b6b80; }
  .hero { background: #1d1d2a; border-radius: 14px; padding: 20px; margin-bottom: 14px; }
  .num { font-size: 54px; font-weight: 700; line-height: 1; letter-spacing: -2px; }
  .num small { font-size: 17px; font-weight: 400; color: #7c7c95; margin-left: 8px; letter-spacing: 0; }
  .cap { font-size: 12px; color: #7c7c95; margin-top: 8px; }
  .slots { display: flex; gap: 7px; margin-top: 16px; }
  .slot { flex: 1; height: 7px; border-radius: 4px; background: #2c2c3d; transition: .3s; }
  .slot.used { background: #5b8cff; }
  .slot.over { background: #ff5b6e; }
  .state { margin-top: 15px; font-size: 13px; padding: 9px 12px; border-radius: 9px;
           background: #23233a; color: #9d9db5; }
  .state.ok { color: #6ee7a8; }
  .state.warn { color: #ffb86b; }
  .state.stop { color: #ff7b8a; }
  .panel { background: #1d1d2a; border-radius: 14px; padding: 16px 18px; margin-bottom: 14px; }
  .row { display: flex; align-items: center; justify-content: space-between; }
  .row + .row { margin-top: 13px; }
  .label { font-size: 13px; color: #9d9db5; }
  .switch { position: relative; width: 44px; height: 24px; background: #33334a; border-radius: 12px;
            cursor: pointer; transition: .25s; flex: none; }
  .switch.on { background: #5b8cff; }
  .switch::after { content: ""; position: absolute; width: 18px; height: 18px; background: #fff;
                   border-radius: 50%; top: 3px; left: 3px; transition: .25s; }
  .switch.on::after { left: 23px; }
  .stepper { display: flex; align-items: center; gap: 10px; }
  .step { width: 26px; height: 26px; border-radius: 8px; border: none; background: #2c2c3d;
          color: #e8e8f0; font-size: 15px; line-height: 1; cursor: pointer; transition: .15s; }
  .step:hover { background: #3a3a55; }
  .step:active { transform: scale(.92); }
  #limitnum { font-size: 15px; font-weight: 600; min-width: 22px; text-align: center; }
  .bars { display: flex; align-items: flex-end; gap: 6px; height: 62px; margin-top: 10px; }
  .bar-wrap { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 5px; }
  .bar { width: 100%; background: #3a3a55; border-radius: 4px 4px 0 0; min-height: 2px; transition: .3s; }
  .bar.today { background: #5b8cff; }
  .bar-day { font-size: 10px; color: #5f5f78; }
  .foot { font-size: 11px; color: #55556b; text-align: center; margin-top: 6px; line-height: 1.7; }
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; }
  .dot.on { background: #6ee7a8; }
  .dot.off { background: #ff5b6e; }
</style>
</head>
<body>
  <div class="head">
    <h1>英雄联盟电子宵禁</h1>
    <span class="date" id="date"></span>
  </div>

  <div class="hero">
    <div class="num" id="count">-<small id="cap">/ 5 把</small></div>
    <div class="cap" id="left">还能再开 - 把</div>
    <div class="slots" id="slots"></div>
    <div class="state" id="state">读取中…</div>
  </div>

  <div class="panel">
    <div class="row">
      <span class="label">每日额度</span>
      <div class="stepper">
        <button class="step" onclick="setLimit(-1)">−</button>
        <span id="limitnum">5</span>
        <button class="step" onclick="setLimit(1)">+</button>
      </div>
    </div>
    <div class="row">
      <span class="label">赢一把就睡觉</span>
      <div class="switch" id="switch" onclick="toggle()"></div>
    </div>
    <div class="row">
      <span class="label">最近一局</span>
      <span class="label" id="lastwin" style="color:#e8e8f0">-</span>
    </div>
  </div>

  <div class="panel">
    <div class="label" style="margin-bottom:2px">最近 7 天</div>
    <div class="bars" id="bars"></div>
    <div class="bars" style="height:auto;margin-top:6px">
      <div id="days" style="display:flex;width:100%;gap:6px"></div>
    </div>
  </div>

  <div class="panel" style="background:#191925">
    <div class="label" style="color:#7c7c95">想换个额度？</div>
    <div style="font-size:12px;color:#6b6b80;margin-top:7px;line-height:1.8">
      改 <span style="color:#9d9db5">%LOCALAPPDATA%\\LolLimit\\config.json</span>，或者命令行：<br>
      <span style="color:#8f8fa8">python lol_limit.py set limit 3</span><br>
      <span style="color:#8f8fa8">python lol_limit.py set reset_hour 4</span>
    </div>
  </div>

  <div class="foot">
    <div><span class="dot off" id="dot"></span><span id="guard">守护进程：-</span></div>
    <div id="rule" style="margin-top:3px"></div>
  </div>

<script>
let cfg = {};
async function load() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    render(d);
  } catch (e) { /* 服务关了 */ }
}
function render(d) {
  cfg = d.config || {};
  document.getElementById('date').textContent = d.date;
  document.getElementById('count').firstChild.nodeValue = d.count;
  document.getElementById('cap').textContent = '/ ' + d.limit + ' 把';

  const left = Math.max(0, d.limit - Math.max(0, d.count - (d.in_game ? 1 : 0)));
  document.getElementById('left').textContent =
      d.count < d.limit ? ('还能再开 ' + (d.limit - d.count) + ' 把') : '额度已用完';

  let slots = '';
  for (let i = 0; i < d.limit; i++) {
    let cls = 'slot';
    if (i < d.count) cls += (d.count > d.limit ? ' over' : ' used');
    slots += '<div class="' + cls + '"></div>';
  }
  document.getElementById('slots').innerHTML = slots;

  const st = document.getElementById('state');
  if (d.in_game) { st.textContent = '局内：正在打第 ' + d.count + ' 把，不会打断你'; st.className = 'state ok'; }
  else if (d.count < d.limit) { st.textContent = '还有额度，随便玩'; st.className = 'state ok'; }
  else if (d.last_win === false) { st.textContent = '上一把输了，放你继续（直到赢一把）'; st.className = 'state warn'; }
  else if (d.last_win === true) { st.textContent = '赢了，今天收工'; st.className = 'state stop'; }
  else { st.textContent = '额度已满，开客户端会被关'; st.className = 'state stop'; }

  document.getElementById('switch').className = 'switch' + (cfg.win_to_sleep ? ' on' : '');
  document.getElementById('limitnum').textContent = d.limit;

  const lw = document.getElementById('lastwin');
  lw.textContent = d.last_win === null ? '还不知道' : (d.last_win ? '赢了' : '输了');
  lw.style.color = d.last_win === null ? '#7c7c95' : (d.last_win ? '#6ee7a8' : '#ffb86b');

  const bars = document.getElementById('bars');
  const days = document.getElementById('days');
  const max = Math.max(1, ...d.week.map(x => x.n));
  bars.innerHTML = ''; days.innerHTML = '';
  for (const w of d.week) {
    bars.innerHTML += '<div class="bar-wrap"><div class="bar' + (w.today ? ' today' : '') +
      '" style="height:' + Math.max(2, (w.n / max) * 52) + 'px"></div></div>';
    days.innerHTML += '<div style="flex:1;text-align:center;font-size:10px;color:' +
      (w.today ? '#5b8cff' : '#5f5f78') + '">' + w.label + '</div>';
  }

  document.getElementById('dot').className = 'dot ' + (d.guard ? 'on' : 'off');
  document.getElementById('guard').textContent =
      '守护进程：' + (d.guard ? '运行中 (pid ' + d.guard + ')' : '没在跑');
  document.getElementById('rule').textContent =
      '前 ' + d.limit + ' 把不拦 · ' + (cfg.win_to_sleep ? '赢一把就睡觉' : '满额即收工') +
      ' · 换日 ' + String(cfg.reset_hour).padStart(2, '0') + ':00';
}
async function toggle() {
  await fetch('/api/toggle', {method: 'POST'});
  load();
}
async function setLimit(delta) {
  const n = Math.max(1, Math.min(20, (cfg.limit || 5) + delta));
  await fetch('/api/set_limit?n=' + n, {method: 'POST'});
  load();
}
load();
setInterval(load, 1000);
</script>
</body>
</html>
"""


def read_status():
    """只读状态，不写 state（避免和守护抢文件）。"""
    st = L.load_state()
    today = L.day_key()
    if st.get("day") != today:
        st = L.fresh_state(st)

    procs = L.list_procs()
    games = [p for p in procs if p.is_game]
    clients = [p for p in procs if p.is_client]
    hard = L.count_game_logs(today)
    count = max(hard, len(st.get("seen") or []))

    week = []
    for i in range(6, -1, -1):
        d = L.datetime.datetime.now() - L.datetime.timedelta(days=i)
        week.append({"label": d.strftime("%m-%d"),
                     "n": L.count_game_logs(d.date().isoformat()),
                     "today": i == 0})

    cfg = L.read_config()
    return {
        "date": L.datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": count,
        "limit": int(cfg.get("limit", 5)),
        "in_game": bool(games),
        "client_on": bool(clients),
        "last_win": st.get("last_win"),
        "guard": L._daemon_pid(),
        "week": week,
        "config": cfg,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            self._send(200, json.dumps(read_status(), ensure_ascii=False))
        else:
            self._send(200, PAGE, "text/html; charset=utf-8")

    def do_POST(self):
        if self.path.startswith("/api/toggle"):
            cfg = L.read_config()
            cfg["win_to_sleep"] = not cfg.get("win_to_sleep", True)
            L.write_config(cfg)
            self._send(200, json.dumps({"ok": True}))
        elif self.path.startswith("/api/set_limit"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            try:
                n = int(q.get("n", ["5"])[0])
            except ValueError:
                n = 5
            n = max(1, min(20, n))
            cfg = L.read_config()
            cfg["limit"] = n
            L.write_config(cfg)
            self._send(200, json.dumps({"ok": True, "limit": n}, ensure_ascii=False))
        else:
            self._send(404, "{}")


def open_app_window(url):
    """优先用 Edge 的应用窗口，看起来像独立软件。"""
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in cands:
        if os.path.exists(p):
            import subprocess
            prof = os.path.join(L.DATA_DIR, "ui_profile")
            subprocess.Popen([p, "--app=" + url,
                              "--window-size=480,860",
                              "--window-position=100,80",
                              "--user-data-dir=" + prof,
                              "--no-first-run",
                              "--no-default-browser-check"])
            return True
    webbrowser.open(url)
    return False


def main():
    url = "http://127.0.0.1:%d/" % PORT
    try:
        srv = HTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # 服务已经在跑了，直接开窗口就行
        if "--no-open" not in sys.argv:
            open_app_window(url)
        print("界面已在运行：%s" % url)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if "--no-open" not in sys.argv:
        open_app_window(url)
    print("界面已打开: %s" % url)
    print("关掉这个窗口就会退出（守护进程不受影响）")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
