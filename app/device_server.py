"""Start the optional private-LAN HTTPS listener, never the main web application."""
import ipaddress
import os
from pathlib import Path
import ssl

import uvicorn

PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_lan_address(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise ValueError("Choose the Mac's explicit private LAN IPv4 address; wildcard/public/loopback binding is not allowed")
    return str(address)


def main():
    private_lan_address(os.environ.get("HARDWARE_BIND_IP", ""))
    root = Path(os.environ.get("HARDWARE_TLS_ROOT", "/run/hardware-tls"))
    cert, key = root / "server.crt", root / "server.key"
    if not cert.is_file() or not key.is_file():
        raise SystemExit("Prepare the private hardware TLS files before starting this optional listener")
    from app.device_api import create_device_app
    uvicorn.run(create_device_app(), host="0.0.0.0", port=8443, proxy_headers=False,
                ssl_certfile=str(cert), ssl_keyfile=str(key), ssl_version=ssl.PROTOCOL_TLS_SERVER)


if __name__ == "__main__":
    main()
