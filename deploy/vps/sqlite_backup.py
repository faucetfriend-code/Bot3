"""SQLite backup and explicit offline restore; no application imports."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile


def backup(source: Path, destination: Path) -> None:
    """Create a consistent SQLite snapshot without replacing an existing file."""
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".bot3-backup-", dir=destination.parent)
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        with closing(
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        ) as incoming:
            with closing(sqlite3.connect(temporary)) as outgoing:
                incoming.backup(outgoing)
                if outgoing.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise ValueError("SQLite integrity check failed")
        # Hard-link publication is atomic and refuses to replace an existing file.
        os.link(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def restore(source: Path, destination: Path, replace: bool = False) -> None:
    """Restore while the service is stopped; preserve the previous database."""
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination:
        raise ValueError("Source and destination must differ")
    if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise ValueError("Database has WAL files; stop all writers before restore")
    if destination.exists() and not replace:
        raise FileExistsError("Use --replace to preserve and replace the old database")
    staged = destination.with_name(destination.name + ".restore-pending")
    backup(source, staged)
    try:
        if destination.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup(
                destination,
                destination.with_name(destination.name + ".before-" + timestamp),
            )
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def main() -> None:
    """Parse the backup or offline restore operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.operation == "backup":
        backup(args.source, args.destination)
    else:
        restore(args.source, args.destination, args.replace)
    print(f"{args.operation} completed: {args.destination}")


if __name__ == "__main__":
    main()
