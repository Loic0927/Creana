# Creana deployment guide (Google Cloud Run)

Google Cloud project: `creana-498404`

## A. Local development

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver
```

With an empty `DATABASE_URL`, Django uses local SQLite. With `USE_GCS=False`, uploads use `media/` and static files work normally during `runserver`. Add real Google OAuth credentials to `.env` if testing Google sign-in.

## B. Local production check

Use a non-development secret and your local host/origin values. Gunicorn runs on Linux; use Docker to test the exact production server on Windows.

```powershell
$env:DEBUG="False"
$env:SECRET_KEY="replace-with-a-long-random-test-value"
$env:ALLOWED_HOSTS="localhost,127.0.0.1"
$env:CSRF_TRUSTED_ORIGINS="http://localhost:8080"
$env:USE_GCS="False"
python manage.py collectstatic --noinput
python manage.py check --deploy
docker build -t creana-local .
docker run --rm -p 8080:8080 --env-file .env creana-local
```

`check --deploy` may warn when local test values are intentionally less strict. Do not weaken the production settings to silence local-only warnings.

## C. Google Cloud resources

The production architecture needs Cloud Run, Cloud SQL for PostgreSQL, Cloud Storage, Secret Manager, and Artifact Registry. Enable the APIs first:

```bash
gcloud config set project creana-498404
gcloud services enable run.googleapis.com sqladmin.googleapis.com storage.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
```

Create:

- an Artifact Registry Docker repository;
- a PostgreSQL Cloud SQL instance, database, and least-privilege database user;
- one GCS bucket via `GS_BUCKET_NAME`, or separate static/media buckets;
- a dedicated Cloud Run service account.

Grant that service account `Cloud SQL Client`, access to the required secrets, and `Storage Object User` on the media bucket. Media is private by default: Django returns signed URLs using IAM `signBlob`, so also grant the runtime service account permission to sign as itself (normally `Service Account Token Creator` on that service account). If policy requires a public media bucket instead, set `GS_QUERYSTRING_AUTH=False`; never make bucket administration public.

## D. Production environment variables and secrets

Set these ordinary Cloud Run variables:

```text
DEBUG=False
ALLOWED_HOSTS=creana.app,www.creana.app,creana-914298722301.australia-southeast1.run.app
CSRF_TRUSTED_ORIGINS=https://creana.app,https://www.creana.app,https://creana-914298722301.australia-southeast1.run.app
USE_GCS=True
GS_BUCKET_NAME=<bucket-name>
GS_QUERYSTRING_AUTH=True
GS_IAM_SIGN_BLOB=True
GS_SA_EMAIL=<cloud-run-service-account-email>
VIDEO_UPLOAD_MAX_BYTES=524288000
VIDEO_FORM_UPLOAD_MAX_BYTES=20971520
GEMINI_MODEL=gemini-2.5-flash
GEMINI_ENABLED=True
SITE_URL=https://creana.app
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=<smtp-host>
EMAIL_PORT=587
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=Creana <no-reply@example.com>
SERVER_EMAIL=Creana <no-reply@example.com>
```

`GEMINI_ENABLED=True` and `GEMINI_MODEL=gemini-2.5-flash` must be configured as two separate environment variables. Do not combine them into one value.

After the final HTTPS domain is verified, set `SECURE_HSTS_SECONDS` (commonly `31536000`). Enable HSTS subdomains/preload only after confirming every affected subdomain is permanently HTTPS.

If static and media are separated, set `GS_STATIC_BUCKET_NAME` and `GS_MEDIA_BUCKET_NAME` instead of `GS_BUCKET_NAME`.

### Direct video upload CORS

Video files upload from the browser directly to the private media bucket through a server-created resumable session. Save this as `cors.json`:

```json
[
  {
    "origin": ["https://creana.app", "https://www.creana.app", "https://creana-914298722301.australia-southeast1.run.app"],
    "method": ["PUT"],
    "responseHeader": ["Content-Type", "Content-Range", "ETag", "x-goog-generation"],
    "maxAgeSeconds": 3600
  }
]
```

Apply it to the media bucket:

```bash
gcloud storage buckets update gs://creana-498404 --cors-file=cors.json
gcloud storage buckets describe gs://creana-498404 --format="default(cors_config)"
```

Add every production custom-domain origin explicitly if the form is served from more than the Cloud Run URL. Keep `GS_QUERYSTRING_AUTH=True`, `GS_IAM_SIGN_BLOB=True`, and `GS_SA_EMAIL` set to the Cloud Run runtime service account. Consider a lifecycle rule for abandoned objects under `social_videos/` because a successful direct upload can outlive a subsequently invalid or abandoned post form.

Store these in Secret Manager and expose them to Cloud Run as environment variables:

```text
SECRET_KEY
DATABASE_URL
GEMINI_API_KEY
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
STRIPE_SECRET_KEY
STRIPE_PUBLISHABLE_KEY
STRIPE_WEBHOOK_SECRET
EMAIL_HOST_USER
EMAIL_HOST_PASSWORD
```

### Production Cloud Run environment variables

PowerShell-safe update command (the custom `|` delimiter preserves commas inside host/origin values):

```powershell
gcloud run services update creana --region=australia-southeast1 --update-env-vars="^|^ALLOWED_HOSTS=creana.app,www.creana.app,creana-914298722301.australia-southeast1.run.app|CSRF_TRUSTED_ORIGINS=https://creana.app,https://www.creana.app,https://creana-914298722301.australia-southeast1.run.app|SITE_URL=https://creana.app|GEMINI_ENABLED=True|GEMINI_MODEL=gemini-2.5-flash"
```

Keep `GS_QUERYSTRING_AUTH=True`, `GS_IAM_SIGN_BLOB=True`, `GS_SA_EMAIL`, and `VIDEO_UPLOAD_MAX_BYTES` configured as documented above. Add Stripe live keys through Cloud Run environment variables or Secret Manager; never commit them to Git.

### Google OAuth production configuration

Add all three Authorized redirect URIs in Google Cloud Console:

```text
https://creana.app/accounts/google/login/callback/
https://www.creana.app/accounts/google/login/callback/
https://creana-914298722301.australia-southeast1.run.app/accounts/google/login/callback/
```

Keep Google OAuth client credentials in Secret Manager or Cloud Run environment variables. Never hardcode or commit them.

### Stripe live readiness

Live mode requires separate production values supplied securely at runtime:

```text
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_SECRET_KEY=sk_live_...
STRIPE_MEMBERSHIP_PRICE_ID=price_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

