"""S07 next synthetic TXT save after an actual prior GUI preferences selection.

Read-only import of *generated* prior preference DB, copy to fresh generated
artifact root, and execute one real local novel owner job. No actual Pixiv data.
"""
from __future__ import annotations
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import shutil
import socket
import sys
from uuid import uuid4
from fastapi import FastAPI

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.p3_residuals_fixtures import create_services
from tools.stage5_offline_parent_fence import OfflineNetworkFence

SOURCE=ROOT/'artifacts/p3-369-s07-s08-prefs-r1/generated-data/preferences/preferences.sqlite3'
# Independent, known generated input: FixtureReader.get_novel_payload('501','91')
# contains 'Generated fixture text.[newpage]Second page.'. The practical TXT
# serialization writes a UTF-8 BOM and renders the page break as \n\n---\n\n.
EXPECTED_RAW_501='Generated fixture text.[newpage]Second page.'
EXPECTED_TXT_501=b'\xef\xbb\xbfGenerated fixture text.\n\n---\n\nSecond page.'

def sha(p):return sha256(p.read_bytes()).hexdigest()


def verify_generated_txt_501(txt_path: Path, receipt_path: Path) -> dict:
    """Verify actual bytes, encoding, identity and receipt, never just suffix.

    Only for explicitly generated work 501 / author 91. Refuse unknown files,
    different work identity, empty/invalid text and mismatched body or metadata.
    """
    if (not txt_path.is_file() or txt_path.is_symlink()
            or txt_path.name!='Generated novel(501).txt'
            or not receipt_path.is_file() or receipt_path.is_symlink()
            or txt_path.parent != receipt_path.parent):
        raise AssertionError('TXT expected generated work 501 file/receipt missing or unsafe')
    if (not txt_path.parent.name.startswith('novel-501-')
            or len(list(txt_path.parent.glob('*.txt')))!=1
            or any(txt_path.parent.glob('*.epub'))):
        raise AssertionError('TXT work 501 output layout is invalid')
    try:
        receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
    except (OSError,UnicodeError,ValueError) as error:
        raise AssertionError('TXT receipt unreadable') from error
    if (type(receipt) is not dict or receipt.get('schema')!=2
            or receipt.get('kind')!='novel-export'
            or receipt.get('work_id')!='501' or receipt.get('author_id')!='91'
            or receipt.get('format')!='TXT'
            or receipt.get('metadata')!={'title':'Generated novel',
                'author':'Fixture author','chapters':['501'],'series_id':None}):
        raise AssertionError('TXT receipt belongs to a different generated work')
    payload=txt_path.read_bytes()
    if not payload or not payload.startswith(b'\xef\xbb\xbf'):
        raise AssertionError('TXT body is empty or missing UTF-8 BOM')
    try:
        body=payload.decode('utf-8-sig',errors='strict')
    except UnicodeError as error:
        raise AssertionError('TXT body is not valid UTF-8 with BOM') from error
    if not body.strip() or payload!=EXPECTED_TXT_501 or body!=EXPECTED_TXT_501.decode('utf-8-sig'):
        raise AssertionError('TXT body differs from exact generated novel 501 content')
    entries=receipt.get('outputs')
    if (type(entries) is not list or len(entries)!=1 or
            entries[0]!={'name':txt_path.name,'bytes':len(payload),
                          'sha256':sha256(payload).hexdigest()}):
        raise AssertionError('TXT receipt output hash/length/name inconsistent with body')
    return {'work_id':'501','author_id':'91','encoding':'utf-8-sig',
            'decoded_characters':len(body),'body_sha256':sha256(payload).hexdigest(),
            'byte_count':len(payload),'text_matches_fixture':True}


def finalize_s07_run(report, services, source, source_hash, result_path, *,
                     sha_func=None, write_func=None, network=None):
    """Never hide a primary error behind a cleanup or receipt failure.

    Called while the outer reversible network fence is still installed.
    The caller's with/finally restores socket functions on *all* outcomes.
    """
    if sha_func is None:sha_func=sha
    if write_func is None:
        def write_func(path,payload):
            with path.open('x',encoding='utf8') as stream:
                json.dump(payload,stream,ensure_ascii=False,indent=2)
    errors=[]
    if services is not None:
        try:services.close()
        except Exception as error:
            errors.append({'stage':'services.close','type':type(error).__name__})
    try:
        same=sha_func(source)==source_hash
        report['source_generated_preference_unchanged']=same
        if not same:errors.append({'stage':'source_sha','type':'HashMismatch'})
    except Exception as error:
        report['source_generated_preference_unchanged']=False
        errors.append({'stage':'source_sha','type':type(error).__name__})
    report['parent_outbound_attempts']=network.attempt_count if network is not None else 0
    if report['parent_outbound_attempts']:
        errors.append({'stage':'offline_parent_fence','type':'OutboundAttempt'})
    report['cleanup_errors']=errors
    report['success']=bool(report.get('success') and not errors and not report.get('failure'))
    try:
        write_func(result_path,report)
    except Exception as error:
        errors.append({'stage':'receipt_write','type':type(error).__name__})
        report['success']=False
        # Keep primary error intact even if the normal receipt cannot be written.
        # No replay of the original write: use a distinct, fresh fallback name.
        fallback=result_path.with_name('result-write-failure.json')
        try:
            write_func(fallback,report)
        except Exception as fallback_error:
            errors.append({'stage':'fallback_receipt_write','type':type(fallback_error).__name__})
            raise RuntimeError('S07 could not persist either result receipt') from fallback_error
    return report


