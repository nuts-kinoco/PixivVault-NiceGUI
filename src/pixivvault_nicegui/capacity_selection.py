"""Select original author storage from already discovered candidates.

No filesystem discovery is performed here. Inputs retain enumeration order.
"""
import re

def choose_author_entry(author_id, entries):
    if type(author_id) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}',author_id):
        raise ValueError('invalid author id')
    folders=[];archives=[]
    for name,kind,size in entries:
        if type(name) is not str or kind not in ('folder','zip') or type(size) is not int or size<0:
            raise ValueError('invalid candidate')
        if kind=='zip' and name.endswith(f'({author_id}).zip'):archives.append((name,size))
        elif kind=='folder' and name.endswith(f'({author_id})'):folders.append((name,size))
    choices=archives or folders
    if not choices:return None
    return max(choices,key=lambda entry:entry[1])[0]
