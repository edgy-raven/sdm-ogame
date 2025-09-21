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
git pull origin main --rebase

screen -S "thoth_bot" -X quit 2>/dev/null || true

screen -S "thoth_bot" -dm bash -c "
    source ~/thoth/bin/activate
    cd ~/sdm-ogame/thoth
    python thoth.py > out.log 2>&1 
"

EOF
