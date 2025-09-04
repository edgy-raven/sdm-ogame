#!/bin/bash

if [ "$1" == "--teardown" ]; then
    ssh "$(jq -r '.deploy_location' 'keyring.json')" << 'EOF'
screen -S "thoth_bot" -X quit 2>/dev/null || true
EOF
    exit 0
fi

# Deploy via SSH
ssh "$(jq -r '.deploy_location' 'keyring.json')" << 'EOF'

cd ~/sdm-ogame/thoth
git pull origin main

# Kill old screen session if it exists
screen -S "thoth_bot" -X quit 2>/dev/null || true

# Start bot in detached screen session with auto-restart
screen -S "thoth_bot" -dm bash -c "
    source ~/thoth/bin/activate
    cd ~/sdm-ogame/thoth
    while true; do
        python thoth.py
        sleep 5
    done
"

EOF
