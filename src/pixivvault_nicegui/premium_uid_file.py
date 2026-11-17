"""The original app's fixed UID list file (nana7mi_uids.txt) and how it is applied.

The file is the authority for the registered UIDs whenever it exists and reads
cleanly: it is shown when the borrow popup opens, saved when the popup is
confirmed, and reflected into an already opened vault at application start.
A missing, unreadable or invalid file never changes the saved UIDs.
"""
from pathlib import Path

# Overridable via ApplicationServices.default_uid_path / PremiumGateDialog(default_uid_path=...).
DEFAULT_UID_PATH=r"S:\Projects\PixivVault\nana7mi_uids.txt"
MAX_UIDS=10
FILE_LIMIT=65536


def uid_text_errors(text):
    """Return ``(uids, errors)``, collecting every problem line.

    The same rules as ``external_vault.parse_uid_text`` (which stops at the first
    bad line and is kept for the vault itself). Messages carry line numbers only;
    a UID value is never written to the screen.
    """
    uids=[];errors=[]
    if type(text) is not str:return [],['ファイルの中身が読めませんでした。']
    for number,raw in enumerate(text.lstrip('\ufeff').splitlines(),1):
        line=raw.split('#',1)[0].strip()
        if not line:continue
        try:value=int(line)
        except ValueError:
            errors.append(f'{number}行目が整数ではありません。');continue
        if value<=0:
            errors.append(f'{number}行目が正の整数ではありません。');continue
        if value not in uids:uids.append(value)
    if len(uids)>MAX_UIDS:errors.append(f'UIDは最大{MAX_UIDS}件までです（{len(uids)}件ありました）。')
    if not uids and not errors:errors.append('UIDが1件も見つかりませんでした。')
    return uids,errors


def read_uid_file(path):
    """Return ``(state, uids, errors)``; state is ``absent``, ``ok`` or ``error``."""
    if not path:return 'absent',[],[]
    try:
        with Path(path).open('rb') as stream:payload=stream.read(FILE_LIMIT+1)
    except FileNotFoundError:return 'absent',[],[]
    except OSError:return 'error',[],['UID一覧ファイルを読めませんでした（UTF-8のテキストにしてください）。']
    if len(payload)>FILE_LIMIT:return 'error',[],['UID一覧ファイルが大きすぎます。']
    try:text=payload.decode('utf-8-sig')
    except UnicodeError:return 'error',[],['UID一覧ファイルを読めませんでした（UTF-8のテキストにしてください）。']
    uids,errors=uid_text_errors(text)
    if errors:return 'error',[],errors
    return 'ok',uids,[]


def apply_uid_file(vault,path):
    """Reflect the file into an OPENED vault. Never raises.

    Returns ``absent``/``error`` (file unusable: saved UIDs kept), ``locked``,
    ``same`` (nothing to write), ``pending`` (an unconfirmed send blocks any
    configuration change, so recovery must come first), ``applied`` or ``failed``.
    """
    try:
        state,uids,_errors=read_uid_file(path)
        if state!='ok':return state
        current=vault.registered_uids()
        if current is None:return 'locked'
        if current==uids:return 'same'
        if vault.status().get('oauth_review_required'):return 'pending'
        vault.update_config(uid_text='\n'.join(map(str,uids)))
        return 'applied'
    except Exception:return 'failed'
