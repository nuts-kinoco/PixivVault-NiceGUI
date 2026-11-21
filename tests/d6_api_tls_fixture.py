"""Verified, loopback-only HTTP/1.1 fixture for the owned Pixiv reader."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
from threading import Event, Lock, Thread

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


HOST = "www.pixiv.net"
CDN = "i.pximg.net"
BODY = b'{"error":false,"body":"' + b'x' * 65536 + b'"}'
PACKED = gzip.compress(BODY)
RANK = f"https://{HOST}/ranking.php?mode=daily&content=illust&format=json&p=1"
EXTRA = f"https://{HOST}/ajax/user/extra"


def write_cookie(root, number=55):
    path = root / f"synthetic-{number}.txt"
    path.write_text("# Netscape HTTP Cookie File\n"
                    f".pixiv.net\tTRUE\t/\tTRUE\t2147483647\tPHPSESSID\t{number}_fixture\n",
                    encoding="ascii")
    return path


def trust_fixture(root, names):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name) for name in names]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = root / "fixture.crt", root / "fixture.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    return server, cert_path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    wbufsize = 128 * 1024

    def setup(self):
        super().setup()
        self.connection.settimeout(5)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with self.server.lock:
            self.connection_number = len(self.server.peers)
            self.server.peers.append(self)
        self.finished = Event()

    def finish(self):
        try:
            super().finish()
        finally:
            self.finished.set()

    def log_message(self, *_):
        pass

    def do_GET(self):
        encoding = self.headers.get("Accept-Encoding")
        self.server.requests.append((self.connection_number, self.headers["Host"],
                                     self.headers.get("Cookie"), encoding))
        self.server.header_names.append(tuple(self.headers))
        status, disposition, body = self.server.reply
        if self.path == "/" and self.server.homepage is not None:
            status, disposition, body = 200, "identity", self.server.homepage
        if disposition == "drop":
            self.close_connection = True
            return
        if disposition == "wait":
            self.server.entered.set()
            self.server.release.wait(5)
        if disposition == "fatal-header":
            self.connection.sendall(
                f"HTTP/1.1 {status} Rejected\r\nX: ".encode() + b'x' * 66000 + b"\r\n\r\n")
            self.close_connection = True
            return
        if disposition == "negotiate" and encoding == "gzip":
            disposition, body = "gzip", PACKED
        if disposition == "stalled-body":
            self.server.header_release.wait(4)
        chunked = disposition in ("chunked", "chunked-gzip")
        self.send_response_only(status)
        if chunked:
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Content-Length", str(len(body) + (5 if disposition == "short" else 0)))
        if disposition in ("gzip", "chunked-gzip", "duplicate-gzip"):
            self.send_header("Content-Encoding", "gzip")
        if disposition == "duplicate-gzip":
            self.send_header("Content-Encoding", "identity")
        for name, value in self.server.response_headers:
            self.send_header(name, value)
        if disposition == "close":
            self.send_header("Connection", "close")
        if disposition in ("short", "close"):
            self.close_connection = True
        self.end_headers()
        if disposition == "stalled-body":
            self.wfile.flush()
            self.server.headers_sent.set()
            self.server.release.wait(4)
            return
        self.wfile.write((f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
                         if chunked else body)


class Server(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, context):
        super().__init__(("127.0.0.1", 0), Handler)
        self.context = context
        self.lock = Lock()
        self.peers = []
        self.requests = []
        self.header_names = []
        self.response_headers = []
        self.homepage = None
        self.reply = 200, "negotiate", BODY
        self.entered, self.release = Event(), Event()
        self.headers_sent, self.header_release = Event(), Event()

    @property
    def connections(self):
        return len(self.peers)

    def get_request(self):
        raw, address = super().get_request()
        raw.settimeout(5)
        try:
            return self.context.wrap_socket(raw, server_side=True), address
        except BaseException:
            raw.close()
            raise

    def handle_error(self, request, address):
        pass  # Client cancellation can interrupt a fixture write.


@contextmanager
def loopback(root, patch, connect, *, use_system_ca=False, names=(HOST, CDN), trusted=True):
    context, ca = trust_fixture(root, names)
    create_context = ssl.create_default_context
    server = Server(context)
    address = server.server_address

    def resolve(host, port, *_):
        if host not in (HOST, CDN) or port != 443:
            raise AssertionError("destination outside D6 API fixture")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address)]

    def connect_loopback(sock, destination):
        if destination != address:
            raise AssertionError("connect outside D6 API fixture")
        return connect(sock, destination)

    def client_context():
        if not trusted:
            return create_context()
        if not use_system_ca:
            return create_context(cafile=str(ca))
        context = create_context()
        context.load_verify_locations(cafile=str(ca))
        return context

    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .02})
    thread.start()
    try:
        with patch.context() as scoped:
            scoped.setattr(socket, "getaddrinfo", resolve)
            scoped.setattr(socket.socket, "connect", connect_loopback)
            scoped.setattr(ssl, "create_default_context", client_context)
            yield server
    finally:
        server.header_release.set()
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join()
