#!/bin/sh
# pwiki host-side vault Git sync.
#
# Runs on the HOST (which holds the SSH keys for the Git remote), NEVER inside
# the container — the image ships without an SSH client or keys. The systemd
# timer in deploy/systemd/ invokes this script; PWIKI_GIT_HOST_DIR (and the
# optional vars below) come from the deployment .env via the unit's
# EnvironmentFile=.
#
# Two modes, selected by PWIKI_GIT_HOST_PUSH:
#
#   unset / 0  -> pull-only (DEFAULT; correct for a read-only server). A
#                 fast-forward pull that refuses on a dirty/diverged tree
#                 instead of auto-merging, so a surprise local change surfaces
#                 as a failure.
#
#   1          -> bidirectional. The container runs with
#                 PWIKI_GIT_AUTO_COMMIT=0, so web uploads/edits land as a dirty
#                 working tree. This mode commits those writes, rebases onto the
#                 remote to absorb Obsidian-side pushes, then pushes — keeping
#                 every Git network operation on the host. A real content
#                 conflict aborts the rebase and leaves the commit unpushed for
#                 manual resolution.
#
# Optional commit identity (bidirectional mode only):
#   PWIKI_GIT_AUTHOR_NAME   (default: pwiki)
#   PWIKI_GIT_AUTHOR_EMAIL  (default: pwiki@localhost)
set -eu

: "${PWIKI_GIT_HOST_DIR:?PWIKI_GIT_HOST_DIR must be set (deployment .env)}"
cd "$PWIKI_GIT_HOST_DIR"

push_mode=$(printf '%s' "${PWIKI_GIT_HOST_PUSH:-0}" | tr '[:upper:]' '[:lower:]')
case "$push_mode" in
    1 | true | yes | on) push_mode=1 ;;
    *) push_mode=0 ;;
esac

if [ "$push_mode" -eq 0 ]; then
    # Read-only server: plain fast-forward pull (the historical behavior).
    exec git pull --ff-only
fi

branch=$(git symbolic-ref --short HEAD)
author_name=${PWIKI_GIT_AUTHOR_NAME:-pwiki}
author_email=${PWIKI_GIT_AUTHOR_EMAIL:-pwiki@localhost}

# 1) Commit local web writes. `git add -A` also stages newly uploaded files;
#    the commit is skipped cleanly when the tree is already clean.
git add -A
if ! git diff --cached --quiet; then
    git -c "user.name=$author_name" -c "user.email=$author_email" \
        commit -m "Update vault via pwiki (web)"
fi

# 2) Absorb remote (Obsidian) changes, replaying the local commit on top. A
#    fast-forward (no local commit) just advances to the remote tip — i.e. the
#    pull half still happens. A real content conflict aborts cleanly.
git fetch --quiet origin
if ! git rebase "origin/$branch"; then
    git rebase --abort 2>/dev/null || true
    echo "pwiki-vault-sync: rebase conflict on '$branch'; resolve manually in the vault." >&2
    exit 1
fi

# 3) Publish. "Everything up-to-date" (nothing to push) exits 0.
git push --quiet origin "$branch"
