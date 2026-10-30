"""Original learning-review filtering, without network or preference mutation."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import json

PAGE_SIZE = 10

@dataclass(frozen=True)
class ReviewFilter:
    text: str = ''
    period: str = 'all'
    positive_unsaved_only: bool = True
    positive_superlike_only: bool = False
    negative_hate_only: bool = False

    def matches(self, row, positive, *, now, session_started):
        if self.period not in ('all','today','7d','30d','session'):
            raise ValueError('invalid review period')
        verdict = row.get('verdict')
        if verdict not in (('like','superLike') if positive else ('skip','hate')):
            return False
        if self.text:
            try: tags=json.loads(row.get('tags') or '[]')
            except (ValueError,TypeError): tags=[]
            hay=(row.get('title') or '')+' '+' '.join(str(t) for t in tags)
            if self.text.lower() not in hay.lower():return False
        if self.period!='all':
            try: judged=datetime.fromisoformat(row.get('judged_at') or '')
            except (ValueError,TypeError):return False
            # A mismatched timezone cannot be silently treated as local time.
            if (judged.tzinfo is None)!=(now.tzinfo is None):return False
            if self.period=='today' and judged.date()!=now.date():return False
            if self.period=='7d' and judged<now-timedelta(days=7):return False
            if self.period=='30d' and judged<now-timedelta(days=30):return False
            if self.period=='session' and judged<session_started:return False
        if positive:
            if self.positive_unsaved_only and row.get('work_meta') and not row.get('is_deleted'):return False
            if self.positive_superlike_only and verdict!='superLike':return False
        elif self.negative_hate_only and verdict!='hate':return False
        return True

    def rows(self, rows, positive, *, now, session_started):
        return tuple(sorted((row for row in rows if self.matches(row,positive,now=now,session_started=session_started)),
                            key=lambda row:row.get('judged_at') or '', reverse=True))
