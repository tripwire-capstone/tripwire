"""`score hash`: sha256 over the scorer package, so a log can show the scorer didn't change."""

import hashlib

from . import PACKAGE_DIR


def package_files(root=PACKAGE_DIR):
    return sorted((path for path in root.rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"),
                  key=lambda path: path.relative_to(root).as_posix())


def scorer_hash(root=PACKAGE_DIR):
    """Each file's relative path and bytes, in sorted path order, NUL-separated."""
    digest = hashlib.sha256()
    for path in package_files(root):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()