def _run_guarded(out, network):
    out=out.resolve()
    if out.exists() or not out.is_relative_to(ROOT/'artifacts'):
        raise ValueError('new generated artifact root required')
    if not SOURCE.is_file() or SOURCE.is_symlink():
        raise RuntimeError('preceding owned generated preferences DB unavailable')
    source_hash=sha(SOURCE)
    out.mkdir(parents=True,exist_ok=False)
    prefs=out/'generated-data/preferences/preferences.sqlite3'
    prefs.parent.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(SOURCE,prefs)
    if sha(prefs)!=source_hash:
        raise RuntimeError('generated preference DB changed during read-only copy')

    report={'schema':1,'original_id':'S07','prior_GUI_preferences_sha256':source_hash,
        'scope':'next actual generated novel save after GUI choice TXT; no original Flet/Cookie',
        'formal_pass_added':0,'success':False,'steps':[],'failure':None}
    services=None
    try:
        services,reader=create_services(FastAPI(),out/'generated-data')
        snap=services.preferences.snapshot()
        assert snap.values['novel_save_format']=='txt'
        assert snap.values['ugoira_save_format']=='mp4'
        assert snap.revision==1
        assert reader.calls==[]
        known=reader.get_novel_payload('501','91')
        if (known.get('id')!='501' or known.get('userId')!='91'
                or known.get('title')!='Generated novel'
                or known.get('content')!=EXPECTED_RAW_501):
            raise AssertionError('known generated novel 501 fixture changed')
        report['steps'].append('copied exclusively prior GUI-set generated preferences, revision 1 txt/mp4')
        # Full author preview would also prepare animation MP4. This process
        # intentionally validates only S07; MP4 codec admission is separately
        # unverified here and must not block an independent novel TXT save.
        candidate=next(c for c in services.author_media_preview('91',target_type='novel')
                       if c.key=='novel:501')
        if candidate.output_format!='TXT':
            raise AssertionError('new novel job ignored GUI-selected default TXT')
        report['steps'].append('next generated author-media preview selected TXT without format override')
        accepted,created,_=services.enqueue_author_media(candidate,uuid4().hex,origin='id')
        if not created:raise AssertionError('fresh novel job not admitted')
        owner=services.novel_jobs.jobs
        with owner._condition:
            if not owner._condition.wait_for(
                lambda:any(j.job_id==accepted.job_id and j.state in ('done','error','stopped')
                           for j in owner.snapshot()),20):
                raise AssertionError('novel save did not settle')
        job=next(j for j in owner.snapshot() if j.job_id==accepted.job_id)
        if job.state!='done':raise AssertionError(f'novel job failed: {job.state}')
        saved=services.novel_jobs.saved_entries()
        if len(saved)!=1 or saved[0].work_id!='501' or saved[0].output_format!='TXT':
            raise AssertionError('generated novel saved in wrong format')
        receipt=services.novel_jobs.store.root/saved[0].relative_receipt
        folder=receipt.parent
        outputs=[p for p in folder.iterdir() if p.is_file()]
        if not any(p.suffix.lower()=='.txt' for p in outputs):
            raise AssertionError('expected TXT output missing')
        if any(p.suffix.lower()=='.epub' for p in outputs):
            raise AssertionError('unwanted EPUB generated for TXT default')
        txt=[p for p in outputs if p.suffix.lower()=='.txt']
        if len(txt)!=1:
            raise AssertionError('TXT file count wrong')
        body_proof=verify_generated_txt_501(txt[0],receipt)
        report['text_content_proof']=body_proof
        report['steps'].append('real job done; actual TXT bytes nonempty UTF-8 BOM, exact fixture 501 text, receipt identity/hash/length matched')
        report['receipt_relative_path']=saved[0].relative_receipt
        # The store may return a Windows extended-length (\\?\) owner path,
        # while the checker root is a normal path. Names are sufficient here.
        report['generated_txt_names']=[p.name for p in outputs if p.suffix.lower()=='.txt']
        report['job_state']=job.state
        report['source_generated_preference_unchanged']=sha(SOURCE)==source_hash
        report['success']=report['source_generated_preference_unchanged']
    except Exception as exc:
        report['failure']=type(exc).__name__
        report['error_short']=str(exc)[:220]
    finally:
        finalize_s07_run(report,services,SOURCE,source_hash,out/'result.json',network=network)
    print(json.dumps({'success':report['success'],'steps':len(report['steps']),
        'failure':report['failure'],'formal_pass_added':0}))
    return 0 if report['success'] else 1


def run(out):
    # A context-manager outer finally guarantees restoration even if close,
    # hash validation or both result writers raise unexpectedly.
    with OfflineNetworkFence() as network:
        return _run_guarded(out,network)

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('usage: check_stage5_s07_next_saved_txt.py NEW_OUTPUT')
    raise SystemExit(run(Path(sys.argv[1])))
