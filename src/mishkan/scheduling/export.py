"""Pure renderers for OS schedulers invoking the same idempotent daemon command."""

from __future__ import annotations

from enum import StrEnum

from mishkan.scheduling.models import CronTriggerSpec, DateTriggerSpec, ScheduleRecord


class ScheduleExportTarget(StrEnum):
    CRON = "cron"
    SYSTEMD = "systemd"
    LAUNCHD = "launchd"


def render_schedule_export(
    schedule: ScheduleRecord,
    target: ScheduleExportTarget,
    *,
    executable: str = "mishkan",
) -> str:
    command = f"{executable} schedule trigger {schedule.schedule_id}"
    trigger = schedule.trigger
    if target is ScheduleExportTarget.CRON:
        expression = (
            trigger.expression if isinstance(trigger, CronTriggerSpec) else _date_cron(trigger)
        )
        timezone = trigger.timezone if isinstance(trigger, CronTriggerSpec) else "UTC"
        return f"CRON_TZ={timezone}\n{expression} {command}\n"
    if target is ScheduleExportTarget.SYSTEMD:
        timer = (
            "OnCalendar=*-*-* *:*:00\nPersistent=true"
            if isinstance(trigger, CronTriggerSpec)
            else f"OnCalendar={trigger.run_at.isoformat()}\nPersistent=true"
        )
        return (
            "# mishkan-schedule.service\n"
            "[Unit]\nDescription=Trigger a governed MISHKAN schedule\n\n"
            f"[Service]\nType=oneshot\nExecStart={command}\n\n"
            "# mishkan-schedule.timer\n"
            "[Unit]\nDescription=Wake the MISHKAN schedule authority\n\n"
            f"[Timer]\n{timer}\nAccuracySec=1s\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
    interval = (
        "<key>StartInterval</key><integer>60</integer>"
        if isinstance(trigger, CronTriggerSpec)
        else _launchd_calendar(trigger)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"<key>Label</key><string>world.aiobi.mishkan.schedule.{schedule.schedule_id}</string>\n"
        "<key>ProgramArguments</key><array>"
        f"<string>{executable}</string><string>schedule</string><string>trigger</string>"
        f"<string>{schedule.schedule_id}</string></array>\n"
        f"{interval}\n"
        "</dict></plist>\n"
    )


def _date_cron(trigger: DateTriggerSpec) -> str:
    instant = trigger.run_at
    return f"{instant.minute} {instant.hour} {instant.day} {instant.month} *"


def _launchd_calendar(trigger: DateTriggerSpec) -> str:
    instant = trigger.run_at
    return (
        "<key>StartCalendarInterval</key><dict>"
        f"<key>Month</key><integer>{instant.month}</integer>"
        f"<key>Day</key><integer>{instant.day}</integer>"
        f"<key>Hour</key><integer>{instant.hour}</integer>"
        f"<key>Minute</key><integer>{instant.minute}</integer>"
        "</dict>"
    )