Configure the live webhook endpoint as `https://creana.app/stripe/webhook/`. Recommended events are:

- `checkout.session.completed`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.payment_failed`
- `invoice.payment_succeeded`

Do not commit Stripe test keys, live keys, or webhook secrets.

A Cloud SQL Unix-socket URL has this shape:

```text
postgresql://DB_USER:URL_ENCODED_PASSWORD@/DB_NAME?host=/cloudsql/PROJECT:REGION:INSTANCE
```

URL-encode reserved characters in the username/password. Never commit the resulting URL or a production `.env` file.

## E. Build and deploy

Replace the placeholders below with the chosen region, repository, service account, Cloud SQL connection name, bucket, hosts, and secret names.

```bash
gcloud builds submit --tag REGION-docker.pkg.dev/creana-498404/REPOSITORY/creana:latest

gcloud run deploy creana \
  --image REGION-docker.pkg.dev/creana-498404/REPOSITORY/creana:latest \
  --region REGION \
  --service-account SERVICE_ACCOUNT_EMAIL \
  --add-cloudsql-instances creana-498404:REGION:INSTANCE \
  --set-env-vars DEBUG=False,USE_GCS=True,GS_BUCKET_NAME=BUCKET_NAME,GEMINI_MODEL=gemini-2.5-flash \
  --set-secrets SECRET_KEY=SECRET_KEY:latest,DATABASE_URL=DATABASE_URL:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,GOOGLE_CLIENT_ID=GOOGLE_CLIENT_ID:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,STRIPE_SECRET_KEY=STRIPE_SECRET_KEY:latest,STRIPE_PUBLISHABLE_KEY=STRIPE_PUBLISHABLE_KEY:latest,STRIPE_WEBHOOK_SECRET=STRIPE_WEBHOOK_SECRET:latest
