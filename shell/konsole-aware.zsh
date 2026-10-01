# konsole-aware.zsh - Tab and Task Awareness for Zsh
# Compatible with Zsh 5.0+ and Konsole / Linux terminals

if [[ -n "$ZSH_VERSION" ]]; then
    autoload -Uz add-zsh-hook 2>/dev/null

    _konsole_aware_bin() {
        if (( $+commands[konsole-aware] )); then
            echo "konsole-aware"
        elif [[ -x "$HOME/.local/bin/konsole-aware" ]]; then
            echo "$HOME/.local/bin/konsole-aware"
        else
            return 1
        fi
    }

    _konsole_aware_preexec() {
        # Arguments: $1 is typed command
        local cmd="$1"
        [[ -z "$cmd" ]] && return

        # Quick immediate visual cue via OSC 30 (Konsole local tab title) and OSC 0
        local short_cmd="${cmd[1,24]}"
        printf '\033]30;⏳ %s\007\033]0;⏳ %s\007' "$short_cmd" "$short_cmd" 2>/dev/null

        local bin
        bin=$(_konsole_aware_bin) || return
        # Run in subshell disowned so it never blocks shell execution
        ( "$bin" preexec "$cmd" >/dev/null 2>&1 & ) 2>/dev/null
    }

    _konsole_aware_precmd() {
        local exit_code=$?
        local bin
        bin=$(_konsole_aware_bin) || return
        ( "$bin" precmd "$exit_code" >/dev/null 2>&1 & ) 2>/dev/null
    }

    if typeset -f add-zsh-hook >/dev/null 2>&1; then
        add-zsh-hook preexec _konsole_aware_preexec
        add-zsh-hook precmd _konsole_aware_precmd
    else
        preexec_functions+=(_konsole_aware_preexec)
        precmd_functions+=(_konsole_aware_precmd)
    fi
fi
