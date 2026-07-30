"""End-to-end check of the outgoing-webhook receiver, over real HTTPS.

Starts the actual server process with TLS and an event token, then delivers a
payload shaped exactly like Bitrix sends one - urlencoded PHP bracket arrays -
and verifies it survives the whole path: HTTP handler, token check, parser,
store, and the tools that read it back.

This covers everything except the portal being the sender. That last step needs
a URL the portal can reach.

Uses a throwaway self-signed certificate, so it also exercises the TLS branch
that mcp.run() cannot serve.

    python scripts/receiver_e2e_check.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PORT = 8771
TOKEN = "e2e-application-token"

sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(label: str, cond: bool) -> bool:
    print(("PASS " if cond else "FAIL ") + label)
    return cond


def make_cert(target: Path) -> tuple[Path, Path] | None:
    """Self-signed cert for localhost. Returns None if we cannot make one."""
    cert, key = target / "cert.pem", target / "key.pem"
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime

        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(private.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName("localhost")]), critical=False)
            .sign(private, hashes.SHA256())
        )
        key.write_bytes(private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
        cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        return cert, key
    except ImportError:
        pass
    openssl = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", "/CN=localhost"],
        capture_output=True, text=True)
    return (cert, key) if openssl.returncode == 0 and cert.exists() else None


def bitrix_delivery(token: str, task_id: str = "477818") -> bytes:
    """Exactly the shape the portal posts: urlencoded, PHP bracket arrays."""
    return (
        f"event=ONTASKUPDATE"
        f"&data%5BFIELDS_AFTER%5D%5BID%5D={task_id}"
        f"&data%5BFIELDS_BEFORE%5D%5BID%5D={task_id}"
        f"&ts={int(time.time())}"
        f"&auth%5Bdomain%5D=resultforyou.ru"
        f"&auth%5Bmember_id%5D=e2e"
        f"&auth%5Bapplication_token%5D={token}"
    ).encode()


def post(url: str, body: bytes, ctx) -> int:
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=10, context=ctx) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> None:
    import ssl

    workdir = Path(tempfile.mkdtemp())
    db = workdir / "e2e.sqlite3"
    pair = make_cert(workdir)
    results = []

    scheme = "https" if pair else "http"
    print(f"certificate: {'self-signed, generated' if pair else 'UNAVAILABLE - falling back to http'}")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["BITRIX_EVENT_TOKEN"] = TOKEN
    env["BITRIX_EVENT_DB"] = str(db)
    env.pop("BITRIX_PULL_CHANNEL", None)
    env.pop("BITRIX_TELEGRAM_TOKEN", None)
    if pair:
        env["BITRIX_HTTP_SSL_CERT"], env["BITRIX_HTTP_SSL_KEY"] = map(str, pair)

    proc = subprocess.Popen(
        [str(PY), "-m", "bitrix_mcp", "--http", "--port", str(PORT)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    ctx = ssl._create_unverified_context() if pair else None
    url = f"{scheme}://localhost:{PORT}/b24/events"
    try:
        for _ in range(30):
            time.sleep(1)
            if proc.poll() is not None:
                break
            try:
                if post(url, b"event=PING", ctx):
                    break
            except Exception:
                continue

        if proc.poll() is not None:
            out, errtext = proc.communicate()
            print("server died:", (errtext or out)[-800:])
            sys.exit(1)

        print(f"server up on {scheme}://localhost:{PORT}\n")
        results.append(check(f"serves {scheme.upper()}", True))

        results.append(check("delivery with a wrong token -> 403",
                             post(url, bitrix_delivery("wrong-token"), ctx) == 403))
        results.append(check("valid delivery -> 200",
                             post(url, bitrix_delivery(TOKEN), ctx) == 200))
        results.append(check("portal retry -> 200 again (never 500)",
                             post(url, bitrix_delivery(TOKEN), ctx) == 200))
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()

    # Read the database back with the same code the tools use.
    os.environ["BITRIX_EVENT_DB"] = str(db)
    from bitrix_mcp.events.store import EventStore

    store = EventStore(str(db))
    rows = store.poll(include_acked=True)
    print()
    for row in rows:
        print(f"   #{row['id']} {row['event']} source={row['source']} "
              f"entity={row['entity']}/{row['entity_id']}")

    results.append(check("exactly one row stored (retry deduplicated)", len(rows) == 1))
    if rows:
        row = rows[0]
        results.append(check("event name preserved", row["event"] == "ONTASKUPDATE"))
        results.append(check("entity extracted from the PHP-bracket payload",
                             (row["entity"], row["entity_id"]) == ("task", "477818")))
        results.append(check("application_token not persisted",
                             "application_token" not in (row["payload"].get("auth") or {})))
        results.append(check("useful auth kept",
                             row["payload"]["auth"]["domain"] == "resultforyou.ru"))
        results.append(check("readable through history",
                             len(store.history(entity="task", entity_id="477818")) == 1))
        results.append(check("rejected delivery left nothing behind",
                             store.stats()["total"] == 1))

    print("\n" + ("ALL CLEAR" if all(results) else "SOMETHING BROKE"))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
