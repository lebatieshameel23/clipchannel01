# YouTube login from your phone (no computer needed)

You will end up with three values to paste into GitHub secrets: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
`YT_REFRESH_TOKEN`. Use your phone browser and tap "Desktop site" in the browser menu on the Google pages,
because they are cramped on mobile. Do this **separately for each channel** (one Google Cloud project per
channel): uploads cost 1,600 quota units each and a project gets 10,000 a day, so two channels sharing one
project would run out.

Use the Google account that owns (or manages) the channel for **the clip channel**.

## Part A: Google Cloud project (about 10 minutes)

1. Open https://console.cloud.google.com and sign in. Accept the terms if asked.
2. Top bar, project picker > **New project**. Name it `clipchannel`. Create, then select it.
3. Menu > **APIs & Services** > **Library**. Search and **Enable**: **YouTube Data API v3**.
4. Menu > **APIs & Services** > **OAuth consent screen** (Google may call it "Google Auth Platform"). Tap **Get started**:
   - App name `clipchannel`, support email: yours. Audience: **External**. Contact email: yours. Agree and Create.
5. **Data access** (or "Scopes") > **Add or remove scopes**. If the list does not show the scopes, paste these into
   "Manually add scopes", add to table, then Update and Save:
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube.force-ssl`
6. **Audience** > **Publish app** > Confirm, so the status reads **In production**.
   Why: while an app is in "Testing", Google expires the refresh token after 7 days and the channel silently
   stops posting. Publishing a personal app does not need Google's review; you only get an "unverified app"
   warning on your own login, which is fine because you are the only user.
7. **Credentials** (or "Clients") > **Create credentials** > **OAuth client ID** > type **Web application**.
   - Name: `playground`.
   - Under **Authorized redirect URIs** > Add URI: `https://developers.google.com/oauthplayground`
   - Create. Copy the **Client ID** and **Client secret**. These are `YT_CLIENT_ID` and `YT_CLIENT_SECRET`.

## Part B: get the refresh token with Google's OAuth Playground (about 5 minutes)

1. Open https://developers.google.com/oauthplayground (Desktop site).
2. Tap the **gear** icon (top right):
   - tick **Use your own OAuth credentials** and paste the Client ID and Client secret from Part A;
   - **Access type: Offline**; tick **Force prompt** (the consent screen option) so a refresh token is always returned.
   - Close the panel.
3. **Step 1**: in the box "Input your own scopes" paste these (one line, separated by spaces):
```
https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl
```
   Tap **Authorize APIs**.
4. Choose the Google account that owns the channel (for a brand channel, choose the brand account when asked).
   You will see "Google hasn't verified this app": tap **Advanced** > **Go to clipchannel (unsafe)**. It is your own
   app. Tick every permission box and Continue.
5. **Step 2**: tap **Exchange authorization code for tokens**. Copy the **Refresh token** (starts with `1//`).
   That is `YT_REFRESH_TOKEN`.

## Part C: put the three values into GitHub

Open `https://github.com/lebatieshameel23/clipchannel01/settings/secrets/actions/new` and add, one at a time:
`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` (name exactly like that, value pasted).
Also check Settings > Actions > General > Workflow permissions is **Read and write**.

Then Actions tab > pick the workflow > **Run workflow**.

## Good to know

- **Private until audited.** Videos uploaded by an unaudited API project are forced to **private**. They still
  upload and are scheduled; open YouTube Studio to make them public until you pass YouTube's API compliance audit
  (search "YouTube API Services audit and quota extension form"). Say you are uploading to your own channel.
- **If uploads stop with "invalid_grant"**, the token was revoked or expired: repeat Part B and replace
  `YT_REFRESH_TOKEN`. Changing the Google account password or removing the app in your Google account's
  security page causes this.
- **Never share these three values.** Anyone holding them can post to your channel. Do not paste them into chat,
  and never commit them to the repo (they belong only in GitHub secrets).
