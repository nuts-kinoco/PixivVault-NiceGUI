"""Explicit recommendation acquisition over injected read-only Pixiv methods.

Original recommend_tab.py: top five trend searches, independent added-tag paging,
judged/hidden filtering, boost registry, nonempty carryover and retry same page.
Only the dedicated recommendation preference sidecar is written.
"""
from __future__ import annotations
from dataclasses import asdict
import json
from threading import RLock
from .recommendation_state import (RecommendationError, RecommendationGroup,
    RecommendationWork, identifier, text, MAX_RECORDS)


def work(row):
    return RecommendationWork.from_record(row)


def encode(group):
    return asdict(group)


def decode(row):
    if type(row) is not dict or len(row['works']) > MAX_RECORDS:
        raise RecommendationError()
    return RecommendationGroup(text(row['key']),text(row['title']),
        tuple(RecommendationWork(**r) for r in row['works']),
        int(row.get('page',1)),int(row.get('total',0)),bool(row.get('exhausted',False)))


class PixivRecommendationAdapter:
    """No model calls, no cookie ownership; the application owns the reader."""
    def __init__(self,reader):
        self.reader=reader
    def search(self,tag,page,mode):
        return self.reader.search_recommendation_illusts(tag,page=page,mode=mode)
    def author(self,author_id,limit,excluded_ids):
        count=0
        def stop(row):
            nonlocal count
            if str(row.get('id','')) not in excluded_ids:
                count+=1
            return count > limit
        rows=self.reader.get_user_works(author_id,early_exit_checker=stop,lane='interactive')
        return [r for r in rows if str(r.get('id','')) not in excluded_ids][:limit]
    def detail(self,work_id):
        return self.reader.get_discovery_work(work_id)


