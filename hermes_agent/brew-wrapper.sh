#!/usr/bin/env bash
# brew-wrapper.sh -- Root-safe Homebrew delegation
# Homebrew refuses to run as root. This wrapper delegates to the linuxbrew user.
set -euo pipefail

BREW_USER="linuxbrew"
BREW_HOME="/config/.linuxbrew"

if ! id "$BREW_USER" &>/dev/null; then
    useradd -r -s /bin/bash -d "$BREW_HOME" "$BREW_USER" 2>/dev/null || true
fi

chown -R "$BREW_USER":"$BREW_USER" "$BREW_HOME" 2>/dev/null || true

exec su - "$BREW_USER" -s /bin/bash -c "
    export HOMEBREW_PREFIX='$BREW_HOME'
    export HOMEBREW_CELLAR='$BREW_HOME/Cellar'
    export HOMEBREW_REPOSITORY='$BREW_HOME/Homebrew'
    export PATH='$BREW_HOME/bin:$BREW_HOME/sbin:\$PATH'
    $BREW_HOME/bin/brew \"\$@\"
" -- "$@"
