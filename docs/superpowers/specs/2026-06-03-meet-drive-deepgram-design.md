# Design: Google Meet Drive Transcriber MVP

## Goal

Build a Python 3.11+ polling worker that runs as a single Docker container on a Linux VPS. The worker monitors a shared Google Drive input folder for MP4/video files, downloads each unprocessed file temporarily, sends the binary MP4 directly to Deepgram for transcription, uploads a readable `.txt` transcript to a shared Google Drive output folder, removes local temporary files, and records successful processing in a persistent JSON state file.

The MVP intentionally has no web UI, no database, no external queue, no Google Drive webhooks, and no multi-user orchestration. Team members will manually move or copy finished Google Meet recordings into the shared input folder.

## Operational Flow

1. A team member records a Google Meet meeting.
2. Google processes the recording and saves an MP4 in the organizer's Drive.
3. A team member moves or copies the MP4 into the shared input folder.
4. The worker polls the input folder every `POLL_INTERVAL_SECONDS` seconds.
5. The worker detects unprocessed MP4/video files.
6. For each eligible file, the worker validates that the Drive file is ready and downloadable.
7. The worker downloads the video into `TMP_DIR`.
8. The worker sends the MP4 bytes directly to Deepgram over HTTP.
9. The worker formats the transcription as readable text.
10. The worker uploads the `.txt` transcript to the shared output folder.
11. The worker deletes local temporary video and text files in a `finally` cleanup path when possible.
12. The worker records the source file ID in `STATE_FILE` only after transcript upload succeeds.

## Project Structure

The project will be created in the current directory, not inside an additional nested folder.

```txt
app/
├── __init__.py
├── main.py
├── processor.py
├── drive_client.py
├── deepgram_client.py
├── state.py
├── config.py
└── logger.py
data/
└── processed_files.json
tmp/
Dockerfile
docker-compose.yml
requirements.txt
.env.example
.gitignore
README.md
```

## Configuration

Configuration comes from environment variables loaded by Docker Compose via `.env`.

Required variables:

```env
DEEPGRAM_API_KEY=
GOOGLE_AUTH_MODE=service_account
GOOGLE_SERVICE_ACCOUNT_FILE=/app/secrets/service-account.json
SOURCE_DRIVE_FOLDER_ID=your_source_drive_folder_id
DESTINATION_DRIVE_FOLDER_ID=your_destination_drive_folder_id
POLL_INTERVAL_SECONDS=300
TMP_DIR=/app/tmp
STATE_FILE=/app/data/processed_files.json
DEEPGRAM_MODEL=nova-3
DEEPGRAM_LANGUAGE=pt-BR
DEEPGRAM_SMART_FORMAT=true
DEEPGRAM_PUNCTUATE=true
DEEPGRAM_DIARIZE=true
DEEPGRAM_UTTERANCES=true
```

`GOOGLE_AUTH_MODE` will support only `service_account` in the MVP. The config object should fail fast when required values are missing or invalid.

## Components

### `main.py`

Provides the CLI entrypoint:

```bash
python -m app.main --once
python -m app.main --watch
python -m app.main --once --reprocess <google_drive_file_id>
```

`--once` processes current pending files and exits. `--watch` loops forever and sleeps for `POLL_INTERVAL_SECONDS`. `--reprocess <google_drive_file_id>` forces one source file ID to be processed again by ignoring any existing state entry for that ID during the current run and replacing the state entry only after the new upload succeeds.

### `config.py`

Defines a `Settings` object and helpers to parse booleans, integers, paths, and required strings from environment variables. It also ensures `TMP_DIR` and the state file parent directory exist at runtime.

### `logger.py`

Configures clear console logs with timestamps and levels such as `[INFO]` and `[ERROR]`. Logs should be readable through `docker logs`.

### `drive_client.py`

Owns Google Drive API interaction using a Service Account:

- Authenticate using `GOOGLE_SERVICE_ACCOUNT_FILE`.
- List files in `SOURCE_DRIVE_FOLDER_ID`.
- Filter files whose MIME type starts with `video/`, especially `video/mp4`, or whose filename ends with `.mp4`.
- Sort by creation or modification time, oldest first, so files are processed predictably.
- Check that files are not trashed, are eligible videos, and have a positive size when Drive reports size. A file is considered ready if it passes these checks and can be downloaded through the Drive media API; transient download failures leave it unprocessed for the next polling cycle.
- Download a file to a local path using Drive API media download.
- Upload a `.txt` file to `DESTINATION_DRIVE_FOLDER_ID` with MIME type `text/plain`.

