#!/bin/bash
# Quick audio device switcher for NanoClaw voice daemon
# Usage: ./switch-audio.sh [preset]
# Presets: macbook, headset, iphone
# Without argument: shows interactive menu

set -euo pipefail

list_devices() {
    echo "INPUT:"
    SwitchAudioSource -t input -a | nl -ba
    echo ""
    echo "OUTPUT:"
    SwitchAudioSource -t output -a | nl -ba
}

switch_to() {
    local input="$1"
    local output="$2"
    SwitchAudioSource -t input -s "$input" && echo "Input: $input"
    SwitchAudioSource -t output -s "$output" && echo "Output: $output"
    say -v Zuzana "Přepnuto"
}

case "${1:-}" in
    macbook)
        switch_to "MacBook Pro Microphone" "MacBook Pro Speakers"
        ;;
    headset)
        switch_to "Trust GXT wireless headset" "Trust GXT wireless headset"
        ;;
    iphone)
        switch_to "Pavel Kudrna's iPhone Microphone" "MacBook Pro Speakers"
        ;;
    "")
        echo "Current:"
        echo "  Input:  $(SwitchAudioSource -t input -c)"
        echo "  Output: $(SwitchAudioSource -t output -c)"
        echo ""
        echo "Presets:"
        echo "  1) macbook  - MacBook mic + speakers"
        echo "  2) headset  - Trust GXT (input + output)"
        echo "  3) iphone   - iPhone mic + MacBook speakers"
        echo ""
        read -rp "Choose [1-3]: " choice
        case "$choice" in
            1) "$0" macbook ;;
            2) "$0" headset ;;
            3) "$0" iphone ;;
            *) echo "Invalid choice" ;;
        esac
        ;;
    *)
        echo "Unknown preset: $1"
        echo "Available: macbook, headset, iphone"
        exit 1
        ;;
esac
