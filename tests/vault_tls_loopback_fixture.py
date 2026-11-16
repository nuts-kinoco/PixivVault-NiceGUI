"""Isolated, owned localhost TLS fixture. Never accesses external services."""
from pathlib import Path
import socket
import ssl
import threading

FIXTURES=Path(__file__).parent/'fixtures'/'cpython_tls'
CA=FIXTURES/'cpython-ca.pem.fixture'
KEYCERT=FIXTURES/'localhost-keycert.pem.fixture'


class LoopbackTLS:
    def __init__(self, mode='normal', status=200):
        self.mode=mode;self.status=status;self.stop=threading.Event()
        self.allow_handshake=threading.Event();self.allow_read=threading.Event()
        self.hello=threading.Event();self.handshake=threading.Event()
        self.connections=[];self.requests=[];self.errors=[];self.accepted=0
        self.tls_version=None;self._lock=threading.Lock();self.workers=[]
        self.context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(KEYCERT))
        self.listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1',0));self.listener.listen(8);self.listener.settimeout(.2)
        self.port=self.listener.getsockname()[1]
        self.thread=threading.Thread(target=self._accept,name='pv-fixture-tls-listener')
        self.thread.start()
    def _accept(self):
        while not self.stop.is_set():
            try:conn,address=self.listener.accept()
            except socket.timeout:continue
            except OSError:break
            if address[0]!='127.0.0.1':
                conn.close();self.errors.append('non-loopback-peer');continue
            with self._lock:self.accepted+=1;self.connections.append(conn)
            worker=threading.Thread(target=self._handle,args=(conn,),name='pv-fixture-tls-server')
            self.workers.append(worker);worker.start()
    def _handle(self,conn):
        tls=None
        try:
            conn.settimeout(5)
            if self.mode=='pause-handshake':
                if not conn.recv(1,socket.MSG_PEEK):return
                self.hello.set()
                if not self.allow_handshake.wait(5):raise TimeoutError('fixture handshake gate')
            tls=self.context.wrap_socket(conn,server_side=True)
            with self._lock:self.connections.append(tls);self.tls_version=tls.version()
            self.handshake.set()
            if self.mode=='drop-after-handshake':return
            if self.mode=='pause-read' and not self.allow_read.wait(5):raise TimeoutError('fixture read gate')
            data=b''
            while b'\r\n\r\n' not in data and len(data)<65536:
                chunk=tls.recv(4096)
                if not chunk:return
                data+=chunk
            if b'\r\n\r\n' not in data:raise ValueError('fixture header limit')
            header,body=data.split(b'\r\n\r\n',1)
            fields=header.split(b'\r\n');length=0
            for field in fields[1:]:
                if field.lower().startswith(b'content-length:'):length=int(field.split(b':',1)[1].strip())
            if length>16*1024*1024:raise ValueError('fixture body limit')
            while len(body)<length:
                chunk=tls.recv(min(65536,length-len(body)))
                if not chunk:return
                body+=chunk
            with self._lock:self.requests.append((fields[0],len(body)))
            reason=b'OK' if self.status==200 else b'Found'
            extra=b'Location: https://outside.invalid/\r\n' if self.status==302 else b''
            tls.sendall(b'HTTP/1.1 '+str(self.status).encode()+b' '+reason+b'\r\nContent-Type: application/json\r\nContent-Length: 2\r\n'+extra+b'Connection: close\r\n\r\n{}')
        except (OSError,ValueError,TimeoutError) as error:
            with self._lock:self.errors.append(type(error).__name__)
        finally:
            if tls is not None:tls.close()
            else:conn.close()
    def close(self):
        self.stop.set();self.allow_handshake.set();self.allow_read.set()
        self.listener.close()
        with self._lock:connections=tuple(self.connections)
        for conn in connections:
            try:conn.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            conn.close()
        self.thread.join(6)
        for worker in self.workers:worker.join(6)
        if self.thread.is_alive() or any(worker.is_alive() for worker in self.workers):
            raise AssertionError('owned fixture server did not stop')
