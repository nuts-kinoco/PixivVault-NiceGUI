"""Read-only capacity measurement of verified active managed illustrations."""

def measure_managed_capacities(owner, author_ids):
    author_ids=tuple(author_ids)
    if len(author_ids)>50 or len(set(author_ids))!=len(author_ids):
        raise ValueError('invalid capacity targets')
    before=owner._generations.entries()
    receipts=owner.saved_receipts_with_dates()
    targets=set(author_ids); totals={};failed=set()
    for relative,receipt,digest,_ in receipts:
        author=receipt['author_id']
        if author not in targets:continue
        try:
            measured_author,size=owner.measure_active_receipt_bytes(relative,digest)
            if measured_author!=author:raise ValueError('author changed')
            totals[author]=totals.get(author,0)+size
        except Exception:
            failed.add(author)
    if owner._generations.entries()!=before:
        raise ValueError('active generation changed')
    return {author:value for author,value in totals.items() if author not in failed}, tuple(sorted(failed)), before
