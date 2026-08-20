from __future__ import annotations

import subprocess
from typing import Any

from .notifications import send_telegram_message


# Common Ctrl+C / SIGINT exit codes across POSIX and Windows.
NORMAL_STUDIO_STOP_CODES = {0, 130, -2, -1073741510, 3221225786}


def _notify_studio_failure(detail: str) -> dict[str, Any]:
    return send_telegram_message(
        "\n".join([
            "🚨 BurnCloud Harness 启动错误",
            "组件: langgraph dev / Agent Server",
            f"错误: {detail}",
            "说明: Graph 尚未正常启动，因此由外层 Studio Supervisor 发送此通知。",
        ]),
        event_key=f"studio-startup-error:{detail[:240]}",
    )


def run_studio_supervisor() -> int:
    """Run `langgraph dev` in the foreground and notify on abnormal process exit.

    Runtime node failures are handled by Graph error boundaries. This supervisor
    covers the earlier phase where graph import/build itself can fail before a
    LangGraph node exists to send a notification.
    """
    try:
        completed = subprocess.run(["langgraph", "dev"], check=False)
    except KeyboardInterrupt:
        return 0
    except FileNotFoundError:
        _notify_studio_failure("langgraph executable was not found in the active environment")
        return 127
    except Exception as exc:
        _notify_studio_failure(f"{type(exc).__name__}: {exc}")
        return 1

    code = int(completed.returncode)
    if code not in NORMAL_STUDIO_STOP_CODES:
        _notify_studio_failure(f"langgraph dev exited with code {code}")
    return 0 if code in NORMAL_STUDIO_STOP_CODES else code
