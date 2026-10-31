"""New S07 downstream EPUB/BOTH byte-level conditions in private generated data.

This tests a marked settings store -> next real owned save, not prior GUI
button clicks, original Flet, real Cookie, or full original acceptance.
"""
from __future__ import annotations

import json
import zipfile
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest
from fastapi import FastAPI
from tools.p3_residuals_fixtures import create_services
from tools.stage5_offline_parent_fence import OfflineNetworkFence
from tools.check_stage5_s07_next_saved_txt import EXPECTED_TXT_501


@pytest.mark.parametrize('chosen', ['epub','both'])
def test_generated_novel_setting_reaches_next_saved_epub_content(tmp_path, chosen):
    with OfflineNetworkFence() as fence:
        services,reader=create_services(FastAPI(),tmp_path/'private-root')
        try:
            before=services.preferences.snapshot()
            services.preferences.update(
                {'novel_save_format':chosen},expected_revision=before.revision)
            after=services.preferences.snapshot()
            assert after.values['novel_save_format']==chosen
            candidates=services.author_media_preview('91',target_type='novel')
            candidate=next(c for c in candidates if c.key=='novel:501')
            assert candidate.output_format==chosen.upper()
            admitted,created,_=services.enqueue_author_media(
                candidate,uuid4().hex,origin='id')
            assert created
            owner=services.novel_jobs.jobs
            with owner._condition:
                assert owner._condition.wait_for(
                    lambda:any(j.job_id==admitted.job_id and j.state in ('done','error','stopped')
                               for j in owner.snapshot()),20)
            completed=next(j for j in owner.snapshot() if j.job_id==admitted.job_id)
            assert completed.state=='done'
            entries=services.novel_jobs.saved_entries()
            assert len(entries)==1
            entry=entries[0]
            assert (entry.work_id,entry.output_format)==('501',chosen.upper())
            root=services.novel_jobs.store.root
            receipt_path=root/entry.relative_receipt
            receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
            assert (receipt['work_id'],receipt['author_id'],receipt['format'])==('501','91',chosen.upper())
            assert receipt['metadata']['title']=='Generated novel'
            outputs={p.name:p for p in receipt_path.parent.iterdir() if p.suffix.lower() in ('.txt','.epub')}
            assert len(outputs)==(2 if chosen=='both' else 1)
            assert {p.suffix.lower() for p in outputs.values()}==(
                {'.txt','.epub'} if chosen=='both' else {'.epub'})
            for meta in receipt['outputs']:
                saved=outputs[meta['name']]
                from hashlib import sha256
                data=saved.read_bytes()
                assert len(data)==meta['bytes']
                assert sha256(data).hexdigest()==meta['sha256']
            if chosen=='both':
                txt=next(p for p in outputs.values() if p.suffix.lower()=='.txt')
                assert txt.read_bytes()==EXPECTED_TXT_501
            epub=next(p for p in outputs.values() if p.suffix.lower()=='.epub')
            with zipfile.ZipFile(epub,'r') as archive:
                assert archive.testzip() is None
                opf=archive.read('OEBPS/content.opf').decode('utf-8')
                chapter=archive.read('OEBPS/Text/chapter.xhtml')
                assert b'Generated fixture text.' in chapter
                assert b'Second page.' in chapter
                tree=ET.fromstring(chapter)
                text=' '.join(''.join(tree.itertext()).split())
                assert 'Generated fixture text.' in text
                assert 'Second page.' in text
                assert 'Generated novel' in opf and 'Fixture author' in opf
            assert fence.clean
        finally:
            services.close()
    assert fence.clean
