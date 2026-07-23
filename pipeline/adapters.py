"""Public source and delivery adapter contracts for Link2News.

Third-party packages can register factories through the ``link2news.sources``
and ``link2news.deliveries`` entry-point groups. A factory receives
``cfg``, ``secrets`` and ``options`` keyword arguments and returns an object
implementing the matching Protocol.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable


AdapterStatus = Literal["queued", "running", "completed", "failed"]


@dataclass(frozen=True)
class SourceLink:
    """One normalized link supplied by an input adapter."""

    url: str
    text: str = ""
    external_id: str = ""
    occurred_at: dt.datetime | None = None
    source_id: str = ""

    def as_pipeline_item(self) -> dict[str, Any]:
        timestamp = int(self.occurred_at.timestamp() * 1000) if self.occurred_at else 0
        return {"url": self.url, "text": self.text, "ts": timestamp}


@dataclass(frozen=True)
class CollectRequest:
    """Context passed to a SourceAdapter."""

    week: str
    since: dt.datetime | None = None
    limit: int | None = None
    mode: str = "all"


@dataclass(frozen=True)
class Artifact:
    """A generated local artifact that can be delivered by an adapter."""

    kind: Literal["html", "pdf", "audio", "markdown"]
    path: Path
    media_type: str
    sha256: str = ""
    duration_ms: int | None = None

    @classmethod
    def from_path(
        cls,
        kind: Literal["html", "pdf", "audio", "markdown"],
        path: Path,
        media_type: str,
        duration_ms: int | None = None,
    ) -> "Artifact":
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
        return cls(kind, path, media_type, digest, duration_ms)


@dataclass(frozen=True)
class DeliveryEvent:
    """Platform-neutral progress or completion event."""

    status: AdapterStatus
    phase: str
    week: str
    progress_done: int | None = None
    progress_total: int | None = None
    summary: str = ""
    artifacts: tuple[Artifact, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeliveryReceipt:
    """Delivery result returned to the pipeline."""

    ok: bool
    items: Mapping[str, Any] = field(default_factory=dict)
    platform_id: str = ""
    error: str = ""


@runtime_checkable
class SourceAdapter(Protocol):
    def collect(self, request: CollectRequest) -> list[SourceLink]:
        """Return links for one run."""


@runtime_checkable
class DeliveryAdapter(Protocol):
    def publish(self, event: DeliveryEvent) -> DeliveryReceipt:
        """Publish or persist one platform-neutral event."""


class CollectorSourceAdapter:
    """Compatibility adapter for the existing Worker/D1 collector."""

    def __init__(self, cfg: dict, secrets: dict, **_: Any):
        self.cfg = cfg
        self.secrets = secrets

    def collect(self, request: CollectRequest) -> list[SourceLink]:
        try:
            from .fetch import fetch_week_links
        except ImportError:  # direct ``python pipeline/main.py`` compatibility
            from fetch import fetch_week_links

        rows = fetch_week_links(self.cfg, self.secrets)
        links = [
            SourceLink(
                url=row["url"],
                text=row.get("text") or "",
                occurred_at=_timestamp_to_datetime(row.get("ts")),
                source_id=self.cfg.get("line", {}).get("push_to", ""),
            )
            for row in rows
        ]
        return links[:request.limit] if request.limit is not None else links


class FileSourceAdapter:
    """Read one public URL per line from a file or stdin."""

    def __init__(self, cfg: dict, secrets: dict, options: Mapping[str, Any] | None = None, **_: Any):
        del cfg, secrets
        self.options = dict(options or {})

    def collect(self, request: CollectRequest) -> list[SourceLink]:
        source = str(self.options.get("path") or "-")
        text = sys.stdin.read() if source == "-" else Path(source).expanduser().read_text(encoding="utf-8")
        try:
            from .common import URL_RE
            from .fetch import canonicalize, normalize_url
        except ImportError:
            from common import URL_RE
            from fetch import canonicalize, normalize_url

        seen: set[str] = set()
        links: list[SourceLink] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            for raw_url in URL_RE.findall(line):
                url = canonicalize(normalize_url(raw_url))
                if url in seen:
                    continue
                seen.add(url)
                links.append(
                    SourceLink(
                        url=url,
                        text=line.replace(raw_url, "").strip(),
                        external_id=f"file:{line_number}:{len(links) + 1}",
                        source_id=source,
                    )
                )
                if request.limit is not None and len(links) >= request.limit:
                    return links
        return links


class LocalDeliveryAdapter:
    """Keep generated files local and return their absolute paths."""

    def __init__(self, cfg: dict, secrets: dict, **_: Any):
        del cfg, secrets

    def publish(self, event: DeliveryEvent) -> DeliveryReceipt:
        missing = [str(item.path) for item in event.artifacts if not item.path.exists()]
        if missing:
            return DeliveryReceipt(False, error=f"missing artifacts: {', '.join(missing)}")
        return DeliveryReceipt(
            True,
            items={item.kind: str(item.path.resolve()) for item in event.artifacts},
            platform_id=f"local:{event.week}",
        )


class LineDeliveryAdapter:
    """Compatibility wrapper around the existing R2 + LINE publication path."""

    def __init__(self, cfg: dict, secrets: dict, **_: Any):
        self.cfg = cfg
        self.secrets = secrets

    def publish(self, event: DeliveryEvent) -> DeliveryReceipt:
        pipeline_main = (
            sys.modules.get("pipeline.main")
            or sys.modules.get("main")
            or sys.modules.get("__main__")
        )
        if pipeline_main is None or not hasattr(pipeline_main, "publish_artifacts"):
            raise RuntimeError("existing LINE publisher is not loaded")

        artifacts = {item.kind: item for item in event.artifacts}
        report = artifacts.get("pdf")
        if report is None:
            return DeliveryReceipt(False, error="LINE delivery requires a PDF artifact")
        audio = artifacts.get("audio")
        remote = pipeline_main.publish_artifacts(
            self.cfg,
            self.secrets,
            event.week,
            report.path,
            audio.path if audio else None,
            dict(event.metadata.get("layout") or {}),
            event.summary,
        )
        return DeliveryReceipt(
            True,
            items=remote,
            platform_id=self.cfg.get("line", {}).get("push_to", ""),
        )


SOURCE_FACTORIES = {
    "collector": CollectorSourceAdapter,
    "file": FileSourceAdapter,
}
DELIVERY_FACTORIES = {
    "line": LineDeliveryAdapter,
    "local": LocalDeliveryAdapter,
}


def deduplicate_external_ids(items: Sequence[SourceLink]) -> list[SourceLink]:
    """Drop repeated non-empty platform IDs while preserving legacy rows."""
    seen: set[str] = set()
    unique: list[SourceLink] = []
    for item in items:
        if item.external_id:
            key = f"{item.source_id}\0{item.external_id}"
            if key in seen:
                continue
            seen.add(key)
        unique.append(item)
    return unique


def load_source_adapter(
    name: str,
    cfg: dict,
    secrets: dict,
    options: Mapping[str, Any] | None = None,
) -> SourceAdapter:
    return _load_adapter("link2news.sources", SOURCE_FACTORIES, name, cfg, secrets, options)


def load_delivery_adapter(
    name: str,
    cfg: dict,
    secrets: dict,
    options: Mapping[str, Any] | None = None,
) -> DeliveryAdapter:
    return _load_adapter("link2news.deliveries", DELIVERY_FACTORIES, name, cfg, secrets, options)


def _load_adapter(
    group: str,
    builtins: Mapping[str, Any],
    name: str,
    cfg: dict,
    secrets: dict,
    options: Mapping[str, Any] | None,
):
    factory = builtins.get(name)
    if factory is None:
        matches = importlib.metadata.entry_points().select(group=group, name=name)
        entry = next(iter(matches), None)
        if entry is None:
            available = ", ".join(sorted(builtins))
            raise LookupError(
                f"Unknown adapter {name!r} in {group}; built-ins: {available}. "
                f"Install a package that registers entry point {group}:{name}."
            )
        try:
            factory = entry.load()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load adapter {group}:{name} from {entry.value}: {exc}"
            ) from exc
    try:
        adapter = factory(cfg=cfg, secrets=secrets, options=dict(options or {}))
    except Exception as exc:
        raise RuntimeError(f"Failed to initialize adapter {group}:{name}: {exc}") from exc
    expected = SourceAdapter if group == "link2news.sources" else DeliveryAdapter
    if not isinstance(adapter, expected):
        method = "collect" if expected is SourceAdapter else "publish"
        raise TypeError(f"Adapter {group}:{name} does not implement {method}()")
    return adapter


def _timestamp_to_datetime(value: Any) -> dt.datetime | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return dt.datetime.fromtimestamp(timestamp / 1000, tz=dt.timezone.utc)
