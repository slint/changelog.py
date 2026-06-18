#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "appdirs",
#   "click",
#   "gitpython",
#   "packaging",
# ]
# ///
"""Changelog generator based on dependency changes.

This script generates a changelog based on the upgraded dependencies tracked via a
version-controlled Python requirements lock file (`Pipfile.lock`, `requirements.txt`, or `uv.lock`).

The script uses `git` to determine the changes made to the lock file in the current
commit. It then inspects the updated dependencies and generates a changelog based on
their commit history between tagged versions.

Warning: this script makes many assumptions about tagging convention, commit messages,
etc. It is probably fit for Invenio packages, but not necessarily for other projects.
"""

import json
import re
import textwrap
from pathlib import Path
from urllib.parse import urlparse

import appdirs
import click
import tomllib
from git import InvalidGitRepositoryError, Repo, Tag
from packaging.utils import canonicalize_name
from packaging.version import Version

CACHE_DIR = Path(appdirs.user_cache_dir("slint.changelog.py"))
GIT_REPOS_DIR = CACHE_DIR / "git_repos"
LOCKFILES = ("uv.lock", "Pipfile.lock", "requirements.txt")


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
    if lockfile.name == "Pipfile.lock":
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
    elif lockfile.name == "uv.lock":
        lock_data = tomllib.loads(data)
        # Parse packages from uv.lock format
        for package_info in lock_data.get("package", []):
            name = package_info.get("name")
            version = package_info.get("version")
            if name and version:
                deps[name] = version

    return {canonicalize_name(k): Version(v) for k, v in deps.items()}


def is_major_bump(prev_ver: Version | None, cur_ver: Version) -> bool:
    """Check if a version change is a major bump (breaking change)."""
    if prev_ver is None:
        return False
    return prev_ver.major < cur_ver.major


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


def format_changelist(changes: list[str], message_filter: re.Pattern | None) -> str:
    """Format a list of changes into a nicely formatted changelist string."""
    if message_filter:
        changes = [c for c in changes if not message_filter.search(c)]
    
    changelist = textwrap.indent("\n".join(changes), "    ")
    changelist = "\n".join(
        [line for line in changelist.splitlines() if "co-authored" not in line.lower()]
    )
    return changelist


def get_bump_icon(prev_ver: Version | None, cur_ver: Version) -> str:
    """Get the appropriate emoji icon for a version bump."""
    if prev_ver is None:
        return " ✨"
    elif prev_ver.major < cur_ver.major:
        return " ⚠️"
    elif prev_ver.minor < cur_ver.minor:
        return " 🌈"
    elif prev_ver.micro < cur_ver.micro:
        return " 🐛"
    elif (
        prev_ver.pre is not None
        and cur_ver.pre is not None
        and prev_ver.pre < cur_ver.pre
    ):
        return " 🚀"
    elif (
        prev_ver.dev is not None
        and cur_ver.dev is not None
        and prev_ver.dev < cur_ver.dev
    ):
        return " 🚀"
    return ""


def get_unreleased_deps(
    lockfile: Path, package_filter: re.Pattern | None, fetch: bool = False
) -> dict[str, list[str]]:
    """Get commits for unreleased dependencies."""
    cur_deps_data = lockfile.read_text()
    cur_deps = deps_from_lockfile(lockfile, cur_deps_data)
    if package_filter:
        cur_deps = {k: v for k, v in cur_deps.items() if package_filter.search(k)}

    res = {}

    with click.progressbar(
        cur_deps.items(),
        label="Fetching commits...",
        item_show_func=lambda i: i and i[0],
    ) as bar:
        for package, version in bar:
            repo = get_package_repo(package)
            # Fetch the latest heads
            if fetch:
                for remote in repo.remotes:
                    remote.fetch("+refs/heads/*:refs/heads/*", filter="blob:none")
            cur_tag = repo_tag(repo, version, fetch=False)  # no need to fetch again
            if not cur_tag:
                raise ValueError(f"Tag for {version} not found in {repo}.")

            for c in repo.iter_commits(f"{cur_tag}..HEAD"):
                res.setdefault(package, [])
                res[package].append(c.message.strip())
    return res


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
    try:
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
    finally:
        # Release the repo's file handles; leaking these across many packages
        # exhausts the open-file limit (Errno 24).
        repo.close()
    return repo_url, res


