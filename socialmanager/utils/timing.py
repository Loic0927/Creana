from contextvars import ContextVar


_current_timing_path = ContextVar("current_timing_path", default="")


def set_timing_path(path):
    return _current_timing_path.set(path or "")


def reset_timing_path(token):
    _current_timing_path.reset(token)


def is_posts_timing_enabled():
    return _current_timing_path.get("").startswith("/posts/")


def current_timing_path():
    return _current_timing_path.get("")
