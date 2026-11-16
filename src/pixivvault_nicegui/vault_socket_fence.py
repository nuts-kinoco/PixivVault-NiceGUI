"""Epoch-scoped socket ownership, including the pre-connect phase."""
from contextlib import contextmanager
from threading import RLock
import errno
import socket


class SocketFenceError(RuntimeError):
    pass


class SocketFence:
    def __init__(self):
        self._lock=RLock();self._epoch=0;self._handles={};self._diagnostics=[]
        self._close_unverified=False
        # Keep generator-based registration contexts alive after an owner-close
        # failure. Otherwise GC would run their finally block and drop the
        # shutdown duplicate before lock can revoke it.
        self._retained_registrations=[]

    def snapshot(self):
        with self._lock:return self._epoch

    @property
    def revocation_unverified(self):
        """Sticky failure of socket shutdown; only a new fence can recover."""
        with self._lock:return self._close_unverified

    def require_healthy(self,epoch):
        """Reject poisoned or revoked sessions before allocating a new socket."""
        with self._lock:
            if self._close_unverified:raise SocketFenceError('socket shutdown not confirmed')
            if epoch!=self._epoch:raise SocketFenceError('stale socket epoch')

    @contextmanager
    def cleanup(self):
        """Serialize socket close and unregister against revocation."""
        with self._lock:
            yield

    def diagnostics(self):
        # Bounded structural facts only: never descriptors, addresses or messages.
        with self._lock:return tuple(dict(x) for x in self._diagnostics)

    def _record(self,phase,operation,error=None):
        self._diagnostics.append({'phase':phase,'operation':operation,
            'errno':getattr(error,'errno',None),'winerror':getattr(error,'winerror',None)})
        del self._diagnostics[:-32]

    def mark_close_unverified(self,error,registration=None):
        """Never confirm a later lock after a request-owned close failed."""
        with self._lock:
            self._close_unverified=True
            if (registration is not None and
                    not any(context is registration for context in self._retained_registrations)):
                self._retained_registrations.append(registration)
            self._record('cleanup','close-unverified',error)

    @contextmanager
    def register(self,sock,epoch):
        with self._lock:
            self.require_healthy(epoch)
            entry={'raw':sock,'duplicate':sock.dup(),'phase':'pending','stopped':False}
            key=object();self._handles[key]=entry
        try:yield
        finally:
            with self._lock:
                handle=self._handles.pop(key,None)
                if handle is not None:
                    try:handle['duplicate'].close()
                    except BaseException as error:
                        self.mark_close_unverified(error)
                        raise

    def activate(self,sock,epoch):
        """After connect, before any TLS bytes: pin the connected resource.

        A duplicate taken before connect is not our evidence of a connected
        Windows resource. Keep the original object owned until this transition;
        SSL may detach it only after a connected duplicate is installed.
        """
        with self._lock:
            self.require_healthy(epoch)
            entries=[e for e in self._handles.values() if e['raw'] is sock and not e['stopped']]
            if len(entries)!=1:raise SocketFenceError('socket registration missing')
            entry=entries[0]
            try:duplicate=sock.dup()
            except OSError as error:
                self._record('connected','duplicate-failed',error);raise
            previous=entry['duplicate'];entry['duplicate']=duplicate;entry['phase']='connected'
            try:previous.close()
            except BaseException as error:
                self.mark_close_unverified(error)
                raise

    def invalidate(self):
        with self._lock:
            self._epoch+=1;failed=self._close_unverified
            signal=None
            for entry in tuple(self._handles.values()):
                if entry['stopped']:continue
                phase=entry['phase']
                try:
                    if phase=='pending':
                        # Closing the actual Python socket also prevents a later
                        # connect() by the paused worker. ENOTCONN is expected
                        # only here; it is never accepted for an active TLS socket.
                        try:entry['raw'].shutdown(socket.SHUT_RDWR)
                        except OSError as error:
                            self._record(phase,'shutdown',error)
                            if not (error.errno in (errno.ENOTCONN,10057) or getattr(error,'winerror',None)==10057):raise
                        entry['raw'].close()
                        self._record(phase,'close-owned-socket')
                    else:
                        entry['duplicate'].shutdown(socket.SHUT_RDWR)
                        self._record(phase,'shutdown-connected-duplicate')
                    entry['stopped']=True
                except BaseException as error:
                    # Socket implementations normally report OSError, but a
                    # wrapper can raise another Exception or an interruption.
                    # Poison before processing the next handle, and still try
                    # to revoke every other owned socket.
                    self._close_unverified=True
                    self._record(phase,'revoke-failed',error);failed=True
                    if not isinstance(error,Exception) and signal is None:
                        signal=error
            if failed:
                # A failed revocation poisons *future* epochs too. Even if a
                # request later unregisters its handle, the OS shutdown was
                # never verified; an unlock must not create a fresh session.
                self._close_unverified=True
                if signal is not None:raise signal
                raise SocketFenceError('socket shutdown not confirmed')