def run_unreleased_mode(
    lockfile: Path,
    package_filter: re.Pattern | None,
    message_filter: re.Pattern | None,
    fetch: bool,
    output
) -> None:
    """Run the changelog generator in unreleased mode."""
    unreleased_deps = get_unreleased_deps(
        lockfile, package_filter, fetch=fetch
    )
    
    for package, changes in unreleased_deps.items():
        changelist = format_changelist(changes, message_filter)
        if not changelist.strip():
            continue

        click.secho(f"\n📁 {package} (unreleased)\n", underline=True, file=output)
        click.echo(changelist, file=output)


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


def run_normal_mode(
    repo: Repo,
    lockfile: Path,
    since: str | None,
    until: str | None,
    package_filter: re.Pattern | None,
    message_filter: re.Pattern | None,
    show_major_bumps: bool,
    output
) -> None:
    """Run the changelog generator in normal mode."""
    changed_deps = diff_deps(repo, lockfile, since, until)
    issue_ref_regex = re.compile(r"(\(| )(#\d+)")
    
    # Separate packages into filtered and major bumps
    filtered_packages = []
    major_bump_packages = []

    for package, (prev_ver, cur_ver) in changed_deps.items():
        if package_filter and package_filter.search(package):
            # Package matches filter - show full changelog
            filtered_packages.append((package, prev_ver, cur_ver))
        elif show_major_bumps and is_major_bump(prev_ver, cur_ver):
            # Package doesn't match filter but has major bump - show only bump info
            major_bump_packages.append((package, prev_ver, cur_ver))
        elif not package_filter:
            # No filter specified - show all packages with changelog
            filtered_packages.append((package, prev_ver, cur_ver))

    # Process filtered packages with full changelog
    for package, prev_ver, cur_ver in filtered_packages:
        try:
            repo_url, changes = generate_changelog(package, prev_ver, cur_ver)
            repo_name = urlparse(repo_url).path[1:].removesuffix(".git")

            # Rewrite "closes #123" to "closes {repo_full_name}#123"
            changes = [issue_ref_regex.sub(rf"\1{repo_name}\2", c) for c in changes]

            bump_icon = get_bump_icon(prev_ver, cur_ver)
            click.secho(
                f"\n📁 {package} ({prev_ver} -> {cur_ver}{bump_icon})\n",
                underline=True,
                file=output,
            )
            
            changelist = format_changelist(changes, message_filter)
            click.echo(changelist, file=output)
        except Exception as e:
            click.secho(f"Error generating changelog for {package}: {e}", err=True)

    # Process major bump packages (show only bump info, no changelog)
    if major_bump_packages:
        click.secho(
            "\n🚨 Major version bumps (potentially breaking changes):",
            bold=True,
            file=output,
        )
        for package, prev_ver, cur_ver in major_bump_packages:
            click.secho(
                f"📁 {package} ({prev_ver} -> {cur_ver} ⚠️)",
                file=output,
            )


def bump_type(prev_ver: Version | None, cur_ver: Version) -> str:
    """Classify a version change (mirrors the text-mode bump icons)."""
    if prev_ver is None:
        return "new"
    if prev_ver.major < cur_ver.major:
        return "major"
    if prev_ver.minor < cur_ver.minor:
        return "minor"
    if prev_ver.micro < cur_ver.micro:
        return "patch"
    if prev_ver.pre is not None and cur_ver.pre is not None and prev_ver.pre < cur_ver.pre:
        return "pre"
    if prev_ver.dev is not None and cur_ver.dev is not None and prev_ver.dev < cur_ver.dev:
        return "dev"
    return "other"


