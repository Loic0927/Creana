from django.core.cache import cache
from storages.backends.gcloud import GoogleCloudStorage
from storages.utils import clean_name


SIGNED_URL_CACHE_TIMEOUT_SECONDS = 50 * 60


class CachedSignedUrlGoogleCloudStorage(GoogleCloudStorage):
    def url(self, name, parameters=None):
        if not name:
            return super().url(name, parameters=parameters)

        if parameters is not None:
            return super().url(name, parameters=parameters)

        normalized_name = self._normalize_name(clean_name(name))
        if not normalized_name:
            return super().url(name, parameters=parameters)

        cache_key = f"signed_url:{normalized_name}"
        cached_url = cache.get(cache_key)
        if cached_url:
            return cached_url

        result = super().url(name, parameters=parameters)
        file_exists = self.exists(normalized_name) if result else False
        if result and file_exists:
            cache.set(cache_key, result, SIGNED_URL_CACHE_TIMEOUT_SECONDS)
        return result
