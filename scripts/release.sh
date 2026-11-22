#!/usr/bin/env bash
# Bump the version, record what changed, tag it, and push.
#
# A release here is a tag: `.github/workflows/release.yml` turns one into a page
# on GitHub, so nothing in this script talks to the API and no token is needed.
# What the script owns is the part a tag cannot police on its own — the two
# files that hold the version, the notes file the workflow refuses to publish
# without, and the rule that a tag is never created from a red suite.
#
# Interactive by design: it prints every file it touches and asks once before
# pushing. `--dry-run` prints the same without writing, `--no-push` stops after
# the tag so the bump can be inspected first.
#
#   ./scripts/release.sh beta      # 0.1b1 -> 0.1b2   (next beta)
#   ./scripts/release.sh release   # 0.1b1 -> 0.1     (promote to stable)
#   ./scripts/release.sh minor     # 0.1b1 -> 0.2b1   (new line, as a beta)
#   ./scripts/release.sh patch     # 1.0.0 -> 1.0.1b1 (a fix line, as a beta)
#   ./scripts/release.sh major     # 0.1b3 -> 1.0.0   (new major line, stable)
set -euo pipefail

readonly PYPROJECT="pyproject.toml"
readonly INIT="src/uwp/__init__.py"
readonly README_EN="README.md"
readonly README_RU="README.ru.md"
readonly NOTES_DIR="docs/releases"

die() {
    printf 'ошибка: %s\n' "$*" >&2
    exit 1
}

# `0.1b1` -> line `0.1`, serial `1`. `1.0.0` -> line `1.0.0`, no beta.
# The line keeps the number of parts it was written with: `release` turns
# `1.0.0b1` into `1.0.0`, never into `1.0`, because that would be a different
# version to pip. Anything the regex rejects (`0.1 beta`, `v0.1`, `1`,
# `0.1.0.0`) is refused rather than guessed at.
parse_version() {
    local version=$1
    [[ $version =~ ^([0-9]+\.[0-9]+(\.[0-9]+)?)(b([0-9]+))?$ ]] ||
        die "не понимаю версию '$version': ожидаю 0.1, 0.1b2 или 1.0.0"
    LINE=${BASH_REMATCH[1]}
    SERIAL=${BASH_REMATCH[4]:-}
}

# A pure function: reads no files, writes nothing. Printed on stdout so the
# tests can pin the whole table without touching a repository.
compute_next() {
    local current=$1 kind=$2
    local line serial major minor rest patch

    parse_version "$current"
    line=$LINE
    serial=$SERIAL
    major=${line%%.*}
    rest=${line#*.}
    minor=${rest%%.*}
    patch=""
    [[ $rest == *.* ]] && patch=${rest#*.}

    case $kind in
    beta)
        # A beta behind its own stable release is a downgrade: that line has
        # shipped, so the next beta belongs to a line nobody has seen. The new
        # line keeps the shape the old one was written with.
        if [[ -n $serial ]]; then
            echo "${line}b$((serial + 1))"
        elif [[ -n $patch ]]; then
            echo "$major.$minor.$((patch + 1))b1"
        else
            echo "$major.$((minor + 1))b1"
        fi
        ;;
    release)
        [[ -z $serial ]] && die "версия $line уже стабильная; выпуск без изменений не нужен"
        echo "$line"
        ;;
    minor)
        if [[ -n $patch ]]; then
            echo "$major.$((minor + 1)).0b1"
        else
            echo "$major.$((minor + 1))b1"
        fi
        ;;
    patch)
        # `0.1` has no patch number, and inventing one silently would produce a
        # version the operator never asked for.
        [[ -z $patch ]] && die "версия $line без номера патча; patch требует X.Y.Z"
        echo "$major.$minor.$((patch + 1))b1"
        ;;
    major)
        # Ships stable straight away: the beta underneath it was the beta of
        # exactly this code, so a `1.0.0b1` in between would describe nothing.
        echo "$((major + 1)).0.0"
        ;;
    *)
        die "неизвестный тип выпуска '$kind': ожидаю beta, release, minor, patch или major"
        ;;
    esac
}

current_version() {
    sed -n 's/^version = "\(.*\)"$/\1/p' "$PYPROJECT"
}

# The suite lives in the project venv (see AGENTS.md); the system interpreter
# usually has no pytest, and a `command not found` there would read as a red
# suite rather than as a broken script. `RELEASE_PYTHON` overrides.
python_bin() {
    local candidate
    for candidate in "${RELEASE_PYTHON:-}" .venv/bin/python python3 python; do
        [[ -n $candidate ]] || continue
        if "$candidate" -c 'import pytest' >/dev/null 2>&1; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    printf 'нет интерпретатора с pytest: создайте .venv или задайте RELEASE_PYTHON\n' >&2
    return 1
}

