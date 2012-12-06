from astm import asynclib
import unittest
import select
import os
import socket
import threading
import time
import errno

try:
    from test import test_support
    from test.test_support import TESTFN, run_unittest, unlink
    from StringIO import StringIO
    def skip(f):
        return f
except ImportError:
    from test import support as test_support
    from test import support
    from test.support import TESTFN, run_unittest, unlink
    from io import BytesIO as StringIO
    from io import FileIO as file
    from unittest import skip


HOST = test_support.HOST

class dummysocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def fileno(self):
        return 42

class dummychannel:
    def __init__(self):
        self.socket = dummysocket()

    def close(self):
        self.socket.close()

class exitingdummy:
    def __init__(self):
        pass

    def handle_read_event(self):
        raise asynclib.ExitNow()

    handle_write_event = handle_read_event
    handle_close = handle_read_event
    handle_expt_event = handle_read_event

class crashingdummy:
    def __init__(self):
        self.error_handled = False

    def handle_read_event(self):
        raise Exception()

    handle_write_event = handle_read_event
    handle_close = handle_read_event
    handle_expt_event = handle_read_event

    def handle_error(self):
        self.error_handled = True

class dispatcherwithsend_noread(asynclib.Dispatcher):

    def __init__(self, sock=None, map=None):
        super(dispatcherwithsend_noread, self).__init__(sock, map)
        self.out_buffer = ''

    def initiate_send(self):
        num_sent = super(dispatcherwithsend_noread, self).send(self.out_buffer[:512])
        self.out_buffer = self.out_buffer[num_sent:]

    def handle_write(self):
        self.initiate_send()

    def writable(self):
        return (not self.connected) or len(self.out_buffer)

    def send(self, data):
        self.out_buffer += data.decode()
        self.initiate_send()

    def readable(self):
        return False

    def handle_connect(self):
        pass

# used when testing senders; just collects what it gets until newline is sent
def capture_server(evt, buf, serv):
    try:
        serv.listen(5)
        conn, addr = serv.accept()
    except socket.timeout:
        pass
    else:
        n = 200
        while n > 0:
            r, w, e = select.select([conn], [], [])
            if r:
                data = conn.recv(10)
                # keep everything except for the newline terminator
                buf.write(data.replace('\n', ''))
                if '\n' in data:
                    break
            n -= 1
            time.sleep(0.01)

        conn.close()
    finally:
        serv.close()
        evt.set()


class HelperFunctionTests(unittest.TestCase):
    def test_readwriteexc(self):
        # Check exception handling behavior of read, write and _exception

        # check that ExitNow exceptions in the object handler method
        # bubbles all the way up through asynclib read/write/_exception calls
        tr1 = exitingdummy()
        self.assertRaises(asynclib.ExitNow, asynclib.read, tr1)
        self.assertRaises(asynclib.ExitNow, asynclib.write, tr1)
        self.assertRaises(asynclib.ExitNow, asynclib.exception, tr1)

        # check that an exception other than ExitNow in the object handler
        # method causes the handle_error method to get called
        tr2 = crashingdummy()
        asynclib.read(tr2)
        self.assertEqual(tr2.error_handled, True)

        tr2 = crashingdummy()
        asynclib.write(tr2)
        self.assertEqual(tr2.error_handled, True)

        tr2 = crashingdummy()
        asynclib.exception(tr2)
        self.assertEqual(tr2.error_handled, True)

    # asynclib.readwrite uses constants in the select module that
    # are not present in Windows systems (see this thread:
    # http://mail.python.org/pipermail/python-list/2001-October/109973.html)
    # These constants should be present as long as poll is available

    if hasattr(select, 'poll'):
        def test_readwrite(self):
            # Check that correct methods are called by readwrite()

            attributes = ('read', 'expt', 'write', 'closed', 'error_handled')

            expected = (
                (select.POLLIN, 'read'),
                (select.POLLPRI, 'expt'),
                (select.POLLOUT, 'write'),
                (select.POLLERR, 'closed'),
                (select.POLLHUP, 'closed'),
                (select.POLLNVAL, 'closed'),
                )

            class testobj:
                def __init__(self):
                    self.read = False
                    self.write = False
                    self.closed = False
                    self.expt = False
                    self.error_handled = False

                def handle_read_event(self):
                    self.read = True

                def handle_write_event(self):
                    self.write = True

                def handle_close(self):
                    self.closed = True

                def handle_expt_event(self):
                    self.expt = True

                def handle_error(self):
                    self.error_handled = True

            for flag, expectedattr in expected:
                tobj = testobj()
                self.assertEqual(getattr(tobj, expectedattr), False)
                asynclib.readwrite(tobj, flag)

                # Only the attribute modified by the routine we expect to be
                # called should be True.
                for attr in attributes:
                    self.assertEqual(getattr(tobj, attr), attr==expectedattr)

                # check that ExitNow exceptions in the object handler method
                # bubbles all the way up through asynclib readwrite call
                tr1 = exitingdummy()
                self.assertRaises(asynclib.ExitNow, asynclib.readwrite, tr1, flag)

                # check that an exception other than ExitNow in the object handler
                # method causes the handle_error method to get called
                tr2 = crashingdummy()
                self.assertEqual(tr2.error_handled, False)
                asynclib.readwrite(tr2, flag)
                self.assertEqual(tr2.error_handled, True)

    def test_closeall(self):
        self.closeall_check(False)

    def test_closeall_default(self):
        self.closeall_check(True)

    def closeall_check(self, usedefault):
        # Check that close_all() closes everything in a given map

        l = []
        testmap = {}
        for i in range(10):
            c = dummychannel()
            l.append(c)
            self.assertEqual(c.socket.closed, False)
            testmap[i] = c

        if usedefault:
            socketmap = asynclib._SOCKET_MAP
            try:
                asynclib._SOCKET_MAP = testmap
                asynclib.close_all()
            finally:
                testmap, asynclib._SOCKET_MAP = asynclib._SOCKET_MAP, socketmap
        else:
            asynclib.close_all(testmap)

        self.assertEqual(len(testmap), 0)

        for c in l:
            self.assertEqual(c.socket.closed, True)


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        asynclib.close_all()

    def test_basic(self):
        d = asynclib.Dispatcher()
        self.assertEqual(d.readable(), True)
        self.assertEqual(d.writable(), True)

    def test_repr(self):
        d = asynclib.Dispatcher()
        self.assertTrue(repr(d).endswith('Dispatcher at %#x>' % id(d)))

    def test_strerror(self):
        # refers to bug #8573
        err = asynclib._strerror(errno.EPERM)
        if hasattr(os, 'strerror'):
            self.assertEqual(err, os.strerror(errno.EPERM))
        err = asynclib._strerror(-1)
        self.assertTrue("unknown error" in err.lower())


