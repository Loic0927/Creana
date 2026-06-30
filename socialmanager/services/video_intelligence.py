import logging

from django.conf import settings


logger = logging.getLogger(__name__)
MAX_LABELS = 20
MAX_SHOTS = 250
MAX_EXPLICIT_FRAMES = 100
LIKELIHOOD_NAMES = {
    0: "LIKELIHOOD_UNSPECIFIED",
    1: "VERY_UNLIKELY",
    2: "UNLIKELY",
    3: "POSSIBLE",
    4: "LIKELY",
    5: "VERY_LIKELY",
}


class VideoAnalysisError(RuntimeError):
    pass


def _video_intelligence_module():
    from google.cloud import videointelligence

    return videointelligence


def _video_client(videointelligence):
    return videointelligence.VideoIntelligenceServiceClient()


def _duration_seconds(duration):
    return round(
        float(getattr(duration, "seconds", 0) or 0)
        + float(getattr(duration, "microseconds", 0) or 0) / 1_000_000
        + float(getattr(duration, "nanos", 0) or 0) / 1_000_000_000,
        3,
    )


def get_video_gcs_uri(post):
    if not post.video_file or not post.video_file.name:
        raise VideoAnalysisError("This post does not have an uploaded video.")

    storage = post.video_file.storage
    bucket_name = (getattr(storage, "bucket_name", "") or getattr(settings, "GS_MEDIA_BUCKET_NAME", "")).strip()
    if not bucket_name:
        raise VideoAnalysisError("Video analysis requires the uploaded video to be stored in Google Cloud Storage.")
    return f"gs://{bucket_name}/{post.video_file.name.lstrip('/')}"


def _extract_labels(annotation_result):
    combined = {}
    annotations = list(getattr(annotation_result, "segment_label_annotations", []) or [])
    annotations += list(getattr(annotation_result, "shot_label_annotations", []) or [])
    annotations += list(getattr(annotation_result, "frame_label_annotations", []) or [])
    for annotation in annotations:
        description = str(getattr(getattr(annotation, "entity", None), "description", "") or "").strip()
        if not description:
            continue
        confidences = [float(getattr(segment, "confidence", 0) or 0) for segment in getattr(annotation, "segments", [])]
        confidences += [float(getattr(frame, "confidence", 0) or 0) for frame in getattr(annotation, "frames", [])]
        confidence = max(confidences, default=0)
        categories = [
            str(getattr(category, "description", "") or "").strip()
            for category in getattr(annotation, "category_entities", [])
            if str(getattr(category, "description", "") or "").strip()
        ]
        current = combined.get(description)
        if current is None or confidence > current["confidence"]:
            combined[description] = {
                "description": description,
                "confidence": round(confidence, 4),
                "categories": categories[:5],
            }
    return sorted(combined.values(), key=lambda item: item["confidence"], reverse=True)[:MAX_LABELS]


def _extract_shots(annotation_result):
    shots = []
    for shot in list(getattr(annotation_result, "shot_annotations", []) or [])[:MAX_SHOTS]:
        start = _duration_seconds(getattr(shot, "start_time_offset", None))
        end = _duration_seconds(getattr(shot, "end_time_offset", None))
        shots.append({"start_seconds": start, "end_seconds": end, "duration_seconds": round(max(end - start, 0), 3)})
    return shots


def _extract_explicit_content(annotation_result):
    explicit_annotation = getattr(annotation_result, "explicit_annotation", None)
    frames = []
    max_value = 0
    annotation_frames = list(getattr(explicit_annotation, "frames", []) or [])
    for frame in annotation_frames:
        value = int(getattr(frame, "pornography_likelihood", 0) or 0)
        max_value = max(max_value, value)
        if len(frames) < MAX_EXPLICIT_FRAMES:
            frames.append({
                "time_seconds": _duration_seconds(getattr(frame, "time_offset", None)),
                "likelihood": LIKELIHOOD_NAMES.get(value, str(value)),
            })
    return {
        "max_likelihood": LIKELIHOOD_NAMES.get(max_value, str(max_value)),
        "analyzed_frame_count": len(annotation_frames),
        "frames": frames,
    }


def analyze_gcs_video(post):
    """Run the three Video Intelligence features and return JSON-safe creator inputs."""
    gcs_uri = get_video_gcs_uri(post)
    videointelligence = _video_intelligence_module()
    client = _video_client(videointelligence)
    features = [
        videointelligence.Feature.LABEL_DETECTION,
        videointelligence.Feature.SHOT_CHANGE_DETECTION,
        videointelligence.Feature.EXPLICIT_CONTENT_DETECTION,
    ]
    operation = client.annotate_video(request={"features": features, "input_uri": gcs_uri})
    response = operation.result(timeout=getattr(settings, "VIDEO_INTELLIGENCE_TIMEOUT_SECONDS", 300))
    if not getattr(response, "annotation_results", None):
        raise VideoAnalysisError("Google Cloud returned no video annotations.")
    annotation_result = response.annotation_results[0]
    error = getattr(annotation_result, "error", None)
    if error and getattr(error, "message", ""):
        raise VideoAnalysisError("Google Cloud could not analyze this video.")

    shots = _extract_shots(annotation_result)
    return {
        "version": 1,
        "labels": _extract_labels(annotation_result),
        "shots": shots,
        "shot_count": len(list(getattr(annotation_result, "shot_annotations", []) or [])),
        "explicit_content": _extract_explicit_content(annotation_result),
    }