# The notes are the release page's body, so they are written from the commits
# that are actually in the tag. Grouped by Conventional Commit prefix, because
# a flat list of 12 subjects reads as noise. English: the page sits on GitHub
# next to the English README, and the subjects under the headings are English —
# mixed headings read as two languages at once (measured on v0.1b2). Only the
# page is English; the script's own output stays Russian, like the rest of the
# CLI.
#
# A promotion (`0.1b2` -> `0.1`) is the same code as the beta with the label
# taken off, so its page always repeats the beta's sections and then lists
# whatever landed since. Measured the wrong way first: with only the range, a
# promotion right after its beta wrote 33 characters and the suite refused it as
# a stub; with a commit in between it wrote one line and still refused it.
write_notes() {
    local version=$1 previous=$2 current=${3:-} kind=${4:-beta}
    local path="$NOTES_DIR/v$1.md"
    local feat fix docs test chore rest

    feat=$(_commits "$previous" '^feat')
    fix=$(_commits "$previous" '^fix')
    docs=$(_commits "$previous" '^docs')
    test=$(_commits "$previous" '^test')
    chore=$(_commits "$previous" '^chore|^ci|^refactor|^build')
    rest=$(_commits "$previous" '^(?!feat|fix|docs|test|chore|ci|refactor|build)' 'raw')

    {
        echo "# v$version"
        echo
        echo "Version \`$version\`. Tag \`v$version\`."
        echo
        if [[ $kind == release ]]; then
            # The beta's own sections are repeated as they are, so the new
            # commits go into one extra section. Grouping them the same way
            # emitted `## Chores` twice, which reads as a bug in the page.
            _inherited_sections "$current" "$version" || true
            # A blank line, because the inherited body ends without one and
            # `## Chores` would otherwise sit flush against the previous item.
            echo
            _section "Since the beta" "$(_format_commits "$(_all_commits "$previous")")"
        else
            _section "New" "$feat"
            _section "Fixed" "$fix"
            _section "Documentation" "$docs"
            _section "Tests" "$test"
            _section "Chores" "$chore"
            _section "Other" "$(_format_commits "$rest")"
            if [[ -z $feat$fix$docs$test$chore$rest ]]; then
                echo "No commits since the previous release."
                echo
            fi
        fi
    } >"$path"

    printf '  %s\n' "$path"
}

# The body of the beta a stable release promotes, minus its heading, under a
# line that says why it is repeated. The beta's trailing blank line is dropped
# so the sections that follow are separated by exactly one.
_inherited_sections() {
    local source="$NOTES_DIR/v$1.md" version=$2
    [[ -f $source ]] || return 1
    echo "Stable version \`$version\` is \`$1\` with the same code. The changes"
    echo "below repeat the beta's notes:"
    echo
    tail -n +2 "$source" |
        # Both spellings: a beta cut before the notes became English still says
        # «Версия … Тег …», and promoting it must not repeat that line either.
        sed -E 's/^(Version|Версия) `.*`\. (Tag|Тег) `.*`\.$//' |
        sed -E '/./,$!d' |
        sed -e :a -e '/^\n*$/{$d;N;ba' -e '}'
}

_all_commits() {
    _commits "$1" '' 'raw'
}

# Subjects in the range, filtered by a prefix, formatted as list items.
# `raw` returns the subjects unformatted, so a caller can filter on the prefix
# itself — `- ` at the start of a line defeats `^(?!...)`.
_commits() {
    local previous=$1 prefix=$2 format=${3:-list}
    local range=${previous:+$previous..HEAD}
    # No tag yet: the whole history is the release.
    [[ -z $previous ]] && range=HEAD
    local subjects
    subjects=$(git log --format='%s' --reverse "$range" 2>/dev/null)
    if [[ -n $prefix ]]; then
        subjects=$(printf '%s\n' "$subjects" | grep -P "$prefix" || true)
    fi
    [[ -z $subjects ]] && return 0
    [[ $format == raw ]] && printf '%s\n' "$subjects" && return 0
    _format_commits "$subjects"
}

_format_commits() {
    [[ -z $1 ]] && return 0
    printf '%s\n' "$1" |
        sed -E 's/^[a-z]+(\([^)]*\))?: //' |
        sed -E 's/^./\U&/' |
        sed -E 's/^/- /'
}

_section() {
    local title=$1 body=$2
    [[ -z $body ]] && return 0
    printf '## %s\n\n%s\n\n' "$title" "$body"
}

usage() {
    cat <<'EOF'
использование: scripts/release.sh {beta|release|minor|patch|major} [--dry-run] [--no-push]

  beta     следующая бета той же линии        1.0.1b1 -> 1.0.1b2
  release  снять бету                         1.0.1b1 -> 1.0.1
  minor    новая минорная линия, бетой        1.2.3b1 -> 1.3.0b1
  patch    новая патч-линия, бетой            1.0.0 -> 1.0.1b1
  major    новая мажорная линия, стабильная   0.1b3 -> 1.0.0

  --dry-run  показать, что изменится, и ничего не писать
  --no-push  сделать коммит и тег, но не отправлять их
EOF
}

