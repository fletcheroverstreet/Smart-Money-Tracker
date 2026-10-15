"""Build a drag-and-drop upload package for GitHub from the repo's committed files.

GitHub's web upload takes at most 100 files at a time, so the committed tree is
split into numbered folders of fewer files, each keeping the full relative
paths. Every exported file is checked byte for byte against the commit.

    python github_package.py <repo> <out_dir>
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

LIMIT = 90  # GitHub allows 100 per upload; leave room.

#: (folder label, path prefixes). First match wins; the last group takes the rest.
GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("optional-automatic-checks", (".github/",)),
    ("backend-code", ("backend/src/",)),
    ("backend-tests-and-settings", ("backend/",)),
    ("web-components", ("web/src/components/",)),
    ("web-pages-and-settings", ("web/",)),
    ("readme-docs-scripts", ("",)),
]
#: Upload order. The automatic checks go last, so they run once, on the whole project.
ORDER = ["readme-docs-scripts", "backend-code", "backend-tests-and-settings",
         "web-components", "web-pages-and-settings", "optional-automatic-checks"]

HOW_TO = """\
SMART MONEY TRACKER: PUTTING IT ON GITHUB BY DRAG AND DROP
==========================================================

This folder holds the whole project: {files} files, exactly as committed
(commit {commit}). GitHub's website accepts at most 100 files per upload, so
the project is split into {total} numbered folders. Upload them in order.

What was checked: no passwords, keys, or email addresses are in these files.
Your settings file (.env) and the collected data are not here; they stay on
this computer. CLAUDE.md does mention your Windows folder name
(C:\\Users\\fletc) and the path of your notes vault, which is a reason to
start with a PRIVATE repository.


STEP 1. Create the repository
-----------------------------
1. Sign in at github.com and open https://github.com/new
2. Repository name: smart-money-tracker
3. Choose "Private". (You can make it public later in Settings.)
4. Leave "Add a README" off, .gitignore "None", license "None". The project
   brings its own.
5. Click "Create repository".


STEP 2. Upload the folders, one at a time
-----------------------------------------
First folder only: on the new, empty repository page, click the link
"uploading an existing file".

For every folder after the first: on the repository page click
"Add file", then "Upload files".

Then, for each folder:
1. Open the folder in File Explorer.
2. Click inside it and press Ctrl+A to select everything in it.
3. Drag the selection onto the GitHub page.
4. Wait until GitHub has finished listing the files.
5. Click the green "Commit changes" button.

IMPORTANT: drag what is INSIDE each numbered folder, not the numbered folder
itself. Otherwise GitHub makes a folder called "upload-1-of-..." in your
repository.

The folders, in order:

{table}

Files whose names start with a dot (.gitignore, .env.example and so on) are
part of the project. Ctrl+A includes them.


STEP 3. The last folder is optional
-----------------------------------
{optional} holds one file,
.github/workflows/ci.yml. Uploading it switches on GitHub's automatic checks:
after every change GitHub runs the tests and builds two Docker images, which it
stores in your GitHub account (private while the repository is private). This
uses the free monthly allowance GitHub gives every account.

These checks have only ever run on this computer, never on GitHub, so the first
run there may show a red X that needs a small fix. That does not affect the
uploaded code. If you only want the project stored on GitHub, skip this folder;
you can add it any time later.


HOW TO TELL IT WORKED
---------------------
The repository's front page shows the folders backend, config, docs, infra,
scripts and web, the README with screenshots below them, and (if you did
step 3) .github.


THE OTHER WAY: ONE COMMAND, AND THE FULL HISTORY
------------------------------------------------
Drag and drop stores the files but not the {commits} saved steps of how the
project was built. To keep that history, create the empty repository as in
step 1, then run these in PowerShell (put your GitHub user name in the
second line):

    cd C:\\Users\\fletc\\Downloads\\finlake_1\\smart-money-tracker
    git remote add origin https://github.com/YOUR-NAME/smart-money-tracker.git
    git push -u origin main

A browser window asks you to sign in to GitHub the first time. Two things to
know: every saved step carries your name and your Gmail address, which anyone
who can see the repository can read; and this way always includes the
automatic checks from step 3. Use one way or the other, not both: a
repository already filled by drag and drop refuses the push.


WHAT ELSE IS IN THIS FOLDER
---------------------------
smart-money-tracker.zip is the same {files} files in one archive, as a
backup or to send to someone. Don't upload it to the repository: GitHub
doesn't unpack zip files.
"""


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def main(repo: Path, out: Path) -> int:
    commit = git(repo, "rev-parse", "--short", "HEAD").decode().strip()
    commits = git(repo, "rev-list", "--count", "HEAD").decode().strip()
    if git(repo, "status", "--porcelain").strip():
        print("The repo has uncommitted changes: commit first.")
        return 1
    # path -> blob id, straight from the commit (not the working folder).
    tree: dict[str, str] = {}
    for line in git(repo, "ls-tree", "-r", "-z", "HEAD").split(b"\x00"):
        if not line:
            continue
        meta, path = line.split(b"\t", 1)
        tree[path.decode()] = meta.split()[2].decode()

    grouped: dict[str, list[str]] = {label: [] for label, _ in GROUPS}
    for path in sorted(tree):
        for label, prefixes in GROUPS:
            if any(path.startswith(p) for p in prefixes):
                grouped[label].append(path)
                break

    batches: list[tuple[str, list[str]]] = []
    for label in ORDER:
        paths = grouped[label]
        count = -(-len(paths) // LIMIT)  # how many uploads this group needs
        size = -(-len(paths) // count) if count else LIMIT  # evenly sized
        parts = [paths[i:i + size] for i in range(0, len(paths), size)]
        for n, part in enumerate(parts, 1):
            batches.append((label if len(parts) == 1 else f"{label}-part{n}", part))

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    total = len(batches)
    summary: list[tuple[str, int]] = []
    zip_path = out / "smart-money-tracker.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for number, (label, paths) in enumerate(batches, 1):
            folder = out / f"upload-{number}-of-{total}-{label}"
            for path in paths:
                data = git(repo, "cat-file", "blob", tree[path])
                if blob_sha(data) != tree[path]:
                    raise SystemExit(f"{path}: content differs from the commit")
                target = folder / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                archive.writestr(f"smart-money-tracker/{path}", data)
            summary.append((folder.name, len(paths)))

    # Check what is on disk: every committed file exactly once, identical bytes.
    seen: dict[str, str] = {}
    for folder_name, _ in summary:
        root = out / folder_name
        for file in root.rglob("*"):
            if file.is_file():
                rel = file.relative_to(root).as_posix()
                if rel in seen:
                    raise SystemExit(f"{rel} is in two batches")
                seen[rel] = blob_sha(file.read_bytes())
    if seen != tree:
        raise SystemExit("The package doesn't match the commit")
    if max(count for _, count in summary) > 100:
        raise SystemExit("A batch is over GitHub's 100-file limit")

    table = "\n".join(f"    {name:<52} {count:>3} file{'s' if count != 1 else ''}"
                      for name, count in summary)
    (out / "HOW-TO-UPLOAD.txt").write_text(
        HOW_TO.format(files=len(seen), commit=commit, commits=commits, total=total, table=table,
                      optional=summary[-1][0]),
        encoding="utf-8", newline="\r\n")

    for name, count in summary:
        print(f"{count:4d}  {name}")
    print(f"{len(seen)} files, commit {commit}, zip {zip_path.stat().st_size / 1_048_576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
