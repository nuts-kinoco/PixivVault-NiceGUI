"""Original discovery candidate refill timing, independent from image prefetch.

Fixed original discovery_tab: look ahead below10; three empty topups spaced2s;
restock only when exhausted/fetch_failed, immediately then30s, max20rounds.
The configured discovery lifecycle arms this after automatic initial acquisition
or an explicit retry. Demo/unconfigured learning never arms it.
"""
from dataclasses import dataclass

LOOKAHEAD=10
EMPTY_TOPUPS=3
TOPUP_DELAY=2.0
RESTOCK_INTERVAL=30.0
RESTOCK_ROUNDS=20

@dataclass
class DiscoveryRefill:
    armed:bool=False
    active:bool=False
    running:bool=False
    phase:str='topup'
    empty_topups:int=0
    rounds:int=0
    failed_rounds:int=0
    next_at:float=0.0
    gave_up:bool=False
    last_failed:bool=False

    def arm(self,now):
        self.armed=True
        self.resume(now)

    def resume(self,now):
        self.active=True
        self.running=False
        self.phase='topup'
        self.empty_topups=0
        self.rounds=0
        self.failed_rounds=0
        self.next_at=now
        self.gave_up=False

    def pause(self):
        self.active=False
        self.running=False

    def delay(self,remaining,now):
        if not self.armed or not self.active or self.running or self.gave_up:return None
        if remaining==0:
            if self.phase!='restock':
                self.phase='restock';self.rounds=0;self.failed_rounds=0;self.next_at=now
            return max(0.0,self.next_at-now)
        if self.phase=='restock':
            self.phase='topup';self.rounds=0;self.failed_rounds=0;self.next_at=now
        if remaining>=LOOKAHEAD or self.empty_topups>=EMPTY_TOPUPS:return None
        return max(0.0,self.next_at-now)

    def begin(self,remaining,now):
        delay=self.delay(remaining,now)
        if delay is None or delay>0:return False
        if self.phase=='restock' and self.rounds>=RESTOCK_ROUNDS:
            self.gave_up=True
            return False
        self.running=True
        return True

    def complete(self,added,failed,now):
        self.running=False
        self.last_failed=bool(failed)
        if added:
            self.empty_topups=0;self.rounds=0;self.failed_rounds=0
            self.phase='topup';self.next_at=now
        elif self.phase=='restock':
            self.rounds+=1
            self.failed_rounds+=int(bool(failed))
            self.next_at=now+RESTOCK_INTERVAL
        else:
            self.empty_topups+=1
            self.next_at=now+TOPUP_DELAY
