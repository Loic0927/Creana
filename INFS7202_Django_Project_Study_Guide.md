# INFS7202 Django Final Study Guide Based On This Project

Project analysed: `SocialManager`, especially `socialmanager_project/urls.py`, `socialmanager/urls.py`, `views.py`, `models.py`, `forms.py`, `context_processors.py`, and templates under `socialmanager/templates/socialmanager/`.

Important project note: this project mostly uses class-based views. For exam purposes, methods like `post(self, request, ...)`, `get_queryset()`, and `form_valid()` contain the same Django ideas often tested with function-based views.

## 1. URL Routing: include app URLs

Original code:
```python
path("", include("socialmanager.urls")),
```

Fill in the blank:
```python
path("", ______("socialmanager.urls")),
```

Answer: `include`

Why: `include()` tells the project-level URLconf to delegate matching to `socialmanager.urls`.

If different: using `path` or a string alone would not load the app's URL patterns, so routes like `/posts/` would not resolve.

## 2. URL Routing: route to admin

Original code:
```python
path("admin/", admin.site.urls),
```

Fill in the blank:
```python
path("admin/", admin.site.______),
```

Answer: `urls`

Why: `admin.site.urls` exposes Django admin URL patterns.

If different: `admin.site` is not itself a URLconf, so Django would fail to route admin pages.

## 3. Media files in development

Original code:
```python
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

Fill in the blank:
```python
if settings.DEBUG:
    urlpatterns += static(settings.______, document_root=settings.MEDIA_ROOT)
```

Answer: `MEDIA_URL`

Why: uploaded files are served under the configured media URL during local development.

If different: using `STATIC_URL` would point uploaded avatars/posts at the wrong URL prefix.

## 4. App namespace

Original code:
```python
app_name = "socialmanager"
```

Fill in the blank:
```python
app_name = "______"
```

Answer: `socialmanager`

Why: templates reverse URLs like `{% url 'socialmanager:post_list' %}` using this namespace.

If different: existing namespaced URL tags and `reverse("socialmanager:...")` calls would break.

## 5. URL parameter converter

Original code:
```python
path("posts/<int:pk>/", views.PostDetailView.as_view(), name="post_detail"),
```

Fill in the blank:
```python
path("posts/<______:pk>/", views.PostDetailView.as_view(), name="post_detail"),
```

Answer: `int`

Why: `<int:pk>` captures only integers and passes them as `pk`.

If different: `<str:pk>` would accept non-numeric values and may cause unnecessary database lookups or 404s later.

## 6. Class-based view URL binding

Original code:
```python
path("posts/new/", views.PostCreateView.as_view(), name="post_create"),
```

Fill in the blank:
```python
path("posts/new/", views.PostCreateView.______(), name="post_create"),
```

Answer: `as_view`

Why: class-based views must be converted into a callable view function with `as_view()`.

If different: passing the class itself would not behave like a Django view callable.

## 7. Redirect after logout

Original code:
```python
return redirect("socialmanager:login")
```

Fill in the blank:
```python
return ______("socialmanager:login")
```

Answer: `redirect`

Why: `redirect()` returns an HTTP redirect response to the named URL.

If different: using `render()` would display a template instead of changing the browser URL.

## 8. Logout clears session

Original code:
```python
logout(request)
request.session.flush()
```

Fill in the blank:
```python
logout(request)
request.______.flush()
```

Answer: `session`

Why: `request.session.flush()` clears session data after logging out.

If different: old session data such as cached profile avatar data could remain.

## 9. LoginRequiredMixin

Original code:
```python
class ActiveSubscriptionMixin(LoginRequiredMixin):
```

Fill in the blank:
```python
class ActiveSubscriptionMixin(______):
```

Answer: `LoginRequiredMixin`

Why: the mixin requires authentication before subscription-protected pages run.

If different: unauthenticated users could reach views that expect `request.user` to be real.

## 10. Authentication check

Original code:
```python
if not request.user.is_authenticated:
```

Fill in the blank:
```python
if not request.user.______:
```

Answer: `is_authenticated`

Why: this property checks whether the current user is logged in.

If different: checking `is_active` is not enough because anonymous users are not authenticated.

## 11. request.user in ownership filtering

Original code:
```python
return SaaSSubscription.objects.filter(owner=self.request.user).order_by("is_archived", "name")
```

Fill in the blank:
```python
return SaaSSubscription.objects.filter(owner=self.request.______).order_by("is_archived", "name")
```

Answer: `user`

Why: `request.user` is the logged-in user used to limit subscriptions to the owner.

If different: filtering by the wrong object could leak another user's subscriptions or return none.

## 12. get_object_or_404

Original code:
```python
target_user = get_object_or_404(User, pk=kwargs.get("user_id"))
```

Fill in the blank:
```python
target_user = ______(User, pk=kwargs.get("user_id"))
```

Answer: `get_object_or_404`

Why: it returns the object or raises a 404 response if no user matches.

If different: `User.objects.get(...)` would raise an uncaught exception unless manually handled.

## 13. Prevent self-follow

Original code:
```python
if target_user == request.user:
    return redirect("socialmanager:profile")
