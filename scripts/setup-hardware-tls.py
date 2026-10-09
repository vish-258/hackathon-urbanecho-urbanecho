#!/usr/bin/env python3
"""Prepare a private TLS identity for the optional hardware listener; starts nothing."""
import argparse
import ipaddress
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PRIVATE_NETWORKS = tuple(ipaddress.ip_network(v) for v in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def prepare(ip, destination, *, also=(), ca_from=None):
    """Bind to ip; also= adds SANs for other networks. ca_from= reuses a CA that boards already trust."""
    addresses = [ipaddress.IPv4Address(value) for value in (ip, *also)]
    if not all(any(address in network for network in PRIVATE_NETWORKS) for address in addresses):
        raise ValueError("Use the Mac's explicit private LAN IPv4 address, not localhost, 0.0.0.0, or a public address")
    address = addresses[0]
    san = ",".join(f"IP:{value}" for value in dict.fromkeys([*addresses, ipaddress.IPv4Address("127.0.0.1")]))
    if ca_from is not None:
        ca_from = Path(ca_from).absolute()
        if not (ca_from / "ca.crt").is_file() or not (ca_from / "signing-private" / "ca.key").is_file():
            raise ValueError("--ca-from must be a directory prepared by this script, with ca.crt and signing-private/ca.key")
    if not shutil.which("openssl"):
        raise RuntimeError("OpenSSL is required to generate the private certificate")
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination already exists; preserve its keys and choose a new private directory for a replacement")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="hardware-tls-", dir=destination.parent) as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        (directory / "ca.cnf").write_text("[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ca\n[dn]\nCN=UrbanEcho private hardware CA\n[ca]\nbasicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n")
        (directory / "server.cnf").write_text(f"[req]\nprompt=no\ndistinguished_name=dn\nreq_extensions=server\n[dn]\nCN=UrbanEcho hardware listener\n[server]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName={san}\n")
        if ca_from is not None:
            # Same trusted CA: provisioned boards keep working after only UE_HOST changes.
            shutil.copyfile(ca_from / "ca.crt", directory / "ca.crt")
        ca_key = str(ca_from / "signing-private" / "ca.key") if ca_from is not None else "ca.key"
        commands = [] if ca_from is not None else [
            ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "3650", "-config", "ca.cnf", "-keyout", "ca.key", "-out", "ca.crt"]]
        commands += [
            ["req", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256", "-config", "server.cnf", "-keyout", "server.key", "-out", "server.csr"],
            ["x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", ca_key, "-CAcreateserial", "-days", "365", "-sha256", "-extfile", "server.cnf", "-extensions", "server", "-out", "server.crt"],
            ["verify", "-CAfile", "ca.crt", "server.crt"],
        ]
        for command in commands:
            result = subprocess.run(["openssl", *command], cwd=directory, capture_output=True)
            if result.returncode:
                raise RuntimeError("Certificate preparation failed; no listener was started and no existing certificate was changed")
        (directory / "listener.env").write_text(f"HARDWARE_BIND_IP={address}\nHARDWARE_TLS_DIR={destination}\n")
        for path in directory.iterdir():
            path.chmod(0o600)
        # The enclosing 0700 host directory protects these files from other Mac
        # users. Read-only container UID 10001 can read its bind-mounted leaf key.
        # The CA signing key is moved into a separate directory not mounted below.
        if ca_from is None:
            private = directory / "signing-private"
            private.mkdir(mode=0o700)
            (directory / "ca.key").rename(private / "ca.key")
        (directory / "server.key").chmod(0o444)
        (directory / "server.crt").chmod(0o444)
        (directory / "ca.crt").chmod(0o444)
        directory.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True, help="Mac's private Wi-Fi/Ethernet IPv4, used as the certificate IP SAN")
    parser.add_argument("--also-ip", action="append", default=[], help="Another private IPv4 this Mac uses on another network")
    parser.add_argument("--ca-from", type=Path, help="Existing TLS directory whose CA the boards already trust")
    parser.add_argument("--directory", type=Path, default=Path(".local/hardware-tls"))
    args = parser.parse_args()
    prepared = prepare(args.ip, args.directory, also=args.also_ip, ca_from=args.ca_from)
    print("Prepared private TLS files at", prepared)
    print("Copy only ca.crt into device trust configuration. Keep all keys private. No listener was started.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error))
