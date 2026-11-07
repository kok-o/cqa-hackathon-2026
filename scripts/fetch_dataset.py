"""Fetch the organizer dataset to an ignored local path."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

from dotenv import load_dotenv
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.cqa.models import DatasetExport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="DEV", help="Only use a group announced by organizers")
    args = parser.parse_args()
    if not args.group or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in args.group):
        parser.error("Use a group identifier containing letters, numbers, hyphen or underscore")

    load_dotenv(ROOT / ".env")
    source = os.environ.get("CQA_DEV_URL", "")
    parts = urlsplit(source)
    if parts.scheme != "https" or not parts.netloc:
        parser.exit(1, "Set CQA_DEV_URL to the signed HTTPS link from the original organizer PDF.\n")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "group_id"]
    query.append(("group_id", args.group))
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    try:
        with urlopen(url, timeout=45) as response:
            raw = response.read()
        payload = json.loads(raw)
        export = DatasetExport.model_validate(payload)
        if export.data.dataset_group_id != args.group:
            parser.exit(1, "The returned group does not match the requested group.\n")
    except HTTPError as error:
        parser.exit(1, f"Dataset endpoint returned HTTP {error.code}.\n")
    except URLError:
        parser.exit(1, "Could not connect to the dataset endpoint. Check connectivity and the organizer link.\n")
    except (ValueError, ValidationError):
        parser.exit(1, "The response did not match the documented dataset contract. Inspect the source locally.\n")

    output_dir = Path(os.environ.get("CQA_DATA_DIR", "data/raw"))
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{args.group}.json"
    with tempfile.NamedTemporaryFile(dir=output_dir, suffix=".tmp", delete=False) as temporary:
        temporary.write(raw)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"Saved {export.data.total_records} records for {args.group} to {output}")


if __name__ == "__main__":
    main()