The app will not publish Drive files publicly and will not pass public Drive links to Deepgram.

### `deepgram_client.py`

Sends the local MP4 file directly to Deepgram via HTTP using the configured query parameters:

- `model=nova-3`
- `language=pt-BR`
- `smart_format=true`
- `punctuate=true`
- `diarize=true`
- `utterances=true`

It returns the parsed Deepgram JSON response. It should raise a clear exception on non-2xx responses or invalid JSON.

### `state.py`

Persists processed file metadata in `data/processed_files.json`:

```json
{
  "google_drive_file_id": {
    "name": "arquivo.mp4",
    "processed_at": "2026-06-03T10:45:00",
    "transcript_drive_file_id": "id_do_txt_no_drive"
  }
}
```

The state file is JSON because only one worker will run. Writes should be atomic enough for a single-process worker by writing to a temporary state file and replacing the original file.

### `processor.py`

Coordinates the end-to-end workflow:

- Load state.
- List eligible source videos.
- Skip files whose Drive ID already exists in state unless `--reprocess` targets that ID.
- Download source video to `TMP_DIR`.
- Transcribe with Deepgram.
- Format a readable transcript.
- Write a local `.txt` file.
- Upload the transcript to the destination Drive folder.
- Mark the source file processed only after upload succeeds.
- Delete local temp files in `finally`.

## Transcript Formatting

The output text should be human-readable and include metadata:

```txt
TRANSCRIÇÃO DA REUNIÃO

Arquivo original: nome-do-video.mp4
Data de processamento: 2026-06-03 10:45
ID Google Drive: abc123

==================================================

[00:00:01] Speaker 0:
Texto da fala...

[00:00:12] Speaker 1:
Texto da fala...

==================================================

Fim da transcrição.
```

If Deepgram returns utterances, the formatter will prefer utterances with timestamps and speaker labels. If utterances are missing, it will fall back to the best transcript text from `results.channels[0].alternatives[0].transcript`.

## File Naming

The transcript filename will be based on the original video name. Problematic characters will be sanitized to safe filename characters. The suffix will be `_Transcricao.txt`.

Example:

```txt
wrb-gcfd-bzv_2026-06-03_Transcricao.txt
```

If the original filename already includes `.mp4`, the extension will be removed before appending the transcript suffix.

## Error Handling

The worker must never mark a file as processed unless both transcription and upload succeed.

Expected behavior:

- If Drive listing fails, log the error and retry on the next polling cycle.
- If a single file fails, log the failure and continue with other pending files.
- If Deepgram returns an error, do not upload a transcript and do not mark the file processed.
- If Drive upload fails, do not mark the file processed.
- Temporary files should be removed in a `finally` block whenever possible.
- In `--watch`, errors should not terminate the process unless startup configuration or authentication fails.

## Docker

The Docker setup will use one service:

```yaml
services:
  meet-transcriber:
    build: .
    container_name: meet-drive-deepgram
    env_file:
      - .env
    volumes:
      - ./data:/app/data
      - ./tmp:/app/tmp
      - ./secrets:/app/secrets:ro
    restart: unless-stopped
```

The container default command will run watch mode. One-off runs can override the command with Docker Compose.

## Security

The following must not be committed:

- `.env`
- `service-account.json`
- `token.json`
- `tmp/`
- `secrets/`

The README will instruct the user to create a Google Cloud project, enable Google Drive API, create a Service Account, download `service-account.json`, place it in `./secrets/`, and share both Drive folders with the Service Account email.

## Testing And Verification

At minimum, implementation verification should include:

- Python import or module execution smoke test.
- Unit-level checks for filename sanitization and transcript formatting where practical.
- `docker compose config` or equivalent Compose validation.
- Build validation with `docker compose build` if Docker is available.

Live end-to-end verification requires valid `.env`, Service Account credentials, folder sharing, and Deepgram API key, so the README will document how to run it but automated local verification may be limited without secrets.

## Out Of Scope For MVP

- Web UI.
- Database.
- External queue.
- Google Drive push notifications or webhooks.
- Multi-user management.
- Automatic access to each team member's personal Drive.
- FFmpeg conversion.
- Public Drive links.
- Google Docs output.
- Email delivery.
- Notion integration.
- AI meeting summary or formal minutes.
