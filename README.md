# SocialManager

SocialManager is a Django-based social media management project for creating campaigns, scheduling posts, tracking engagement analytics, and generating AI-assisted captions, hashtags, and performance insights.

## Test Accounts

Use this account for marking and admin testing:

| Purpose  | Value                                                      |
| -------- | ---------------------------------------------------------- |
| Username | `AdminTest`                                                |
| Password | `Testadmin01`                                              |
| Access   | Django admin, subscription admin, campaign/post management |

Admin panel:

```text
/admin/
```

Application login:

```text
/login/
```

The login form accepts either the username or email address.

## Project Structure

```text
SocialManager/
  manage.py
  socialmanager_project/   Django project settings and root URLs
  socialmanager/           Main application
  media/                   Uploaded media during local development
  staticfiles/             Collected static files
  db.sqlite3               Local SQLite database
```

## Local Setup

Run commands from inside the `SocialManager` folder:

```bash
python manage.py migrate
python manage.py runserver
```

Then open:

```text
http://127.0.0.1:8000/
```

Optional system check:

```bash
python manage.py check
```

### Password Reset Email

During local development, password reset emails are printed to the runserver terminal. Submit the forgot password form, then copy the reset link from the terminal output.

For production Gmail SMTP, create a Google App Password and configure:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-gmail-address@gmail.com
EMAIL_HOST_PASSWORD=your-google-app-password
DEFAULT_FROM_EMAIL=your-gmail-address@gmail.com
```

## Deployment Notes

Before deploying to UQCloud Zone or another server, update production settings as needed:

- Set `DEBUG = False`.
- Add the deployed domain/IP to `ALLOWED_HOSTS`.
- Run `python manage.py migrate` on the server.
- Run `python manage.py collectstatic` if the server is configured to serve collected static files.
- Provide the deployed URL and the test account above for marking.
- Configure `OPENAI_API_KEY` or `LLM_API_KEY` in the server environment if AI features should call the external LLM provider.

## Core Features

- Landing page with login and signup.
- Password-based authentication using Django's built-in user system.
- Role-aware access using subscription membership roles.
- Django admin management for SaaS subscriptions.
- Subscription archive flow instead of delete.
- Campaign create, list, edit, detail, and delete workflows.
- Social media post create, list, edit, detail, and delete workflows.
- Support for article, image, carousel, and video posts.
- Photo upload support.
- Scheduled date/time support for posts.
- Public feed, profile pages, comments, likes, shares, and follow actions.
- Dashboard and post analytics using stored engagement, view, comment, share, and retention data.

## Project-Specific Features

### Social Media Post Scheduler

The scheduler stores scheduled posts in the database and publishes due posts by changing their status from `scheduled` to `published`.

Scheduled posts require the existing management command to run periodically:

```bash
python manage.py publish_scheduled_posts
```

### Analytics Dashboard

Analytics are calculated from the application's own engagement data because external social media API access was not available for this assignment. The app tracks:

- Post views
- Likes
- Comments
- Shares
- Video watch sessions
- Timed video engagement events
- Campaign and dashboard-level performance summaries

This internal API/data approach was discussed with the professor because access to third-party social media APIs could not be obtained.

## GenAI Features

The app includes GenAI functionality for social media management:

- AI caption suggestions.
- AI hashtag suggestions.
- AI feedback for titles, captions, and hashtags.
- AI dashboard insights.
- AI post analytics insights.
- AI campaign insights.
- AI video retention insights.

If no OpenAI/LLM key is available, analytics insight features use rule-based fallback summaries where implemented.

Environment variable:

```text
OPENAI_API_KEY=your_key_here
```

or:

```text
LLM_API_KEY=your_key_here
```

## AI Usage Statement

AI tools were used during the development of this project to support coding, debugging, design refinement, and documentation. I used AI assistance to help understand Django errors, review view/model logic, improve frontend styling, draft README content, and identify possible issues in authentication, authorisation, scheduling, analytics, and user interface workflows.

AI-generated suggestions were not accepted automatically. I reviewed, tested, and modified the suggested code or text before including it in the final project. I remained responsible for the final implementation, design decisions, testing, and submitted documentation.

AI was not used as a substitute for understanding the project requirements. It was used as a support tool to speed up implementation, clarify errors, and improve code quality and presentation.

## Rubric Mapping

| Rubric item           | Implementation                                                  |
| --------------------- | --------------------------------------------------------------- |
| Landing/login         | Landing page, signup, login, logout                             |
| Role authorization    | Subscription membership roles and owner/admin checks            |
| SaaS subscriptions    | Admin + app UI with create, list, edit, archive, paging         |
| Campaign CRUD         | Create, list, detail, edit, delete                              |
| Post creator CRUD     | Create, list, detail, edit, delete                              |
| Photos and scheduling | Image/carousel upload, video upload, scheduled date/time        |
| Scheduler             | Internal scheduled-post publishing workflow                     |
| Analytics             | Dashboard, campaign, post, and video retention analytics        |
| UI/UX                 | Responsive templates, sidebar navigation, messages, form errors |
| GenAI                 | Captions, hashtags, field feedback, analytics insights          |
