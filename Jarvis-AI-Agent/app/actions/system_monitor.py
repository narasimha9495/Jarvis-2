"""System health monitoring (CPU, memory, disk, battery) using psutil.

Nothing here blocks the event loop: CPU readings use psutil's
non-blocking mode, and the slower calls run in a worker thread.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.actions.base import BaseAction

logger = logging.getLogger(__name__)

# The drive Jarvis runs from: "C:\\" on Windows, "/" elsewhere
DISK_PATH = Path.cwd().anchor or "/"


def prime_cpu_counters() -> None:
    """Call once at startup so the first non-blocking CPU reading isn't 0.0."""
    try:
        import psutil
        psutil.cpu_percent(interval=None)
    except Exception:
        pass


def _gb(n: float) -> float:
    return round(n / (1024 ** 3), 1)


class SystemMonitorAction(BaseAction):
    """Monitor system health — CPU, memory, disk, and battery."""

    @property
    def name(self) -> str:
        return "system_monitor"

    @property
    def description(self) -> str:
        return "Monitor system health - CPU, memory, disk, and battery status"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "system_status": self._system_status,
            "cpu_alert": self._cpu_alert,
            "disk_alert": self._disk_alert,
            "top_processes": self._top_processes,
        }
        handler = handlers.get(params.get("action", ""))
        if not handler:
            return {"success": False, "message": f"Unknown monitor action: {params.get('action')}"}
        try:
            import psutil  # noqa: F401
        except ImportError:
            return {"success": False, "message": "psutil is not installed. Run: pip install psutil"}
        try:
            return await handler(params)
        except Exception as e:
            logger.error(f"System monitor failed: {e}")
            return {"success": False, "message": f"System monitor failed: {e}"}

    async def _system_status(self, params: dict) -> dict[str, Any]:
        import psutil

        def collect() -> dict[str, Any]:
            cpu_percent = psutil.cpu_percent(interval=None)  # since last call, non-blocking
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage(DISK_PATH)

            battery = None
            try:
                bat = psutil.sensors_battery()
                if bat:
                    if bat.power_plugged:
                        time_left = "Charging"
                    elif bat.secsleft and bat.secsleft > 0:
                        time_left = f"{bat.secsleft // 3600}h {(bat.secsleft % 3600) // 60}m"
                    else:
                        time_left = "Unknown"
                    battery = {"percent": round(bat.percent), "plugged": bat.power_plugged, "time_left": time_left}
            except Exception:
                pass

            return {
                "cpu_percent": cpu_percent,
                "cpu_count": psutil.cpu_count(),
                "memory": {"total_gb": _gb(mem.total), "used_gb": _gb(mem.used), "percent": mem.percent},
                "disk": {
                    "path": DISK_PATH,
                    "total_gb": _gb(disk.total),
                    "used_gb": _gb(disk.used),
                    "free_gb": _gb(disk.free),
                    "percent": disk.percent,
                },
                "battery": battery,
                "boot_time": datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S"),
            }

        data = await asyncio.to_thread(collect)
        return {
            "success": True,
            **data,
            "message": f"CPU: {data['cpu_percent']}% | RAM: {data['memory']['percent']}% | Disk: {data['disk']['percent']}%",
        }

    async def _cpu_alert(self, params: dict) -> dict[str, Any]:
        import psutil

        threshold = int(params.get("threshold") or 80)
        current = psutil.cpu_percent(interval=None)
        above = current > threshold
        return {
            "success": True,
            "above_threshold": above,
            "current": current,
            "threshold": threshold,
            "message": (
                f"⚠️ CPU at {current}% (above {threshold}% threshold)"
                if above
                else f"✅ CPU at {current}% (below {threshold}% threshold)"
            ),
        }

    async def _disk_alert(self, params: dict) -> dict[str, Any]:
        import psutil

        threshold = int(params.get("threshold") or 90)
        disk = await asyncio.to_thread(psutil.disk_usage, DISK_PATH)
        above = disk.percent > threshold
        return {
            "success": True,
            "above_threshold": above,
            "current": disk.percent,
            "threshold": threshold,
            "free_gb": _gb(disk.free),
            "message": (
                f"⚠️ Disk at {disk.percent}% (above {threshold}% threshold)"
                if above
                else f"✅ Disk at {disk.percent}% ({_gb(disk.free)}GB free)"
            ),
        }

    async def _top_processes(self, params: dict) -> dict[str, Any]:
        import psutil

        def snapshot():
            procs = []
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    p.cpu_percent(None)  # first call starts the measurement
                    procs.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return procs

        def measure(procs):
            out = []
            for p in procs:
                try:
                    with p.oneshot():
                        out.append({
                            "pid": p.pid,
                            "name": p.info.get("name") or "?",
                            "cpu_percent": round(p.cpu_percent(None), 1),
                            "memory_mb": round(p.memory_info().rss / (1024 ** 2), 1),
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return out

        procs = await asyncio.to_thread(snapshot)
        await asyncio.sleep(0.5)  # measurement window, without blocking the server
        processes = await asyncio.to_thread(measure, procs)
        processes.sort(key=lambda p: (p["cpu_percent"], p["memory_mb"]), reverse=True)
        top_5 = processes[:5]
        return {
            "success": True,
            "processes": top_5,
            "message": "Top processes: " + ", ".join(f"{p['name']} ({p['cpu_percent']}%)" for p in top_5),
        }