```

Fill in the blank:
```python
if target_user == request.______:
    return redirect("socialmanager:profile")
```

Answer: `user`

Why: compares the target profile with the logged-in user.

If different: users might be able to follow themselves, violating the relationship logic.

## 14. get_or_create relationship

Original code:
```python
relationship, created = UserFollow.objects.get_or_create(
    follower=request.user,
    following=target_user,
)
```

Fill in the blank:
```python
relationship, created = UserFollow.objects.______ (
    follower=request.user,
    following=target_user,
)
```

Answer: `get_or_create`

Why: it finds an existing follow or creates one atomically.

If different: plain `create()` could violate the model's `unique_together` constraint for duplicate follows.

## 15. Django messages success

Original code:
```python
messages.success(request, "Notifications marked as read.")
```

Fill in the blank:
```python
messages.______(request, "Notifications marked as read.")
```

Answer: `success`

Why: adds a success-level message displayed by the base template.

If different: `messages.error` would display the action as a failure even though it worked.

## 16. Template messages loop

Original code:
```django
{% for message in messages %}
    <div class="app-message {{ message.tags }}">{{ message }}</div>
{% endfor %}
```

Fill in the blank:
```django
{% for message in ______ %}
    <div class="app-message {{ message.tags }}">{{ message }}</div>
{% endfor %}
```

Answer: `messages`

Why: the messages context processor exposes queued Django messages to templates.

If different: looping over the wrong variable would show no flash messages.

## 17. request.POST

Original code:
```python
submitted_username = request.POST.get("username", "").strip()
```

Fill in the blank:
```python
submitted_username = request.______.get("username", "").strip()
```

Answer: `POST`

Why: submitted form fields from a POST request are stored in `request.POST`.

If different: `request.GET` would look in the query string, not the submitted form body.

## 18. request.FILES

Original code:
```python
avatar = request.FILES.get("avatar")
```

Fill in the blank:
```python
avatar = request.______.get("avatar")
```

Answer: `FILES`

Why: uploaded files are stored in `request.FILES`.

If different: `request.POST.get("avatar")` would not give the uploaded file object.

## 19. Multiple uploaded files

Original code:
```python
illustration_images = self.request.FILES.getlist("illustration_images")
```

Fill in the blank:
```python
illustration_images = self.request.FILES.______("illustration_images")
```

Answer: `getlist`

Why: multiple files under the same field name must be read as a list.

If different: `.get()` would return only one uploaded file.

## 20. HTML form file upload

Original code:
```django
<form method="post" enctype="multipart/form-data" novalidate autocomplete="off">
```

Fill in the blank:
```django
<form method="post" enctype="______" novalidate autocomplete="off">
```

Answer: `multipart/form-data`

Why: file uploads require multipart form encoding.

If different: uploaded images/videos would not appear correctly in `request.FILES`.

## 21. CSRF token

Original code:
```django
{% csrf_token %}
```

Fill in the blank:
```django
{% ______ %}
```

Answer: `csrf_token`

Why: Django requires CSRF protection for unsafe methods like POST.

If different: POST forms may fail with a 403 CSRF verification error.

## 22. CSRF middleware

Original code:
```python
"django.middleware.csrf.CsrfViewMiddleware",
```

Fill in the blank:
```python
"django.middleware.csrf.______",
```

Answer: `CsrfViewMiddleware`

Why: this middleware validates CSRF tokens on POST requests.

If different: CSRF protection could be disabled or fail to load.

## 23. ensure_csrf_cookie decorator

Original code:
```python
@method_decorator(ensure_csrf_cookie, name="dispatch")
class PostDetailView(ActiveSubscriptionMixin, DetailView):
```

Fill in the blank:
```python
@method_decorator(______, name="dispatch")
class PostDetailView(ActiveSubscriptionMixin, DetailView):
```

Answer: `ensure_csrf_cookie`

Why: ensures a CSRF cookie is sent for JavaScript-backed interactions on the detail page.

If different: AJAX POST features may not have a CSRF cookie available.

## 24. ModelForm class

Original code:
```python
class SocialMediaPostForm(DesignSystemFormMixin, forms.ModelForm):
```

Fill in the blank:
```python
class SocialMediaPostForm(DesignSystemFormMixin, forms.______):
```

Answer: `ModelForm`

Why: a ModelForm builds form fields from a Django model.

If different: `forms.Form` would require manually declaring and saving all model fields.

## 25. ModelForm Meta model

Original code:
```python
class Meta:
    model = SocialMediaPost
