import fnmatch
from pathlib import Path


IGNORED_DIR_NAMES = {".git", ".terraform"}
IGNORED_FILE_PATTERNS = (
    "*.tfstate",
    "*.tfstate.backup",
    ".terraform.lock.hcl",
)


def find_tf_files(root: Path) -> list[Path]:
    """Find Terraform files while applying the SPEC ignore list."""
    root = Path(root)
    results: list[Path] = []

    for path in sorted(root.rglob("*.tf")):
        relative_parts = path.relative_to(root).parts

        if any(
            part in IGNORED_DIR_NAMES
            for part in relative_parts[:-1]
        ):
            continue

        if any(
            fnmatch.fnmatch(path.name, pattern)
            for pattern in IGNORED_FILE_PATTERNS
        ):
            continue

        results.append(path)

    return results