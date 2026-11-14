#!/usr/bin/env bash
# The two database scripts need the same two things: a PostgreSQL binary that is
# usually not on PATH on this host, and a way to tell a dump from an archive that
# only looks like one. A failed `pg_dump | gzip` writes a valid 20-byte gzip of
# nothing, which every size check accepts and which restores "successfully" while
# restoring no rows — so the payload is what proves a backup exists, never the
# byte count.

# Windows PostgreSQL installs keep the binaries outside PATH; Git Bash spells the
# drive "/c". Debian/Ubuntu packs put them under /usr/lib/postgresql. The loop
# that finds a tool is one line, so it lives here rather than in each caller.
pg_path_probe() {
    local tool="$1" d
    command -v "$tool" >/dev/null 2>&1 && return 0
    for d in "/c/Program Files/PostgreSQL"/*/bin /usr/lib/postgresql/*/bin; do
        # The Windows install ships psql.exe, and `[ -x "$d/psql" ]` is false for it.
        if [ -x "$d/$tool" ] || [ -x "$d/$tool.exe" ]; then
            export PATH="$d:$PATH"
            return 0
        fi
    done
    return 1
}

# True only when the archive opens AND its text carries the line every plain
# `pg_dump` writes first. `head` closes the pipe early, which under pipefail
# would read as a failure of the dump, so the capture is allowed to break.
pg_archive_is_real() {
    local file="$1" head_text
    [ -s "$file" ] || return 1
    gzip -t "$file" 2>/dev/null || return 1
    head_text="$(gunzip -c "$file" 2>/dev/null | { head -n 20; } || true)"
    case "$head_text" in
        *"PostgreSQL database dump"*) return 0 ;;
        *) return 1 ;;
    esac
}