```

Fill in the blank:
```python
class Meta:
    ______ = SocialMediaPost
```

Answer: `model`

Why: `Meta.model` tells the ModelForm which model it edits.

If different: Django would not know which database model the form maps to.

## 26. ModelForm fields

Original code:
```python
fields = ["campaign", "title", "platform", "content_format", "status"]
```

Fill in the blank:
```python
______ = ["campaign", "title", "platform", "content_format", "status"]
```

Answer: `fields`

Why: `fields` controls which model fields appear in the form.

If different: misspelling it means the form may raise an ImproperlyConfigured error.

## 27. Form instance save with commit=False

Original code:
```python
self.object = form.save(commit=False)
```

Fill in the blank:
```python
self.object = form.save(______=False)
```

Answer: `commit`

Why: `commit=False` creates the model object without saving yet, so extra fields can be set first.

If different: saving immediately could create a user before setting email or related setup data.

## 28. Access cleaned form data

Original code:
```python
self.object.email = form.cleaned_data["email"]
```

Fill in the blank:
```python
self.object.email = form.______["email"]
```

Answer: `cleaned_data`

Why: validated form values are read from `cleaned_data`.

If different: reading raw POST data bypasses validation and type conversion.

## 29. Form validation method

Original code:
```python
def clean_video_file(self):
    video_file = self.cleaned_data.get("video_file")
```

Fill in the blank:
```python
def ______(self):
    video_file = self.cleaned_data.get("video_file")
