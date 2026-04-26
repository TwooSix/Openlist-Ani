#!/bin/sh

set -m

if [ "$ENABLE_ASSISTANT" = "true" ]; then
    echo "Starting openlist-ani and openlist-ani-assistant..."
    
    openlist-ani &
    PID_MAIN=$!
    
    openlist-ani-assistant &
    PID_ASSISTANT=$!
    
    trap "kill $PID_MAIN $PID_ASSISTANT" TERM INT
    
    wait $PID_MAIN $PID_ASSISTANT
    
    echo "One process exited, shutting down..."
    kill $PID_MAIN 2>/dev/null
    kill $PID_ASSISTANT 2>/dev/null
    exit 0
else
    echo "Starting openlist-ani..."
    exec openlist-ani
fi