class DispatcherWithSendTests(unittest.TestCase):
    usepoll = False

    def setUp(self):
        pass

    def tearDown(self):
        asynclib.close_all()

    @skip
    def test_send(self):
        self.evt = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(3)
        self.port = test_support.bind_port(self.sock)

        cap = StringIO()
        args = (self.evt, cap, self.sock)
        threading.Thread(target=capture_server, args=args).start()

        # wait a little longer for the server to initialize (it sometimes
        # refuses connections on slow machines without this wait)
        time.sleep(0.2)

        data = "Suppose there isn't a 16-ton weight?".encode()
        d = dispatcherwithsend_noread()
        d.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        d.connect((HOST, self.port))

        # give time for socket to connect
        time.sleep(0.1)

        d.send(data)
        d.send(data)
        d.send('\n')

        n = 1000
        while d.out_buffer and n > 0:
            asynclib.poll()
            n -= 1

        self.evt.wait()

        self.assertEqual(cap.getvalue(), data*2)


class DispatcherWithSendTests_UsePoll(DispatcherWithSendTests):
    usepoll = True


class FileWrapperTest(unittest.TestCase):
    def setUp(self):
        self.d = "It's not dead, it's sleeping!".encode()
        file(TESTFN, 'w').write(self.d)

    def tearDown(self):
        unlink(TESTFN)

    def test_recv(self):
        fd = os.open(TESTFN, os.O_RDONLY)
        w = asynclib.FileWrapper(fd)
        os.close(fd)

        self.assertNotEqual(w.fd, fd)
        self.assertNotEqual(w.fileno(), fd)
        self.assertEqual(w.recv(13), "It's not dead".encode())
        self.assertEqual(w.read(6), ", it's".encode())
        w.close()
        self.assertRaises(OSError, w.read, 1)

    def test_send(self):
        d1 = "Come again?".encode()
        d2 = "I want to buy some cheese.".encode()
        fd = os.open(TESTFN, os.O_WRONLY | os.O_APPEND)
        w = asynclib.FileWrapper(fd)
        os.close(fd)

        w.write(d1)
        w.send(d2)
        w.close()
        self.assertEqual(file(TESTFN).read(), self.d + d1 + d2)

    def test_dispatcher(self):
        fd = os.open(TESTFN, os.O_RDONLY)
        data = []
        class FileDispatcher(asynclib.FileDispatcher):
            def handle_read(self):
                c = self.recv(29)
                try:
                    c = c.decode()
                except AttributeError:
                    pass
                data.append(c)
        s = FileDispatcher(fd)
        os.close(fd)
        asynclib.loop(timeout=0.01, count=2)
        self.assertEqual("".join(data).encode(), self.d)


def test_main():
    tests = [HelperFunctionTests, DispatcherTests, DispatcherWithSendTests,
             DispatcherWithSendTests_UsePoll]
    if hasattr(asynclib, 'FileWrapper'):
        tests.append(FileWrapperTest)

    run_unittest(*tests)

if __name__ == "__main__":
    test_main()
