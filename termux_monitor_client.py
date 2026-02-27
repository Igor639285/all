#!/usr/bin/env python3
"""Termux-клиент с локальным веб-дашбордом для мониторинга ПК."""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _ask(prompt: str, default: str | None = None) -> str:
    text = input(prompt).strip()
    if text:
        return text
    return default or ""



class MonitorState:
    def __init__(self, source_url: str, token: str, interval: float, history_size: int = 240):
        self.source_url = source_url.rstrip("/") + "/status"
        self.token = token
        self.interval = interval
        self.last_error = "Ожидание первого запроса..."
        self.last_status: dict[str, Any] | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latest = self.last_status or {}
            return {
                "source_url": self.source_url,
                "interval": self.interval,
                "last_error": self.last_error,
                "latest": latest,
                "history": list(self.history),
                "last_update": int(time.time()),
            }

    def update(self, status: dict[str, Any]) -> None:
        now = int(time.time())
        point = {
            "ts": now,
            "cpu_percent": float(status.get("cpu_percent", 0)),
            "mem_percent": float(status.get("memory", {}).get("used_percent", 0)),
            "disk_percent": float(status.get("disk_root", {}).get("used_percent", 0)),
        }
        with self._lock:
            self.last_status = status
            self.last_error = ""
            self.history.append(point)

    def set_error(self, error_message: str) -> None:
        with self._lock:
            self.last_error = error_message


