# Cloud API clients

How each kind of FranklinWH Cloud API client reaches the emulator's Cloud API (#327). The
emulator serves plain HTTP on the address `--cloud-port` (and `--cloud-host`) gives it; see
[FranklinWH Cloud API](cloud-api.md). A client reaches it in one of three ways:

| Route | For | What the client needs |
|---|---|---|
| **Base URL** | clients with a base URL setting | the emulator's address, e.g. `http://127.0.0.1:8503/` |
| **DNS and a TLS front** | clients with no base URL setting, always on `https://energy.franklinwh.com/` | `energy.franklinwh.com` resolving to the TLS front, and trust in the test CA |
| **Intercepting proxy** | the official FranklinWH app on a phone | HTTP Toolkit (or similar) on the phone's traffic, forwarding to the emulator |

## Which client, which route

| Client | Route | Tested |
|---|---|---|
| [franklinwh-cloud](https://github.com/david2069/franklinwh-cloud) after 0.6.5 | Base URL (`url_base`) | yes: `tests/test_cloud*.py` |
| franklinwh-cloud 0.6.5 and earlier | Base URL, with a token from the emulator (`TokenAuth`): its login ignores `url_base` | yes: `tests/test_cloud.py` |
| richo's [franklinwh](https://github.com/richo/franklinwh-python) library 2026.3, patched | Base URL, plus `DEFAULT_URL_BASE` (login, `sendMqtt`, `getHomeGatewayList` and `updateTouMode` ignore `url_base`) | yes: `tests/test_cloud_richo.py` |
| richo's franklinwh library 2026.3, unmodified | TLS front | yes, with the routing done in the client instead of DNS: `tests/results/2026-11-16_3_richo-library-via-proxy.txt` |
| [homeassistant-franklinwh](https://github.com/richo/homeassistant-franklinwh) (Home Assistant) | DNS and the TLS front, the test CA in Home Assistant's certificate bundle | not yet |
| The official FranklinWH app | Intercepting proxy (HTTP Toolkit) | not yet (#316 step 4) |
| Anything else | Base URL if it has one, else DNS and the TLS front | |

richo's library calls `get_controllable_loads` (`selectTerGatewayControlLoadByGatewayId`) an
unknown method; the emulator doesn't serve it (404), as it isn't in the app's captures. Every other
call franklinwh 2026.3 makes is served.

## Base URL

Point the client at the emulator and change nothing else. See
[Point a client at it](cloud-api.md#point-a-client-at-it) for franklinwh-cloud and
[richo's franklinwh library](cloud-api.md#richos-franklinwh-library-homeassistant-franklinwh)
for richo's.

## DNS and a TLS front

For a client that always calls `https://energy.franklinwh.com/`. Three pieces:

1. **A test CA and a certificate** for `energy.franklinwh.com`, signed by it.
2. **A TLS front:** it ends TLS with that certificate and pipes the bytes to the emulator's Cloud
   API. It doesn't read the HTTP, so it needs no proxy library, nginx or Caddy.
3. **The client's side:** `energy.franklinwh.com` resolves to the TLS front, and the client
   trusts the test CA.

### 1. The test CA and the certificate

With `openssl`, in a folder of your own (never commit the keys):

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=Emulator Test CA" \
  -keyout ca.key -out ca.pem
openssl req -newkey rsa:2048 -nodes -subj "/CN=energy.franklinwh.com" \
  -keyout energy.key -out energy.csr
printf '%s\n' "subjectAltName=DNS:energy.franklinwh.com" "basicConstraints=CA:FALSE" \
  "extendedKeyUsage=serverAuth" "keyUsage=digitalSignature,keyEncipherment" > ext.cnf
openssl x509 -req -in energy.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 365 \
  -extfile ext.cnf -out energy.pem
```

### 2. The TLS front

Python's standard library only. Save it as `tls_front.py` next to the certificate:

```python
"""TLS front for energy.franklinwh.com: ends TLS with the test certificate and pipes the bytes
to the emulator's Cloud API (plain HTTP/1.1). usage: tls_front.py CLOUD_PORT [LISTEN_PORT=8443]"""
import asyncio, pathlib, ssl, sys
HERE = pathlib.Path(__file__).parent

async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data); await w.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        w.close()

async def handle(cr, cw, port):
    peer = cw.get_extra_info("peername")
    try:
        er, ew = await asyncio.open_connection("127.0.0.1", port)
    except OSError as e:
        print(f"{peer}: emulator unreachable: {e}", flush=True); cw.close(); return
    print(f"{peer}: connected -> 127.0.0.1:{port}", flush=True)
    await asyncio.gather(pipe(cr, ew), pipe(er, cw))

async def main():
    port = int(sys.argv[1]); listen = int(sys.argv[2]) if len(sys.argv) > 2 else 8443
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(HERE / "energy.pem", HERE / "energy.key")
    ctx.set_alpn_protocols(["http/1.1"])          # the emulator speaks HTTP/1.1, not HTTP/2
    srv = await asyncio.start_server(lambda r, w: handle(r, w, port), "127.0.0.1", listen, ssl=ctx)
    print(f"tls on 127.0.0.1:{listen} -> emulator 127.0.0.1:{port}", flush=True)
    await srv.serve_forever()

asyncio.run(main())
```

Start the emulator, then the front:

```bash
franklinwh-emulator ui examples/fleet.yaml --cloud-port 8503
python3 tls_front.py 8503            # listens on 127.0.0.1:8443
```

It offers HTTP/1.1 only, so a client that prefers HTTP/2 (richo's library does) falls back to
HTTP/1.1. It listens on 127.0.0.1; the client must reach it on port 443. Port 443 usually needs
administrator rights, so forward 443 to 8443 instead (a firewall rule, or QEMU's `guestfwd` for a
VM).

### 3. The client's side

* **The name:** make `energy.franklinwh.com` resolve to the front's address, and give it no IPv6
  (AAAA) answer, or the client may go round it. On one machine, a hosts file line; for a VM or
  another device, a DNS server you control that answers that one name and forwards the rest.
* **The trust:** add `ca.pem` to the CA bundle the client uses. Python clients built on `httpx`
  or `requests` use `certifi`'s bundle (`python3 -c "import certifi; print(certifi.where())"`);
  Home Assistant builds its SSL contexts from `certifi` at start-up, so restart it after adding
  the CA. Neither homeassistant-franklinwh nor richo's library pins certificates.

!!! warning "Check the name before the client starts"
    If `energy.franklinwh.com` doesn't resolve to the front, the client reaches the real cloud,
    and with a bundle that also trusts the public CAs (certifi plus the test CA) the handshake
    succeeds and the login is sent there. Check it first, for example
    `python3 -c "import socket; print(socket.getaddrinfo('energy.franklinwh.com', 443))"` on the
    client's machine. A client that trusts only the test CA can't complete a handshake with the
    real cloud.

    Note: a Python test that patches `socket.getaddrinfo` doesn't redirect `httpx`; route in the
    client's network backend or in DNS instead. The 2026-11-16 results file records this.

## The official FranklinWH app: an intercepting proxy

The app has no base URL setting and its traffic comes from a phone. The Cloud API was reverse
engineered by intercepting the app with HTTP Toolkit; the same tool can send it to the emulator:

1. Start the emulator with the Cloud API on an address the HTTP Toolkit machine can reach:
   `--cloud-host` (see [Start it](cloud-api.md#start-it); it warns when that isn't loopback).
   Set the fleet file's `cloud:` account to the one the app logs in with, in a local fleet file
   you don't commit.
2. In HTTP Toolkit, intercept the phone, and add a rule forwarding `energy.franklinwh.com` to the
   emulator's address. HTTP Toolkit ends the app's TLS itself, so the emulator needs no
   certificate.
3. Use the app. The Monitor's "Calls not emulated yet" list shows what it asked for that the
   emulator doesn't serve.

This hasn't been run yet (#316 step 4). What the app does with a call the emulator doesn't serve
isn't known.
