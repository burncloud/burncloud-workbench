from __future__ import annotations

from types import SimpleNamespace

import burncloud_ui_rebuild.studio_supervisor as supervisor


def test_studio_supervisor_does_not_alert_on_normal_exit(monkeypatch):
    alerts = []
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(supervisor, "_notify_studio_failure", lambda detail: alerts.append(detail))

    assert supervisor.run_studio_supervisor() == 0
    assert alerts == []


def test_studio_supervisor_alerts_abnormal_exit(monkeypatch):
    alerts = []
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=2))
    monkeypatch.setattr(supervisor, "_notify_studio_failure", lambda detail: alerts.append(detail))

    assert supervisor.run_studio_supervisor() == 2
    assert alerts == ["langgraph dev exited with code 2"]


def test_studio_supervisor_treats_ctrl_c_exit_as_normal(monkeypatch):
    alerts = []
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=130))
    monkeypatch.setattr(supervisor, "_notify_studio_failure", lambda detail: alerts.append(detail))

    assert supervisor.run_studio_supervisor() == 0
    assert alerts == []
