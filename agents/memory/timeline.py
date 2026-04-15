"""
时序索引层 — L3
按天维护有序链，每天一个锚点 (DailyAnchor)。
提供时间衰减加权和快速按日期范围查询。
"""

import math
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional

from agents.memory.config import TIME_DECAY_LAMBDA

logger = logging.getLogger("memory.timeline")


@dataclass
class DailyAnchor:
    date: date
    mem_ids: list[str] = field(default_factory=list)


class Timeline:
    """按 Agent 维护的时间索引"""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self._days: dict[date, DailyAnchor] = {}

    def append(self, mem_id: str, dt: Optional[date] = None):
        day = dt or date.today()
        if day not in self._days:
            self._days[day] = DailyAnchor(date=day)
        self._days[day].mem_ids.append(mem_id)

    def get_recent(self, days: int = 7) -> list[DailyAnchor]:
        today = date.today()
        cutoff = today - timedelta(days=days)
        anchors = [a for a in self._days.values() if a.date >= cutoff]
        return sorted(anchors, key=lambda a: a.date, reverse=True)

    def get_mem_ids_in_range(self, days: int = 7) -> list[str]:
        ids = []
        for anchor in self.get_recent(days):
            ids.extend(anchor.mem_ids)
        return ids

    def get_all_mem_ids(self) -> list[str]:
        ids = []
        for anchor in self._days.values():
            ids.extend(anchor.mem_ids)
        return ids

    def get_previous_mem_id(self, current_date: Optional[date] = None) -> Optional[str]:
        """获取前一天最后一条记忆 ID，用于建立 temporal_next 边"""
        day = (current_date or date.today()) - timedelta(days=1)
        for offset in range(7):
            check_day = day - timedelta(days=offset)
            anchor = self._days.get(check_day)
            if anchor and anchor.mem_ids:
                return anchor.mem_ids[-1]
        return None

    def remove_ids(self, mem_ids: set[str]):
        for anchor in self._days.values():
            anchor.mem_ids = [m for m in anchor.mem_ids if m not in mem_ids]
        self._days = {d: a for d, a in self._days.items() if a.mem_ids}

    @property
    def total_days(self) -> int:
        return len(self._days)

    @property
    def total_memories(self) -> int:
        return sum(len(a.mem_ids) for a in self._days.values())


def time_decay(mem_date: date, today: Optional[date] = None) -> float:
    """exp(-lambda * days_ago)"""
    today = today or date.today()
    days_ago = (today - mem_date).days
    if days_ago < 0:
        days_ago = 0
    return math.exp(-TIME_DECAY_LAMBDA * days_ago)


def parse_date(date_str: str) -> date:
    """从 YYYY-MM-DD 字符串解析日期"""
    return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
