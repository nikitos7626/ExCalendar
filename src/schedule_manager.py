from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Set


class ScheduleManager:
    """Manages blocked/closed time slots for booking."""

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: Dict = {
            "blocked_slots": {},  # {"YYYY-MM-DD": ["HH:MM", ...]}
            "work_settings": None,  # {"workdays": [0,1,2,3,4], "work_start_hour": 8, "work_end_hour": 22}
        }
        self._load()

    def _load(self) -> None:
        if self.storage_path.exists():
            with self.storage_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
                # Merge with defaults to handle old files
                self._data = {
                    "blocked_slots": loaded.get("blocked_slots", {}),
                    "work_settings": loaded.get("work_settings"),
                }
        else:
            self._flush()

    def _flush(self) -> None:
        with self.storage_path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def is_slot_blocked(self, date_str: str, time_str: str) -> bool:
        """Check if a specific slot is blocked."""
        return time_str in self._data.get("blocked_slots", {}).get(date_str, [])

    def block_slot(self, date_str: str, time_str: str) -> None:
        """Block a specific time slot."""
        with self._lock:
            if "blocked_slots" not in self._data:
                self._data["blocked_slots"] = {}
            if date_str not in self._data["blocked_slots"]:
                self._data["blocked_slots"][date_str] = []
            if time_str not in self._data["blocked_slots"][date_str]:
                self._data["blocked_slots"][date_str].append(time_str)
                self._data["blocked_slots"][date_str].sort()
            self._flush()

    def unblock_slot(self, date_str: str, time_str: str) -> None:
        """Unblock a specific time slot."""
        with self._lock:
            if date_str in self._data.get("blocked_slots", {}):
                if time_str in self._data["blocked_slots"][date_str]:
                    self._data["blocked_slots"][date_str].remove(time_str)
                    if not self._data["blocked_slots"][date_str]:
                        del self._data["blocked_slots"][date_str]
                    self._flush()

    def block_day(self, date_str: str, times: List[str]) -> None:
        """Block all specified times for a day."""
        with self._lock:
            if "blocked_slots" not in self._data:
                self._data["blocked_slots"] = {}
            self._data["blocked_slots"][date_str] = sorted(set(times))
            self._flush()

    def unblock_day(self, date_str: str) -> None:
        """Unblock all slots for a day."""
        with self._lock:
            if date_str in self._data.get("blocked_slots", {}):
                del self._data["blocked_slots"][date_str]
                self._flush()

    def block_week(self, week_start: date) -> None:
        """Block entire week (Monday to Sunday)."""
        with self._lock:
            if "blocked_slots" not in self._data:
                self._data["blocked_slots"] = {}
            # Find Monday of the week
            days_since_monday = week_start.weekday()
            monday = week_start - timedelta(days=days_since_monday)
            # Block all 7 days
            for day_offset in range(7):
                day = monday + timedelta(days=day_offset)
                date_str = day.isoformat()
                # We'll need to get all available times from config, but for now just mark the day
                # This will be handled in the bot logic
                if date_str not in self._data["blocked_slots"]:
                    self._data["blocked_slots"][date_str] = []
            self._flush()

    def unblock_week(self, week_start: date) -> None:
        """Unblock entire week."""
        with self._lock:
            days_since_monday = week_start.weekday()
            monday = week_start - timedelta(days=days_since_monday)
            for day_offset in range(7):
                day = monday + timedelta(days=day_offset)
                date_str = day.isoformat()
                if date_str in self._data.get("blocked_slots", {}):
                    del self._data["blocked_slots"][date_str]
            self._flush()

    def get_blocked_slots_for_day(self, date_str: str) -> List[str]:
        """Get list of blocked time slots for a specific day."""
        return self._data.get("blocked_slots", {}).get(date_str, [])

    def is_day_blocked(self, date_str: str) -> bool:
        """Check if entire day is blocked (all slots)."""
        return date_str in self._data.get("blocked_slots", {})

    def get_all_blocked_dates(self) -> Set[str]:
        """Get all dates that have at least one blocked slot."""
        return set(self._data.get("blocked_slots", {}).keys())

    def cleanup_past_blocks(self, now: datetime) -> None:
        """Remove blocked slots for past dates."""
        now_str = now.strftime("%Y-%m-%d")
        with self._lock:
            if "blocked_slots" not in self._data:
                return
            to_remove = [
                date_str
                for date_str in self._data["blocked_slots"].keys()
                if date_str < now_str
            ]
            for date_str in to_remove:
                del self._data["blocked_slots"][date_str]
            if to_remove:
                self._flush()

    def get_work_settings(self) -> Dict | None:
        """Get custom work settings (workdays, hours). Returns None if not set."""
        return self._data.get("work_settings")

    def set_work_settings(
        self, workdays: List[int], work_start_hour: int, work_end_hour: int
    ) -> None:
        """Set custom work settings."""
        with self._lock:
            self._data["work_settings"] = {
                "workdays": workdays,
                "work_start_hour": work_start_hour,
                "work_end_hour": work_end_hour,
            }
            self._flush()

    def clear_work_settings(self) -> None:
        """Clear custom work settings (use defaults from config)."""
        with self._lock:
            self._data["work_settings"] = None
            self._flush()

