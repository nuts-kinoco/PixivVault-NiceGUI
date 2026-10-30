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

SOURCE=ROOT/'artifacts/p3-369-s07-s08-prefs-r1/generated-data/preferences/preferences.sqlite3'

def sha(p):return sha256(p.read_bytes()).hexdigest()

def run(out):
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

    # All reader calls are synthetic, including the image bytes. Deny any
    # accidental network egress from this explicitly offline follow-up.
    original_socket_connect=socket.socket.connect
    original_dns=socket.getaddrinfo
    def local_socket(self,address):
        if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1','localhost'):
            raise RuntimeError('outside network prohibited')
        return original_socket_connect(self,address)
    def local_dns(host,*args,**kwargs):
        if host not in ('127.0.0.1','::1','localhost',None):
            raise RuntimeError('outside DNS prohibited')
        return original_dns(host,*args,**kwargs)
    socket.socket.connect=local_socket
    socket.getaddrinfo=local_dns

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
        report['steps'].append('real managed generated novel job completed, receipt TXT with .txt output and no EPUB')
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
        if services is not None:services.close()
        report['source_generated_preference_unchanged']=sha(SOURCE)==source_hash
        with (out/'result.json').open('x',encoding='utf8') as f:
            json.dump(report,f,ensure_ascii=False,indent=2)
        socket.socket.connect=original_socket_connect
        socket.getaddrinfo=original_dns
    print(json.dumps({'success':report['success'],'steps':len(report['steps']),
        'failure':report['failure'],'formal_pass_added':0}))
    return 0 if report['success'] else 1

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('usage: check_stage5_s07_next_saved_txt.py NEW_OUTPUT')
    raise SystemExit(run(Path(sys.argv[1])))