```

Answer: `clean_video_file`

Why: `clean_<fieldname>()` validates one specific form field.

If different: Django would not automatically call the method for `video_file`.

## 30. Form-wide clean

Original code:
```python
cleaned_data = super().clean()
```

Fill in the blank:
```python
cleaned_data = super().______
```

Answer: `clean()`

Why: form-wide validation should start by getting the parent class cleaned data.

If different: existing field validation errors and cleaned values may be lost.

## 31. ORM filter

Original code:
```python
Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
```

Fill in the blank:
```python
Notification.objects.______(recipient=request.user, is_read=False).update(is_read=True)
```

Answer: `filter`

Why: `filter()` returns all matching unread notifications for the user.

If different: `get()` would fail if there are zero or multiple notifications.

## 32. ORM update

Original code:
```python
.update(is_read=True)
```

Fill in the blank:
```python
.______(is_read=True)
```

Answer: `update`

Why: updates all rows in the queryset in one database query.

If different: looping and saving is slower; using `delete()` would remove notifications instead of marking them read.

## 33. select_related for ForeignKey

Original code:
```python
Notification.objects.filter(recipient=self.request.user).select_related("actor", "actor__profile", "post", "comment")
```

Fill in the blank:
```python
Notification.objects.filter(recipient=self.request.user).______("actor", "actor__profile", "post", "comment")
```

Answer: `select_related`

Why: `select_related()` efficiently follows ForeignKey/OneToOne relationships in SQL joins.

If different: `prefetch_related()` may still work but uses extra queries and is normally for many-to-many or reverse relations.

## 34. prefetch_related for ManyToMany

Original code:
```python
.prefetch_related("campaign_posts")
```

Fill in the blank:
```python
.______("campaign_posts")
```

Answer: `prefetch_related`

Why: `campaign_posts` is a ManyToMany relationship, best loaded with prefetching.

If different: `select_related("campaign_posts")` would be invalid because many-to-many cannot be joined into one object row.

## 35. Count annotation

Original code:
```python
post_count=Count("campaign_posts", distinct=True)
```

Fill in the blank:
```python
post_count=______("campaign_posts", distinct=True)
```

Answer: `Count`

Why: `Count()` annotates each campaign with the number of related posts.

If different: using `Sum()` would try to add values rather than count related rows.

## 36. Q object OR

Original code:
```python
queryset.filter(Q(status=SocialMediaPost.Status.PUBLISHED) | Q(author=self.request.user))
```

Fill in the blank:
```python
queryset.filter(Q(status=SocialMediaPost.Status.PUBLISHED) ______ Q(author=self.request.user))
```

Answer: `|`

Why: `|` combines `Q` objects with SQL OR.

If different: using `&` would require both conditions and hide posts that should be visible by either rule.

## 37. Exists annotation

Original code:
```python
user_has_liked=Exists(user_likes)
```

Fill in the blank:
```python
user_has_liked=______(user_likes)
```

Answer: `Exists`

Why: `Exists()` annotates whether a related subquery has at least one row.

If different: using `Count()` would return a number instead of a boolean-like value.

## 38. OuterRef

Original code:
```python
post=OuterRef("pk")
```

Fill in the blank:
```python
post=______("pk")
```

Answer: `OuterRef`

Why: `OuterRef("pk")` links a subquery to the current row of the outer queryset.

If different: using a fixed primary key would test only one post instead of each post.

## 39. ForeignKey to user

Original code:
```python
author = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    on_delete=models.CASCADE,
    related_name="social_posts",
)
```

Fill in the blank:
```python
author = models.______ (
    settings.AUTH_USER_MODEL,
    on_delete=models.CASCADE,
    related_name="social_posts",
)
```

Answer: `ForeignKey`

Why: many social media posts can belong to one user.

If different: `OneToOneField` would allow only one post per user.

## 40. ForeignKey on_delete

Original code:
```python
on_delete=models.CASCADE
```

Fill in the blank:
```python
on_delete=models.______
```

Answer: `CASCADE`

Why: deleting the related user/subscription deletes dependent rows.

If different: `SET_NULL` would require `null=True` and would preserve orphaned rows.

## 41. ForeignKey related_name

Original code:
```python
related_name="comments"
```

Fill in the blank:
```python
related_name="______"
```

Answer: `comments`

Why: this lets code use `post.comments` to access related comments.

If different: code like `self.object.comments.select_related(...)` would break.

## 42. ManyToManyField

Original code:
```python
campaign_posts = models.ManyToManyField(
    "SocialMediaPost",
    blank=True,
    related_name="campaign_groups",
)
```

Fill in the blank:
```python
campaign_posts = models.______ (
    "SocialMediaPost",
    blank=True,
    related_name="campaign_groups",
)
```

Answer: `ManyToManyField`

Why: one campaign can contain many posts, and one post can appear in many campaign groups.

If different: a ForeignKey would allow each post to point to only one campaign through that field.

## 43. Set ManyToMany values

Original code:
```python
campaign.campaign_posts.set(selected_posts)
```

Fill in the blank:
```python
campaign.campaign_posts.______(selected_posts)
```

Answer: `set`

Why: `.set()` replaces the many-to-many relation with the selected posts.

If different: `.add()` would append posts but leave old unselected posts attached.

## 44. Add ManyToMany item

Original code:
```python
selected_campaign.campaign_posts.add(post)
```

Fill in the blank:
```python
selected_campaign.campaign_posts.______(post)
```

Answer: `add`

Why: `.add()` creates a many-to-many link without removing other links.

If different: `.set(post)` is invalid because `set()` expects an iterable.

## 45. Remove ManyToMany item

Original code:
```python
campaign.campaign_posts.remove(post)
```

Fill in the blank:
```python
campaign.campaign_posts.______(post)
```

Answer: `remove`

Why: `.remove()` deletes the relationship row but not the post itself.

If different: `post.delete()` would delete the actual social media post.

## 46. Authorization with UserPassesTestMixin

Original code:
```python
class OwnerOrAdminMixin(ActiveSubscriptionMixin, UserPassesTestMixin):
```

Fill in the blank:
```python
class OwnerOrAdminMixin(ActiveSubscriptionMixin, ______):
```

Answer: `UserPassesTestMixin`

Why: this mixin calls `test_func()` to decide whether the user is allowed.

If different: without it, `test_func()` would not protect the view.

## 47. Authorization staff override

Original code:
```python
return owner_field == self.request.user
```

Fill in the blank:
```python
return owner_field == self.request.______
```

Answer: `user`

Why: authorization compares the object's owner to the logged-in user.

If different: comparing to `self.user` would fail because the view does not define that attribute.

## 48. Staff user check

Original code:
```python
if self.request.user.is_staff:
```

Fill in the blank:
```python
if self.request.user.______:
```

Answer: `is_staff`

Why: staff users are allowed broader access in some admin-like checks.

If different: `is_superuser` would be stricter and block staff users who should have access.

## 49. Authentication backend login

Original code:
```python
login(
    self.request,
    form.get_user(),
    backend="django.contrib.auth.backends.ModelBackend",
)
```

Fill in the blank:
```python
login(
    self.request,
    form.______(),
    backend="django.contrib.auth.backends.ModelBackend",
)
```

Answer: `get_user`

Why: authentication forms expose the authenticated user through `get_user()`.

If different: using `form.user` may not exist and could raise an attribute error.

## 50. Authentication form

Original code:
```python
class LoginForm(AuthenticationForm):
```

Fill in the blank:
```python
class LoginForm(______):
```

Answer: `AuthenticationForm`

Why: Django's `AuthenticationForm` handles username/password validation.

If different: a plain `forms.Form` would not automatically authenticate the user.

## 51. Email login lookup

Original code:
```python
User.objects.filter(email__iexact=lookup_value).order_by("id").first()
```

Fill in the blank:
```python
User.objects.filter(email________=lookup_value).order_by("id").first()
```

Answer: `iexact`

Why: `iexact` performs a case-insensitive exact match for email login.

If different: `exact` could fail for the same email with different capitalization.

## 52. Password check before delete

Original code:
```python
if not request.user.check_password(password):
```

Fill in the blank:
```python
if not request.user.______(password):
```

Answer: `check_password`

Why: it verifies the raw password against Django's hashed password.

If different: comparing raw strings would be insecure and incorrect because passwords are hashed.

## 53. Static template tag load

Original code:
```django
{% load static %}
```

Fill in the blank:
```django
{% load ______ %}
```

Answer: `static`

Why: loads the `{% static %}` template tag used for CSS, JS, and images.

If different: template rendering would fail when `{% static ... %}` is used.

## 54. Static CSS path

Original code:
```django
<link href="{% static 'socialmanager/css/socialmanager.css' %}" rel="stylesheet">
```

Fill in the blank:
```django
<link href="{% ______ 'socialmanager/css/socialmanager.css' %}" rel="stylesheet">
```

Answer: `static`

Why: converts an app static path into a public URL.

If different: the browser may request a broken relative path.

## 55. Media file URL in template

Original code:
```django
<img alt="{{ user.username|default:user.email }}" class="avatar sidebar-user-avatar" src="{{ user.profile.avatar.url }}">
```

Fill in the blank:
```django
src="{{ user.profile.avatar.______ }}"
```

Answer: `url`

Why: ImageField/FileField values expose `.url` for the public media URL.

If different: using `.path` may expose a server filesystem path and fail in the browser.

## 56. Template variable output

Original code:
```django
{{ row.post.title }}
```

Fill in the blank:
```django
{{ row.post.______ }}
```

Answer: `title`

Why: `title` is a field on `SocialMediaPost`.

If different: a missing attribute renders blank in templates, which can hide data bugs.

## 57. Template loop

Original code:
```django
{% for row in metric_rows %}
```

Fill in the blank:
```django
{% ______ row in metric_rows %}
```

Answer: `for`

Why: `{% for %}` iterates over a list/queryset in a Django template.

If different: the template tag would be invalid and raise a TemplateSyntaxError.

## 58. Template loop end

Original code:
```django
{% endfor %}
```

Fill in the blank:
```django
{% ______ %}
```

Answer: `endfor`

Why: Django templates require explicit loop closing tags.

If different: the template parser will complain about an unclosed `for` block.

## 59. Template conditional

Original code:
```django
{% if metric_rows %}
```

Fill in the blank:
```django
{% ______ metric_rows %}
```

Answer: `if`

Why: `{% if %}` conditionally renders content when a variable is truthy.

If different: `{% for metric_rows %}` is invalid syntax and not a conditional.

## 60. Template conditional end

Original code:
```django
{% endif %}
```

Fill in the blank:
```django
{% ______ %}
```

Answer: `endif`

Why: Django requires explicit closing of `if` blocks.

If different: the rest of the template may be parsed inside the conditional and error.

## 61. Template current user

Original code:
```django
{% if user.is_authenticated %}
```

Fill in the blank:
```django
{% if user.______ %}
```

Answer: `is_authenticated`

Why: the auth context processor exposes `user`, and this property checks login status.

If different: checking `user` alone can be misleading because `AnonymousUser` still exists.

## 62. Template URL reversing

Original code:
```django
{% url 'socialmanager:post_detail' row.post.pk %}
```

Fill in the blank:
```django
{% ______ 'socialmanager:post_detail' row.post.pk %}
```

Answer: `url`

Why: the `{% url %}` tag reverses a named URL using arguments.

If different: hardcoding `/posts/{{ row.post.pk }}/` makes future URL changes harder.

## 63. Template default filter

Original code:
```django
{{ user.username|default:user.email }}
```

Fill in the blank:
```django
{{ user.username|______:user.email }}
```

Answer: `default`

Why: displays email if username is empty.

If different: without the filter, an empty username would render blank.

## 64. Context processor for notification count

Original code:
```python
"socialmanager.context_processors.notification_counts",
```

Fill in the blank:
```python
"socialmanager.context_processors.______",
```

Answer: `notification_counts`

Why: this adds `unread_notification_count` to template context globally.

If different: the sidebar notification badge would not receive that variable.

## 65. Context processor uses request.user

Original code:
```python
Notification.objects.filter(
    recipient=request.user,
    is_read=False,
).count()
```

Fill in the blank:
```python
Notification.objects.filter(
    recipient=request.______,
    is_read=False,
).count()
```

Answer: `user`

Why: unread notifications are counted for the logged-in recipient.

If different: using a fixed user would show the wrong count to everyone.

## 66. Equivalent render() idea for this project

Original project code:
```python
class LandingPageView(TemplateView):
    template_name = "socialmanager/landing.html"
