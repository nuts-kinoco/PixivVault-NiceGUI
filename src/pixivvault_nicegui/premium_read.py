"""Borrowed read routing; own Cookie sessions never receive borrowed tokens."""
from .external_vault import VaultError

# Only resolved availability failures may start the ordinary own-account read.
# STOP, unknown send outcomes, stale sessions and persistence faults propagate.
FALLBACK_CODES=frozenset(('locked','rate-limited','backoff','token-unavailable',
    'payout-failed','expired-payout','short-payout','oauth-failed','not-premium',
    'transport-unavailable','invalid-payout','refresh-token-rejected','premium-read-rejected'))

def unavailable_message(code, *, reason=None):
    if code == 'borrow-accounts-unavailable' and reason == 'not-premium':
        return '利用可能な借用アカウントがありません。候補に非プレミアムのアカウントが含まれています。Shaft連携の状態を確認してください。'
    return {
        'busy':'\u501f\u7528\u30a2\u30ab\u30a6\u30f3\u30c8\u306e\u6e96\u5099\u4e2d\u3067\u3059\u3002\u3057\u3070\u3089\u304f\u5f85\u3063\u3066\u304b\u3089\u3082\u3046\u4e00\u5ea6\u300c\u53d6\u5f97\u30fb\u66f4\u65b0\u300d\u3092\u62bc\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'not-premium':'返された借用アカウントは非プレミアムです。Shaft連携の状態を確認してください。',
        'expired-payout':'Shaftが失効済みアカウントを返しました。外部連携でアカウントの状態を確認してから再試行してください。',
        'fallback-date-range-unavailable':'本人の経路では期間指定を適用できません。借用プレミアムを有効にしてください。',
        'invalid-search-date':'検索期間を YYYY-MM-DD で指定し、開始日を終了日以前にしてください。',
        'paging-account-unavailable':'この検索で使用した借用アカウントを利用できないため、続きの取得を止めました。先頭から再検索してください。',
        'stale-page':'検索条件または保管庫の状態が変わりました。先頭から再検索してください。',
        'premium-disabled':'借用プレミアムはOFFです。ONにする場合はパスワードを確認してください。',
        'borrow-accounts-unavailable':'借用アカウントの取得枠またはトークンが利用できません。通信を止めました。Shaft連携で各登録の待ち時間を確認してください。',
        'borrow-accounts-cooling-down':'すべての借用アカウントが待機中です。通信を止めました。Shaft連携で再開までの時間を確認してください。',
        'premium-budget-unavailable':'利用回数の保管状態を確認できないため、通信を止めました。保管庫を確認してください。',
        'pixiv-network-stopped':'通信の停止状態を確認しました。Shaft連携で停止状態を確認してください。自動で再送しません。',
        'pixiv-outcome-uncertain':'通信結果を確認できないため、追加の通信を止めました。Shaft連携で保管状態を確認してください。',
    }.get(code)

def borrowed_ready(vault):
    if vault is None:return False
    status=vault.status()
    gate=getattr(vault,'premium_authorization',None)
    return bool((gate is None or gate.status()['enabled']) and status.get('open') and status.get('mode')=='nana7mi'
                and status.get('transport_available',True))

class PremiumSearch:
    """Search adapter used by personal search and tag rankings, including fallback."""
    def __init__(self,vault,reader=None,*,preserve_expiry=False):
        self.vault=vault;self.reader=reader;self.preserve_expiry=preserve_expiry
    def available(self):return borrowed_ready(self.vault) or self.reader is not None
    def search(self,word,bookmark_min=0,*,sort='popular_desc',r18=False,exclude_ai=False,offset=0,start_date=None,end_date=None,page_session=None):
        from .external_vault_transport import search_params
        validated=search_params(word,bookmark_min,sort=sort,r18=r18,exclude_ai=exclude_ai,offset=offset,start_date=start_date,end_date=end_date)
        dates={k:validated[k] for k in ('start_date','end_date') if k in validated}
        ready=borrowed_ready(self.vault)
        if page_session is not None and not ready:raise VaultError('paging-account-unavailable')
        if ready:
            try:
                paging={'with_page_session':True} if getattr(self.vault,'supports_page_sessions',False) else {}
                if page_session is not None:paging['page_session']=page_session
                packet=self.vault.search(word,bookmark_min,sort=sort,r18=r18,exclude_ai=exclude_ai,offset=offset,**dates,**paging)
                if self.preserve_expiry and packet.get('_borrow_warning')=='expired-payout':
                    import logging
                    logging.getLogger(__name__).warning('個人検索: Shaftの払い出しが失効済みです（expired-payout）。別の登録で検索を継続しました。')
                return dict(packet,order_label=(
                    '人気順（プレミアム）・R-18 絞り込み未適用（原 API）' if sort=='popular_desc'
                    else '借用プレミアム検索・R-18 絞り込み未適用（原 API）'))
            except VaultError as error:
                if self.preserve_expiry and (getattr(error,'warning',None)=='expired-payout'
                        or (getattr(error,'reason',None) or error.code)=='expired-payout'):
                    import logging
                    logging.getLogger(__name__).warning('個人検索: Shaftの払い出しが失効済みです（expired-payout）。')
                    if (getattr(error,'reason',None) or error.code)=='expired-payout':
                        raise VaultError('expired-payout') from None
                    raise  # Preserve STOP/network codes alongside the bounded warning.
                if page_session is not None and error.code in FALLBACK_CODES:raise VaultError('paging-account-unavailable') from None
                if error.code not in FALLBACK_CODES:raise
        if dates:raise VaultError('fallback-date-range-unavailable')
        if self.reader is None:raise VaultError('token-unavailable')
        if offset:raise VaultError('fallback-page-unavailable')
        self.reader.require_identity()
        packet=self.reader.search_illusts(word,page=1,order='date_d',mode='r18' if r18 else 'safe')
        if type(packet) is not dict or type(packet.get('works')) is not list:raise VaultError('invalid-response')
        rows=[]
        for raw in packet['works']:
            if type(raw) is not dict or not raw.get('id'):continue
            rows.append(dict(id=raw['id'],title=raw.get('title',''),
                user=dict(id=raw.get('user_id'),name=raw.get('user_name','')),
                type={0:'illust',1:'manga',2:'ugoira'}.get(raw.get('illust_type',0),'illust'),
                image_urls=dict(medium=raw.get('thumbnail_url')),
                tags=[dict(name=t) for t in raw.get('tags',[])],
                **{k:raw[k] for k in ('page_count','x_restrict','create_date') if k in raw},
                **({'illust_ai_type':raw['ai_type']} if 'ai_type' in raw else {})))
        return dict(illusts=rows,next_url=None,
            order_label='新着順のみ（本人の経路）・人気順／ブクマ数下限は未適用')
