#!/usr/bin/env bash
set -euo pipefail
# Mock solver: writes QoI close to reference value (0.371)
cat > qoi.json <<'EOF'
{"centerline_umax": 0.373}
EOF
exit 0
