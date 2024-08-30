#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "appdirs",
#   "click",
#   "gitpython",
#   "packaging",
# ]
# ///
"""Changelog generator based on dependency changes.

This script generates a changelog based on the upgraded dependencies tracked via a
version-controlled Python requirements lock file (`Pipfile.lock`, `requirements.txt`).

The script uses `git` to determine the changes made to the lock file in the current
commit. It then inspects the updated dependencies and generates a changelog based on
their commit history between tagged versions.

Warning: this script makes many assumptions about tagging convention, commit messages,
etc. It is probably fit for Invenio packages, but not necessarily for other projects.
"""

from packaging.utils import canonicalize_name
from packaging.version import Version
from pathlib import Path
import appdirs
from git import Repo, InvalidGitRepositoryError, Tag
from urllib.parse import urlparse
import json
import click
import re
import textwrap


CACHE_DIR = Path(appdirs.user_cache_dir("slint.changelog.py"))
GIT_REPOS_DIR = CACHE_DIR / "git_repos"


def find_repo(lockfile: Path, depth=2) -> Repo | None:
    # Go up the chain until we find a git repository
    parent = lockfile.parent.absolute()
    for _ in range(depth):
        try:
            return Repo(parent.absolute())
        except InvalidGitRepositoryError:
            parent = parent.parent


def deps_from_lockfile(lockfile: Path, data: str) -> dict[str, Version]:
    deps = {}
    if lockfile.match("Pipfile.lock"):
        for package, info in json.loads(data)["default"].items():
            if "version" in info:
                deps[package] = info["version"].replace("==", "")
    elif lockfile.match("requirements*.txt"):
        lines = data.splitlines()
        for line in lines:
            if line.startswith("#"):
                continue
            package, version = line.split("==")
            deps[package] = version

    return {canonicalize_name(k): Version(v) for k, v in deps.items()}


def diff_deps(
    repo: Repo,
    lockfile: Path,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, tuple[Version, Version]]:
    """Diff dependencies between lockfiles."""
    since_commit = repo.commit(since or "HEAD")
    prev_deps_data = (since_commit.tree / str(lockfile)).data_stream.read().decode()
    prev_deps = deps_from_lockfile(lockfile, prev_deps_data)

    if until:
        cur_deps_data = (
            (repo.commit(until).tree / str(lockfile)).data_stream.read().decode()
        )
    else:
        cur_deps_data = lockfile.read_text()
    cur_deps = deps_from_lockfile(lockfile, cur_deps_data)

    changed_deps = {}
    for package, cur_version in cur_deps.items():
        prev_version = prev_deps.get(package)
        if prev_version != cur_version:
            changed_deps[package] = (prev_version, cur_version)

    return changed_deps


def repo_tag(repo: Repo, version: Version, fetch: bool = True) -> Tag | None:
    """Get the version of a tag in the repository."""
    repo_tags = repo.tags
    for tag in (str(version), f"v{version}"):
        if tag in repo_tags:
            return repo_tags[tag]

    # Do a reverse search without the "v" prefix
    for t in repo_tags:
        if t.name.lstrip("v") == str(version):
            return t
    if fetch:
        click.secho(f"Fetching {repo}...", fg="yellow", err=True)
        for remote in repo.remotes:
            remote.fetch("+refs/heads/*:refs/heads/*", filter="blob:none")
        return repo_tag(repo, version, fetch=False)


def generate_changelog(
    package: str,
    prev_ver: Version | None,
    cur_ver: Version,
    fetch: bool = True,
) -> tuple[str, list[str]]:
    res = []
    repo = get_package_repo(package)
    repo_url = list(repo.remote("origin").urls)[0]

    if prev_ver is None:
        prev_tag = ""
    else:
        prev_tag = repo_tag(repo, prev_ver, fetch=fetch)
        if not prev_tag:
            raise ValueError(f"Tag for {prev_ver} not found in {repo_url}.")

    cur_tag = repo_tag(repo, cur_ver, fetch=fetch)
    if not cur_tag:
        raise ValueError(f"Tag for {cur_ver} not found in {repo_url}.")

    if not prev_tag:
        commit_range = f"{cur_tag}"
    else:
        commit_range = f"{prev_tag}...{cur_tag}"
    for c in repo.iter_commits(commit_range):
        res.append(c.message.strip())
    return repo_url, res


