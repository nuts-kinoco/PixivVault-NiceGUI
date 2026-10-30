# L12 通常旧構成の接続：全体容量は未完

固定248 `73e7e9625790456926474a9621e280344d33fd8f6b1ef5a0f2afec695eb3abce` から分離。通常の `library_readonly_db` / `library_image_root` と `preferences_root` を明示する旧ライブラリ構成で、専用設定root内の所有マーカー付き legacy-capacity.sqlite3 を開き、既存LegacyCapacityReader/CapacityCacheをconfigureするコードを追加。既存の明示注入fixture経路は維持。未所有DBとリンクを拒否、services closeで所有cacheを閉じる。実設定/実旧データは開いていない。既存のフォルダー/ZIP選択・UI・停止待ちlockは248そのまま。

合成回帰 `tests/test_capacity_normal_configuration.py` は旧DB/画像root/専用preferences構成、原旧作者の容量・0・再起動復元、旧DB/画像保全、未知cache不変、cache不存在時のパス検査発火を対象にする。Codexによるpytest/import/構文/build/GUIは未実行。

未完の具体的依存: `services.py:103–110` と `bootstrap.py:178–185` は旧read-onlyライブラリとmanaged Pixiv保存の同時構成を拒否する。原版 `library_tab.py:420–446` は旧rootのZIPかfolderいずれかの実容量であり、同一作品が旧ZIP・旧folder・managed CAS・投影copyに存在するときの作者全体計数・重複判定は定義していない。旧ZIPの圧縮bytesを個別作品単位に切り分けられる根拠もない。248にはactive receipt検証と所有projection manifestがあるが、それだけで任意の旧ZIP/旧folderに含まれるコピーとの同一性・容量按分は証明できない。排他を緩めて単純加算すると同一コピーが混ざり、旧ZIP優先を勝手に変える。採用済み重複判定・合算契約（対象source、同一判定、ZIPの数え方）を特定する必要がある。

取消: 248のMeasurementLeaseはlock内でSQLite publishしcancelも同じlockを取得する。これにより失効とpublishの順序を直列化する一方、SQLite待ち中はUI側cancelが待機し得る。失効後永続化禁止とUI非ブロックを同時に満たすcancel/commitの線形化を未実装。安全契約を弱める非同期fire-and-forgetには置き換えていない。L12実装出口は未到達、CoSによる製品受入の対象として提出しない。

次担当: 上記の原仕様/採用契約を確定し、旧・managed併存のデータcatalogと安全な構成/重複排除、非ブロック取消とcache世代競合を実装。合成fixtureで旧ZIP/folder/managed重複・別作者・破損/未検出0・両者切替/再起動・途中cancel・高遅延SQLiteを確認する回帰を作り、CoSへ一括手順を用意。実データは走査しない。通常旧構成の部分差分のみ単独検証するなら `python -B -m pytest tests/test_capacity_normal_configuration.py tests/test_legacy_capacity_connected.py tests/test_capacity_cache_injected.py -q` をS隔離環境で実行。元の33/152・43未達・76未確認は変更しない。
