"""Download the flickr30k-lance dataset from Hugging Face into data/raw/.

Usage:
    python scripts/download_dataset.py
"""
import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

REPO_ID = "lance-format/flickr30k-lance"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dest",
        default=str(Path(__file__).resolve().parent.parent / "data" / "raw"),
        help="Directory to download the dataset into",
    )
    args = parser.parse_args()

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    path = snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        local_dir=str(dest),
    )
    print(f"Dataset downloaded to: {path}")


if __name__ == "__main__":
    main()
