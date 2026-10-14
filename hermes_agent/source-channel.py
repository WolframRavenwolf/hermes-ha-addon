"""Select the add-on's shared source checkout without taking over its runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

import yaml

OFFICIAL = "https://github.com/NousResearch/hermes-agent.git"
TAG = re.compile(r"v(?:0|[1-9][0-9]{0,2})\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


def git(source, *args):
    return subprocess.run(["git", *args], cwd=source, capture_output=True,
                          text=True, check=True, timeout=90).stdout.strip()


def choice(source, homes):
    key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:16]
    selected = {}
    for home in homes:
        config = Path(home) / "config.yaml"
        if not config.is_file():
            continue
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        update = data.get("update")
        installs = update.get("installs") if isinstance(update, dict) else None
        record = installs.get(key) if isinstance(installs, dict) else None
        channel = record.get("channel") if isinstance(record, dict) else None
        if channel is not None:
            if not isinstance(channel, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", channel):
                raise ValueError(f"Invalid saved update channel in {home}; correct its config.yaml")
            selected[str(home)] = channel
    if len(set(selected.values())) > 1:
        raise ValueError("Conflicting saved update channels: " +
                         ", ".join(f"{home}={channel}" for home, channel in selected.items()) +
                         "; select one channel for the shared install")
    return next(iter(selected.values()), "stable"), bool(selected)


def published_release():
    """Only GitHub's published stable release, verified against its origin tag."""
    base = "https://api.github.com/repos/NousResearch/hermes-agent"

    def get(path):
        request = urllib.request.Request(base + path, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "hermes-ha-addon"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    release = get("/releases/latest")
    tag = release.get("tag_name") if isinstance(release, dict) else None
    if (not isinstance(tag, str) or not TAG.fullmatch(tag) or
            release.get("draft") is not False or release.get("prerelease") is not False):
        raise ValueError("No verified published stable release")
    commit = get("/commits/" + tag)
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("Published release has no valid commit")
    refs = git(None, "ls-remote", "--tags", OFFICIAL, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
    found = dict(reversed(line.split()) for line in refs.splitlines() if len(line.split()) == 2)
    if found.get(f"refs/tags/{tag}^{{}}", found.get(f"refs/tags/{tag}")) != sha:
        raise ValueError("Published release tag differs from origin commit")
    return tag, sha


def clone_ref(url, ref, source, homes):
    if ref or url != OFFICIAL:
        return ref, ""
    channel, explicit = choice(source, homes)
    if channel == "main":
        return "", ""  # The repository's default branch.
    if channel != "stable":
        raise ValueError(f"Saved {channel} requires an existing native-capable checkout; "
                         "bootstrap a stable checkout, then select the channel manually")
    tag, published_sha = published_release()
    return tag, published_sha


def native_target(channel, git_cmd, source, forward_only):
    sys.path.insert(0, str(source))
    from hermes_cli.source_releases import resolve_source_target
    from hermes_cli.update_channel import rides_default_channel
    import inspect
    parameters = inspect.signature(resolve_source_target).parameters
    if (not callable(resolve_source_target) or not callable(rides_default_channel) or
            not {"git_cmd", "cwd", "repository", "forward_only"}.issubset(parameters)):
        raise ImportError("native source-channel API unavailable")
    return resolve_source_target(channel, git_cmd, source,
                                 repository="NousResearch/hermes-agent",
                                 forward_only=forward_only)


def transport_failure(exc):
    """Only typed pre-checkout download failures may leave an update pending."""
    while exc is not None:
        if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
            return True
        if (isinstance(exc, (subprocess.TimeoutExpired, subprocess.CalledProcessError)) and
                isinstance(exc.cmd, (list, tuple)) and
                tuple(exc.cmd[:2]) == ("git", "ls-remote")):
            return True
        exc = exc.__cause__
    return False


def hold_update(source, exc):
    # A failed first clone or an unborn/corrupt checkout cannot start from old source.
    if not (source / ".git").exists():
        raise ValueError("Cannot retain source: Git checkout is missing") from exc
    git(source, "rev-parse", "--verify", "HEAD^{commit}")
    print(f"[run] WARNING: source update held: {exc}; existing source retained",
          file=sys.stderr)


def update(source, homes):
    channel, explicit = choice(source, homes)
    try:
        target = native_target(channel, ["git"], source, forward_only=not explicit)
    except ImportError:
        # Older installed checkouts cannot resolve source channels themselves.
        # The published stable selector is verified before touching Git's tree.
        from types import SimpleNamespace
        if channel == "main":
            target = SimpleNamespace(commit=None, branch="main", ahead=False)
        elif channel == "stable":
            try:
                _, commit = published_release()
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                if not transport_failure(exc):
                    raise
                hold_update(source, exc)
                return
            target = SimpleNamespace(commit=commit, branch=None, ahead=False)
        else:
            print(f"[run] WARNING: {channel} update held: this legacy checkout lacks the native "
                  "source-channel API; manually upgrade to a current published Hermes release",
                  file=sys.stderr)
            return
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if not transport_failure(exc):
            raise
        hold_update(source, exc)
        return
    if target.commit:
        if not SHA.fullmatch(target.commit):
            raise ValueError("Native source target has an invalid commit")
        head = git(source, "rev-parse", "HEAD")
        if head == target.commit or target.ahead:
            return
        if subprocess.run(["git", "cat-file", "-e", target.commit + "^{commit}"],
                          cwd=source, check=False, capture_output=True, timeout=90).returncode != 0:
            try:
                git(source, "fetch", "--no-tags", "origin", target.commit)
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                hold_update(source, exc)
                return
        git(source, "cat-file", "-e", target.commit + "^{commit}")
        if not explicit:
            if subprocess.run(["git", "merge-base", "--is-ancestor", target.commit, head],
                              cwd=source, check=False).returncode == 0:
                return
            if subprocess.run(["git", "merge-base", "--is-ancestor", head, target.commit],
                              cwd=source, check=False).returncode != 0:
                raise ValueError("Cannot prove a forward-only stable update; source unchanged")
        # Git refuses a checkout that would overwrite local source edits.
        if subprocess.run(["git", "merge-base", "--is-ancestor", head, target.commit],
                          cwd=source, check=False).returncode == 0:
            git(source, "merge", "--ff-only", target.commit)
        elif explicit:
            git(source, "checkout", "--detach", target.commit)
        else:
            raise ValueError("Stable update is not forward-only; source unchanged")
    elif target.branch:
        try:
            git(source, "fetch", "--no-tags", "origin", target.branch)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            hold_update(source, exc)
            return
        git(source, "merge", "--ff-only", "FETCH_HEAD")
    else:
        raise ValueError("Native source target has no commit or branch")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("clone", "update"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--url", default="")
    parser.add_argument("--ref", default="")
    parser.add_argument("--home", action="append", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == "clone":
            ref, sha = clone_ref(args.url, args.ref, args.source, args.home)
            print(f"{ref}\t{sha}")
        else:
            update(args.source, args.home)
    except (OSError, ValueError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        print(f"[run] FATAL: source channel: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
