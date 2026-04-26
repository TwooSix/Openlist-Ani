"""Magnet-link resolver: extract title and detect collection torrents.

Two-stage flow:

1. Parse the ``dn=`` parameter from the magnet URI.  This is instant and
   covers most well-formed magnets.
2. If ``dn`` is empty / equals the info-hash, fetch the torrent metadata
   via libtorrent (DHT + trackers).  Wrapped in :func:`asyncio.to_thread`
   so the async router stays non-blocking.

The resolver also flags *collection* resources by matching the title
against a set of well-known keywords (合集 / 全集 / Complete / Batch /
``\\d+-\\d+`` ranges …).  Callers must surface this to the user and
abort, because the OpenList-Ani downloader cannot currently rename
multi-episode payloads.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp

from ....logger import logger

# Cap a downloaded .torrent file at 10 MiB — real torrents are a few KiB
# to a few hundred KiB; anything larger is almost certainly a wrong URL
# or a malicious response.
_MAX_TORRENT_BYTES = 10 * 1024 * 1024
_TORRENT_DOWNLOAD_TIMEOUT_SECS = 30.0

# ── Models ───────────────────────────────────────────────────────────


@dataclass
class TorrentFile:
    name: str
    size: int = 0


@dataclass
class ResolveResult:
    """Outcome of :func:`resolve_magnet`.

    ``title`` is ``None`` only when both ``dn`` and metadata fetch fail.
    Callers must NOT fabricate one — they should ask the user instead.
    """

    success: bool
    message: str
    title: str | None = None
    source: str | None = None  # "dn" | "metadata" | None
    file_count: int | None = None
    files: list[TorrentFile] = field(default_factory=list)
    is_collection: bool = False
    collection_reason: str | None = None


# ── Collection keyword detection ─────────────────────────────────────

# Keep each pattern small and well-anchored to keep false positives low.
# Word-boundary `\b` is unreliable across CJK so the Chinese terms rely on
# direct substring presence.
_COLLECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"合集"),
    re.compile(r"全集"),
    re.compile(r"总集篇"),
    re.compile(r"(?i)\bcomplete\b"),
    re.compile(r"(?i)\bbatch\b"),
    re.compile(r"(?i)BD\s*BOX"),
    # Episode ranges like 01-12, 01~24, 01–24.  Require BOTH sides to be
    # zero-padded 2-3 digit numbers so single-episode titles such as
    # "Season 2 - 14" (which has a bare "2 - 14") don't trigger.  Real
    # collection releases conventionally use padded indices ("01-12").
    re.compile(r"(?<!\d)0\d{1,2}\s*[-~–—]\s*\d{2,3}(?!\d)"),
    # SxxExx-Eyy
    re.compile(r"(?i)\bS\d{1,2}E\d{1,3}\s*-\s*E?\d{1,3}\b"),
    # SxxComplete / SeasonxxComplete
    re.compile(r"(?i)\bS(?:eason)?\s*\d{1,2}\s*Complete\b"),
)


def detect_collection(title: str) -> tuple[bool, str | None]:
    """Return ``(is_collection, matched_fragment)`` for ``title``.

    A non-empty match always means the title looks like a collection
    release; the matched substring is returned so the caller can show it
    to the user.
    """
    if not title:
        return False, None
    for pat in _COLLECTION_PATTERNS:
        m = pat.search(title)
        if m:
            return True, m.group(0)
    return False, None


# ── Magnet ``dn`` extraction ─────────────────────────────────────────

_MAGNET_HASH_RE = re.compile(r"(?i)urn:btih:([a-z0-9]{32,40})")


def _extract_dn(magnet: str) -> str | None:
    """Return the URL-decoded ``dn=`` parameter, or ``None`` if absent."""
    try:
        parsed = urlparse(magnet)
    except ValueError:
        return None
    if parsed.scheme.lower() != "magnet":
        return None

    # ``parse_qs`` understands magnet's query-style `xt=` / `dn=` syntax
    # because urllib treats the fragment after `?` as a query string.
    qs = parse_qs(parsed.query)
    dn_values = qs.get("dn") or []
    if not dn_values:
        return None

    dn = unquote(dn_values[0]).strip()
    if not dn:
        return None

    # Some clients duplicate the info-hash into ``dn``; that isn't a
    # human-readable title.
    if _MAGNET_HASH_RE.fullmatch(dn):
        return None
    if re.fullmatch(r"[A-Fa-f0-9]{32,40}", dn):
        return None

    return dn


def _is_valid_magnet(magnet: str) -> bool:
    if not magnet or not isinstance(magnet, str):
        return False
    if not magnet.lower().startswith("magnet:?"):
        return False
    return _MAGNET_HASH_RE.search(magnet) is not None


# ── libtorrent metadata fetch ────────────────────────────────────────


_DHT_BOOTSTRAP_ROUTERS: tuple[tuple[str, int], ...] = (
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("dht.libtorrent.org", 25401),
)


def _build_session(lt) -> "object":  # noqa: ANN001 - libtorrent is dynamic
    """Create a libtorrent session pre-configured for metadata fetching."""
    settings = {
        "listen_interfaces": "0.0.0.0:0",
        "enable_dht": True,
        "enable_lsd": True,
        "enable_upnp": True,
        "enable_natpmp": True,
        "alert_mask": (
            lt.alert.category_t.error_notification  # type: ignore[attr-defined]
            | lt.alert.category_t.status_notification  # type: ignore[attr-defined]
        ),
    }
    session = lt.session(settings)
    for host, port in _DHT_BOOTSTRAP_ROUTERS:
        try:
            session.add_dht_router(host, port)
        except Exception:
            pass
    try:
        session.start_dht()
    except Exception:
        pass
    return session


def _force_announce(handle) -> None:  # noqa: ANN001 - dynamic
    """Trigger immediate tracker + DHT announce for ``handle``."""
    try:
        handle.force_reannounce()
        handle.force_dht_announce()
    except Exception:
        pass


def _is_metadata_alert(alert) -> bool:  # noqa: ANN001 - dynamic
    """True iff ``alert`` signals BEP-9 metadata arrival."""
    if type(alert).__name__ == "metadata_received_alert":
        return True
    what = getattr(alert, "what", lambda: "")
    try:
        return what() == "metadata_received"
    except Exception:
        return False


def _wait_for_metadata(session, handle, deadline: float) -> bool:  # noqa: ANN001
    """Block until metadata arrives or ``deadline`` (monotonic) elapses.

    Returns True iff the torrent has metadata at the time of return.
    """
    import time

    while time.monotonic() < deadline:
        if handle.status().has_metadata:
            return True
        remaining = max(0.05, deadline - time.monotonic())
        wait_ms = int(min(1000.0, remaining * 1000))
        session.wait_for_alert(wait_ms)
        for alert in session.pop_alerts():
            if _is_metadata_alert(alert):
                return handle.status().has_metadata
    return handle.status().has_metadata


def _torrent_files(ti) -> list[TorrentFile]:  # noqa: ANN001 - dynamic
    """Enumerate the file list of a libtorrent ``torrent_info``."""
    files: list[TorrentFile] = []
    try:
        file_storage = ti.files()
        for i in range(file_storage.num_files()):
            files.append(
                TorrentFile(
                    name=file_storage.file_path(i),
                    size=int(file_storage.file_size(i)),
                )
            )
    except Exception as e:  # pragma: no cover
        logger.warning(f"libtorrent file listing failed: {e}")
    return files


def _fetch_metadata_blocking(
    magnet: str, deadline_secs: float
) -> tuple[str | None, list[TorrentFile]]:
    """Block until libtorrent fetches metadata or ``deadline_secs`` elapses.

    Runs entirely on the calling thread (callers wrap this with
    :func:`asyncio.to_thread`).  Returns ``(name, files)`` on success,
    ``(None, [])`` on timeout / error.

    Implementation notes:
        - DHT bootstrap routers are added explicitly; without them a cold
          session can spend 30-60 s discovering peers before any tracker
          response arrives.
        - LSD / UPnP / NAT-PMP enabled to maximise the chance of finding
          peers behind common router setups.
        - ``upload_mode`` is intentionally NOT set: in some libtorrent
          versions it suppresses the BEP-9 metadata exchange that we
          actually need.  We never enter the piece-download phase because
          the torrent is removed as soon as metadata arrives.
        - We block on ``session.wait_for_alert`` and look for
          ``metadata_received_alert`` instead of polling
          ``has_metadata``; this returns within milliseconds of arrival
          rather than up to one poll-interval late.
    """
    try:
        import libtorrent as lt  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover - environment dependent
        logger.warning(f"libtorrent not available: {e}")
        return None, []

    import time

    with tempfile.TemporaryDirectory(prefix="oani-magnet-") as save_dir:
        session = _build_session(lt)
        params = lt.parse_magnet_uri(magnet)
        params.save_path = save_dir
        # NOTE: do NOT set upload_mode here — see docstring.
        handle = session.add_torrent(params)
        _force_announce(handle)

        deadline = time.monotonic() + max(1.0, float(deadline_secs))
        try:
            if not _wait_for_metadata(session, handle, deadline):
                return None, []
            ti = handle.torrent_file()
            if ti is None:
                return None, []
            return ti.name() or None, _torrent_files(ti)
        finally:
            try:
                session.remove_torrent(handle)
            except Exception:
                pass


# ── Public entry point ───────────────────────────────────────────────


async def resolve_magnet(magnet: str, metadata_timeout: float = 30.0) -> ResolveResult:
    """Resolve ``magnet`` to a real title and detect collection releases.

    Args:
        magnet: Magnet URI starting with ``magnet:?``.
        metadata_timeout: Seconds to wait for libtorrent metadata; ignored
            when the ``dn`` parameter is already usable. Named to make
            clear this is a budget, not an HTTP/IO socket timeout.

    Returns:
        :class:`ResolveResult` with the best title we could obtain plus
        the collection flag.  Errors are reported as ``success=False``
        with a human-readable ``message`` rather than exceptions.
    """
    if not _is_valid_magnet(magnet):
        return ResolveResult(
            success=False,
            message="Invalid magnet URI (expected 'magnet:?xt=urn:btih:…').",
        )

    # Step 1: dn=
    dn_title = _extract_dn(magnet)
    if dn_title:
        is_coll, reason = detect_collection(dn_title)
        return ResolveResult(
            success=True,
            message="Resolved title from magnet 'dn=' parameter.",
            title=dn_title,
            source="dn",
            is_collection=is_coll,
            collection_reason=reason,
        )

    # Step 2: libtorrent
    logger.info(
        f"Fetching torrent metadata via libtorrent (budget={metadata_timeout}s)…"
    )
    try:
        name, files = await asyncio.to_thread(
            _fetch_metadata_blocking, magnet, metadata_timeout
        )
    except Exception as e:
        logger.warning(f"libtorrent metadata fetch failed: {e}")
        return ResolveResult(
            success=False,
            message=(
                f"Failed to fetch torrent metadata: {e}. "
                "Provide a .torrent file or supply the title manually."
            ),
        )

    if not name:
        return ResolveResult(
            success=False,
            message=(
                f"Metadata fetch timed out after {metadata_timeout:.0f}s "
                "and magnet has no usable 'dn=' parameter. Ask the user "
                "for the resource title; do NOT fabricate one."
            ),
        )

    is_coll, reason = detect_collection(name)
    return ResolveResult(
        success=True,
        message="Resolved title from torrent metadata.",
        title=name,
        source="metadata",
        file_count=len(files),
        files=files,
        is_collection=is_coll,
        collection_reason=reason,
    )


# ── .torrent file resolver ───────────────────────────────────────────


def _looks_like_torrent_url(url: str) -> bool:
    """Cheap syntactic check for a .torrent URL."""
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    return bool(parsed.netloc)


async def _download_torrent_bytes(url: str) -> tuple[bytes | None, str | None]:
    """Download a .torrent file via HTTPS.

    Returns ``(bytes, None)`` on success, ``(None, error_msg)`` on
    failure.  Bounded by :data:`_MAX_TORRENT_BYTES` and
    :data:`_TORRENT_DOWNLOAD_TIMEOUT_SECS`.
    """
    timeout = aiohttp.ClientTimeout(total=_TORRENT_DOWNLOAD_TIMEOUT_SECS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    return None, (f"HTTP {resp.status} while fetching torrent file.")
                # Stream read with size cap.
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > _MAX_TORRENT_BYTES:
                        return None, (
                            f"Torrent file exceeds {_MAX_TORRENT_BYTES} "
                            "bytes; refusing to read."
                        )
                    chunks.append(chunk)
                return b"".join(chunks), None
    except asyncio.TimeoutError:
        return None, (
            f"Timed out after {_TORRENT_DOWNLOAD_TIMEOUT_SECS:.0f}s "
            "while downloading the .torrent file."
        )
    except aiohttp.ClientError as e:
        return None, f"HTTP client error: {e}"


def _parse_torrent_blob(blob: bytes) -> tuple[str | None, list[TorrentFile]]:
    """Parse a .torrent blob with libtorrent, returning ``(name, files)``.

    Runs on the calling thread; callers should wrap with
    :func:`asyncio.to_thread` when invoked from async code.
    """
    try:
        import libtorrent as lt  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover - environment dependent
        logger.warning(f"libtorrent not available: {e}")
        return None, []

    try:
        ti = lt.torrent_info(blob)
    except Exception as e:
        logger.warning(f"libtorrent could not parse .torrent blob: {e}")
        return None, []
    try:
        return ti.name() or None, _torrent_files(ti)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"libtorrent torrent_info inspection failed: {e}")
        return None, []


async def resolve_torrent(url: str) -> ResolveResult:
    """Resolve a ``.torrent`` file URL to its real title and file list.

    Mirrors :func:`resolve_magnet`'s output shape so the two can share
    the same downstream pipeline (collection rejection, duplicate check,
    user confirmation, download).  We deliberately parse the torrent
    blob locally with libtorrent rather than joining the swarm — it is
    much faster and avoids the DHT/peer bootstrap needed for magnets.

    Args:
        url: ``http://`` or ``https://`` URL to a .torrent file.

    Returns:
        :class:`ResolveResult` — ``title`` is ``None`` only when the
        download or parse failed; callers must ask the user rather than
        fabricate one.
    """
    if not _looks_like_torrent_url(url):
        return ResolveResult(
            success=False,
            message=("Invalid torrent URL (expected 'http(s)://…/*.torrent')."),
        )

    blob, err = await _download_torrent_bytes(url)
    if blob is None:
        return ResolveResult(
            success=False,
            message=(
                f"Failed to download .torrent: {err}. "
                "Supply the title manually or pick a working URL."
            ),
        )

    name, files = await asyncio.to_thread(_parse_torrent_blob, blob)
    if not name:
        return ResolveResult(
            success=False,
            message=(
                "Downloaded the .torrent file but could not parse a "
                "name from it. Ask the user for the resource title; "
                "do NOT fabricate one."
            ),
        )

    is_coll, reason = detect_collection(name)
    return ResolveResult(
        success=True,
        message="Resolved title from .torrent file metadata.",
        title=name,
        source="torrent_file",
        file_count=len(files),
        files=files,
        is_collection=is_coll,
        collection_reason=reason,
    )


# Suppress static-analyzer complaint about unused Path import — kept for
# possible future torrent-file caching.
_ = Path
