#!/bin/bash
# Starts PubMed Downloader: finds Python 3, runs the app in the background
# (it opens a browser window), and explains what to do if Python is missing.

RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
LOG_DIR="$HOME/Library/Logs/PubMed Downloader"
LOG="$LOG_DIR/app.log"
mkdir -p "$LOG_DIR"

usable() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; }

find_python() {
  local py
  for py in /Library/Frameworks/Python.framework/Versions/3.*/bin/python3 \
            /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$py" ] && usable "$py"; then echo "$py"; return 0; fi
  done
  # Apple's /usr/bin/python3 only works once the Command Line Tools are
  # installed; before that, running it pops up an installer instead.
  if /usr/bin/xcode-select -p >/dev/null 2>&1 && usable /usr/bin/python3; then
    echo /usr/bin/python3; return 0
  fi
  return 1
}

ask() { /usr/bin/osascript -e "$1" 2>/dev/null; }

PY="$(find_python)"
if [ -z "$PY" ]; then
  choice=$(ask 'button returned of (display dialog "PubMed Downloader needs Python 3. It is free and only needs installing once.

Click Install to let macOS add Apple’s Command Line Tools, which include Python. When that has finished, open PubMed Downloader again." with title "PubMed Downloader" buttons {"Cancel", "Get it from python.org", "Install"} default button "Install" with icon note)')
  case "$choice" in
    "Install") /usr/bin/xcode-select --install >/dev/null 2>&1 ;;
    "Get it from python.org") /usr/bin/open "https://www.python.org/downloads/macos/" ;;
  esac
  exit 0
fi

echo "=== $(date): starting with $PY" >>"$LOG"
nohup "$PY" -u "$RES/pubmed_app.py" >>"$LOG" 2>&1 &
PID=$!

# If it stops straight away with an error, say so instead of doing nothing.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.5
  if ! kill -0 "$PID" 2>/dev/null; then
    wait "$PID"
    if [ $? -ne 0 ]; then
      button=$(ask 'button returned of (display dialog "PubMed Downloader couldn’t start. The details are in its log file." with title "PubMed Downloader" buttons {"Show log", "OK"} default button "OK" with icon stop)')
      [ "$button" = "Show log" ] && /usr/bin/open -R "$LOG"
    fi
    exit 0
  fi
done
exit 0