```

For comma-containing values such as `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, use the custom-delimiter PowerShell command above, the Cloud Run console, or an environment-variable YAML file so `gcloud` does not split them incorrectly.

To verify the deployed revision references the Gemini secret without printing its value:

```bash
gcloud run services describe creana \
  --region australia-southeast1 \
  --format="yaml(spec.template.spec.containers[0].env)"
```

The `GEMINI_API_KEY` entry must contain a `valueFrom.secretKeyRef`, not an empty `value`. Run `python manage.py test_gemini` in a one-off Cloud Run Job using the same image, service account, environment variables, and secrets to verify client initialization and a real `generate_content` request from the production runtime.

Run migrations as a Cloud Run Job using the same image, service account, Cloud SQL connection, variables, and secrets:

```bash
gcloud run jobs create creana-migrate --image REGION-docker.pkg.dev/creana-498404/REPOSITORY/creana:latest --region REGION --command python --args manage.py,migrate
gcloud run jobs execute creana-migrate --region REGION --wait
```

When `USE_GCS=True`, also run `python manage.py collectstatic --noinput` as a similarly configured one-off Cloud Run Job. This uploads versioned static assets to `GS_STATIC_BUCKET_NAME` (or `GS_BUCKET_NAME`); the build-time collection uses WhiteNoise only so the image can build without production credentials.

Configure a separate one-off job with `python manage.py createsuperuser` if needed. Do not run migrations or create a superuser automatically in every web-container startup.

## F. Deployment checklist

- `python manage.py check`
- `python manage.py check --deploy` with production-like variables
- `python manage.py collectstatic --noinput`
- `python manage.py migrate`
- Create the first superuser securely
- Verify the Cloud Run `/` response and custom-domain HTTPS
- Add Google OAuth redirect URI: `https://<host>/accounts/google/login/callback/`
- Add Stripe webhook URL and confirm webhook signature verification
- Test avatar, post image, video, and thumbnail upload persistence in GCS
- Test AI generation with the production Gemini secret
- Test checkout and webhook-driven membership updates
- Confirm `.env`, database files, media, and logs are absent from the image

## G. Search engine registration

Production SEO endpoints depend on `SITE_URL=https://creana.app`. After deploying:

1. Verify the `https://creana.app` property in [Google Search Console](https://search.google.com/search-console/).
2. Submit `https://creana.app/sitemap.xml` in Search Console.
3. Use URL Inspection for the landing page and a published, public post after deployment.
4. Verify `https://creana.app` in [Bing Webmaster Tools](https://www.bing.com/webmasters/).
5. Submit `https://creana.app/sitemap.xml` in Bing Webmaster Tools.

Do not add a Google or Bing verification token to source control. Add a verification
mechanism only after the provider supplies the exact value, preferably through an
environment variable or provider-managed DNS record.

IndexNow is intentionally not enabled yet. A future implementation must read its key
from an environment variable, expose only the required key-verification URL, and submit
only public canonical URLs. Never submit drafts, scheduled/private posts, internal
application pages, action endpoints, or private media URLs.

Deployment smoke checks:

```bash
curl -i https://creana.app/robots.txt
curl -i https://creana.app/sitemap.xml
curl -s https://creana.app/ | grep -E "description|robots|og:|twitter:|canonical"
curl -s https://creana.app/posts/POST_ID/POST_SLUG/ | grep -E "description|robots|og:|twitter:|canonical"
curl -s -I https://creana.app/login/
```

The login response should include `X-Robots-Tag: noindex, nofollow`. The sitemap must
contain only the landing page and published/public canonical post URLs.
