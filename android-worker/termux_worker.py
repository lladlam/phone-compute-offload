# Termux Worker for Phone Compute Offload

import json
import socket
import time
import threading
import uuid
from pathlib import Path
from typing import Dict, List

from android_worker.probe import probe
from android_worker.executor import execute_task, get_executors, get_apis

class TermuxWorker:
    def __init__(self, host: str = "0.0.0.0", port: int = 47821, name: str = "android-worker"):
        self.host = host
        self.port = port
        self.name = name
        self.worker_id = uuid.uuid4().hex[:12]
        self.hello = probe()
        self._stop = threading.Event()
        
    def serve_forever(self):
        announcer = threading.Thread(target=self._announce_loop, daemon=True)
        announcer.start()
        
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(4)
        server.settimeout(1.0)
        
        print(f"pco-worker {self.worker_id} listening on {self.host}:{self.port}", flush=True)
        print(f"executors: {sorted(self.hello['executors'])}", flush=True)
        
        try:
            while not self._stop.is_set():
                try:
                    conn, addr = server.accept()
                except TimeoutError:
                    continue
                try:
                    self._handle(conn, addr)
                except Exception as exc:
                    print(f"connection {addr} failed: {exc}", flush=True)
                finally:
                    conn.close()
        finally:
            server.close()
    
    def stop(self):
        self._stop.set()
    
    def _announce_loop(self):
        names = sorted(self.hello["executors"])
        while not self._stop.is_set():
            try:
                # Send UDP announcement
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                payload = json.dumps({
                    "type": "announce",
                    "magic": "pco-announce",
                    "port": self.port,
                    "name": self.name,
                    "worker_id": self.worker_id,
                    "executors": names,
                }).encode("utf-8")
                sock.sendto(payload, ("255.255.255.255", 47820))
                sock.close()
            except OSError as exc:
                print(f"announce failed: {exc}", flush=True)
            self._stop.wait(2.0)
    
    def _handle(self, conn, addr):
        conn.settimeout(30)
        try:
            # Receive hello
            data = conn.recv(4096)
            if not data:
                return
            hello = json.loads(data.decode("utf-8"))
            
            if hello.get("type") != "hello":
                conn.send(json.dumps({"type": "error", "message": "expected hello"}).encode())
                return
            
            # Send worker_hello
            conn.send(json.dumps(self.hello).encode())
            
            # Receive task
            data = conn.recv(4096)
            if not data:
                return
            task = json.loads(data.decode("utf-8"))
            
            if task.get("type") != "task":
                conn.send(json.dumps({"type": "error", "message": "expected task"}).encode())
                return
            
            # Execute task
            result = execute_task(task)
            conn.send(json.dumps(result).encode())
            
        except Exception as e:
            print(f"handle error: {e}", flush=True)
            try:
                conn.send(json.dumps({"type": "error", "message": str(e)}).encode())
            except:
                pass

def main():
    worker = TermuxWorker()
    worker.serve_forever()

if __name__ == "__main__":
    main()
