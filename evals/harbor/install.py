from __future__ import annotations

import hashlib
import socket
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from urllib.parse import urlsplit, urlunsplit


INSTALL_PROXY_ENV = "LANTU_INSTALL_PROXY"
LOCAL_PROXY_PORTS = (7897, 7890, 10809, 10808)
UV_VERSION = "0.7.13"
UV_ARCHIVE_URL = (
    f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
    "uv-x86_64-unknown-linux-gnu.tar.gz"
)
LOCAL_PROXY_CA_PATHS = (
    Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem",
    Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.cer",
)


def container_proxy_url(proxy: str) -> str:
    """Translate a host loopback proxy URL into a Docker-reachable URL."""
    value = proxy.strip()
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlsplit(value)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return value
    if parsed.port is None:
        raise ValueError(f"Proxy URL must include a port: {proxy}")
    credentials = ""
    if parsed.username:
        credentials = parsed.username
        if parsed.password:
            credentials += f":{parsed.password}"
        credentials += "@"
    netloc = f"{credentials}host.docker.internal:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def discover_host_proxy(get_env: Callable[[str], str | None]) -> str | None:
    for name in (INSTALL_PROXY_ENV, "HTTPS_PROXY", "HTTP_PROXY"):
        value = get_env(name)
        if value:
            value = value.strip()
            return value if "://" in value else f"http://{value}"

    for port in LOCAL_PROXY_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return None


def discover_install_proxy(get_env: Callable[[str], str | None]) -> str | None:
    proxy = discover_host_proxy(get_env)
    return container_proxy_url(proxy) if proxy else None


def proxy_env(proxy: str | None) -> dict[str, str]:
    if proxy is None:
        return {}
    no_proxy = (
        "localhost,127.0.0.1,::1,archive.ubuntu.com,security.ubuntu.com,"
        ".aliyuncs.com"
    )
    return {
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }


def prepare_ca_bundle() -> Path:
    """Create a complete public CA bundle with an optional local proxy CA."""
    import certifi

    cache_dir = Path(tempfile.gettempdir()) / "lantu-harbor-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    bundle = cache_dir / "ca-bundle.pem"
    content = Path(certifi.where()).read_bytes()
    for path in LOCAL_PROXY_CA_PATHS:
        if path.is_file():
            extra = path.read_bytes()
            if extra not in content:
                content += b"\n" + extra
            break
    bundle.write_bytes(content)
    return bundle


def prepare_install_assets(
    repository_root: Path,
    commit: str,
    host_proxy: str | None,
) -> tuple[Path, Path]:
    """Cache Linux uv and archive the current LANTU source on the host."""
    cache_dir = Path(tempfile.gettempdir()) / "lantu-harbor-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    uv_binary = cache_dir / f"uv-{UV_VERSION}-x86_64-linux"

    if not uv_binary.is_file():
        archive_path = cache_dir / f"uv-{UV_VERSION}-x86_64-linux.tar.gz"
        opener = build_opener(
            ProxyHandler({"http": host_proxy, "https": host_proxy})
            if host_proxy
            else ProxyHandler()
        )
        with opener.open(UV_ARCHIVE_URL, timeout=90) as response:
            archive_path.write_bytes(response.read())
        with tarfile.open(archive_path, "r:gz") as archive:
            member = next(
                item for item in archive.getmembers() if Path(item.name).name == "uv"
            )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RuntimeError("uv archive does not contain a readable uv binary")
            uv_binary.write_bytes(extracted.read())
        archive_path.unlink(missing_ok=True)

    source_paths = [repository_root / "pyproject.toml"] + sorted(
        path
        for path in (repository_root / "lantu").rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    digest = hashlib.sha256()
    digest.update(commit.encode("utf-8"))
    for path in source_paths:
        digest.update(path.relative_to(repository_root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    source_archive = cache_dir / f"lantu-{digest.hexdigest()[:16]}.tar"
    if not source_archive.is_file():
        with tarfile.open(source_archive, "w") as archive:
            for path in source_paths:
                archive.add(path, arcname=path.relative_to(repository_root).as_posix())
    return uv_binary, source_archive
