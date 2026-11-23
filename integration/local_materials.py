"""Optional local transcripts and Seeking Alpha files. Nothing is downloaded or invented."""
from pathlib import Path

MANIFESTS = (
    ("management_manifest_path", "management_manifest.json"),
    ("seekingalpha_manifest_path", "seekingalpha_manifest.json"),
)


def discover_local_manifests(project_root, ticker):
    """Return config paths for files the user actually placed under imports/{ticker}/.

    Missing files stay None so Data records not_checked instead of a fake document.
    """
    root = Path(project_root)
    found = {}
    for key, name in MANIFESTS:
        path = root / "imports" / ticker / name
        found[key] = path.relative_to(root).as_posix() if path.is_file() else None
    return found