main() {
    local kind=${1:-} dry_run=0 no_push=0
    [[ -z $kind || $kind == -h || $kind == --help ]] && {
        usage
        exit 0
    }
    shift || true
    for arg in "$@"; do
        case $arg in
        --dry-run) dry_run=1 ;;
        --no-push) no_push=1 ;;
        *) die "неизвестный аргумент '$arg'" ;;
        esac
    done

    cd "$(git rev-parse --show-toplevel)"

    # Everything below assumes a clean tree on main: a bump commit that also
    # carries unrelated edits cannot be reviewed, and a tag on a branch cannot
    # be published by `release.yml` without a merge.
    local branch
    branch=$(git rev-parse --abbrev-ref HEAD)
    [[ $branch == main ]] || die "ветка '$branch'; релиз делается с main"
    [[ -z $(git status --porcelain) ]] || die "рабочее дерево грязное:
$(git status --short)"
    git fetch --quiet origin main
    [[ $(git rev-parse HEAD) == $(git rev-parse origin/main) ]] ||
        die "локальный main расходится с origin/main: сначала git pull"

    local current previous
    current=$(current_version)
    [[ -n $current ]] || die "в $PYPROJECT нет строки version"
    next=$(compute_next "$current" "$kind")
    NOTES_FILE="$NOTES_DIR/v$next.md"
    previous=$(git describe --tags --abbrev=0 2>/dev/null || true)

    # A tag is immutable in practice: unlike a commit, a pushed one cannot be
    # amended, so the collision is checked before anything is written.
    git rev-parse -q --verify "refs/tags/v$next" >/dev/null && die "тег v$next уже существует"
    [[ -n $(git ls-remote --tags origin "refs/tags/v$next") ]] && die "тег v$next уже есть на origin"

    # Nothing has landed since the last tag, so the release would be a number
    # with no changes — and its notes a stub the suite refuses. Measured: a
    # `beta` immediately after a stable release hit exactly that. A promotion
    # is exempt: `0.1b2 -> 0.1` is the same code by definition, and its notes
    # inherit the beta's sections.
    if [[ $kind != release && -n $previous ]]; then
        [[ -n $(git log --format='%s' "$previous..HEAD") ]] ||
            die "с $previous нет ни одного коммита: выпускать нечего"
    fi

    printf 'версия: %s -> %s\n' "$current" "$next"
    ((dry_run)) && printf '(dry-run: файлы не меняются)\n'

    # A red suite must leave the tree as it was found. Measured: an aborted run
    # left a bumped pyproject, an edited README and a notes file behind, and the
    # next invocation refused because the tree was dirty — the script had
    # blocked itself. The originals are captured only when it will write.
    #
    # ``rollback`` is deliberately global: the trap runs at EXIT, i.e. after
    # ``main`` has returned and its locals are gone. As a ``local`` it was
    # unbound there, `set -u` killed the trap, and *every* successful release
    # exited 1 — a script that created the tag and then reported failure, so
    # `./scripts/release.sh beta && git push` never ran the second half.
    rollback=0
    _restore() {
        ((rollback)) || return 0
        # Unstage first: ``git add -A`` precedes the commit, and
        # ``git checkout --`` restores from the *index*. Without the reset an
        # interrupted commit left the bump staged and the next run refused
        # because the tree was dirty — the same self-block, one step later.
        git reset --quiet HEAD -- "$PYPROJECT" "$INIT" "$README_EN" "$README_RU" \
            2>/dev/null || true
        git checkout -- "$PYPROJECT" "$INIT" "$README_EN" "$README_RU" 2>/dev/null || true
        # Global for the same reason as ``rollback``: the trap outlives main.
        [[ -n ${NOTES_FILE:-} ]] && rm -f "$NOTES_FILE"
        printf 'изменения отменены: дерево как было\n' >&2
    }
    trap '_restore' EXIT

    if ((!dry_run)); then
        rollback=1
        sed -i "s/^version = \"$current\"$/version = \"$next\"/" "$PYPROJECT"
        sed -i "s/^__version__ = \"$current\"$/__version__ = \"$next\"/" "$INIT"
        for readme in "$README_EN" "$README_RU"; do
            sed -i "s|docs/releases/v$current\.md|docs/releases/v$next.md|g" "$readme"
        done
        printf '  %s\n  %s\n  %s, %s\n' "$PYPROJECT" "$INIT" "$README_EN" "$README_RU"
        write_notes "$next" "$previous" "$current" "$kind"
    fi

    printf '\nтесты...\n'
    local python
    python=$(python_bin) || exit 1
    "$python" -m pytest -q || die "тесты красные: тег не ставится"

    if ((dry_run)); then
        printf '\ndry-run: остановлено до записи и коммита\n'
        return 0
    fi

    rollback=0
    git add -A
    git commit --quiet -m "chore: release $next"
    git tag -a "v$next" -m "UltimateWebParser $next"
    printf 'коммит и тег v%s созданы\n' "$next"

    if ((no_push)); then
        printf '\n--no-push: отправьте вручную:\n  git push origin main\n  git push origin v%s\n' "$next"
        return 0
    fi

    git push origin main
    git push origin "v$next"
    printf '\nтег отправлен. Страница релиза появится здесь:\n'
    printf '  %s/actions/workflows/release.yml\n' "$(git remote get-url origin |
        sed -E 's#^git@([^:]+):#https://\1/#; s#\.git$##')"
}

# Sourced by the tests for `compute_next`; executed for a release.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
