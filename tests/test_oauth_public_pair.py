"""Registered public Android client pair; provenance: pixivpy3/api.py BasePixivAPI."""
import hashlib
from pixivvault_nicegui.external_public_profiles import OAUTH_PROFILES

def test_android_profile_registered_public_pair():
    profile=next(p for p in OAUTH_PROFILES if p['name']=='android')
    actual=hashlib.sha256((profile['client_id']+'\n'+profile['client_secret']).encode()).hexdigest()
    assert actual == '1e827be4204ae343bc19e3f07ca1735742619124d01e709897a89fb56f24f2c3'
