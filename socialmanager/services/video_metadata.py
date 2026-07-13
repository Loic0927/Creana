import shutil
import subprocess
import tempfile
from pathlib import Path


VIDEO_DURATION_UNREADABLE_MESSAGE = "The video duration could not be read. Please choose a supported video file."
VIDEO_TOO_LONG_MESSAGE = "Please provide a video that is 60 seconds or shorter."


class VideoDurationError(Exception):
    pass


def _get_ffprobe_path():
    return shutil.which("ffprobe")


def _copy_video_to_temp(video_file, temp_dir):
    source_name = Path(getattr(video_file, "name", "video")).name
    suffix = Path(source_name).suffix or ".mp4"
    temp_video = Path(temp_dir) / f"duration-source{suffix}"
    position = None
    was_closed = bool(getattr(video_file, "closed", False))

    try:
        if hasattr(video_file, "open"):
            video_file.open("rb")
        if hasattr(video_file, "tell"):
            try:
                position = video_file.tell()
            except (OSError, ValueError):
                position = None
        if hasattr(video_file, "seek"):
            try:
                video_file.seek(0)
            except (OSError, ValueError):
                pass
        with temp_video.open("wb") as destination:
            if hasattr(video_file, "chunks"):
                for chunk in video_file.chunks():
                    destination.write(chunk)
            else:
                while True:
                    chunk = video_file.read(1024 * 1024)
                    if not chunk:
                        break
                    destination.write(chunk)
    finally:
        if position is not None and hasattr(video_file, "seek"):
            try:
                video_file.seek(position)
            except (OSError, ValueError):
                pass
        if was_closed and hasattr(video_file, "close"):
            try:
                video_file.close()
            except Exception:
                pass

    return temp_video


def get_video_duration_seconds(video_file):
    ffprobe_path = _get_ffprobe_path()
    if not ffprobe_path:
        raise VideoDurationError(VIDEO_DURATION_UNREADABLE_MESSAGE)

    try:
        source_path = Path(video_file.path)
    except (AttributeError, NotImplementedError, OSError, ValueError):
        source_path = None

    try:
        if source_path and source_path.exists():
            return _probe_duration(ffprobe_path, source_path)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_video = _copy_video_to_temp(video_file, temp_dir)
            return _probe_duration(ffprobe_path, temp_video)
    except VideoDurationError:
        raise
    except Exception as exc:
        raise VideoDurationError(VIDEO_DURATION_UNREADABLE_MESSAGE) from exc


def _probe_duration(ffprobe_path, video_path):
    try:
        completed = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VideoDurationError(VIDEO_DURATION_UNREADABLE_MESSAGE) from exc

    if completed.returncode != 0:
        raise VideoDurationError(VIDEO_DURATION_UNREADABLE_MESSAGE)

    try:
        duration = float((completed.stdout or "").strip())
    except (TypeError, ValueError) as exc:
        raise VideoDurationError(VIDEO_DURATION_UNREADABLE_MESSAGE) from exc

    if duration <= 0:
        raise VideoDurationError(VIDEO_DURATION_UNREADABLE_MESSAGE)
    return duration


def validate_video_duration_value(duration_seconds, *, max_seconds, tolerance_seconds=0.05):
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise VideoDurationError(VIDEO_DURATION_UNREADABLE_MESSAGE) from exc

    if duration <= 0:
        raise VideoDurationError(VIDEO_DURATION_UNREADABLE_MESSAGE)
    if duration > float(max_seconds) + float(tolerance_seconds):
        raise VideoDurationError(VIDEO_TOO_LONG_MESSAGE)
    return duration


def validate_video_duration_file(video_file, *, max_seconds, tolerance_seconds=0.05):
    duration = get_video_duration_seconds(video_file)
    return validate_video_duration_value(
        duration,
        max_seconds=max_seconds,
        tolerance_seconds=tolerance_seconds,
    )
