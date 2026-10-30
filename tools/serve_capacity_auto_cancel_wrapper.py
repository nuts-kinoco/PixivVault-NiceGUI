"""CoS-owned shutdown endpoint around ORIGINAL synthetic auto-capacity fixture.
The fixture __file__ stays in this candidate tools directory.
"""
import importlib.util,os,secrets,sys
from pathlib import Path
from fastapi import Header,HTTPException
from nicegui import app
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
source=ROOT/'tools/serve_capacity_auto_fixture.py'
spec=importlib.util.spec_from_file_location('original_288_capacity_auto',source)
fixture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
nonce=os.environ['COS_SHUTDOWN_NONCE']
@app.post('/_cos/shutdown')
def shutdown(x_cos_shutdown:str|None=Header(None)):
    if not secrets.compare_digest(x_cos_shutdown or '',nonce):raise HTTPException(403)
    app.shutdown()
    return {'stopping':True}
if __name__=='__main__':fixture.main()