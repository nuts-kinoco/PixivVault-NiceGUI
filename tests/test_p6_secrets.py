import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import p6_secrets


def test_secret_findings_contain_only_location_and_kind():
    password = 'example' + '-only-9172'
    session = '12345_' + 'fixtureabcdefghij'
    bearer = 'fixture_' + 'abcdefghijklmnop'
    data = (f'.example.invalid\tTRUE\t/\tTRUE\t0\tPHPSESSID\t{session}\n'
            f'Authorization: Bearer {bearer}\nDEV_PASSWORD = "{password}"\n').encode()
    found = list(p6_secrets.find(data, 'generated.log'))
    assert {item['kind'] for item in found} == {'cookie', 'token', 'development-password'}
    payload = json.dumps(found)
    assert all(value not in payload for value in (password, session, bearer))
