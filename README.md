# Meet Transcription

Python Docker worker that watches a Google Drive folder for Google Meet recordings, sends MP4 files to Deepgram, and uploads plain text transcripts back to Google Drive.

## Features

- Google Drive polling worker
- OAuth authentication for personal Google accounts
- Service Account authentication for compatible Google Workspace setups
- MP4 download from Drive
- Direct MP4 transcription with Deepgram
- TXT transcript generation
- Upload transcript to Google Drive
- Persistent processed-file state
- Docker Compose support
- No FFmpeg required
- No database required
- No web UI required

## How It Works

1. Record a Google Meet meeting.
2. Wait for Google to process the MP4.
3. Move or copy the MP4 to a shared Google Drive input folder.
4. The worker detects the video.
5. The worker downloads the MP4 temporarily.
6. The worker sends it to Deepgram.
7. A readable TXT transcript is generated.
8. The TXT is uploaded to a Google Drive output folder.
9. Temporary local files are removed.
10. The file is marked as processed.

## Requirements

- Docker and Docker Compose
- Deepgram API key
- Google Cloud project
- Google Drive API enabled
- Google OAuth Client ID for personal Gmail/Google One accounts
- Google Service Account JSON key for compatible Workspace setups
- Two Google Drive folders accessible by the chosen Google identity

## Quick Start

```bash
git clone https://github.com/gabedsam01/meet-transcription.git
cd meet-transcription

cp .env.example .env
mkdir -p secrets data tmp
```

For personal Gmail or Google One accounts, OAuth is recommended. Place your OAuth client JSON file at:

```bash
secrets/oauth-client.json
```

Generate `token.json` locally:

```bash
python -m pip install -r requirements.txt
python scripts/generate_google_oauth_token.py \
  --client-secrets secrets/oauth-client.json \
  --token-file secrets/token.json
```

Edit `.env`:

```env
DEEPGRAM_API_KEY=your_deepgram_api_key
GOOGLE_AUTH_MODE=oauth
GOOGLE_OAUTH_CLIENT_SECRETS_FILE=/app/secrets/oauth-client.json
GOOGLE_OAUTH_TOKEN_FILE=/app/secrets/token.json
GOOGLE_SERVICE_ACCOUNT_FILE=/app/secrets/service-account.json
SOURCE_DRIVE_FOLDER_ID=your_source_drive_folder_id
DESTINATION_DRIVE_FOLDER_ID=your_destination_drive_folder_id
POLL_INTERVAL_SECONDS=300
TMP_DIR=/app/tmp
STATE_FILE=/app/data/processed_files.json
MAX_PROCESSING_ATTEMPTS=2
FAILED_RETRY_AFTER_SECONDS=86400
DEEPGRAM_MODEL=nova-3
DEEPGRAM_LANGUAGE=pt-BR
DEEPGRAM_SMART_FORMAT=true
DEEPGRAM_PUNCTUATE=true
DEEPGRAM_DIARIZE=true
DEEPGRAM_UTTERANCES=true
```

## Run Once

```bash
docker compose build
docker compose run --rm meet-transcriber python -m app.main --once
```

## Run Continuously

```bash
docker compose up -d
docker logs -f meet-drive-deepgram
```

## Reprocess A File

```bash
docker compose run --rm meet-transcriber python -m app.main --once --reprocess GOOGLE_DRIVE_FILE_ID
```

## Google Drive Authentication

### Recommended: OAuth For Personal Google Accounts

Use OAuth when the destination folder is in a personal Google Drive account. Service Accounts can read folders shared with them, but uploads to a personal `My Drive` can fail with:

```txt
Service Accounts do not have storage quota. Service accounts can't own files.
```

OAuth uploads the transcript as the human Google user and uses that user's Drive quota.

### Create OAuth Credentials

1. Create or open a Google Cloud project.
2. Enable Google Drive API.
3. Go to `APIs & Services` > `Credentials`.
4. Click `Create Credentials` > `OAuth client ID`.
5. Choose `Desktop app`.
6. Download the JSON file.
7. Save it locally as `secrets/oauth-client.json`.
8. Generate the token:

```bash
python scripts/generate_google_oauth_token.py \
  --client-secrets secrets/oauth-client.json \
  --token-file secrets/token.json
```

The script opens a local browser, asks for Google consent, and writes `secrets/token.json`.

### Optional: Service Account

Service Account mode can still work for Google Workspace setups, especially with Shared Drives or folders where the Service Account is allowed to create files without personal Drive ownership problems.

Use:

```env
GOOGLE_AUTH_MODE=service_account
GOOGLE_SERVICE_ACCOUNT_FILE=/app/secrets/service-account.json
```

Then:

1. Create a Google Cloud project.
2. Enable Google Drive API.
3. Create a Service Account.
4. Create and download a JSON key.
5. Save it as `secrets/service-account.json`.
6. Share your input and output Drive folders with the Service Account email.

## Dokploy Setup

For OAuth deployments, create two file mounts:

```txt
/app/secrets/oauth-client.json
/app/secrets/token.json
```

Use these environment variables:

```env
GOOGLE_AUTH_MODE=oauth
GOOGLE_OAUTH_CLIENT_SECRETS_FILE=/app/secrets/oauth-client.json
GOOGLE_OAUTH_TOKEN_FILE=/app/secrets/token.json
```

Keep the container volume mount for `/app/data` persistent so `processed_files.json` preserves successful processing and failure-attempt state.

## Security

Never commit:

```txt
.env
service-account.json
oauth-client.json
token.json
tmp/
data/processed_files.json
```

The app does not make Drive files public. It downloads files through the Google Drive API and sends the MP4 binary directly to Deepgram.

## Privacy Notice

Make sure all meeting participants know that the meeting is being recorded and transcribed. You are responsible for complying with privacy laws and internal policies.

## Development

```bash
python -m pip install -r requirements.txt
python -m pytest -v
python -m compileall app
docker compose config
```

`docker compose config` requires a local `.env` file. Create it first with `cp .env.example .env`.

## Roadmap

- Google Docs output
- AI summary generation
- Meeting minutes
- Email delivery
- Webhook mode
- Queue support
- Multi-user dashboard

## License

MIT