```

Fill in the blank equivalent function-based view:
```python
def landing(request):
    return render(request, "socialmanager/______.html")
```

Answer: `landing`

Why: `TemplateView` renders the named template; the function-based equivalent would call `render(request, template_name)`.

If different: using the wrong template name would display the wrong page or raise `TemplateDoesNotExist`.

## 67. render_to_string for AJAX

Original code:
```python
html = render_to_string(
    "socialmanager/partials/feed_posts.html",
    context,
    request=self.request,
)
```

Fill in the blank:
```python
html = ______(
    "socialmanager/partials/feed_posts.html",
    context,
    request=self.request,
)
```

Answer: `render_to_string`

Why: it renders a template fragment into a string for a JSON response.

If different: `render()` would return an HttpResponse, not just HTML text to embed in JSON.

## 68. JsonResponse

Original code:
```python
return JsonResponse({"success": False, "error": "No active subscription."}, status=400)
```

Fill in the blank:
```python
return ______({"success": False, "error": "No active subscription."}, status=400)
```

Answer: `JsonResponse`

Why: this returns JSON data to JavaScript clients.

If different: returning a dict directly is not a valid Django HTTP response.

## 69. request.method concept in this project

Original project pattern:
```python
class PostCreateView(PostFormMixin, CreateView):
    def post(self, request, *args, **kwargs):
