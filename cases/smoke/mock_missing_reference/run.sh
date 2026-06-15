#!/usr/bin/env bash
set -euo pipefail
# Mock solver: success exit but no reference file to compare
cat > qoi.json <<'EOF'
{"centerline_umax": 0.373}
EOF
exit 0
