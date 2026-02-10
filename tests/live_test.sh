#!/bin/bash
# Live testing script for Ambient Swarm with Kimi K2.5

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}Ambient Swarm Live Testing Suite${NC}"
echo -e "${BLUE}======================================${NC}"
echo ""

# Configuration
FIXTURE_REPO="tests/fixtures/test_repo"
RESULTS_DIR="tests/live/results"

# Create results directory
mkdir -p "$RESULTS_DIR"

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

# Check Ollama
if ! command -v ollama &> /dev/null; then
    echo -e "${RED}✗ Ollama not found${NC}"
    echo "Install from: https://ollama.ai"
    exit 1
fi
echo -e "${GREEN}✓ Ollama installed${NC}"

# Check if Ollama is running
if ! curl -s http://localhost:11434/v1/models > /dev/null 2>&1; then
    echo -e "${RED}✗ Ollama not running${NC}"
    echo "Start with: ollama serve"
    exit 1
fi
echo -e "${GREEN}✓ Ollama running${NC}"

# Check for Kimi model
if ! ollama list | grep -q "kimi-k2.5:cloud"; then
    echo -e "${RED}✗ Kimi K2.5 model not found${NC}"
    echo "Install with: ollama pull kimi-k2.5:cloud"
    exit 1
fi
echo -e "${GREEN}✓ Kimi K2.5 model available${NC}"

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Docker installed${NC}"

# Check sandbox image
if ! docker images | grep -q "ambient-sandbox"; then
    echo -e "${YELLOW}! Sandbox image not found, building minimal image...${NC}"
    cd docker && ./build.sh minimal && cd ..
fi
echo -e "${GREEN}✓ Sandbox image available${NC}"

echo ""

# Test 1: Dry Run
echo -e "${BLUE}Test 1: Dry Run (No Changes Applied)${NC}"
echo -e "${YELLOW}Running: ambient run-once <temp_repo> --dry-run${NC}"
echo ""

TMP_REPO="$(mktemp -d)"
trap 'rm -rf "$TMP_REPO"' EXIT

# Build a clean git repo from the fixture files (do not rely on a checked-in .git directory).
rsync -a --exclude ".git" --exclude ".ambient" --exclude "__pycache__" "$FIXTURE_REPO/" "$TMP_REPO/"
git -C "$TMP_REPO" init -q
git -C "$TMP_REPO" config user.email "ambient@test.local"
git -C "$TMP_REPO" config user.name "Ambient Test"
git -C "$TMP_REPO" add -A
git -C "$TMP_REPO" commit -qm "fixture: init"

time ambient run-once "$TMP_REPO" --dry-run -o "$RESULTS_DIR/dry-run.json" 2>&1 | tee "$RESULTS_DIR/dry-run.log"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Test 1 passed${NC}"

    # Check results
    if [ -f "$RESULTS_DIR/dry-run.json" ]; then
        PROPOSALS=$(cat "$RESULTS_DIR/dry-run.json" | jq -r '.proposals_count')
        echo -e "${GREEN}Proposals generated: $PROPOSALS${NC}"
    fi
else
    echo -e "${RED}✗ Test 1 failed${NC}"
fi

echo ""
echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}Test Results${NC}"
echo -e "${BLUE}======================================${NC}"
echo ""

# Show summary
if [ -f "$RESULTS_DIR/dry-run.json" ]; then
    echo "Summary:"
    cat "$RESULTS_DIR/dry-run.json" | jq '.'
fi

echo ""
echo "Telemetry log:"
if [ -f "$TMP_REPO/.ambient/telemetry.jsonl" ]; then
    echo "Event count: $(wc -l < $TMP_REPO/.ambient/telemetry.jsonl)"
    echo ""
    echo "Events by type:"
    cat "$TMP_REPO/.ambient/telemetry.jsonl" | jq -r '.type' | sort | uniq -c
fi

echo ""
echo -e "${GREEN}Live testing complete!${NC}"
echo ""
echo "Results saved to: $RESULTS_DIR/"
echo "View full log: cat $RESULTS_DIR/dry-run.log"
echo "View telemetry: cat $TMP_REPO/.ambient/telemetry.jsonl | jq ."