class DashboardHandler(BaseHTTPRequestHandler):
    state: MonitorState

    def do_GET(self) -> None:
        if self.path == "/":
            self._respond_html(DASHBOARD_HTML.encode("utf-8"))
            return

        if self.path == "/api/state":
            payload = json.dumps(self.state.snapshot(), ensure_ascii=False).encode("utf-8")
            self._respond_json(payload)
            return

        self.send_error(404, "Not Found")

    def _respond_html(self, payload: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _respond_json(self, payload: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        return


def poll_remote(state: MonitorState) -> None:
    while True:
        headers = {}
        if state.token:
            headers["X-Monitor-Token"] = state.token

        request = urllib.request.Request(state.source_url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
                state.update(payload)
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            state.set_error(f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # pylint: disable=broad-except
            state.set_error(f"{type(exc).__name__}: {exc}")

        time.sleep(state.interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Termux веб-дашборд мониторинга ПК")
    parser.add_argument("--source", default="", help="Базовый адрес ПК/туннеля (например https://abc.ngrok.io)")
    parser.add_argument("--token", default="", help="Токен для X-Monitor-Token")
    parser.add_argument("--interval", type=float, default=4.0, help="Интервал опроса (сек)")
    parser.add_argument("--listen-host", default="127.0.0.1", help="Хост локального сайта")
    parser.add_argument("--listen-port", type=int, default=8088, help="Порт локального сайта")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    interactive = not bool(args.source)
    source = args.source or _ask("Введите адрес туннеля/переадресации (без /status): ")
    token = args.token or (_ask("Введите токен (Enter если без токена): ", default="") if interactive else "")

    if interactive:
        interval_text = _ask(f"Интервал обновления в секундах [{args.interval}]: ", default=str(args.interval))
        try:
            interval = max(float(interval_text), 1.0)
        except ValueError:
            interval = args.interval
    else:
        interval = max(float(args.interval), 1.0)

    state = MonitorState(source_url=source, token=token, interval=interval)
    DashboardHandler.state = state

    worker = threading.Thread(target=poll_remote, args=(state,), daemon=True)
    worker.start()

    server = ThreadingHTTPServer((args.listen_host, args.listen_port), DashboardHandler)
    url = f"http://{args.listen_host}:{args.listen_port}"
    print("\nЛокальный дашборд запущен!")
    print(f"Откройте в браузере Termux: {url}")
    print(f"Источник данных: {state.source_url}")
    print("Для остановки нажмите Ctrl+C")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка...")
    finally:
        server.server_close()


DASHBOARD_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PC Monitor Dashboard</title>
  <style>
    :root { color-scheme: dark; }
    body { font-family: Arial, sans-serif; margin: 0; padding: 16px; background: #0f172a; color: #e2e8f0; }
    h1 { margin-top: 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 12px; }
    .card { background: #1e293b; border-radius: 12px; padding: 12px; box-shadow: 0 4px 14px rgba(0,0,0,.25); }
    .label { color: #94a3b8; font-size: 12px; }
    .value { font-size: 24px; margin-top: 4px; }
    canvas { width: 100%; height: 220px; background: #111827; border-radius: 12px; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; background: #1e293b; border-radius: 12px; overflow: hidden; }
    th, td { padding: 8px 10px; border-bottom: 1px solid #334155; text-align: left; }
    .error { color: #fda4af; font-weight: bold; }
    .ok { color: #86efac; font-weight: bold; }
  </style>
</head>
<body>
  <h1>📊 Мониторинг ПК</h1>
  <div id="status"></div>

  <div class="grid">
    <div class="card"><div class="label">CPU</div><div class="value" id="cpuValue">--</div></div>
    <div class="card"><div class="label">RAM</div><div class="value" id="memValue">--</div></div>
    <div class="card"><div class="label">Disk /</div><div class="value" id="diskValue">--</div></div>
    <div class="card"><div class="label">Uptime</div><div class="value" id="uptimeValue">--</div></div>
  </div>

  <div class="card">
    <h3>Нагрузка по времени</h3>
    <canvas id="chart" width="900" height="220"></canvas>
  </div>

  <table>
    <thead>
      <tr><th>Параметр</th><th>Значение</th></tr>
    </thead>
    <tbody id="details"></tbody>
  </table>

<script>
function fmtPct(v){ return `${Number(v || 0).toFixed(1)}%`; }
function fmtBytes(v){
  let n=Number(v||0), u=['B','KB','MB','GB','TB'], i=0;
  while(n>=1024 && i<u.length-1){ n/=1024; i++; }
  return `${n.toFixed(2)} ${u[i]}`;
}
function fmtUptime(sec){
  sec = Number(sec||0);
  const d = Math.floor(sec/86400); sec%=86400;
  const h = Math.floor(sec/3600); sec%=3600;
  const m = Math.floor(sec/60); const s = Math.floor(sec%60);
  return `${d}д ${h}ч ${m}м ${s}с`;
}

function drawChart(points){
  const c = document.getElementById('chart');
  const ctx = c.getContext('2d');
  const w = c.width, h = c.height;
  ctx.clearRect(0,0,w,h);
  ctx.fillStyle = '#111827'; ctx.fillRect(0,0,w,h);

  ctx.strokeStyle = '#334155';
  for(let i=0;i<=5;i++){
    const y = 10 + (h-20)*i/5;
    ctx.beginPath(); ctx.moveTo(40,y); ctx.lineTo(w-10,y); ctx.stroke();
    ctx.fillStyle = '#94a3b8'; ctx.fillText(`${100-20*i}%`, 5, y+4);
  }

  function series(key, color){
    if(!points.length) return;
    ctx.strokeStyle = color; ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((p, idx) => {
      const x = 40 + (w-50) * (idx / Math.max(1, points.length-1));
      const y = 10 + (h-20) * (1 - (Number(p[key]||0) / 100));
      if(idx===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
    });
    ctx.stroke();
  }

  series('cpu_percent', '#38bdf8');
  series('mem_percent', '#fbbf24');
  series('disk_percent', '#f87171');

  ctx.fillStyle = '#38bdf8'; ctx.fillRect(50,h-18,12,4); ctx.fillStyle = '#e2e8f0'; ctx.fillText('CPU', 65, h-14);
  ctx.fillStyle = '#fbbf24'; ctx.fillRect(115,h-18,12,4); ctx.fillStyle = '#e2e8f0'; ctx.fillText('RAM', 130, h-14);
  ctx.fillStyle = '#f87171'; ctx.fillRect(180,h-18,12,4); ctx.fillStyle = '#e2e8f0'; ctx.fillText('DISK', 195, h-14);
}

async function refresh(){
  try{
    const res = await fetch('/api/state', {cache: 'no-store'});
    const data = await res.json();
    const st = data.latest || {};
    const mem = st.memory || {};
    const disk = st.disk_root || {};

    document.getElementById('cpuValue').textContent = fmtPct(st.cpu_percent);
    document.getElementById('memValue').textContent = fmtPct(mem.used_percent);
    document.getElementById('diskValue').textContent = fmtPct(disk.used_percent);
    document.getElementById('uptimeValue').textContent = fmtUptime(st.uptime_seconds);

    const statusEl = document.getElementById('status');
    if(data.last_error){
      statusEl.innerHTML = `<p class='error'>Ошибка опроса: ${data.last_error}</p>`;
    } else {
      statusEl.innerHTML = `<p class='ok'>Источник: ${data.source_url}</p>`;
    }

    document.getElementById('details').innerHTML = `
      <tr><td>Хост</td><td>${st.hostname || '-'}</td></tr>
      <tr><td>Платформа</td><td>${st.platform || '-'}</td></tr>
      <tr><td>RAM (used/total)</td><td>${fmtBytes(mem.used_bytes)} / ${fmtBytes(mem.total_bytes)}</td></tr>
      <tr><td>Disk / (used/total)</td><td>${fmtBytes(disk.used_bytes)} / ${fmtBytes(disk.total_bytes)}</td></tr>
      <tr><td>Последний серверный ts</td><td>${st.server_time || '-'}</td></tr>
      <tr><td>Точек в истории</td><td>${(data.history || []).length}</td></tr>
    `;

    drawChart(data.history || []);
  }catch(err){
    document.getElementById('status').innerHTML = `<p class='error'>Ошибка UI: ${err}</p>`;
  }
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
