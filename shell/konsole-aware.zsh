# konsole-aware.zsh - Tab and task awareness for Zsh (5.0+)
#
# Source from ~/.zshrc:   source /path/to/konsole-aware.zsh
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
# The CLI receives the alias-expanded command ($3, falling back to $2/$1) so
# e.g. `agy` -> `agy --dangerously-skip-permissions` is classified correctly;
# the instant tab-title cue shows the line as typed ($1).
#
# Environment:
#   KONSOLE_AWARE_BIN=/path/to/konsole-aware   binary override (source time)
#   KONSOLE_AWARE_DISABLE=1                    opt out (checked at hook time)
#
# Sourcing this file twice is harmless.

if [[ -n "${ZSH_VERSION:-}" ]]; then

# --------------------------------------------------------------------------
# Resolve the CLI once, at source time.
# --------------------------------------------------------------------------
typeset -g _konsole_aware_bin=''
() {
    emulate -L zsh
    local c=''
    if [[ -n ${KONSOLE_AWARE_BIN:-} ]]; then
        if [[ $KONSOLE_AWARE_BIN == */* ]]; then
            [[ -f $KONSOLE_AWARE_BIN && -x $KONSOLE_AWARE_BIN ]] && c=$KONSOLE_AWARE_BIN
        else
            c=${commands[$KONSOLE_AWARE_BIN]:-}
        fi
    fi
    [[ -z $c ]] && c=${commands[konsole-aware]:-}
    [[ -z $c && -x ${HOME:-}/.local/bin/konsole-aware ]] && c=$HOME/.local/bin/konsole-aware
    _konsole_aware_bin=$c
}

if [[ -z $_konsole_aware_bin ]]; then
    # CLI not installed: no-op hooks that preserve $?. Nothing new is
    # registered, but hooks left by an earlier sourcing keep resolving.
    _konsole_aware_preexec() { :; }
    _konsole_aware_precmd()  { return $?; }
else

# Per-shell state. Kept across re-sourcing so ids stay monotonic.
typeset -gi _konsole_aware_cmd_id=${_konsole_aware_cmd_id:-0}    # last id sent to preexec
typeset -gi _konsole_aware_done_id=${_konsole_aware_done_id:-0}  # last id sent to precmd

# Instant tab-title cue (OSC 30 = Konsole tab title, OSC 0 = window title).
# The text is stripped of control characters and truncated.
_konsole_aware_title() {
    emulate -L zsh
    setopt extended_glob
    [[ -t 1 ]] || return 0
    local t=${1//[[:cntrl:]]/ }
    t=${t##[[:space:]]#}
    (( ${#t} > 40 )) && t="${t[1,39]}…"
    printf '\033]30;⏳ %s\007\033]0;⏳ %s\007' "$t" "$t" 2>/dev/null
    return 0
}

# preexec: $1 = typed line (empty if not saved to history),
#          $2 = single-line alias-expanded, $3 = full alias-expanded text.
_konsole_aware_preexec() {
    emulate -L zsh
    [[ ${KONSOLE_AWARE_DISABLE:-} == 1 ]] && return 0
    local typed=${1:-} full=${3:-}
    [[ -n $full ]] || full=${2:-}
    [[ -n $full ]] || full=$typed
    [[ -n ${full//[[:space:]]/} ]] || return 0
    [[ -n $typed ]] || typed=$full

    (( ++_konsole_aware_cmd_id ))
    _konsole_aware_title "$typed"
    ( "$_konsole_aware_bin" preexec --id "$_konsole_aware_cmd_id" --shell-pid "$$" -- "$full" \
        </dev/null >/dev/null 2>&1 & ) 2>/dev/null
    return 0
}

# precmd: report completion of the last typed command, then hand the original
# $? on to later precmd hooks.
_konsole_aware_precmd() {
    local s=$?
    emulate -L zsh
    # Report only if a preexec went out since the last prompt (skips empty
    # Enter). Not gated on KONSOLE_AWARE_DISABLE so a busy tab is always
    # released.
    if (( _konsole_aware_cmd_id != _konsole_aware_done_id )); then
        _konsole_aware_done_id=$_konsole_aware_cmd_id
        ( "$_konsole_aware_bin" precmd --id "$_konsole_aware_cmd_id" --shell-pid "$$" "$s" \
            </dev/null >/dev/null 2>&1 & ) 2>/dev/null
    fi
    return s
}

# --------------------------------------------------------------------------
# Registration (idempotent: add-zsh-hook and the fallback both de-duplicate).
# --------------------------------------------------------------------------
if autoload -Uz add-zsh-hook 2>/dev/null \
    && add-zsh-hook preexec _konsole_aware_preexec 2>/dev/null \
    && add-zsh-hook precmd _konsole_aware_precmd 2>/dev/null; then
    :
else
    typeset -ga preexec_functions precmd_functions
    (( ${preexec_functions[(Ie)_konsole_aware_preexec]} )) \
        || preexec_functions+=(_konsole_aware_preexec)
    (( ${precmd_functions[(Ie)_konsole_aware_precmd]} )) \
        || precmd_functions+=(_konsole_aware_precmd)
fi

fi  # binary found
fi  # zsh
