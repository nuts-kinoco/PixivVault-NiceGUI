from test_premium_authorization import create,enable,Protection,TEST_PASSWORD

def test_session_only_never_persists_key_and_reopens_with_password(tmp_path):
    root=tmp_path/'v';protect=Protection();now=[1000]
    v,g,t=create(root,protect,now)
    try:
        g.authenticate(TEST_PASSWORD)
        assert not (root/'premium-material.dpapi').exists()
        g.confirm(False);g.initialize_vault('1','generated-secret')
        assert not (root/'premium-material.dpapi').exists()
    finally:g.close();v.close()
    v,g,t=create(root,protect,now)
    try:
        enable(g,False)
        assert g.restore_vault() and not (root/'premium-material.dpapi').exists()
    finally:g.close();v.close()

def test_existing_off_state_removes_key_left_by_previous_version(tmp_path):
    root=tmp_path/'v';protect=Protection();now=[1000]
    v,g,t=create(root,protect,now)
    enable(g,True);g.initialize_vault('1','generated-secret')
    g._atomic('activation-state',b'off\n')
    assert (root/'premium-material.dpapi').exists()
    g.close();v.close()
    v,g,t=create(root,protect,now)
    try:
        assert not g.status()['enabled'] and not (root/'premium-material.dpapi').exists()
        enable(g,False);assert g.restore_vault()
    finally:g.close();v.close()

def test_separately_password_protected_legacy_vault_remains_accessible(tmp_path):
    root=tmp_path/'v';protect=Protection();now=[1000];v,g,t=create(root,protect,now)
    v.initialize('generated-legacy-password','1','generated-secret')
    enable(g,False);g.close();v.close()
    v,g,t=create(root,protect,now)
    try:
        enable(g,False)
        assert not v.status()['open'] and not (root/'premium-material.dpapi').exists()
        v.unlock('generated-legacy-password');g.remember_local_unlock()
        assert v.status()['open'] and not (root/'premium-material.dpapi').exists()
    finally:g.close();v.close()
def test_off_removes_previously_saved_key_keeps_vault(tmp_path):
    root=tmp_path/'v';v,g,t=create(root,Protection(),[1000])
    try:
        enable(g,True);g.initialize_vault('1','generated-secret')
        assert (root/'premium-material.dpapi').exists()
        pointer=(root/'CURRENT').read_bytes();g.disable()
        assert not (root/'premium-material.dpapi').exists()
        assert (root/'CURRENT').read_bytes()==pointer
    finally:g.close();v.close()

def test_legacy_local_unlock_after_saved_confirmation_never_writes_key(tmp_path):
    root=tmp_path/'v';protect=Protection();now=[1000];v,g,t=create(root,protect,now)
    v.initialize('generated-legacy-password','1','generated-secret');v.lock()
    enable(g,True)
    try:
        assert g.status()['enabled'] and not v.status()['open']
        v.unlock('generated-legacy-password');g.remember_local_unlock()
        assert not (root/'premium-material.dpapi').exists()
    finally:g.close();v.close()
    v,g,t=create(root,protect,now)
    try:
        assert g.status()['enabled'] and not v.status()['open']
        v.unlock('generated-legacy-password');g.remember_local_unlock()
        assert v.status()['open'] and not (root/'premium-material.dpapi').exists()
    finally:g.close();v.close()
