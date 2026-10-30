# Public CPython TLS test fixture — NOT a secret

These files are publicly distributed CPython test material, not production credentials. Never use the key outside isolated tests. Source is CPython v3.13.11:

* https://github.com/python/cpython/blob/v3.13.11/Lib/test/certdata/keycert3.pem — public test private key and localhost server certificate.
* https://github.com/python/cpython/blob/v3.13.11/Lib/test/certdata/pycacert.pem — public test CA.
* https://github.com/python/cpython/blob/v3.13.11/Lib/test/test_ssl.py — identifies keycert3.pem as the CA-signed localhost fixture.
* https://github.com/python/cpython/blob/v3.13.11/LICENSE — PSF License Version 2, included in PSF-LICENSE.txt. Copyright (c) 2001-2024 Python Software Foundation; All Rights Reserved.

The PEM blocks are copied from the source files; explanatory OpenSSL text between blocks is omitted. The test CA and certificate are valid 2018-08-29 through 2037-10-28 according to their published source descriptions. SHA-256 of the local PEM subset files:

* localhost-keycert.pem.fixture: 8ef4dd7369ace2c53a2614c24ca4f06be6e1fb473ef3c21e3011c5ae6cf8f3da
* cpython-ca.pem.fixture: eb64fdd2fc857e4306c74177f95c543a5c64deb7fc25556ec60ab14c436c0565

The private key is intentionally public test material. The test process loads its CA into an isolated SSLContext, not the OS trust store. All socket destinations are restricted to its own 127.0.0.1 listener; no real accounts, service endpoints, cookies or user data.
