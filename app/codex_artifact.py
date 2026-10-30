"""Select imagegen output by CLI session identity, never by agent-selected paths."""
import json
import os
import stat
from pathlib import Path
from uuid import UUID


class CodexArtifactError(ValueError):
    """Messages are fixed diagnostic codes, without CLI output or private paths."""


def _completed_thread_id(events: str) -> str:
    thread_ids = []
    completed = 0
    try:
        for line in events.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError()
            kind = event.get("type")
            if kind == "thread.started":
                thread_id = event.get("thread_id")
                if not isinstance(thread_id, str) or str(UUID(thread_id)) != thread_id:
                    raise ValueError()
                thread_ids.append(thread_id)
            elif kind == "turn.completed":
                completed += 1
            elif kind == "turn.failed":
                raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise CodexArtifactError("invalid-session-events") from None
    if len(thread_ids) != 1 or completed != 1:
        raise CodexArtifactError("ambiguous-or-incomplete-session")
    return thread_ids[0]


def read_session_artifact(
    events: str, generated_root: Path, max_bytes: int,
) -> tuple[str, bytes]:
    """Return the sole image in generated_images/<thread.started.thread_id>.

    A separate cwd does not isolate imagegen's shared output root. The trusted CLI
    JSON identity scopes this lookup; agent messages, out.png and other sessions
    cannot nominate a result. Directory-relative no-follow reads also reject links.
    """
    thread_id = _completed_thread_id(events)
    try:
        directory_fd = os.open(
            generated_root / thread_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            candidates = [
                name for name in os.listdir(directory_fd)
                if Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ]
            if len(candidates) != 1:
                raise CodexArtifactError("missing-or-ambiguous-session-artifact")
            file_fd = os.open(
                candidates[0], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
            with os.fdopen(file_fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= max_bytes:
                    raise CodexArtifactError("invalid-session-artifact")
                data = stream.read(max_bytes + 1)
                if len(data) != info.st_size or len(data) > max_bytes:
                    raise CodexArtifactError("changed-session-artifact")
        finally:
            os.close(directory_fd)
    except OSError:
        raise CodexArtifactError("session-artifact-unavailable") from None
    return thread_id, data
