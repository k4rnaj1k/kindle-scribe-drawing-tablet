#!/bin/sh
# KUAL menu action: Show tablet mode status
STATUS=$(/mnt/us/extensions/kindle-tablet/bin/tablet-mode.sh status)
eips -c
eips 10 20 "Tablet Mode: $STATUS"
sleep 3