class RecommendationAcquisition:
    def __init__(self,controller,source=None,trend_provider=None,judged_provider=None):
        self.controller=controller
        self.source=source
        self.trend_provider=trend_provider
        self.judged_provider=judged_provider
        self.errors={}
        self._mutex=RLock()
        # Original two grid rows; shell card-size updates can change this count.
        self.visible_limit=12

    def envelope(self,source,settings):
        raw=settings.get('recommend_acquisition_state')
        if raw is None:
            return dict(cache={},boosts={},added=[dict(tag=t,display=d) for t,d in source.added_tags])
        result=json.loads(raw)
        if type(result) is not dict or set(result)!={'cache','boosts','added'}:
            raise RecommendationError()
        if not set(result['cache']) <= {'certain','boosted','superlike','trend_added'}:
            raise RecommendationError()
        return result

    def decorate(self,state,source,settings):
        env=self.envelope(source,settings)
        sections=dict(state['sections'])
        unavailable=set(state['unavailable'])
        hidden=set(state['hidden_works']);authors=set(state['hidden_authors'])
        registry=dict(source.boosted)
        for aid,entry in env['boosts'].items():
            identifier(aid)
            if entry['enabled']:registry[aid]=text(entry['name'])
            else:registry.pop(aid,None)
        for kind,rows in env['cache'].items():
            sections[kind]=tuple(decode(row) for row in rows)
            unavailable.discard(kind)
        # A cached detail is not a judgment: current verdict order owns membership.
        current_super=dict(source.sections).get('superlike')
        if current_super is not None:
            cached={w.work_id:w for g in sections.get('superlike',()) for w in g.works}
            sections['superlike']=tuple(RecommendationGroup(g.key,g.title,
                tuple(cached.get(w.work_id,w) for w in g.works)) for g in current_super)
        added_tags={row['tag'] for row in env['added']}
        if 'trend_added' in sections:
            sections['trend_added']=tuple(g for g in sections['trend_added'] if g.key in added_tags)
        if 'boosted' in sections:
            # Preserve old carryover without a registry table; apply explicit removals.
            sections['boosted']=tuple(g for g in sections['boosted']
                if g.key not in env['boosts'] or env['boosts'][g.key]['enabled'])
        for kind,groups in sections.items():
            sections[kind]=tuple(RecommendationGroup(g.key,g.title,
                tuple(w for w in g.works if w.work_id not in hidden and w.author_id not in authors),
                g.page,g.total,g.exhausted) for g in groups)
        trends=()
        if self.trend_provider is not None:
            try:
                trends=tuple(dict(row) for row in self.trend_provider())
                for row in trends:
                    text(row['tag']);text(row.get('display') or row['tag'])
                unavailable.discard('trend')
            except Exception:
                self.errors['trend']='trend_unavailable'
        if not env['added']:
            sections.setdefault('trend_added',())
            # The original list is known empty, even when no searches were made.
            unavailable.discard('trend_added')
        state.update(sections=sections,unavailable=tuple(sorted(unavailable)),
            trends=trends,added_tags=tuple(env['added']),boosted=tuple(registry.items()),
            errors=dict(self.errors),can_acquire=self.source is not None and state['writable'])
        return state

    def begin(self,revision,*,require_source=True):
        c=self.controller
        if c.preferences is None or (require_source and self.source is None):
            raise RecommendationError()
        state=c.snapshot()
        if revision!=state['revision']:raise RecommendationError()
        _,_,settings=c.preferences.snapshot()
        env=self.envelope(c._snapshot,settings)
        return state,env

    def excluded(self,state):
        judged=set(self.controller._snapshot.judged)
        if self.judged_provider is not None:
            judged.update(identifier(str(v)) for v in self.judged_provider())
        return judged|set(state['hidden_works'])

    def filtered(self,rows,state,existing=()):
        excluded=self.excluded(state)|set(existing)
        hidden_authors=set(state['hidden_authors']);result=[]
        for raw in rows:
            value=work(raw)
            if value.work_id in excluded or value.author_id in hidden_authors:continue
            excluded.add(value.work_id);result.append(value)
            if len(result)>MAX_RECORDS:raise RecommendationError()
        return tuple(result)

    def search(self,tag,display,page,state,existing=()):
        result=self.source.search(tag,page,self.controller._snapshot.search_mode)
        values=self.filtered(result['works'],state,existing)
        total=result.get('total') or len(values)
        if type(total) is not int or total<0:raise RecommendationError()
        return RecommendationGroup(tag,display,values,page,total,
            not values or len(values)+len(existing)>=total)

    def store(self,env,revision):
        self.controller.preferences.store_acquisition(env,revision)
        return self.controller.snapshot()

    def update(self,revision):
        with self._mutex:
            self.controller.refresh()
            state,env=self.begin(revision)
            self.errors={}
            # Each shelf may fail independently; successful shelves publish together
            # with CAS. Empty/failing searches do not erase previous good carryover.
            for kind,tags in (('certain',state['trends'][:5]),('trend_added',env['added'])):
                picked=[]
                for row in tags:
                    tag=text(row['tag']);display=text(row.get('display') or tag)
                    try:
                        group=self.search(tag,display,1,state)
                        if group.works:picked.append(encode(group))
                    except Exception:self.errors[kind+':'+tag]='fetch_failed'
                if picked:env['cache'][kind]=picked
            boosted=[]
            excluded=self.excluded(state)
            for aid,name in state['boosted']:
                if aid in state['hidden_authors']:continue
                try:
                    rows=self.source.author(aid,self.visible_limit,excluded)
                    values=self.filtered(rows,state)[:self.visible_limit]
                    if values:boosted.append(encode(RecommendationGroup(aid,name,values)))
                except Exception:self.errors['boosted:'+aid]='fetch_failed'
            if boosted:env['cache']['boosted']=boosted
            previous=dict(self.controller._snapshot.sections).get('superlike',())
            if previous:
                values=[]
                for old in previous[0].works:
                    if old.work_id in state['hidden_works']:continue
                    try:
                        fresh=work(self.source.detail(old.work_id))
                        if fresh.work_id!=old.work_id:raise RecommendationError()
                    except Exception:
                        self.errors['superlike:'+old.work_id]='fetch_failed'
                        fresh=old
                    if fresh.author_id not in state['hidden_authors']:values.append(fresh)
                    if len(values)>=self.visible_limit*3:break
                if values:env['cache']['superlike']=[encode(RecommendationGroup('superlike',previous[0].title,tuple(values)))]
            return self.store(env,revision)

    def add_trend(self,tag,display,revision):
        tag=text(tag);display=text(display)
        if not tag:raise RecommendationError()
        with self._mutex:
            state,env=self.begin(revision)
            if any(r['tag']==tag for r in env['added']):return state
            # Failure commits neither registration nor page; retry is the same GET.
            group=self.search(tag,display or tag,1,state)
            if not group.works:return state
            env['added'].append(dict(tag=tag,display=display or tag))
            env['cache'].setdefault('trend_added',[]).append(encode(group))
            return self.store(env,revision)

    def remove_trend(self,tag,revision):
        with self._mutex:
            state,env=self.begin(revision,require_source=False)
            env['added']=[r for r in env['added'] if r['tag']!=tag]
            env['cache']['trend_added']=[r for r in env['cache'].get('trend_added',[]) if r['key']!=tag]
            return self.store(env,revision)

    def next_page(self,kind,tag,revision):
        if kind not in ('certain','trend_added'):raise RecommendationError()
        with self._mutex:
            state,env=self.begin(revision)
            raw_groups=(tuple(decode(r) for r in env['cache'][kind]) if kind in env['cache'] else dict(self.controller._snapshot.sections).get(kind,()))
            old=next((g for g in raw_groups if g.key==tag),None)
            if old is None or old.exhausted:return state
            try:
                group=self.search(tag,old.title,old.page+1,state,tuple(w.work_id for w in old.works))
            except Exception:
                self.errors[kind+':'+tag]='fetch_failed_retry_same_page'
                raise
            combined=RecommendationGroup(tag,old.title,old.works+group.works,
                group.page,group.total,group.exhausted)
            rows=env['cache'].get(kind,[encode(g) for g in raw_groups])
            env['cache'][kind]=[encode(combined) if r['key']==tag else r for r in rows]
            self.errors.pop(kind+':'+tag,None)
            return self.store(env,revision)

    def boost(self,author_id,name,enabled,revision):
        identifier(author_id);text(name)
        if type(enabled) is not bool:raise RecommendationError()
        with self._mutex:
            # Registration needs no GET and remains available in offline mode.
            c=self.controller
            if c.preferences is None:raise RecommendationError()
            state=c.snapshot()
            if state['revision']!=revision:raise RecommendationError()
            _,_,settings=c.preferences.snapshot()
            env=self.envelope(c._snapshot,settings)
            env['boosts'][author_id]=dict(name=name,enabled=enabled)
            self.store(env,revision)
            if enabled and self.source is not None:
                state=c.snapshot();revision=state['revision']
                try:
                    values=self.filtered(self.source.author(author_id,self.visible_limit,self.excluded(state)),state)
                except Exception:
                    self.errors['boosted:'+author_id]='fetch_failed'
                else:
                    if values:
                        rows=env['cache'].get('boosted',[encode(g) for g in state['sections'].get('boosted',())])
                        env['cache']['boosted']=[r for r in rows if r['key']!=author_id]+[encode(RecommendationGroup(author_id,name,values))]
                        return self.store(env,revision)
            return c.snapshot()

    def more_author(self,author_id,limit,revision):
        identifier(author_id)
        if type(limit) is not int or limit not in (self.visible_limit*2,self.visible_limit*3):
            raise RecommendationError()
        with self._mutex:
            state,env=self.begin(revision)
            name=dict(state['boosted']).get(author_id)
            if name is None or author_id in state['hidden_authors']:return state
            values=self.filtered(self.source.author(author_id,limit,self.excluded(state)),state)[:limit]
            if values:
                rows=env['cache'].get('boosted',[encode(g) for g in state['sections'].get('boosted',())])
                env['cache']['boosted']=[r for r in rows if r['key']!=author_id]+[encode(RecommendationGroup(author_id,name,values))]
                return self.store(env,revision)
            return state
