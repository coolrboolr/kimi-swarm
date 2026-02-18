#!/bin/bash
# Build script for Ambient Swarm sandbox images

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=================================="
echo "Ambient Swarm Sandbox Builder"
echo "=================================="
echo ""

# Parse arguments
BUILD_TYPE="${1:-full}"
if [ -n "${2:-}" ]; then
    TAG="$2"
else
    if command -v python3 >/dev/null 2>&1 && [ -f "$PROJECT_ROOT/scripts/get_version.py" ]; then
        TAG="$(python3 "$PROJECT_ROOT/scripts/get_version.py" 2>/dev/null || echo latest)"
    else
        TAG="latest"
    fi
fi

if [ "$BUILD_TYPE" = "minimal" ]; then
    DOCKERFILE="$SCRIPT_DIR/Dockerfile.minimal"
    IMAGE_NAME="ambient-sandbox-minimal:$TAG"
    echo "Building minimal sandbox image..."
elif [ "$BUILD_TYPE" = "full" ]; then
    DOCKERFILE="$SCRIPT_DIR/Dockerfile"
    IMAGE_NAME="ambient-sandbox:$TAG"
    echo "Building full sandbox image..."
else
    echo -e "${RED}Error: Invalid build type. Use 'full' or 'minimal'${NC}"
    exit 1
fi

echo "Image: $IMAGE_NAME"
echo "Dockerfile: $DOCKERFILE"
echo ""

# Build the image
echo "Building Docker image..."
docker build -f "$DOCKERFILE" -t "$IMAGE_NAME" "$PROJECT_ROOT"

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✓ Successfully built $IMAGE_NAME${NC}"
    echo ""
    echo "Image size:"
    docker images "$IMAGE_NAME" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
    echo ""
    echo "To use this image:"
    echo "  ambient watch /path/to/repo"
    echo ""
    echo "To test the image:"
    echo "  docker run --rm -it $IMAGE_NAME bash"
else
    echo -e "${RED}✗ Build failed${NC}"
    exit 1
fi
