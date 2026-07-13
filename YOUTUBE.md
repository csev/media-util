# YouTube API Setup

This document explains how to obtain the OAuth credentials needed for a local script that uploads videos or updates titles, descriptions, tags, playlists, thumbnails, privacy settings, and other metadata on your own YouTube channel.

## Important terminology

Google gives you a **client credentials JSON file** containing a client ID and client secret.

That file is not the actual YouTube access token.

The first time your script runs, it opens a browser and asks you to authorize access to your Google/YouTube account. The script then receives an OAuth access token and usually saves it locally in a file such as `token.json`. The saved token allows later runs without signing in again.

Typical files:

```text
client_secret.json   # Downloaded from Google Cloud
token.json           # Created by your script after authorization
```

Keep both files private and do not commit them to Git.

## 1. Create or select a Google Cloud project

1. Go to the Google Cloud Console:
   <https://console.cloud.google.com/>
2. Use the project selector at the top of the page.
3. Create a project, or select an existing project.

A name such as `YouTube Media Tools` is fine.

Make sure the correct project remains selected while completing all of the following steps.

## 2. Enable the YouTube Data API v3

The API controls are not inside the **Google Auth Platform** sidebar.

1. Click the hamburger menu in the upper-left corner.
2. Choose **APIs & Services → Library**.
3. Search for **YouTube Data API v3**.
4. Open it.
5. Click **Enable**.

Direct API Library page:

<https://console.cloud.google.com/apis/library>

## 3. Configure the OAuth application

Open:

**Google Auth Platform → Branding**

Enter the required information, including:

- App name
- User support email
- Developer contact email

For a personal command-line utility, the exact public branding is not especially important. The app name is what appears on Google's authorization screen.

## 4. Configure the audience and add yourself as a test user

Open:

**Google Auth Platform → Audience**

For a personal tool, the app can remain in **Testing** while you develop it.

Under **Test users**:

1. Click **Add users**.
2. Add the Google account that owns or manages the YouTube channel.
3. Save the change.

For example:

```text
your-google-account@example.com
```

Without this step, Google may show:

```text
Access blocked: <App Name> has not completed the Google verification process
Error 403: access_denied
```

An app in Testing mode can be used only by accounts listed as approved test users.

## 5. Select OAuth scopes

Open:

**Google Auth Platform → Data Access**

Add only the scopes your program needs.

For full management of your own YouTube account, including metadata updates, playlists, and uploads:

```text
https://www.googleapis.com/auth/youtube
```

For upload-only access:

```text
https://www.googleapis.com/auth/youtube.upload
```

A metadata-management program normally needs the broader `youtube` scope.

## 6. Create a Desktop OAuth client

Open:

**Google Auth Platform → Clients**

Then:

1. Click **Create client**.
2. Choose **Desktop app** as the application type.
3. Enter a name such as `YouTube Local Script`.
4. Click **Create**.
5. Download the JSON credentials file.

The downloaded filename will be long, similar to:

```text
client_secret_1234567890-abc123.apps.googleusercontent.com.json
```

You may rename it:

```bash
mv ~/Downloads/client_secret_*.json client_secret.json
```

Make sure your program points to the actual filename.

## 7. Install the Python libraries

For a Python script:

```bash
python3 -m pip install     google-api-python-client     google-auth-oauthlib     google-auth-httplib2
```

Using a virtual environment is recommended:

```bash
python3 -m venv .venv
source .venv/bin/activate

python3 -m pip install     google-api-python-client     google-auth-oauthlib     google-auth-httplib2
```

## 8. Generate the local OAuth token

The following program performs the initial browser authorization and saves the resulting credentials in `token.json`.