def get_package_repo(package: str) -> Repo:
    """Clone the dependency repository."""
    # Sometimes Python deps are available both using underscores ("_"), but their
    # canonical name needs dashes ("_").
    package = package.replace("_", "-")
    if not CACHE_DIR.exists():
        CACHE_DIR.mkdir(parents=True)
    if package.startswith("git+"):
        repo_url = package[4:]
    elif package.startswith("https://"):
        repo_url = package
    elif package.startswith("invenio-"):
        repo_url = f"https://github.com/inveniosoftware/{package}"

    repo_dir = GIT_REPOS_DIR / f"{package}.git"
    if not repo_dir.exists():
        repo_dir.mkdir(parents=True)
        repo = Repo.clone_from(
            repo_url,
            repo_dir,
            origin="origin",
            bare=True,
            filter="blob:none",
        )
    else:
        repo = Repo(repo_dir)
    return repo


@click.command()
@click.argument(
    "lockfile",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
# TODO: See if could support shell completion for "commit-ish" arguments.
@click.option("--since", default=None, help="The tag or commit to start from.")
@click.option("--until", default=None, help="The tag or commit to end at.")
@click.option(
    "--package-filter",
    default=None,
    help="A regular expression to filter the changelog entries.",
)
@click.option(
    "--message-filter",
    default=r"(tests?|chore)\:",
    help="A regular expression to filter commit message entries.",
)
@click.option("--lockfile", default=None, help="The file to write the changelog to.")
@click.option(
    "--output",
    type=click.File("w"),
    default="-",
    help="The file to write the changelog to.",
)
@click.option("--cache-dir", is_flag=True, help="Print the cache directory.")
def main_cli(
    lockfile,
    since,
    until,
    package_filter,
    message_filter,
    output,
    cache_dir,
):
    """Run the changelog generator."""
    if cache_dir:
        click.echo(CACHE_DIR)
        return

    lockfile = lockfile or next(
        (Path(p) for p in ("Pipfile.lock", "requirements.txt") if Path(p).exists()),
        None,
    )
    if not lockfile:
        raise click.UsageError("No lock file found (Pipfile.lock or requirements.txt).")

    if not (repo := find_repo(lockfile)):
        raise click.ClickException("Could not find git repository of lockfile.")

    changed_deps = diff_deps(repo, lockfile, since, until)
    message_filter = message_filter and re.compile(message_filter)
    package_filter = package_filter and re.compile(package_filter)
    issue_ref_regex = re.compile(r"(\(| )(#\d+)")
    for package, (prev_ver, cur_ver) in changed_deps.items():
        if package_filter and not package_filter.search(package):
            continue

        try:
            repo_url, changes = generate_changelog(package, prev_ver, cur_ver)
            repo_name = urlparse(repo_url).path[1:].removesuffix(".git")

            if message_filter:
                changes = [c for c in changes if not message_filter.search(c)]

            # Rewrite "closes #123" to "closes {repo_full_name}#123"
            changes = [issue_ref_regex.sub(rf"\1{repo_name}\2", c) for c in changes]

            bump_icon = ""
            if prev_ver is None:
                bump_icon = "✨"
            elif prev_ver.major < cur_ver.major:
                bump_icon = "⚠️"
            elif prev_ver.minor < cur_ver.minor:
                bump_icon = "🌈"
            elif prev_ver.micro < cur_ver.micro:
                bump_icon = "🐛"
            click.secho(
                f"\n📁 {package} ({prev_ver} -> {cur_ver} {bump_icon})\n",
                underline=True,
                file=output,
            )
            changelist = textwrap.indent("\n".join(changes), "    ")
            changelist = "\n".join(
                [l for l in changelist.splitlines() if "co-authored" not in l.lower()]
            )
            click.echo(changelist)
        except Exception as e:
            click.secho(f"Error generating changelog for {package}: {e}", err=True)


if __name__ == "__main__":
    main_cli()
