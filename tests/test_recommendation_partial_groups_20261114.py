from pixivvault_nicegui.recommendation_state import RecommendationController, RecommendationPreferences, RecommendationSnapshot, RecommendationGroup, RecommendationWork

class Source:
    def load(self):
        return RecommendationSnapshot(sections=(('superlike',(RecommendationGroup('superlike','S',(RecommendationWork('11','old','9','A'),)),)),),boosted=(('8','A'),))

class Fetch:
    fail=set()
    def search(self,tag,page,mode):
        if tag in self.fail: raise RuntimeError('synthetic')
        n=(1000 if tag=='alpha' else 2000)+page*100
        return {'works':[dict(id=str(n+i),title='synthetic',user_id='9',user_name='A') for i in range(1,4)],'total':30}
    def author(self,aid,limit,excluded):
        if aid in self.fail: raise RuntimeError('synthetic')
        return [dict(id=str(3000+i),title='synthetic',user_id=aid,user_name='A') for i in range(1,limit+1)]
    def detail(self,wid): return dict(id=wid,title='synthetic',user_id='9',user_name='A')

def group(state,kind,key): return next(g for g in state['sections'][kind] if g.key==key)

def test_partial_retains_only_failed_groups_and_success_replaces(tmp_path):
    f=Fetch(); f.fail=set()
    c=RecommendationController(Source(),RecommendationPreferences(tmp_path/'prefs'),acquisition=f,trend_provider=lambda:[dict(tag='alpha',display='Alpha'),dict(tag='beta',display='Beta')])
    try:
        s=c.refresh(); s=c.update(s['revision']); s=c.add_trend('beta','Beta',s['revision']); s=c.next_page('certain','alpha',s['revision']); s=c.more_author('8',24,s['revision'])
        alpha=group(s,'certain','alpha'); assert len(alpha.works)==6
        assert len(group(s,'boosted','8').works)==24
        f.fail={'alpha'}; s=c.update(s['revision'])
        assert group(s,'certain','alpha')==alpha and 'certain:alpha' in s['errors']
        assert len(group(s,'certain','beta').works)==3
        assert len(group(s,'boosted','8').works)==12
        f.fail=set(); s=c.more_author('8',24,s['revision']); before=group(s,'boosted','8')
        f.fail={'8'}; s=c.update(s['revision']); assert group(s,'boosted','8')==before
        f.fail=set(); s=c.update(s['revision']); assert len(group(s,'certain','alpha').works)==3 and len(group(s,'boosted','8').works)==12 and not s['errors']
    finally: c.close()