def run_json_mode(
    repo: Repo,
    lockfile: Path,
    since: str | None,
    until: str | None,
    package_filter: re.Pattern | None,
    message_filter: re.Pattern | None,
    show_major_bumps: bool,
    detect: bool,
    fetch: bool,
    output,
) -> None:
    """Emit changed dependencies as JSON, mirroring the text output.

    Filtered packages carry their full changelog (and Alembic/mapping flags under
    `--detect-migrations`); with `--show-major-bumps`, major bumps outside the
    filter are included as version-only entries.
    """
    changed_deps = diff_deps(repo, lockfile, since, until)
    issue_ref_regex = re.compile(r"(\(| )(#\d+)")
    packages = []
    for package, (prev_ver, cur_ver) in sorted(changed_deps.items()):
        matched = package_filter is None or bool(package_filter.search(package))
        major = is_major_bump(prev_ver, cur_ver)
        if not matched and not (show_major_bumps and major):
            continue
        entry = {
            "name": package,
            "prev": str(prev_ver) if prev_ver is not None else None,
            "cur": str(cur_ver),
            "bump": bump_type(prev_ver, cur_ver),
            "matched_filter": matched,
        }
        if matched:
            pkg_repo = None
            try:
                pkg_repo = get_package_repo(package)
                repo_url = list(pkg_repo.remote("origin").urls)[0]
                entry["repo"] = repo_url
                repo_name = urlparse(repo_url).path[1:].removesuffix(".git")

                prev_tag = (
                    repo_tag(pkg_repo, prev_ver, fetch=fetch)
                    if prev_ver is not None
                    else None
                )
                cur_tag = repo_tag(pkg_repo, cur_ver, fetch=fetch)
                if prev_tag is not None:
                    entry["prev_tag"] = prev_tag.name
                if cur_tag is not None:
                    entry["cur_tag"] = cur_tag.name

                if cur_tag is not None:
                    commit_range = (
                        cur_tag.name
                        if prev_tag is None
                        else f"{prev_tag.name}...{cur_tag.name}"
                    )
                    changelog = []
                    for c in pkg_repo.iter_commits(commit_range):
                        msg = c.message.strip()
                        if message_filter and message_filter.search(msg):
                            continue
                        msg = issue_ref_regex.sub(rf"\1{repo_name}\2", msg)
                        msg = "\n".join(
                            line
                            for line in msg.splitlines()
                            if "co-authored" not in line.lower()
                        ).strip()
                        if msg:
                            changelog.append(msg)
                    entry["changelog"] = changelog

                if detect and prev_tag is not None and cur_tag is not None:
                    changed = pkg_repo.git.diff(
                        prev_tag.commit.hexsha, cur_tag.commit.hexsha, "--name-only"
                    ).splitlines()
                    entry["alembic"] = [p for p in changed if "/alembic/" in p]
                    entry["mappings"] = [p for p in changed if "/mappings/" in p]
            except Exception as e:
                entry["error"] = str(e)
                click.secho(f"Warning: failed on {package}: {e}", fg="yellow", err=True)
            finally:
                if pkg_repo is not None:
                    pkg_repo.close()
        packages.append(entry)
    json.dump({"since": since, "until": until, "packages": packages}, output, indent=2)
    output.write("\n")


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
    default=r"(tests?|chore|i18n|ci)\:",
    help="A regular expression to filter commit message entries.",
)
@click.option(
    "--show-major-bumps",
    is_flag=True,
    help="Show major version bumps (breaking changes) of all dependencies, even if they don't match the package filter.",
)
@click.option("--lockfile", default=None, help="The file to write the changelog to.")
@click.option(
    "--output",
    type=click.File("w"),
    default="-",
    help="The file to write the changelog to.",
)
@click.option(
    "--unreleased", is_flag=True, help="Generate the changelog for unreleased."
)
@click.option("--fetch", is_flag=True, help="Fetch the latest commits.")
@click.option("--cache-dir", is_flag=True, help="Print the cache directory.")
@click.option(
    "--json", "as_json", is_flag=True, help="Output changed dependencies as JSON."
)
@click.option(
    "--detect-migrations",
    is_flag=True,
    help="In JSON mode, detect Alembic/mapping changes per package (clones repos).",
)
def main_cli(
    lockfile,
    since,
    until,
    package_filter,
    message_filter,
    show_major_bumps,
    output,
    unreleased,
    fetch,
    cache_dir,
    as_json,
    detect_migrations,
):
    """Run the changelog generator."""
    if cache_dir:
        click.echo(CACHE_DIR)
        return

    lockfile = Path(lockfile) if lockfile else next((Path(p) for p in LOCKFILES if Path(p).exists()), None)
    if not lockfile:
        raise click.UsageError(f"No lock file found ({','.join(LOCKFILES)}).")

    if not (repo := find_repo(lockfile)):
        raise click.ClickException("Could not find git repository of lockfile.")

    message_filter = message_filter and re.compile(message_filter)
    package_filter = package_filter and re.compile(package_filter)

    # Dispatch to appropriate mode
    if as_json:
        run_json_mode(
            repo,
            lockfile,
            since,
            until,
            package_filter,
            message_filter,
            show_major_bumps,
            detect_migrations,
            fetch,
            output,
        )
    elif unreleased:
        run_unreleased_mode(lockfile, package_filter, message_filter, fetch, output)
    else:
        run_normal_mode(repo, lockfile, since, until, package_filter, message_filter, show_major_bumps, output)


if __name__ == "__main__":
    main_cli()
