from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    size: int | None
    created_time: str | None
    modified_time: str | None


def sanitize_filename(name: str) -> str:
    base = re.sub(r"(?i)\.mp4$", "", name.strip())
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("._-")
    return base or "transcricao"


def format_transcript(
    deepgram_response: dict[str, Any], original_name: str, drive_file_id: str
) -> str:
    processed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "TRANSCRIÇÃO DA REUNIÃO",
        "",
        f"Arquivo original: {original_name}",
        f"Data de processamento: {processed_at}",
        f"ID Google Drive: {drive_file_id}",
        "",
        "==================================================",
        "",
    ]

    utterances = deepgram_response.get("results", {}).get("utterances") or []
    if utterances:
        for utterance in utterances:
            start = _format_seconds(float(utterance.get("start", 0)))
            speaker = utterance.get("speaker", "unknown")
            transcript = (utterance.get("transcript") or "").strip()
            if not transcript:
                continue
            lines.extend([f"[{start}] Speaker {speaker}:", transcript, ""])
    else:
        transcript = _extract_plain_transcript(deepgram_response)
        lines.extend([transcript or "Transcrição não disponível.", ""])

    lines.extend(["==================================================", "", "Fim da transcrição."])
    return "\n".join(lines) + "\n"


class FileProcessor:
    def __init__(self, drive_client, deepgram_client, state, tmp_dir: str | Path):
        self.drive_client = drive_client
        self.deepgram_client = deepgram_client
        self.state = state
        self.tmp_dir = Path(tmp_dir)

    def process_pending(self, reprocess_file_id: str | None = None) -> int:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        processed_count = 0

        LOGGER.info("Buscando vídeos na pasta de origem...")
        for file in self.drive_client.list_video_files():
            if reprocess_file_id and file.id != reprocess_file_id:
                continue
            if not reprocess_file_id and self.state.is_processed(file.id):
                LOGGER.info("Arquivo já processado, ignorando: %s", file.name)
                continue
            if reprocess_file_id != file.id and self.state.is_processed(file.id):
                LOGGER.info("Arquivo já processado, ignorando: %s", file.name)
                continue

            try:
                LOGGER.info("Novo arquivo encontrado: %s", file.name)
                self._process_file(file)
                processed_count += 1
            except Exception as exc:  # noqa: BLE001 - keep watch mode resilient per spec.
                LOGGER.error("Falha ao transcrever arquivo %s", file.name)
                LOGGER.error("Motivo: %s", exc)

        return processed_count

    def _process_file(self, file: DriveFile) -> None:
        safe_base = sanitize_filename(file.name)
        video_path = self.tmp_dir / f"{file.id}_{safe_base}.mp4"
        transcript_filename = f"{safe_base}_Transcricao.txt"
        transcript_path = self.tmp_dir / f"{file.id}_{transcript_filename}"

        try:
            LOGGER.info("Baixando vídeo...")
            self.drive_client.download_file(file, video_path)

            LOGGER.info("Enviando para Deepgram...")
            deepgram_response = self.deepgram_client.transcribe(video_path)
            LOGGER.info("Transcrição recebida.")

            transcript_text = format_transcript(deepgram_response, file.name, file.id)
            transcript_path.write_text(transcript_text, encoding="utf-8")

            LOGGER.info("Enviando TXT para Google Drive...")
            transcript_drive_file_id = self.drive_client.upload_text_file(
                transcript_path, transcript_filename
            )
            LOGGER.info("Upload concluído.")

            self.state.mark_processed(file.id, file.name, transcript_drive_file_id)
            LOGGER.info("Arquivo marcado como processado.")
        finally:
            LOGGER.info("Limpando arquivos temporários...")
            _unlink_if_exists(video_path)
            _unlink_if_exists(transcript_path)


def _format_seconds(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    remaining_seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"


def _extract_plain_transcript(deepgram_response: dict[str, Any]) -> str:
    try:
        return (
            deepgram_response["results"]["channels"][0]["alternatives"][0][
                "transcript"
            ]
            or ""
        ).strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        LOGGER.warning("Não foi possível remover arquivo temporário %s: %s", path, exc)
