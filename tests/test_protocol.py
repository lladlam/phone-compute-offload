"""Frame codec over a real socketpair: size, hash, traversal, chunk offset."""

import hashlib
import socket
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pco.protocol.codec import FrameCodec, ProtocolError


class CodecTest(unittest.TestCase):
    def test_roundtrip_file_and_message(self):
        a, b = socket.socketpair()
        try:
            with TemporaryDirectory() as tmp:
                src = Path(tmp) / "in.bin"
                payload = b"phone-compute-offload" * 10000
                src.write_bytes(payload)
                sender = FrameCodec(a)

                def send():
                    sender.send({"type": "ping", "n": 1})
                    sender.send_file("t1", src)

                thread = threading.Thread(target=send)
                thread.start()
                receiver = FrameCodec(b)
                self.assertEqual(receiver.recv()["type"], "ping")
                spec, dest = receiver.recv_file(Path(tmp) / "out")
                thread.join()
                self.assertEqual(spec.sha256, hashlib.sha256(payload).hexdigest())
                self.assertEqual(dest.read_bytes(), payload)
                self.assertEqual(spec.size, len(payload))
        finally:
            a.close()
            b.close()

    def test_rejects_path_traversal(self):
        a, b = socket.socketpair()
        try:
            def send():
                FrameCodec(a).send(
                    {
                        "type": "file_begin",
                        "task_id": "t",
                        "name": "../etc/passwd",
                        "size": 0,
                        "sha256": hashlib.sha256(b"").hexdigest(),
                        "role": "output",
                        "compressed": "none",
                    }
                )

            thread = threading.Thread(target=send)
            thread.start()
            with TemporaryDirectory() as tmp:
                with self.assertRaises(ProtocolError):
                    FrameCodec(b).recv_file(Path(tmp))
            thread.join()
        finally:
            a.close()
            b.close()

    def test_detects_corrupt_chunk(self):
        a, b = socket.socketpair()
        try:
            digest = hashlib.sha256(b"hello").hexdigest()

            def send():
                codec = FrameCodec(a)
                codec.send(
                    {
                        "type": "file_begin",
                        "task_id": "t",
                        "name": "x.txt",
                        "size": 5,
                        "sha256": digest,
                        "role": "input",
                        "compressed": "none",
                    }
                )
                import base64

                codec.send(
                    {
                        "type": "chunk",
                        "task_id": "t",
                        "name": "x.txt",
                        "offset": 0,
                        "total": 5,
                        "sha256": digest,
                        "eof": True,
                        "data_b64": base64.b64encode(b"hellp").decode(),
                    }
                )

            thread = threading.Thread(target=send)
            thread.start()
            with TemporaryDirectory() as tmp:
                with self.assertRaises(ProtocolError):
                    FrameCodec(b).recv_file(Path(tmp))
            thread.join()
        finally:
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()
