# AI ERIK AUTOPILOT

A cloud-run YouTube Shorts pipeline controlled from an iPad browser.

## What it does
- Reads public trend RSS feeds for Germany.
- Uses Gemini to write an original short script and metadata.
- Creates a simple vertical video with original typography and narration.
- Runs a quality check before upload.
- Uploads automatically through the official YouTube Data API when credentials are configured.

## Required GitHub Actions secrets
Set these in **Settings → Secrets and variables → Actions → New repository secret**:

- `GEMINI_API_KEY` — Google AI Studio API key.
- `YOUTUBE_CLIENT_ID` — OAuth client ID for a Google Cloud project.
- `YOUTUBE_CLIENT_SECRET` — OAuth client secret.
- `YOUTUBE_REFRESH_TOKEN` — refresh token for the YouTube channel owner.

Never put these values in source files or send them in chat.

## Important YouTube setup
Enable **YouTube Data API v3** in Google Cloud and configure OAuth consent. The channel owner must authorize the application once to obtain a refresh token. New/unverified API projects can have upload restrictions (for example, uploads may be limited to private visibility until the project passes Google's audit). The workflow defaults to **private** until `YOUTUBE_PRIVACY_STATUS` is explicitly configured as `public`.

## Run
GitHub → Actions → **AI ERIK Autopilot** → Run workflow.

It also runs on a schedule (one run per day by default). Change the cron schedule in `.github/workflows/ai-erik-autopilot.yml`.

## Cost and reliability
GitHub Actions and Gemini may have quotas/limits. This is not guaranteed to run forever for free. The video is made from original generated text and narration; it does not download or re-upload other creators' videos. Review platform rules and copyright before enabling public uploads.
