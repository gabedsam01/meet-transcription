from app.main import build_parser


def test_parser_accepts_once_and_reprocess():
    args = build_parser().parse_args(["--once", "--reprocess", "file123"])

    assert args.once is True
    assert args.watch is False
    assert args.reprocess == "file123"


def test_parser_defaults_to_watch_when_no_mode_is_passed():
    args = build_parser().parse_args([])

    assert args.watch is True
    assert args.once is False
