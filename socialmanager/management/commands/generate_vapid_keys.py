import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.core.management.base import BaseCommand


def urlsafe(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class Command(BaseCommand):
    help = "Generate a VAPID key pair for Web Push environment variables."

    def handle(self, *args, **options):
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_bytes = private_key.private_numbers().private_value.to_bytes(32, "big")
        public_bytes = private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        self.stdout.write(f"WEB_PUSH_VAPID_PUBLIC_KEY={urlsafe(public_bytes)}")
        self.stdout.write(f"WEB_PUSH_VAPID_PRIVATE_KEY={urlsafe(private_bytes)}")
