#!/usr/bin/env python3
"""Локальный HTTP-сервер для мониторинга состояния ПК.

Скрипт слушает только localhost (127.0.0.1), чтобы вы могли безопасно
использовать любую внешнюю переадресацию/туннель по своему выбору.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def cpu_percent() -> float:
    """Возвращает загрузку CPU в процентах."""
    if hasattr(os, "getloadavg"):
        load1 = os.getloadavg()[0]
        cpus = os.cpu_count() or 1
        return round((load1 / cpus) * 100, 2)

    if platform.system().lower().startswith("win"):
        output = subprocess.check_output(
            ["wmic", "cpu", "get", "loadpercentage", "/value"], text=True
        )
        for line in output.splitlines():
            if line.startswith("LoadPercentage="):
                return float(line.split("=", 1)[1])

    return 0.0


def memory_info() -> dict[str, Any]:
    """Возвращает данные по памяти."""
    if platform.system().lower() == "linux":
        meminfo = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key, val = line.split(":", 1)
                meminfo[key.strip()] = int(val.strip().split()[0])

        total = meminfo.get("MemTotal", 0) * 1024
        available = meminfo.get("MemAvailable", 0) * 1024
        used = max(total - available, 0)

        return {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": available,
            "used_percent": round((used / total) * 100, 2) if total else 0,
        }

    return {"note": "Подробная статистика памяти поддерживается только на Linux без сторонних зависимостей."}


def disk_info() -> dict[str, Any]:
    usage = shutil.disk_usage("/")
    used = usage.used
    total = usage.total
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": usage.free,
        "used_percent": round((used / total) * 100, 2) if total else 0,
    }


def uptime_seconds() -> int:
    if platform.system().lower() == "linux":
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            return int(float(f.read().split()[0]))

    return int(time.time() - ps_start_time())


def ps_start_time() -> float:
    return psutil_process_start_time_fallback()


def psutil_process_start_time_fallback() -> float:
    # Фолбэк: если не Linux, просто считаем uptime как время жизни процесса.
    return PROCESS_START


def collect_status() -> dict[str, Any]:
    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "cpu_percent": cpu_percent(),
        "memory": memory_info(),
        "disk_root": disk_info(),
        "uptime_seconds": uptime_seconds(),
        "server_time": int(time.time()),
    }


class StatusHandler(BaseHTTPRequestHandler):
    token = ""

    def do_GET(self) -> None:
        if self.path != "/status":
            self.send_error(404, "Not Found")
            return

        provided = self.headers.get("X-Monitor-Token", "")
        if self.token and provided != self.token:
            self.send_error(401, "Unauthorized")
            return

        payload = json.dumps(collect_status(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.address_string()}] {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальный сервер мониторинга ПК")
    parser.add_argument("--port", type=int, default=8765, help="Порт localhost (по умолчанию: 8765)")
    parser.add_argument(
        "--token",
        default="",
        help="Токен для заголовка X-Monitor-Token (рекомендуется задать)",
    )
    args = parser.parse_args()

    StatusHandler.token = args.token
    server = HTTPServer(("127.0.0.1", args.port), StatusHandler)

    print("Сервер запущен")
    print(f"Локальный адрес: http://127.0.0.1:{args.port}/status")
    print("Скрипт слушает только localhost. Для внешнего доступа используйте вашу переадресацию/туннель.")
    if args.token:
        print("Токен включен: клиент должен передавать заголовок X-Monitor-Token")
    else:
        print("ВНИМАНИЕ: токен не задан, доступ открыт для всех кто видит endpoint")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка...")
    finally:
        server.server_close()


PROCESS_START = time.time()

if __name__ == "__main__":
    main()
