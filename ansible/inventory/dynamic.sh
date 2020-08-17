#!/bin/bash

set -eo pipefail
ssh -F inventory/ssh.cfg root@gw \
    curl -f -s http://mdb/call/ansible | jq '.data' || echo '{}'
