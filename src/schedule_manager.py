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
            "manual_slots": {},  # {"0": ["09:00", ...], ...} - ручное расписание по дням недели
            "regular_clients": [],  # [{"user_id": int, "username": str, "full_name": str}, ...]
            "group_slots": [],  # [{"weekday": int, "time": "HH:MM"}, ...] - слоты где можно записываться >1 человеку
            "trainers": [],  # [{"user_id": int, "username": str, "full_name": str}, ...] - динамически добавленные тренеры
            "user_stats": {},  # {"user_id": {"first_seen": "YYYY-MM-DD", "last_seen": "YYYY-MM-DD", "activity_days": ["YYYY-MM-DD", ...]}}
        }
        self._load()

    def _load(self) -> None:
        if self.storage_path.exists():
            with self.storage_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
                # Merge with defaults to handle old files
                regular_clients = loaded.get("regular_clients", [])
                # Migrate old regular clients format (add weekday and time if missing)
                for client in regular_clients:
                    if "weekday" not in client:
                        client["weekday"] = None
                    if "time" not in client:
                        client["time"] = None
                self._data = {
                    "blocked_slots": loaded.get("blocked_slots", {}),
                    "work_settings": loaded.get("work_settings"),
                    "manual_slots": loaded.get("manual_slots", {}),
                    "regular_clients": regular_clients,
                    "group_slots": loaded.get("group_slots", []),
                    "trainers": loaded.get("trainers", []),
                    "user_stats": loaded.get("user_stats", {}),
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

    def get_manual_slots(self) -> Dict[str, List[str]]:
        """Return manual weekly schedule (keys are weekday numbers as strings)."""
        return self._data.get("manual_slots", {})

    def set_manual_slots_for_weekday(self, weekday: int, slots: List[str]) -> None:
        """Persist manual slots for a specific weekday (0=Mon)."""
        weekday_key = str(weekday)
        # Ensure times are unique and sorted
        normalized = sorted(set(slots))
        with self._lock:
            if "manual_slots" not in self._data:
                self._data["manual_slots"] = {}
            if normalized:
                self._data["manual_slots"][weekday_key] = normalized
            elif weekday_key in self._data["manual_slots"]:
                # Remove key if empty to avoid clutter
                del self._data["manual_slots"][weekday_key]
            self._flush()

    def clear_manual_slots(self) -> None:
        """Remove manual weekly schedule."""
        with self._lock:
            self._data["manual_slots"] = {}
            self._flush()

    def has_manual_slots(self) -> bool:
        """Return True if at least one weekday has manual slots configured."""
        manual = self._data.get("manual_slots", {})
        if not manual:
            return False
        return any(slots for slots in manual.values())

    def get_regular_clients(self) -> List[Dict]:
        """Get list of regular clients."""
        return self._data.get("regular_clients", []).copy()

    def add_regular_client(self, user_id: int, username: str, full_name: str) -> None:
        """Add a client to regular clients list."""
        with self._lock:
            if "regular_clients" not in self._data:
                self._data["regular_clients"] = []
            # Check if already exists
            if not any(c["user_id"] == user_id for c in self._data["regular_clients"]):
                self._data["regular_clients"].append({
                    "user_id": user_id,
                    "username": username or "",
                    "full_name": full_name,
                    "weekday": None,  # 0-6, None if not set
                    "time": None,  # HH:MM, None if not set
                })
                self._flush()

    def remove_regular_client(self, user_id: int) -> None:
        """Remove a client from regular clients list."""
        with self._lock:
            if "regular_clients" not in self._data:
                return
            self._data["regular_clients"] = [
                c for c in self._data["regular_clients"] if c["user_id"] != user_id
            ]
            self._flush()

    def is_regular_client(self, user_id: int) -> bool:
        """Check if user is a regular client."""
        return any(c["user_id"] == user_id for c in self._data.get("regular_clients", []))

    def set_regular_client_slot(self, user_id: int, weekday: int, time_str: str) -> None:
        """Set fixed slot (weekday + time) for a regular client."""
        with self._lock:
            if "regular_clients" not in self._data:
                return
            for client in self._data["regular_clients"]:
                if client["user_id"] == user_id:
                    client["weekday"] = weekday
                    client["time"] = time_str
                    self._flush()
                    return

    def clear_regular_client_slot(self, user_id: int) -> None:
        """Clear fixed slot for a regular client."""
        with self._lock:
            if "regular_clients" not in self._data:
                return
            for client in self._data["regular_clients"]:
                if client["user_id"] == user_id:
                    client["weekday"] = None
                    client["time"] = None
                    self._flush()
                    return

    def get_regular_client_slot(self, user_id: int) -> Dict | None:
        """Get fixed slot for a regular client. Returns None if not set."""
        for client in self._data.get("regular_clients", []):
            if client["user_id"] == user_id:
                if client.get("weekday") is not None and client.get("time"):
                    return {"weekday": client["weekday"], "time": client["time"]}
        return None

    def get_all_regular_client_slots(self) -> List[Dict]:
        """Get all regular clients with their fixed slots."""
        result = []
        for client in self._data.get("regular_clients", []):
            if client.get("weekday") is not None and client.get("time"):
                result.append({
                    "user_id": client["user_id"],
                    "username": client.get("username", ""),
                    "full_name": client.get("full_name", ""),
                    "weekday": client["weekday"],
                    "time": client["time"],
                })
        return result

    def get_group_slots(self) -> List[Dict]:
        """Get list of group slots (weekday + time where multiple bookings allowed)."""
        return self._data.get("group_slots", []).copy()

    def add_group_slot(self, weekday: int, time_str: str) -> None:
        """Add a group slot (weekday 0-6, time HH:MM)."""
        with self._lock:
            if "group_slots" not in self._data:
                self._data["group_slots"] = []
            slot = {"weekday": weekday, "time": time_str}
            # Check if already exists
            if slot not in self._data["group_slots"]:
                self._data["group_slots"].append(slot)
                self._flush()

    def remove_group_slot(self, weekday: int, time_str: str) -> None:
        """Remove a group slot."""
        with self._lock:
            if "group_slots" not in self._data:
                return
            self._data["group_slots"] = [
                s for s in self._data["group_slots"]
                if not (s["weekday"] == weekday and s["time"] == time_str)
            ]
            self._flush()

    def is_group_slot(self, date_str: str, time_str: str) -> bool:
        """Check if a specific date+time is a group slot."""
        from datetime import datetime
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            weekday = dt.weekday()
            for slot in self._data.get("group_slots", []):
                if slot["weekday"] == weekday and slot["time"] == time_str:
                    return True
        except (ValueError, KeyError):
            pass
        return False

    def get_trainers(self) -> List[Dict]:
        """Get list of dynamically added trainers."""
        return self._data.get("trainers", []).copy()

    def add_trainer(self, user_id: int, username: str, full_name: str) -> None:
        """Add a trainer to the trainers list."""
        with self._lock:
            if "trainers" not in self._data:
                self._data["trainers"] = []
            # Check if already exists
            if not any(t["user_id"] == user_id for t in self._data["trainers"]):
                self._data["trainers"].append({
                    "user_id": user_id,
                    "username": username or "",
                    "full_name": full_name,
                })
                self._flush()

    def remove_trainer(self, user_id: int) -> None:
        """Remove a trainer from the trainers list."""
        with self._lock:
            if "trainers" not in self._data:
                return
            self._data["trainers"] = [
                t for t in self._data["trainers"] if t["user_id"] != user_id
            ]
            self._flush()

    def get_all_trainer_ids(self) -> List[int]:
        """Get all trainer IDs (from config + dynamically added)."""
        return [t["user_id"] for t in self._data.get("trainers", [])]

    def track_user_activity(self, user_id: int) -> None:
        """Track user activity - record when user interacts with bot."""
        with self._lock:
            if "user_stats" not in self._data:
                self._data["user_stats"] = {}
            
            today_str = datetime.now().strftime("%Y-%m-%d")
            
            if str(user_id) not in self._data["user_stats"]:
                # New user
                self._data["user_stats"][str(user_id)] = {
                    "first_seen": today_str,
                    "last_seen": today_str,
                    "activity_days": [today_str],
                }
            else:
                # Existing user - update last_seen and add today if not present
                user_stat = self._data["user_stats"][str(user_id)]
                user_stat["last_seen"] = today_str
                if today_str not in user_stat["activity_days"]:
                    user_stat["activity_days"].append(today_str)
                    user_stat["activity_days"].sort()
            
            self._flush()

    def get_user_stats_summary(self) -> Dict:
        """Get summary statistics about users."""
        user_stats = self._data.get("user_stats", {})
        if not user_stats:
            return {
                "total_users": 0,
                "active_today": 0,
                "active_week": 0,
                "active_month": 0,
            }
        
        today = datetime.now().date()
        today_str = today.strftime("%Y-%m-%d")
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)
        
        active_today = 0
        active_week = 0
        active_month = 0
        
        for user_id_str, stat in user_stats.items():
            last_seen = datetime.strptime(stat["last_seen"], "%Y-%m-%d").date()
            activity_days = [datetime.strptime(d, "%Y-%m-%d").date() for d in stat.get("activity_days", [])]
            
            if last_seen == today:
                active_today += 1
            
            if last_seen >= week_ago:
                active_week += 1
            
            if last_seen >= month_ago:
                active_month += 1
        
        return {
            "total_users": len(user_stats),
            "active_today": active_today,
            "active_week": active_week,
            "active_month": active_month,
        }

