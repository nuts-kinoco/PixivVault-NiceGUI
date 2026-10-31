"""Original S08 GIF default, generated next-save content acceptance only.

This is a new content/identity condition. Prior frame-folder and settings GUI
successes are inherited, not repeated. No real account or external network.
"""
import asyncio
import base64
from io import BytesIO
from uuid import uuid4
from fastapi import FastAPI
from PIL import Image

from tools.p3_residuals_fixtures import create_services
from tools.stage5_offline_parent_fence import OfflineNetworkFence


def test_generated_gif_default_next_save_three_frames_content_and_timing(tmp_path):
    with OfflineNetworkFence() as fence:
        services,reader=create_services(FastAPI(),tmp_path/'generated')
        try:
            assert services.preferences.snapshot().values['ugoira_save_format']=='gif'
            assert reader.calls==[]
            job=asyncio.run(services.save_explicit_work('123',uuid4().hex))
            owner=services.ugoira.jobs
            with owner._condition:
                assert owner._condition.wait_for(
                    lambda:any(j.job_id==job.job_id and j.state in ('done','error','stopped')
                               for j in owner.snapshot()),25)
            current=next(j for j in owner.snapshot() if j.job_id==job.job_id)
            assert current.state=='done'
            saved=services.ugoira.library()
            assert len(saved)==1
            entry=saved[0]
            assert (entry['spec']['work_id'],entry['spec']['author_id'],
                    entry['spec']['output'])==('123','91','gif')
            receipt=services.ugoira.root/entry['proof']['receipt']
            folder=receipt.parent
            assert (folder/'animation.gif').is_file()
            with Image.open(folder/'animation.gif') as gif:
                assert gif.format=='GIF' and gif.n_frames==3
                assert gif.size==(96,64)
                # GIF timing is 10ms-granular and all provided delays align.
                assert [gif.seek(i) or gif.info.get('duration') for i in range(3)]==[40,90,170]
            decoded=services.ugoira.playback(entry)
            assert decoded['source_duration_ms']==300
            assert len(decoded['frames'])==3
            assert [row['delay'] for row in decoded['frames']]==[40,90,170]
            pixels=[]
            for frame in decoded['frames']:
                assert frame['src'].startswith('data:image/png;base64,')
                with Image.open(BytesIO(base64.b64decode(frame['src'].split(',',1)[1]))) as image:
                    image.load()
                    assert image.size==(96,64)
                    pixels.append(image.convert('RGB').getpixel((48,32)))
            assert pixels==[(255,0,0),(0,128,0),(0,0,255)]
            assert ('archive','123') in reader.calls
            assert fence.clean
        finally:
            services.close()
    assert fence.clean
