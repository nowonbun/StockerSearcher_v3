"""외부 스케줄러 없이 사용하는 5필드 cron 판정기. 시간대는 Asia/Seoul 고정."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

SEOUL = timezone(timedelta(hours=9), 'Asia/Seoul')


class Cron:
    def __init__(self, expression: str):
        if not isinstance(expression, str) or len(expression) > 200:
            raise ValueError('cron은 5필드 문자열이어야 합니다')
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError('cron은 분 시 일 월 요일의 5필드여야 합니다')
        self.fields = fields
        self.values = [self._parse(text, lo, hi) for text, lo, hi in zip(fields, (0, 0, 1, 1, 0), (59, 23, 31, 12, 7))]
        if 7 in self.values[4]:
            self.values[4].add(0)

    @staticmethod
    def _parse(text: str, lo: int, hi: int) -> set[int]:
        values: set[int] = set()
        try:
            for part in text.split(','):
                bits = part.split('/')
                if len(bits) > 2:
                    raise ValueError()
                step = int(bits[1]) if len(bits) == 2 else 1
                if step <= 0:
                    raise ValueError()
                base = bits[0]
                if base == '*':
                    start, end = lo, hi
                elif '-' in base:
                    start, end = map(int, base.split('-'))
                else:
                    start = int(base)
                    end = hi if len(bits) == 2 else start
                if not lo <= start <= end <= hi:
                    raise ValueError()
                values.update(range(start, end + 1, step))
        except (ValueError, TypeError):
            raise ValueError(f'잘못된 cron 필드: {text}') from None
        return values

    def matches(self, moment: datetime) -> bool:
        if moment.tzinfo is None:
            raise ValueError('시간대가 있는 날짜가 필요합니다')
        local = moment.astimezone(SEOUL)
        minute, hour, dom, month, dow = self.values
        day_match = local.day in dom
        week_match = (local.weekday() + 1) % 7 in dow
        # POSIX cron: 일과 요일을 모두 제한하면 둘 중 하나만 일치해도 실행합니다.
        day = (day_match or week_match) if self.fields[2] != '*' and self.fields[4] != '*' else (day_match and week_match)
        return local.minute in minute and local.hour in hour and local.month in month and day
