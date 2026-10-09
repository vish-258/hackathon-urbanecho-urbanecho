#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUILD_DIRECTORY=${1:-"$HERE/../../../../work/firmware-core-tests"}
mkdir -p "$BUILD_DIRECTORY"
COMPILER=${CXX:-c++}
"$COMPILER" -std=c++17 -Wall -Wextra -Wpedantic -Werror -O2 \
  -I"$HERE/include" "$HERE/tests/test_device_core.cpp" -o "$BUILD_DIRECTORY/test-device-core"
"$BUILD_DIRECTORY/test-device-core" --write-fixture "$BUILD_DIRECTORY"
if [ -n "${PYTHON:-}" ]; then
  "$PYTHON" "$HERE/tests/verify_backend_contract.py" "$BUILD_DIRECTORY"
else
  printf '%s\n' 'Set PYTHON to the project Python interpreter to also run backend-contract validation.'
fi