```python
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/youtube",
]

CLIENT_SECRET_FILE = Path("client_secret.json")
TOKEN_FILE = Path("token.json")


def get_credentials() -> Credentials:
    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES,
        )

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CLIENT_SECRET_FILE),
            SCOPES,
        )
        credentials = flow.run_local_server(port=0)

    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def main() -> None:
    credentials = get_credentials()
    youtube = build("youtube", "v3", credentials=credentials)

    response = youtube.channels().list(
        part="snippet",
        mine=True,
    ).execute()

    for channel in response.get("items", []):
        print(channel["snippet"]["title"])


if __name__ == "__main__":
    main()
```

Run it:

```bash
python3 youtube-auth.py
```

On the first run:

1. A browser window opens.
2. Sign in with the Google account connected to the desired YouTube channel.
3. Approve the requested access.
4. The browser redirects to a temporary local web server.
5. The program writes `token.json`.

Subsequent runs normally reuse and refresh that token automatically.

## 9. Keep credentials out of Git

Add these lines to `.gitignore`:

```gitignore
client_secret*.json
token.json
```

Do not publish either file.

If either file is exposed publicly:

1. Delete or rotate the OAuth client in Google Cloud.
2. Revoke the application's access in your Google Account.
3. Create fresh credentials and authorize again.

## 10. Changing scopes or fixing a stale login

Delete `token.json` and run the program again when:

- You change the requested OAuth scopes.
- You authorized the wrong Google account.
- You authorized the wrong YouTube channel.
- The saved refresh token has become invalid.
- Google continues using an old authorization after configuration changes.

```bash
rm token.json
python3 youtube-auth.py
```

Do not normally delete `client_secret.json`; that is the application's credential file.

## 11. Testing-mode limitation

An OAuth app in **Testing** mode is limited to approved test users.

Google may also issue short-lived refresh tokens for some testing-mode configurations. If a token stops refreshing, delete `token.json` and authorize again.

For a private tool used only by its developer, full public verification is often unnecessary. Publishing the app or allowing unrelated users to authorize it can trigger additional verification requirements.

## 12. YouTube API quota

YouTube Data API usage is limited by a daily quota assigned to the Google Cloud project.

The standard project allocation is commonly 10,000 quota units per day. Quota resets at midnight Pacific Time.

Examples:

- `videos.list`: usually 1 unit
- `videos.update`: 50 units
- `videos.insert` uploads have a much higher cost

A read-then-update workflow therefore consumes about 51 units for each changed video. Avoid submitting updates when the metadata has not changed.

View usage under:

**APIs & Services → YouTube Data API v3 → Quotas**

A quota-exceeded response looks similar to:

```text
HttpError 403
reason: quotaExceeded
```

There is no simple pay-as-you-go option for more YouTube Data API quota. Larger quotas require a quota extension request and compliance review.

## 13. Common problems

### `Access blocked ... app is currently being tested`

Add the account under:

**Google Auth Platform → Audience → Test users**

Also confirm that the script is using the client JSON downloaded from the same Google Cloud project you configured.

### The wrong channel appears

A single Google account can have access to multiple YouTube channels or Brand Accounts.

Delete `token.json`, run the authorization again, and carefully choose the Google account/channel presented by YouTube.

### `The request cannot be completed because you have exceeded your quota`

Wait until the daily reset at midnight Pacific Time, then run the script again.

For bulk metadata work, the script should:

- skip videos that already match;
- stop cleanly on `quotaExceeded`;
- preserve progress;
- support a dry-run mode.

### Service account authentication fails

Ordinary YouTube channels do not support service-account authentication. Use OAuth 2.0 with a Desktop application client.

## Official documentation

- YouTube Data API overview:  
  <https://developers.google.com/youtube/v3/getting-started>
- YouTube OAuth authorization:  
  <https://developers.google.com/youtube/v3/guides/authentication>
- OAuth for desktop applications:  
  <https://developers.google.com/youtube/v3/guides/auth/installed-apps>
- Creating YouTube API credentials:  
  <https://developers.google.com/youtube/registering_an_application>
- YouTube quota costs:  
  <https://developers.google.com/youtube/v3/determine_quota_cost>
