"""A minimal Modbus TCP read (function 3) using only the standard library, for the examples."""
import socket
import struct


def read_holding(host: str, port: int, address: int, count: int, unit: int = 1) -> list[int]:
    with socket.create_connection((host, port), timeout=3) as s:
        s.sendall(struct.pack(">HHHBBHH", 1, 0, 6, unit, 3, address, count))
        head = s.recv(9)
        if head[7] & 0x80:
            raise RuntimeError(f"Modbus exception {head[8]} at {address}x{count}")
        data = b""
        while len(data) < head[8]:
            data += s.recv(head[8] - len(data))
    return list(struct.unpack(f">{count}H", data))


def s16(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def s32(hi: int, lo: int) -> int:
    v = hi << 16 | lo
    return v - (1 << 32) if v >= 1 << 31 else v