```

Fill in the blank equivalent function-based view:
```python
def post_create(request):
    if request.______ == "POST":
        ...
```

Answer: `method`

Why: class-based views dispatch POST requests to `.post()`. In function-based views you usually check `request.method`.

If different: checking `request.POST == "POST"` is wrong because `request.POST` is form data, not the HTTP verb.

## 70. Scheduled/published status assignment

Original code:
```python
if form.instance.status == SocialMediaPost.Status.PUBLISHED:
```

Fill in the blank:
```python
if form.instance.status == SocialMediaPost.Status.______:
```

Answer: `PUBLISHED`

Why: the code sets `published_at` when a post becomes published.

If different: using `DRAFT` would timestamp drafts as if they were public posts.

## 71. FileField upload path

Original code:
```python
video_file = models.FileField(upload_to="social_videos/", blank=True, null=True)
```

Fill in the blank:
```python
video_file = models.FileField(______="social_videos/", blank=True, null=True)
```

Answer: `upload_to`

Why: `upload_to` decides the media subdirectory for uploaded video files.

If different: omitting it stores files at the media root or uses an unintended location.

## 72. ImageField upload path

Original code:
```python
avatar = models.ImageField(upload_to="profile_avatars/", blank=True, null=True)
```

Fill in the blank:
```python
avatar = models.______(upload_to="profile_avatars/", blank=True, null=True)
```

Answer: `ImageField`

Why: avatars are uploaded images and need image-specific file handling.

If different: `CharField` would store text, not an uploaded file.

## 73. Template condition for owner controls

Original code:
```django
{% if request.user == object.author %}
```

Fill in the blank:
```django
{% if request.______ == object.author %}
```

Answer: `user`

Why: edit controls display only when the current user owns the post.

If different: comparing to `user.id` would compare a User object to an integer and fail.

## 74. Comment form from POST

Original code:
```python
form = PostCommentForm(request.POST)
```

Fill in the blank:
```python
form = PostCommentForm(request.______)
```

Answer: `POST`

Why: the comment form is bound to submitted POST data.

If different: an unbound form would not validate the submitted comment body.

## 75. Assign current user as author

Original code:
```python
comment.author = request.user
```

Fill in the blank:
```python
comment.author = request.______
```

Answer: `user`

Why: comments are saved under the logged-in user's account.

If different: trusting a posted author id would let users impersonate others.

## 76. Delete only own comments

Original code:
```python
comment = get_object_or_404(
    PostComment.objects.select_related("post"),
    pk=kwargs.get("pk"),
    author=request.user,
)
```

Fill in the blank:
```python
comment = get_object_or_404(
    PostComment.objects.select_related("post"),
    pk=kwargs.get("pk"),
    author=request.______,
)
```

Answer: `user`

Why: the lookup includes ownership, so users cannot delete comments owned by someone else.

If different: omitting `author=request.user` would allow unauthorized comment deletion if the URL id is known.

## 77. Login URL setting

Original code:
```python
LOGIN_URL = "socialmanager:login"
```

Fill in the blank:
```python
______ = "socialmanager:login"
```

Answer: `LOGIN_URL`

Why: `LoginRequiredMixin` redirects unauthenticated users to this route.

If different: users may be sent to Django's default `/accounts/login/` instead of your custom login page.

## 78. Login redirect setting

Original code:
```python
LOGIN_REDIRECT_URL = "socialmanager:post_list"
```

Fill in the blank:
```python
LOGIN_REDIRECT_URL = "socialmanager:______"
```

Answer: `post_list`

Why: after login, users land on the feed route.

If different: users would land on a different page after signing in.

## 79. Logout redirect setting

Original code:
```python
LOGOUT_REDIRECT_URL = "socialmanager:login"
```

Fill in the blank:
```python
LOGOUT_REDIRECT_URL = "socialmanager:______"
```

Answer: `login`

Why: after logout, users return to the login page.

If different: they may be redirected to a protected page and immediately bounced again.

## 80. Authorization queryset scoping

Original code:
```python
return SocialMediaPost.objects.filter(subscription=self.subscription)
```

Fill in the blank:
```python
return SocialMediaPost.objects.filter(______=self.subscription)
```

Answer: `subscription`

Why: tenant scoping keeps users inside their active workspace subscription.

If different: filtering by author only would miss shared/admin cases; no filter could leak other workspaces' posts.
