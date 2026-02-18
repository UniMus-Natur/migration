#!/usr/bin/env python3
import subprocess
import re
import sys

def get_latest_tag():
    try:
        # Get the latest tag that looks like a version (v*)
        tags = subprocess.check_output(
            ["git", "tag", "--list", "v*"], stderr=subprocess.DEVNULL
        ).decode().strip().split('\n')
        
        if not tags or tags == ['']:
            return None
            
        # Sort tags by version not by string (imperfect but usually sufficient for vX.Y.Z)
        # Ideally we'd use semver sorting, but wanting to keep this dep-free
        # Let's rely on git describe to find the *closest* tag in history which is what we usually want
        pass
    except subprocess.CalledProcessError:
        return None

    try:
        # git describe --tags --abbrev=0 returns the most recent tag reachable from HEAD
        latest_tag = subprocess.check_output(
            ["git", "describe", "--tags", "--match", "v*", "--abbrev=0"], 
            stderr=subprocess.DEVNULL
        ).decode().strip()
        return latest_tag
    except subprocess.CalledProcessError:
        return None

def get_commits_since_tag(tag):
    if not tag:
        # If no tag, get all commits
        range_spec = "HEAD"
        # If there are no commits at all, this might fail, but assuming repo has commits
        try:
             commits = subprocess.check_output(
                ["git", "log", "--pretty=format:%s"], stderr=subprocess.DEVNULL
            ).decode().strip().split('\n')
        except subprocess.CalledProcessError:
             return []
    else:
        try:
            commits = subprocess.check_output(
                ["git", "log", f"{tag}..HEAD", "--pretty=format:%s"], stderr=subprocess.DEVNULL
            ).decode().strip().split('\n')
        except subprocess.CalledProcessError:
            return []
    return [c for c in commits if c]

def parse_version(tag):
    # expect vX.Y.Z
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", tag)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    return 0, 0, 0

def calculate_next_version(current_version_str, commits):
    major, minor, patch = parse_version(current_version_str or "0.0.0")
    
    bump_major = False
    bump_minor = False
    bump_patch = False
    
    for commit in commits:
        if "BREAKING CHANGE" in commit or "!:" in commit:
            bump_major = True
        elif commit.strip().startswith("feat"):
            bump_minor = True
        elif commit.strip().startswith("fix"):
            bump_patch = True
            
    if bump_major:
        major += 1
        minor = 0
        patch = 0
    elif bump_minor:
        minor += 1
        patch = 0
    elif bump_patch:
        patch += 1
    else:
        # If no relevant commits found, do we bump patch? 
        # Usually yes if there are *any* changes, or maybe we don't build?
        # The user's request implies "whenever I commit... based on commit message".
        # If I commit "docs: update readme", it's not a fix or feat.
        # Strict semver might say no release. 
        # But for CI/CD, if we are running this, we probably want a new version if we push code.
        # Let's default to PATCH if there are commits but no specific keywords, 
        # OR return current if we want to be strict.
        # Let's default to patch bump for any "other" changes to ensure unique version on main.
        if commits:
            patch += 1
            
    return f"v{major}.{minor}.{patch}"


def update_file(filepath, patterns):
    """
    patterns: list of (regex, replacement)
    """
    with open(filepath, 'r') as f:
        content = f.read()
    
    new_content = content
    for regex, replacement in patterns:
        new_content = re.sub(regex, replacement, new_content, count=1)
        
    with open(filepath, 'w') as f:
        f.write(new_content)

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--update":
        # Usage: --update <version> <chart_file> <values_file>
        if len(sys.argv) < 5:
            print("Usage: --update <version> <chart_path> <values_path> [staging_values_path]")
            sys.exit(1)
            
        version = sys.argv[2]
        chart_path = sys.argv[3]
        values_path = sys.argv[4]
        staging_path = sys.argv[5] if len(sys.argv) > 5 else None
        
        # Update Chart.yaml
        # version: 0.1.0 -> version: <version_no_v>
        # appVersion: "..." -> appVersion: "<version>"
        version_no_v = version.lstrip('v')
        
        # Update Chart.yaml
        print(f"Updating {chart_path}...")
        update_file(chart_path, [
            (r'(^|\n)version:\s*[\d\.]+', f'\\g<1>version: {version_no_v}'),
            (r'(^|\n)appVersion:\s*"[^"]+"', f'\\g<1>appVersion: "{version}"')
        ])

        # Update values.yaml
        # tag: "..." assuming it's under image. This is a bit risky with regex but 
        # based on file content viewed earlier, image key is at top.
        # We'll look for `  tag: ".*"` or `tag: ".*"`
        print(f"Updating {values_path}...")
        update_file(values_path, [
            (r'(\n\s+tag:\s*)"[^"]+"', f'\\g<1>"{version}"')
        ])
        
        if staging_path:
             print(f"Updating {staging_path}...")
             update_file(staging_path, [
                (r'(\n\s+tag:\s*)"[^"]+"', f'\\g<1>"{version}"')
            ])

        return

    latest_tag = get_latest_tag()
    commits = get_commits_since_tag(latest_tag)
    
    if not commits and latest_tag:
        # No new commits since last tag, output current tag
        print(latest_tag)
        return

    next_ver = calculate_next_version(latest_tag, commits)
    print(next_ver)

if __name__ == "__main__":
    main()
