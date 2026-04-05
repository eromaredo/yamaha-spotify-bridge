#!/usr/bin/env python3
"""
Spotify Connect Bridge for OpenWrt
Proxies Spotify Connect from guest network to Yamaha in LAN.

Usage: python3 spotify-bridge.py
"""

import socket
import threading
import urllib.request
import urllib.error
import struct
import time
import sys

# --- Configuration ---
YAMAHA_IP   = "192.168.1.220"
YAMAHA_PORT = 80

BRIDGE_IP   = "192.168.2.1"  # Router IP in guest network
BRIDGE_PORT = 8080            # Port to listen on (avoid 80 if LuCI uses it)

DEVICE_NAME = "Yamaha RN-803D"
MDNS_ADDR   = "224.0.0.251"
MDNS_PORT   = 5353
# ---------------------


def log(msg):
    print(f"[spotify-bridge] {msg}", flush=True)


# ── HTTP Proxy ──────────────────────────────────────────────────────────────

def recv_request(conn):
    """Read full HTTP request (headers + body)."""
    data = b""
    conn.settimeout(5)
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data:
                header_part, body_part = data.split(b"\r\n\r\n", 1)
                cl = 0
                for line in header_part.split(b"\r\n")[1:]:
                    if line.lower().startswith(b"content-length:"):
                        cl = int(line.split(b":", 1)[1].strip())
                if len(body_part) >= cl:
                    break
    except socket.timeout:
        pass
    return data


def handle_client(conn, addr):
    try:
        data = recv_request(conn)
        if not data:
            return

        lines = data.split(b"\r\n")
        first_line = lines[0].decode(errors="replace")
        parts = first_line.split(" ")
        if len(parts) < 2:
            return

        method = parts[0]
        path   = parts[1]
        log(f"[proxy] {method} {path} from {addr[0]}")

        url = f"http://{YAMAHA_IP}:{YAMAHA_PORT}{path}"

        body = b""
        if b"\r\n\r\n" in data:
            body = data.split(b"\r\n\r\n", 1)[1]

        req = urllib.request.Request(
            url,
            data=body if body else None,
            method=method
        )

        # Forward headers, skip hop-by-hop
        skip = {"host", "connection", "content-length",
                 "transfer-encoding", "keep-alive"}
        for line in lines[1:]:
            if b": " not in line:
                continue
            key, val = line.split(b": ", 1)
            k = key.decode(errors="replace").lower()
            if k not in skip:
                try:
                    req.add_header(k, val.decode(errors="replace"))
                except Exception:
                    pass

        req.add_header("Host", YAMAHA_IP)
        if body:
            req.add_header("Content-Length", str(len(body)))

        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp_body    = resp.read()
                status       = resp.status
                reason       = resp.reason
                content_type = resp.headers.get("Content-Type", "application/json")

            response = (
                f"HTTP/1.1 {status} {reason}\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(resp_body)}\r\n"
                f"Access-Control-Allow-Origin: *\r\n"
                f"Connection: close\r\n\r\n"
            )
            conn.sendall(response.encode() + resp_body)
            log(f"[proxy] → {status} ({len(resp_body)} bytes)")

        except urllib.error.URLError as e:
            log(f"[proxy] Error reaching Yamaha: {e}")
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")

    except Exception as e:
        log(f"[proxy] Exception: {e}")
    finally:
        conn.close()


def run_proxy():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((BRIDGE_IP, BRIDGE_PORT))
    srv.listen(20)
    log(f"HTTP proxy listening on {BRIDGE_IP}:{BRIDGE_PORT}")
    while True:
        conn, addr = srv.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
        t.start()


# ── mDNS ────────────────────────────────────────────────────────────────────

def encode_name(name):
    """Encode DNS name to wire format."""
    out = b""
    for part in name.rstrip(".").split("."):
        if not part:
            continue
        encoded = part.encode()
        out += bytes([len(encoded)]) + encoded
    out += b"\x00"
    return out


def txt_entry(s):
    """Single TXT string with length prefix."""
    b = s.encode()
    return bytes([len(b)]) + b


def build_mdns_response():
    instance   = f"{DEVICE_NAME}._spotify-connect._tcp.local"
    service    = "_spotify-connect._tcp.local"
    host_local = f"{BRIDGE_IP.replace('.', '-')}.local"
    ttl        = 4500

    header = struct.pack("!HHHHHH", 0, 0x8400, 0, 4, 0, 0)

    # PTR
    ptr_rdata  = encode_name(instance)
    ptr_record = (encode_name(service)
        + struct.pack("!HHIH", 12, 1, ttl, len(ptr_rdata))
        + ptr_rdata)

    # SRV
    srv_target = encode_name(host_local)
    srv_rdata  = struct.pack("!HHH", 0, 0, BRIDGE_PORT) + srv_target
    srv_record = (encode_name(instance)
        + struct.pack("!HHIH", 33, 1, ttl, len(srv_rdata))
        + srv_rdata)

    # TXT – lengths computed dynamically
    txt_rdata = (
        txt_entry("CPath=/goform/spotifyConfig") +
        txt_entry("VERSION=2.9.0")
    )
    txt_record = (encode_name(instance)
        + struct.pack("!HHIH", 16, 1, ttl, len(txt_rdata))
        + txt_rdata)

    # A
    a_rdata  = socket.inet_aton(BRIDGE_IP)
    a_record = (encode_name(host_local)
        + struct.pack("!HHIH", 1, 1, ttl, len(a_rdata))
        + a_rdata)

    return header + ptr_record + srv_record + txt_record + a_record


def run_mdns():
    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                         socket.inet_aton(BRIDGE_IP))

    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    recv_sock.bind(("", MDNS_PORT))
    mreq = socket.inet_aton(MDNS_ADDR) + socket.inet_aton(BRIDGE_IP)
    recv_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    recv_sock.settimeout(1.0)

    packet = build_mdns_response()
    log(f"mDNS started on {BRIDGE_IP} → announcing '{DEVICE_NAME}'")

    last_announce = 0

    while True:
        now = time.time()
        if now - last_announce >= 5:
            try:
                send_sock.sendto(packet, (MDNS_ADDR, MDNS_PORT))
                last_announce = now
            except Exception as e:
                log(f"[mDNS] announce error: {e}")

        try:
            data, addr = recv_sock.recvfrom(4096)
            # QR bit = 0 means query
            if len(data) >= 12 and not (data[2] & 0x80):
                if b"spotify-connect" in data or b"_spotify" in data:
                    log(f"[mDNS] query from {addr[0]}, responding")
                    send_sock.sendto(packet, (MDNS_ADDR, MDNS_PORT))
        except socket.timeout:
            pass
        except Exception as e:
            log(f"[mDNS] recv error: {e}")


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log("Starting Spotify Connect Bridge")
    log(f"  Yamaha:  {YAMAHA_IP}:{YAMAHA_PORT}")
    log(f"  Bridge:  {BRIDGE_IP}:{BRIDGE_PORT}")
    log(f"  Device:  {DEVICE_NAME}")

    threads = [
        threading.Thread(target=run_proxy, daemon=True),
        threading.Thread(target=run_mdns,  daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Stopping.")
        sys.exit(0)