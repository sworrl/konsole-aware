# konsole-aware.bash - Tab and Task Awareness for Bash
# Compatible with Bash 4.0+ and Konsole / Linux terminals

if [[ -n "$BASH_VERSION" ]]; then
    _konsole_aware_bin() {
        if command -v konsole-aware >/dev/null 2>&1; then
            echo "konsole-aware"
        elif [[ -x "$HOME/.local/bin/konsole-aware" ]]; then
            echo "$HOME/.local/bin/konsole-aware"
        else
            return 1
        fi
    }

    _konsole_aware_preexec() {
        # Only fire for interactive commands, skip prompt command itself
        [[ "$BASH_COMMAND" == "$PROMPT_COMMAND"* ]] && return
        [[ "$BASH_COMMAND" == "_konsole_aware_"* ]] && return

        local cmd="$BASH_COMMAND"
        local short_cmd="${cmd:0:24}"
        printf '\033]30;⏳ %s\007\033]0;⏳ %s\007' "$short_cmd" "$short_cmd" 2>/dev/null

        local bin
        bin=$(_konsole_aware_bin) || return
        ( "$bin" preexec "$cmd" >/dev/null 2>&1 & ) 2>/dev/null
    }

    _konsole_aware_precmd() {
        local exit_code=$?
        local bin
        bin=$(_konsole_aware_bin) || return
        ( "$bin" precmd "$exit_code" >/dev/null 2>&1 & ) 2>/dev/null
    }

    # Hook DEBUG trap for preexec
    trap '_konsole_aware_preexec' DEBUG

    # Hook PROMPT_COMMAND for precmd
    if [[ -z "$PROMPT_COMMAND" ]]; then
        PROMPT_COMMAND="_konsole_aware_precmd"
    elif [[ "$PROMPT_COMMAND" != *"_konsole_aware_precmd"* ]]; then
        PROMPT_COMMAND="_konsole_aware_precmd; $PROMPT_COMMAND"
    fi
fi
