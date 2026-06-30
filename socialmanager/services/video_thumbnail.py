import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.core.files import File


logger = logging.getLogger(__name__)

PRIMARY_THUMBNAIL_TIMESTAMP = "00:00:01"
FALLBACK_THUMBNAIL_TIMESTAMP = "00:00:00"


def get_ffmpeg_path():
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    try:
        import imageio_ffmpeg
    except ImportError:
        return None

    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _video_source_path(video_file):
    try:
        return Path(video_file.path)
    except (AttributeError, NotImplementedError, ValueError):
        return None


def _copy_video_to_temp(video_file, temp_dir):
    source_name = Path(getattr(video_file, "name", "video")).name
    suffix = Path(source_name).suffix or ".mp4"
    temp_video = Path(temp_dir) / f"source{suffix}"

    try:
        if hasattr(video_file, "open"):
            video_file.open("rb")
        with temp_video.open("wb") as destination:
            for chunk in video_file.chunks():
                destination.write(chunk)
    finally:
        try:
            if hasattr(video_file, "close"):
                video_file.close()
        except Exception:
            pass

    return temp_video


def _run_ffmpeg(ffmpeg_path, video_path, output_path, timestamp):
    command = [
        ffmpeg_path,
        "-y",
        "-ss",
        timestamp,
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_path),
    ]
    return subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0 and output_path.exists() and output_path.stat().st_size > 0


def generate_video_thumbnail(post, *, force=False):
    post_id = getattr(post, "pk", None)
    video_file = getattr(post, "video_file", None)
    video_name = getattr(video_file, "name", "") or ""

    if not post or post.content_format != post.Format.VIDEO or not post.video_file:
        logger.warning(
            "Video thumbnail generation skipped post_id=%r video_file=%r reason=%s",
            post_id,
            video_name,
            "post is missing, is not a video post, or has no video file",
        )
        return False

    if post.video_thumbnail and not force:
        return True

    ffmpeg_path = get_ffmpeg_path()
    if not ffmpeg_path:
        logger.warning(
            "Video thumbnail generation failed post_id=%r video_file=%r reason=%s",
            post_id,
            video_name,
            "ffmpeg executable is unavailable",
        )
        return False

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = _video_source_path(post.video_file)
            if not source_path or not source_path.exists():
                source_path = _copy_video_to_temp(post.video_file, temp_dir)
            if not source_path:
                logger.warning(
                    "Video thumbnail generation failed post_id=%r video_file=%r reason=%s",
                    post_id,
                    video_name,
                    "video could not be copied to a local temporary file",
                )
                return False

            output_path = Path(temp_dir) / f"post-{post.pk or 'video'}-thumbnail.jpg"
            if not _run_ffmpeg(ffmpeg_path, source_path, output_path, PRIMARY_THUMBNAIL_TIMESTAMP):
                if not _run_ffmpeg(ffmpeg_path, source_path, output_path, FALLBACK_THUMBNAIL_TIMESTAMP):
                    logger.warning(
                        "Video thumbnail generation failed post_id=%r video_file=%r reason=%s",
                        post_id,
                        video_name,
                        "ffmpeg could not extract a frame at 1s or 0s",
                    )
                    return False

            thumbnail_name = f"post-{post.pk or 'video'}-thumbnail.jpg"
            with output_path.open("rb") as thumbnail_file:
                post.video_thumbnail.save(thumbnail_name, File(thumbnail_file), save=False)

        update_fields = ["video_thumbnail"]
        if post.pk:
            update_fields.append("updated_at")
            post.save(update_fields=update_fields)
        return True
    except Exception:
        logger.exception(
            "Video thumbnail generation raised an exception post_id=%r video_file=%r reason=%s",
            post_id,
            video_name,
            "unexpected thumbnail generation error",
        )
        return False
