import logging
from urllib.parse import urlparse

import requests
from django.conf import settings


logger = logging.getLogger(__name__)
INDEXNOW_HOST = "creana.app"


def _format_diagnostics(*, key, key_location, response=None, exception=None):
    status_code = response.status_code if response is not None else "Unavailable"
    response_body = response.text if response is not None else "Unavailable"
    exception_message = str(exception) if exception is not None else "None"
    return (
        f"IndexNow endpoint:\n{settings.INDEXNOW_ENDPOINT}\n\n"
        f"HTTP {status_code}\n\n"
        f"Response:\n{response_body}\n\n"
        f"Exception:\n{exception_message}\n\n"
        f"INDEXNOW_KEY exists:\n{'yes' if key else 'no'}\n\n"
        f"host:\n{INDEXNOW_HOST}\n\n"
        f"keyLocation:\n{key_location}"
    )


def _emit_diagnostics(message, diagnostic_callback=None, *, failure=False):
    if failure:
        logger.error(message)
    else:
        logger.info(message)
    if diagnostic_callback is not None:
        diagnostic_callback(message)


def submit_indexnow_urls(urls, diagnostic_callback=None):
    key = settings.INDEXNOW_KEY
    key_location = f"https://{INDEXNOW_HOST}/{key}.txt"
    if not key:
        _emit_diagnostics(
            _format_diagnostics(key=key, key_location=key_location),
            diagnostic_callback,
        )
        return False

    unique_urls = []
    seen = set()
    for value in urls or []:
        url = str(value).strip() if value else ""
        parsed = urlparse(url)
        if not url or not parsed.scheme or not parsed.netloc or url in seen:
            continue
        seen.add(url)
        unique_urls.append(url)

    if not unique_urls:
        _emit_diagnostics(
            _format_diagnostics(key=key, key_location=key_location),
            diagnostic_callback,
        )
        return False

    payload = {
        "host": INDEXNOW_HOST,
        "key": key,
        "keyLocation": key_location,
        "urlList": unique_urls,
    }

    try:
        response = requests.post(
            settings.INDEXNOW_ENDPOINT,
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        _emit_diagnostics(
            _format_diagnostics(
                key=key,
                key_location=key_location,
                response=response,
            ),
            diagnostic_callback,
        )
        return True
    except requests.RequestException as exc:
        _emit_diagnostics(
            _format_diagnostics(
                key=key,
                key_location=key_location,
                response=response if "response" in locals() else exc.response,
                exception=exc,
            ),
            diagnostic_callback,
            failure=True,
        )
        return False
