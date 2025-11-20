from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(slots=True)
class BookingRequest:
    id: str
    user_id: int
    username: str
    full_name: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    status: str  # pending|confirmed|declined|cancelled_by_client|cancelled_by_trainer
    created_at: str
    reminder_sent: bool = False  # Whether 2-hour reminder was sent


class BookingStore:
    """Very small JSON storage for booking requests."""

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: Dict[str, List[Dict]] = {"requests": []}
        self._load()

    def _load(self) -> None:
        if self.storage_path.exists():
            with self.storage_path.open("r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._flush()

    def _flush(self) -> None:
        with self.storage_path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def _filter_slot(self, date_str: str, time_str: str) -> List[Dict]:
        return [
            req
            for req in self._data["requests"]
            if req["date"] == date_str and req["time"] == time_str
        ]

    def get_slot_state(self, date_str: str, time_str: str) -> Optional[str]:
        for request in self._filter_slot(date_str, time_str):
            if request["status"] in {"pending", "confirmed"}:
                return request["status"]
        return None

    def create_request(
        self,
        *,
        user_id: int,
        username: str | None,
        full_name: str,
        date_str: str,
        time_str: str,
    ) -> BookingRequest:
        with self._lock:
            if self.get_slot_state(date_str, time_str):
                raise ValueError("Slot already requested or confirmed")
            request = BookingRequest(
                id=str(uuid.uuid4()),
                user_id=user_id,
                username=username or "",
                full_name=full_name,
                date=date_str,
                time=time_str,
                status="pending",
                created_at=datetime.utcnow().isoformat(),
                reminder_sent=False,
            )
            self._data["requests"].append(asdict(request))
            self._flush()
            return request

    def update_status(self, request_id: str, status: str) -> BookingRequest:
        with self._lock:
            for item in self._data["requests"]:
                if item["id"] == request_id:
                    item["status"] = status
                    self._flush()
                    return BookingRequest(**item)
        raise KeyError(f"Request {request_id} not found")

    def get_request(self, request_id: str) -> BookingRequest:
        for item in self._data["requests"]:
            if item["id"] == request_id:
                # Handle old records without reminder_sent field
                if "reminder_sent" not in item:
                    item["reminder_sent"] = False
                return BookingRequest(**item)
        raise KeyError(request_id)

    def list_day_states(self, date_str: str) -> Dict[str, str]:
        """Return map time -> status for slots with any activity."""
        result: Dict[str, str] = {}
        for item in self._data["requests"]:
            if item["date"] == date_str:
                result[item["time"]] = item["status"]
        return result

    def list_user_requests(self, user_id: int) -> List[BookingRequest]:
        requests = []
        for item in self._data["requests"]:
            if item["user_id"] == user_id:
                # Handle old records without reminder_sent field
                if "reminder_sent" not in item:
                    item["reminder_sent"] = False
                requests.append(BookingRequest(**item))
        requests.sort(key=lambda r: (r.date, r.time))
        return requests

    def list_all_requests(self) -> List[BookingRequest]:
        requests = []
        for item in self._data["requests"]:
            # Handle old records without reminder_sent field
            if "reminder_sent" not in item:
                item["reminder_sent"] = False
            requests.append(BookingRequest(**item))
        requests.sort(key=lambda r: (r.date, r.time))
        return requests

    def get_upcoming_confirmed(self, now: datetime) -> List[BookingRequest]:
        """Get confirmed requests that need reminder (2 hours before start)."""
        result = []
        for item in self._data["requests"]:
            if item["status"] != "confirmed":
                continue
            if item.get("reminder_sent", False):
                continue
            # Handle old records
            if "reminder_sent" not in item:
                item["reminder_sent"] = False
            try:
                slot_dt = datetime.strptime(f"{item['date']} {item['time']}", "%Y-%m-%d %H:%M")
                # Check if reminder time (2 hours before) has passed
                reminder_time = slot_dt - timedelta(hours=2)
                if now >= reminder_time and now < slot_dt:
                    result.append(BookingRequest(**item))
            except (ValueError, KeyError):
                continue
        return result

    def mark_reminder_sent(self, request_id: str) -> None:
        """Mark reminder as sent for a request."""
        with self._lock:
            for item in self._data["requests"]:
                if item["id"] == request_id:
                    item["reminder_sent"] = True
                    self._flush()
                    return
        raise KeyError(f"Request {request_id} not found")

    def cleanup_expired(self, now: datetime) -> None:
        """Remove requests that finished before 'now'."""
        now_str = now.strftime("%Y-%m-%d %H:%M")
        with self._lock:
            before = len(self._data["requests"])
            self._data["requests"] = [
                item
                for item in self._data["requests"]
                if f"{item['date']} {item['time']}" >= now_str
            ]
            if len(self._data["requests"]) != before:
                self._flush()

