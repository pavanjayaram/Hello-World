#!/usr/bin/env python3
"""
create_sprint_pr_from_anywhere.py

Run this from ANY folder. The script will clone (or use an existing) repo under a
target folder (e.g. /folder1) and then create a sprint branch, copy files from local path,
commit, push, create PR (via gh) and optionally merge.

Prereqs:
 - git installed + SSH configured
 - gh installed and authenticated (gh auth login)
 - Python 3.7+

Usage: just run and follow prompts:
  python3 create_sprint_pr_from_anywhere.py
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

def run(cmd, cwd=None, capture=False, check=True):
    if capture:
        proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if check and proc.returncode != 0:
            raise RuntimeError(f"Command failed: {' '.join(cmd)}\nstdout:{proc.stdout}\nstderr:{proc.stderr}")
        return proc
    else:
        proc = subprocess.run(cmd, cwd=cwd)
        if check and proc.returncode != 0:
            raise RuntimeError(f"Command failed: {' '.join(cmd)} (exit {proc.returncode})")
        return proc

def ensure_cli(name):
    try:
        run([name, "--version"], capture=True)
    except Exception:
        print(f"ERROR: '{name}' is required in PATH. Install it and try again.")
        sys.exit(1)

def safe_input(prompt, default=None):
    s = input(f"{prompt}{' ['+default+']' if default else ''}: ").strip()
    if s:
        return s
    return default

def clone_or_use(repo_spec, clone_parent):
    """
    repo_spec: either ssh url or owner/repo
    clone_parent: absolute path of parent directory where repo will be cloned
    Returns path to cloned repo dir and the repo_url used.
    """
    if ":" in repo_spec or repo_spec.startswith("git@") or repo_spec.endswith(".git"):
        repo_url = repo_spec
        repo_name = Path(repo_spec).stem if repo_spec.endswith(".git") else repo_spec.split("/")[-1]
    else:
        repo_url = f"git@github.com:{repo_spec}.git"
        repo_name = repo_spec.split("/")[-1]

    target_dir = Path(clone_parent).expanduser() / repo_name
    if target_dir.exists():
        # If the folder exists but is not a git repo, abort or offer to use it
        if not (target_dir / ".git").exists():
            raise RuntimeError(f"Target directory {target_dir} exists but is not a git repository.")
        print(f"Using existing repo at {target_dir}")
    else:
        print(f"Cloning {repo_url} -> {target_dir}")
        proc = subprocess.run(["git", "clone", repo_url, str(target_dir)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            print("git clone stdout:\n", proc.stdout)
            print("git clone stderr:\n", proc.stderr)
            raise RuntimeError(f"git clone failed (exit {proc.returncode})")
        print(proc.stdout)
    return str(target_dir.resolve()), repo_url

def create_branch_from_source(repo_dir, sprint_branch, source_branch="develop", git_user_name=None, git_user_email=None):
    run(["git", "fetch", "origin"], cwd=repo_dir)
    # ensure source_branch exists locally
    rev = subprocess.run(["git", "rev-parse", "--verify", source_branch], cwd=repo_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if rev.returncode != 0:
        # try to create tracking branch from origin/<source_branch>
        run(["git", "checkout", "-b", source_branch, f"origin/{source_branch}"], cwd=repo_dir)
    else:
        run(["git", "checkout", source_branch], cwd=repo_dir)
        run(["git", "pull", "origin", source_branch], cwd=repo_dir)

    if git_user_name:
        run(["git", "config", "user.name", git_user_name], cwd=repo_dir)
    if git_user_email:
        run(["git", "config", "user.email", git_user_email], cwd=repo_dir)

    # create sprint branch
    run(["git", "checkout", "-b", sprint_branch], cwd=repo_dir)
    print(f"Created branch {sprint_branch} from {source_branch}")

def copy_changes(src_path, repo_dir, dest_subpath=None):
    src = Path(src_path).expanduser().resolve()
    if not src.exists():
        raise RuntimeError(f"Changes source does not exist: {src}")
    dest = Path(repo_dir).resolve()
    if dest_subpath:
        dest = dest / dest_subpath
    dest.mkdir(parents=True, exist_ok=True)
    print(f"Copying {src} -> {dest}")

    if src.is_dir():
        for item in src.iterdir():
            targ = dest / item.name
            if item.is_dir():
                if targ.exists():
                    # merge
                    for sub in item.rglob('*'):
                        rel = sub.relative_to(item)
                        tt = targ / rel
                        if sub.is_dir():
                            tt.mkdir(parents=True, exist_ok=True)
                        else:
                            tt.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(sub, tt)
                else:
                    shutil.copytree(item, targ)
            else:
                targ.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, targ)
    else:
        tgt = dest / src.name
        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tgt)
    print("Copy complete.")

def commit_and_push(repo_dir, commit_msg, branch):
    run(["git", "add", "-A"], cwd=repo_dir)
    # check if staged changes exist
    proc = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=repo_dir)
    if proc.returncode == 0:
        print("No changes to commit.")
        return False
    run(["git", "commit", "-m", commit_msg], cwd=repo_dir)
    run(["git", "push", "--set-upstream", "origin", branch], cwd=repo_dir)
    print("Pushed branch to origin.")
    return True

def create_pr(repo_dir, head, base, title, body):
    # uses gh to create PR. gh must be logged in (gh auth login)
    print("Creating PR with gh...")
    proc = subprocess.run(["gh", "pr", "create", "--base", base, "--head", head, "--title", title, "--body", body],
                          cwd=repo_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    print("gh stdout:\n", proc.stdout)
    print("gh stderr:\n", proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError("gh pr create failed")
    # attempt to parse URL from stdout
    for line in proc.stdout.splitlines():
        if line.startswith("http"):
            return line.strip()
    return None

def merge_pr(repo_dir, pr_ref, method="merge"):
    flag = {"merge":"--merge","squash":"--squash","rebase":"--rebase"}.get(method,"--merge")
    run(["gh", "pr", "merge", pr_ref, flag, "--admin"], cwd=repo_dir)

def main():
    ensure_cli("git")
    ensure_cli("gh")
    print("----- Create Sprint Branch & PR (run from anywhere) -----")
    repo_spec = safe_input("Repo (ssh url or owner/repo)", default=None)
    clone_parent = safe_input("Parent folder to clone/use repo in (absolute or relative)", default=str(Path.cwd()))
    clone_parent = str(Path(clone_parent).expanduser().resolve())

    sprint_name = safe_input("Sprint name (e.g. sprint-42). Leave empty to auto-generate", default="")
    if not sprint_name:
        sprint_name = "sprint-" + datetime.utcnow().strftime("%Y%m%d%H%M%S")
    sprint_branch = f"sprint/{sprint_name}"

    changes_path = safe_input("Local path containing files to add (absolute or relative)", default="sprint_changes")
    dest_subpath = safe_input("Destination subpath inside repo (leave empty for repo root)", default="")
    commit_msg = safe_input("Commit message", default=f"Add {sprint_name} changes")
    pr_title = safe_input("PR title", default=f"{sprint_name}: merge into develop")
    pr_body = safe_input("PR body", default="Auto-created PR")
    auto_merge = safe_input("Auto-merge PR after creation? (y/N)", default="N").lower() == "y"
    merge_method = "merge"
    if auto_merge:
        merge_method = safe_input("Merge method (merge/squash/rebase)", default="merge")

    # clone or use existing
    try:
        repo_dir, repo_url = clone_or_use(repo_spec, clone_parent)
    except Exception as e:
        print("Clone error:", e)
        sys.exit(1)

    # create branch from develop
    try:
        create_branch_from_source(repo_dir, sprint_branch, source_branch="develop")
    except Exception as e:
        print("Failed to create branch from develop:", e)
        sys.exit(1)

    # copy changes from changes_path (which can be anywhere, e.g. in /folder2)
    try:
        copy_changes(changes_path, repo_dir, dest_subpath if dest_subpath else None)
    except Exception as e:
        print("Copy failed:", e)
        sys.exit(1)

    # commit and push
    try:
        pushed = commit_and_push(repo_dir, commit_msg, sprint_branch)
    except Exception as e:
        print("Commit/push failed:", e)
        sys.exit(1)
    if not pushed:
        print("Nothing pushed; exiting.")
        sys.exit(0)

    # create PR using gh
    try:
        pr_url = create_pr(repo_dir, sprint_branch, "develop", pr_title, pr_body)
        if pr_url:
            print("PR URL:", pr_url)
        else:
            print("PR created but URL not parsed; check gh output above.")
    except Exception as e:
        print("PR creation failed:", e)
        sys.exit(1)

    # optionally merge
    if auto_merge and pr_url:
        try:
            merge_pr(repo_dir, pr_url, method=merge_method)
            print("PR merged.")
        except Exception as e:
            print("Auto-merge failed:", e)
            print("You can merge manually or re-run gh pr merge.")

    print("Done.")

if __name__ == "__main__":
    main()
