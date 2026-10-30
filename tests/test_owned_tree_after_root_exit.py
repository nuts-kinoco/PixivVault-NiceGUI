"""No OS calls: descendants stay observable after the originally spawned root exits."""
from threading import Lock
from types import SimpleNamespace

from tools.check_library_calendar_slider_owned import OwnedTree
from tools import check_native_window as native


def tree(monkeypatch, live, pairs):
    monkeypatch.setattr(native,'process_identity',lambda pid:live.get(pid))
    observer=object.__new__(OwnedTree)
    observer.process=SimpleNamespace(pid=100,poll=lambda:0)
    observer.identities={100:1000,200:2000}
    observer.errors=[]
    observer.last_living=None
    observer._lock=Lock()
    observer.native=SimpleNamespace(
        process_identity=lambda pid:live.get(pid),
        process_pairs=lambda:pairs,
        living_pids=lambda ids:{pid for pid,birth in ids.items() if live.get(pid)==birth},
        adopt_descendants=native.adopt_descendants)
    return observer


def test_known_live_child_new_descendant_is_not_lost_when_root_has_exited(monkeypatch):
    observer=tree(monkeypatch,{200:2000,300:3000},[(200,100),(300,200)])
    observer.sample()
    assert not observer.errors
    assert observer.identities[300]==3000
    assert observer.last_living==[200,300]


def test_reused_known_child_pid_never_adopts_its_new_descendants(monkeypatch):
    observer=tree(monkeypatch,{200:9000,300:10000},[(200,77),(300,200)])
    observer.sample()
    assert 300 not in observer.identities
    assert observer.last_living==[]


def test_reverse_pid_order_grandchild_is_discovered_to_fixed_point(monkeypatch):
    observer=tree(monkeypatch,{200:2000,50:3000,21:4000},[(21,50),(200,100),(50,200)])
    observer.sample()
    assert observer.last_living==[21,50,200]
    assert observer.identities[21]==4000
