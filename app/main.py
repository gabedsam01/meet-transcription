from __future__ import annotations

import argparse
import logging
import time

from app.config import Settings
from app.deepgram_client import DeepgramClient
from app.drive_client import DriveClient
from app.logger import setup_logging
from app.processor import FileProcessor
from app.state import ProcessedState


class ModeParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args, namespace)
        if parsed.once and parsed.watch:
            self.error("Use only one mode: --once or --watch")
        if not parsed.once and not parsed.watch:
            parsed.watch = True
        if parsed.once:
            parsed.watch = False
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = ModeParser(description="Google Meet Drive to Deepgram transcriber")
    parser.add_argument("--once", action="store_true", help="Process pending files once and exit")
    parser.add_argument("--watch", action="store_true", help="Continuously poll for new files")
    parser.add_argument(
        "--reprocess",
        metavar="GOOGLE_DRIVE_FILE_ID",
        help="Force one Google Drive file ID to be processed again",
    )
    return parser


def main() -> int:
    setup_logging()
    args = build_parser().parse_args()
    settings = Settings.from_env()
    processor = _build_processor(settings)

    if args.once:
        processor.process_pending(reprocess_file_id=args.reprocess)
        return 0

    reprocess_file_id = args.reprocess
    while True:
        try:
            processor.process_pending(reprocess_file_id=reprocess_file_id)
        except Exception as exc:  # noqa: BLE001 - keep worker alive in watch mode.
            logging.exception("Erro inesperado no ciclo de monitoramento: %s", exc)
        reprocess_file_id = None
        time.sleep(settings.poll_interval_seconds)


def _build_processor(settings: Settings) -> FileProcessor:
    return FileProcessor(
        drive_client=DriveClient(settings),
        deepgram_client=DeepgramClient.from_settings(settings),
        state=ProcessedState(settings.state_file),
        tmp_dir=settings.tmp_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
