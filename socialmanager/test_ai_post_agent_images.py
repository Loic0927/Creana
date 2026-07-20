from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from socialmanager.services.ai_post_agent_images import (
    AgentImageError,
    MAX_IMAGE_UPLOAD_BYTES,
    MAX_TOTAL_IMAGE_BYTES,
    MAX_IMAGE_DIMENSION,
    encode_agent_image_data_url,
    prepare_agent_image,
)


def image_upload(image, image_format, name, content_type):
    output = BytesIO()
    image.save(output, format=image_format)
    return SimpleUploadedFile(name, output.getvalue(), content_type=content_type)


class AIPostAgentImageTests(SimpleTestCase):
    def test_per_image_and_total_request_limits_are_distinct(self):
        self.assertEqual(MAX_IMAGE_UPLOAD_BYTES, 10 * 1024 * 1024)
        self.assertEqual(MAX_TOTAL_IMAGE_BYTES, 25 * 1024 * 1024)

    def test_jpeg_png_and_webp_are_normalised_to_jpeg(self):
        cases = [
            (Image.new("RGB", (20, 10), "red"), "JPEG", "a.jpg", "image/jpeg"),
            (Image.new("RGBA", (20, 10), (0, 0, 255, 100)), "PNG", "a.png", "image/png"),
            (Image.new("RGB", (20, 10), "green"), "WEBP", "a.webp", "image/webp"),
        ]
        for image, image_format, name, content_type in cases:
            with self.subTest(image_format=image_format):
                prepared = prepare_agent_image(image_upload(image, image_format, name, content_type))
                self.assertEqual(prepared.content_type, "image/jpeg")
                with Image.open(BytesIO(prepared.data)) as result:
                    self.assertEqual(result.format, "JPEG")
                    self.assertEqual(result.mode, "RGB")

    def test_content_is_sniffed_instead_of_trusting_mime(self):
        upload = image_upload(Image.new("RGB", (10, 10)), "PNG", "fake.jpg", "text/html")
        self.assertTrue(prepare_agent_image(upload).data)

    def test_svg_and_corrupt_binary_are_rejected(self):
        for data, name in [(b"<svg></svg>", "x.svg"), (b"not an image", "x.png")]:
            with self.subTest(name=name), self.assertRaises(AgentImageError) as caught:
                prepare_agent_image(SimpleUploadedFile(name, data))
            self.assertIn(caught.exception.code, {"invalid_image", "unsupported_image_type"})

    def test_large_dimensions_are_resized_and_metadata_removed(self):
        upload = image_upload(Image.new("RGB", (2000, 1000), "white"), "JPEG", "large.jpg", "image/jpeg")
        prepared = prepare_agent_image(upload)
        with Image.open(BytesIO(prepared.data)) as result:
            self.assertLessEqual(max(result.size), MAX_IMAGE_DIMENSION)
            self.assertFalse(result.getexif())

    def test_data_url_has_correct_mime_and_does_not_expose_raw_bytes(self):
        prepared = prepare_agent_image(image_upload(Image.new("RGB", (5, 5)), "PNG", "x.png", "image/png"))
        url = encode_agent_image_data_url(prepared)
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))
        self.assertNotIn(str(prepared.data), url)

    def test_over_10_mb_uses_ai_analysis_wording_and_safe_filename(self):
        upload = SimpleUploadedFile("oversized.jpg", b"x" * (MAX_IMAGE_UPLOAD_BYTES + 1), content_type="image/jpeg")
        with self.assertRaises(AgentImageError) as caught:
            prepare_agent_image(upload)
        self.assertEqual(caught.exception.code, "image_too_large")
        self.assertIn("oversized.jpg", caught.exception.safe_message)
        self.assertIn("10 MB AI analysis limit", caught.exception.safe_message)
        self.assertNotIn("/", caught.exception.safe_message)
