# konsole-aware.bash - Tab and task awareness for Bash (4.0+; best on 4.4+)
#
# Source from ~/.bashrc:   source /path/to/konsole-aware.bash
#
# Reports every interactively typed command line to the konsole-aware CLI:
#
#   konsole-aware preexec --id <ID> --shell-pid <PID> -- "<full command line>"
#   konsole-aware precmd  --id <ID> <exit_code>
#
# <ID> is a per-shell counter, incremented once per typed command, so the CLI
# can discard a preexec that is delivered after its matching precmd. Both calls
# run as fully detached background jobs with all output discarded.
#
# Environment:
#   KONSOLE_AWARE_BIN=/path/to/konsole-aware   binary override (source time)
#   KONSOLE_AWARE_DISABLE=1                    opt out (checked at hook time)
#
# Mechanism (bash-preexec style):
#   * PROMPT_COMMAND gets _konsole_aware_precmd FIRST (so it sees the real $?)
#     and _konsole_aware_arm LAST.
#   * The arm function sets a flag; the first DEBUG trap after the prompt is
#     the first simple command of the typed line. It consumes the flag and
#     fires preexec exactly once. Later simple commands (loops, pipelines) and
#     PROMPT_COMMAND internals never see the flag set.
#   * The full typed line comes from the history list when the command was
#     added to it (HISTCMD advanced), else from $BASH_COMMAND.
#   * Installation completes on the first prompt (one-shot bootstrap in
#     PROMPT_COMMAND), after the rest of ~/.bashrc has run. An existing DEBUG
#     trap is captured there and chained.
#   * If bash-preexec is already loaded, its preexec/precmd arrays are used
#     instead of installing a second DEBUG trap.
#   * Sourcing this file twice is harmless.

if [ -n "${BASH_VERSION:-}" ]; then

