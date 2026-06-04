import pytest

from app.web.drive_links import extract_google_drive_folder_id

VALID_ID = "1A2b3C4d5E6f7G8h9I0jKlMnOpQ"


@pytest.mark.parametrize(
    "value",
    [
        f"https://drive.google.com/drive/folders/{VALID_ID}",
        f"https://drive.google.com/drive/folders/{VALID_ID}?usp=sharing",
        f"https://drive.google.com/drive/u/0/folders/{VALID_ID}",
        f"  {VALID_ID}  ",
    ],
)
def test_extracts_id_from_supported_forms(value):
    assert extract_google_drive_folder_id(value) == VALID_ID


@pytest.mark.parametrize(
    "value",
    ["", "   ", "https://drive.google.com/drive/folders/", "short", "https://example.com/x"],
)
def test_rejects_invalid(value):
    with pytest.raises(ValueError):
        extract_google_drive_folder_id(value)