# --------------------------------------------------------------------------
# Resolve the CLI once, at source time.
# --------------------------------------------------------------------------
_konsole_aware_bin=''
if [[ -n "${KONSOLE_AWARE_BIN:-}" ]]; then
    if [[ "$KONSOLE_AWARE_BIN" == */* ]]; then
        [[ -f "$KONSOLE_AWARE_BIN" && -x "$KONSOLE_AWARE_BIN" ]] \
            && _konsole_aware_bin=$KONSOLE_AWARE_BIN
    else
        _konsole_aware_bin=$(type -P -- "$KONSOLE_AWARE_BIN" 2>/dev/null) || _konsole_aware_bin=''
    fi
fi
if [[ -z "$_konsole_aware_bin" ]]; then
    _konsole_aware_bin=$(type -P konsole-aware 2>/dev/null) || _konsole_aware_bin=''
fi
if [[ -z "$_konsole_aware_bin" && -x "${HOME:-}/.local/bin/konsole-aware" ]]; then
    _konsole_aware_bin=$HOME/.local/bin/konsole-aware
fi

if [[ -z "$_konsole_aware_bin" ]]; then
    # CLI not installed: no-op hooks that preserve $?. Nothing new is
    # registered, but names left in PROMPT_COMMAND / the DEBUG trap by an
    # earlier sourcing keep resolving.
    _konsole_aware_debug()  { return $?; }
    _konsole_aware_precmd() { return $?; }
    _konsole_aware_arm()    { return $?; }
    _konsole_aware_install() { local s=$?; unset _konsole_aware_trap_str; return $s; }
    _konsole_aware_bp_preexec() { :; }
else

# Per-shell state. Kept across re-sourcing so ids stay monotonic.
: "${_konsole_aware_cmd_id:=0}"          # id of the last command reported to preexec
: "${_konsole_aware_done_id:=0}"         # id of the last command reported to precmd
: "${_konsole_aware_armed:=0}"           # 1 between the end of PROMPT_COMMAND and the first DEBUG trap
: "${_konsole_aware_prompt_histcmd:=}"   # $HISTCMD as seen while drawing the prompt

# Instant tab-title cue (OSC 30 = Konsole tab title, OSC 0 = window title).
# The text is stripped of control characters and truncated.
_konsole_aware_title() {
    [[ -t 1 ]] || return 0
    local t=${1//[[:cntrl:]]/ }
    t=${t#"${t%%[![:space:]]*}"}
    (( ${#t} > 40 )) && t="${t:0:39}…"
    printf '\033]30;⏳ %s\007\033]0;⏳ %s\007' "$t" "$t" 2>/dev/null
    return 0
}

# Report a typed command line. $1 = line as typed.
_konsole_aware_preexec() {
    local typed=${1:-}
    [[ -n "${typed//[[:space:]]/}" ]] || return 0

    # Expand a leading alias (one level) so the CLI sees what actually runs,
    # e.g. `agy` -> `agy --dangerously-skip-permissions`.
    local full=$typed lead rest word
    lead=${typed%%[![:space:]]*}
    rest=${typed#"$lead"}
    word=${rest%%[[:space:];|&<>()]*}
    if [[ -n "$word" && -n "${BASH_ALIASES[$word]+x}" ]]; then
        full=$lead${BASH_ALIASES[$word]}${rest#"$word"}
    fi

    (( ++_konsole_aware_cmd_id ))
    _konsole_aware_title "$typed"
    ( "$_konsole_aware_bin" preexec --id "$_konsole_aware_cmd_id" --shell-pid "$$" -- "$full" \
        </dev/null >/dev/null 2>&1 & ) 2>/dev/null
    return 0
}

# DEBUG trap body. Must preserve $? (the chained original trap sees it).
_konsole_aware_debug() {
    local s=$?
    [[ "${_konsole_aware_armed:-0}" == 1 ]] || return $s
    # Programmable completion and bind -x widgets run while the prompt is up.
    [[ -z "${COMP_LINE:-}" && -z "${READLINE_LINE+x}" ]] || return $s
    # With `set -T` the trap also fires inside our own functions.
    [[ "${FUNCNAME[1]:-}" == _konsole_aware_* ]] && return $s

    _konsole_aware_armed=0
    # Reaching our own prompt hook while still armed means nothing that the
    # trap can see ran (empty line, Ctrl-C, or a bare `( subshell )`).
    [[ "$BASH_COMMAND" == _konsole_aware_* ]] && return $s
    [[ "${KONSOLE_AWARE_DISABLE:-}" == 1 ]] && return $s

    local line='' re='^ *[0-9]+[* ] (.*)$'
    # Inside the trap HISTCMD equals its prompt-time value only if this line
    # was saved to history (otherwise it is one less: ignorespace/ignoredups),
    # so `history 1` is known to be the line being executed.
    if [[ -n "${HISTCMD:-}" && "$HISTCMD" == "${_konsole_aware_prompt_histcmd:-}" ]]; then
        line=$(HISTTIMEFORMAT='' builtin history 1 2>/dev/null)
        if [[ "$line" =~ $re ]]; then
            line=${BASH_REMATCH[1]}
        else
            line=''
        fi
    fi
    [[ -n "$line" ]] || line=$BASH_COMMAND

    _konsole_aware_preexec "$line"
    return $s
}

# First entry of PROMPT_COMMAND: report completion of the last typed command.
_konsole_aware_precmd() {
    local s=$?
    _konsole_aware_armed=0
    # Report only if a preexec went out since the last prompt (skips empty
    # Enter). Not gated on KONSOLE_AWARE_DISABLE so a busy tab is always
    # released.
    if (( _konsole_aware_cmd_id != _konsole_aware_done_id )); then
        _konsole_aware_done_id=$_konsole_aware_cmd_id
        ( "$_konsole_aware_bin" precmd --id "$_konsole_aware_cmd_id" --shell-pid "$$" "$s" \
            </dev/null >/dev/null 2>&1 & ) 2>/dev/null
    fi
    return $s
}

# PROMPT_COMMAND may be a string or (bash 5.1+) an array. Lengths are taken
# from a copy because ${#scalar[@]} trips `set -u` in bash 5.2.

# True when PROMPT_COMMAND still has our precmd and ends with our arm.
_konsole_aware_pc_ok() {
    local n=0 last
    local -a pc=(); [[ -z "${PROMPT_COMMAND[*]+x}" ]] || pc=("${PROMPT_COMMAND[@]}"); n=${#pc[@]}
    (( n > 0 )) || return 1
    last=${pc[n-1]}
    [[ "$last" == _konsole_aware_arm || "$last" == *$'\n'_konsole_aware_arm ]] || return 1
    [[ "${pc[*]}" == *_konsole_aware_precmd* ]]
}

# One-shot bootstrap appended to PROMPT_COMMAND at source time. It runs at
# top level on the first prompt, after the rest of ~/.bashrc, where
# `trap -p DEBUG` can still see a pre-existing DEBUG trap (bash hides it
# inside functions and sourced files).
_konsole_aware_boot=$'_konsole_aware_trap_str=$(trap -p DEBUG)\n_konsole_aware_install'

# (Re)write PROMPT_COMMAND (string or array) as: precmd, <others>, arm.
# Drops any earlier copies of our entries and the bootstrap.
_konsole_aware_pc_install() {
    local nl=$'\n' pre=_konsole_aware_precmd arm=_konsole_aware_arm
    local boot=$_konsole_aware_boot e n=0
    local -a pc=(); [[ -z "${PROMPT_COMMAND[*]+x}" ]] || pc=("${PROMPT_COMMAND[@]}"); n=${#pc[@]}
    if (( n > 1 )); then
        local -a out=()
        for e in "${PROMPT_COMMAND[@]}"; do
            e=${e//"$nl$boot"/}; e=${e//"$nl$arm"/}
            e=${e#"$boot$nl"}; e=${e#"$pre$nl"}; e=${e#"$pre; "}
            [[ "$e" == "$pre" || "$e" == "$arm" || "$e" == "$boot" ]] && continue
            [[ -n "${e//[[:space:];]/}" ]] && out+=("$e")
        done
        PROMPT_COMMAND=("$pre" ${out[@]+"${out[@]}"} "$arm")
    else
        # Newline separators: safe with user strings ending in ';' or '&'.
        e=${PROMPT_COMMAND:-}
        e=${e//"$nl$boot"/}; e=${e//"$nl$arm"/}
        e=${e#"$boot$nl"}; e=${e#"$pre$nl"}; e=${e#"$pre; "}
        [[ "$e" == "$pre" || "$e" == "$arm" || "$e" == "$boot" ]] && e=''
        if [[ -n "${e//[[:space:];]/}" ]]; then
            PROMPT_COMMAND=$pre$nl$e$nl$arm
        else
            PROMPT_COMMAND=$pre$nl$arm
        fi
    fi
}

# Last entry of PROMPT_COMMAND: arm the DEBUG trap for the next typed line.
_konsole_aware_arm() {
    local s=$?
    _konsole_aware_prompt_histcmd=${HISTCMD:-}
    if _konsole_aware_pc_ok; then
        _konsole_aware_armed=1
    elif [[ "${PROMPT_COMMAND[*]:-}" != *_konsole_aware_install* ]]; then
        # Something was appended after us: a DEBUG trap fired now would be a
        # PROMPT_COMMAND entry, not user input. Repair for the next prompt
        # and stay disarmed this once. (If the bootstrap is pending it does
        # the repair and arming itself.)
        _konsole_aware_pc_install
    fi
    return $s
}

# Run by the bootstrap: chain/install the DEBUG trap, fix up PROMPT_COMMAND,
# and arm for the first typed line if the bootstrap was the last entry.
_konsole_aware_install() {
    local s=$? n=0 last='' cur=${_konsole_aware_trap_str:-} orig=''
    local -a pc=(); [[ -z "${PROMPT_COMMAND[*]+x}" ]] || pc=("${PROMPT_COMMAND[@]}"); n=${#pc[@]}
    unset _konsole_aware_trap_str
    (( n > 0 )) && last=${pc[n-1]}

    # Our function runs first and returns the original $?; the original body
    # then runs in the same (top-level) trap context.
    if [[ "$cur" != *_konsole_aware_debug* ]]; then
        if [[ -n "$cur" ]]; then
            eval "set -- $cur"      # trap -- '<body>' DEBUG
            orig=${3:-}
        fi
        if [[ -n "$orig" ]]; then
            trap "_konsole_aware_debug"$'\n'"$orig" DEBUG
        else
            trap '_konsole_aware_debug' DEBUG
        fi
    fi

    _konsole_aware_pc_install
    if [[ "$last" == "$_konsole_aware_boot" || "$last" == *$'\n'"$_konsole_aware_boot" ]]; then
        _konsole_aware_prompt_histcmd=${HISTCMD:-}
        _konsole_aware_armed=1
    fi
    return $s
}

# bash-preexec adapter: $1 is the full typed line from history.
_konsole_aware_bp_preexec() {
    [[ "${KONSOLE_AWARE_DISABLE:-}" == 1 ]] && return 0
    _konsole_aware_preexec "${1:-}"
}

# --------------------------------------------------------------------------
# Registration (idempotent).
# --------------------------------------------------------------------------
if [[ -n "${bash_preexec_imported:-}${__bp_imported:-}" ]]; then
    [[ " ${preexec_functions[*]:-} " == *" _konsole_aware_bp_preexec "* ]] \
        || preexec_functions+=(_konsole_aware_bp_preexec)
    [[ " ${precmd_functions[*]:-} " == *" _konsole_aware_precmd "* ]] \
        || precmd_functions+=(_konsole_aware_precmd)
elif [[ "${PROMPT_COMMAND[*]:-}" != *_konsole_aware_install* ]]; then
    # Defer the real installation to the first prompt (see above).
    if [[ -n "${PROMPT_COMMAND[1]+x}" ]]; then   # array with 2+ elements
        PROMPT_COMMAND+=("$_konsole_aware_boot")
    elif [[ -n "${PROMPT_COMMAND:-}" ]]; then
        PROMPT_COMMAND=$PROMPT_COMMAND$'\n'$_konsole_aware_boot
    else
        PROMPT_COMMAND=$_konsole_aware_boot
    fi
fi

fi  # binary found
fi  # bash
