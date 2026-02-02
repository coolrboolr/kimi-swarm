# Ambient Swarm Sandbox Docker Images

Sandboxed execution environment for running verification checks safely.

## Quick Start

### Build Full Image

```bash
cd docker
./build.sh full
```

This builds `ambient-sandbox:latest` with all tools:
- pytest, pytest-cov, pytest-asyncio
- ruff, mypy, black, flake8, pylint
- semgrep (static analysis)
- trivy (security scanning)

### Build Minimal Image (Faster)

```bash
./build.sh minimal
```

This builds `ambient-sandbox-minimal:latest` with only:
- pytest
- ruff
- mypy

Useful for development/testing when you don't need all tools.

## Usage

The sandbox is used automatically by the `ambient` CLI:

```bash
# Uses ambient-sandbox:latest by default
ambient verify /path/to/repo

# Or specify in config (.ambient.yml)
sandbox:
  image: ambient-sandbox-minimal:latest
```

## Manual Testing

Test the sandbox interactively:

```bash
# Full image
docker run --rm -it ambient-sandbox:latest bash

# Minimal image
docker run --rm -it ambient-sandbox-minimal:latest bash
```

Inside the container:
```bash
python --version    # Python 3.11
pytest --version    # pytest 7.4+
ruff --version      # ruff 0.1+
mypy --version      # mypy 1.5+
semgrep --version   # semgrep 1.45+ (full image only)
trivy --version     # trivy (full image only)
```

## Security Features

The sandbox enforces multiple security layers:

### Network Isolation
```bash
--network none  # No network access by default
```

### Resource Limits
```bash
--memory 2g           # 2GB memory limit
--cpus 2.0            # 2 CPU cores
--pids-limit 100      # Max 100 processes
```

### Non-Root User
The sandbox runs as user `ambient` (UID 1000), not root.

### Read-Only Mounts
Repository is mounted read-write at `/repo`, but other paths are read-only.

### Command Allowlist
Only whitelisted commands can be executed (enforced by ambient).

## Customization

### Adding Language Support

Edit `Dockerfile` to add support for other languages:

```dockerfile
# Node.js
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g eslint prettier

# Go
RUN wget https://go.dev/dl/go1.21.0.linux-amd64.tar.gz \
    && tar -C /usr/local -xzf go1.21.0.linux-amd64.tar.gz
ENV PATH=$PATH:/usr/local/go/bin

# Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
```

Then rebuild:
```bash
./build.sh full
```

### Custom Tools

Add project-specific tools to `Dockerfile`:

```dockerfile
# Install custom linter
RUN pip install --no-cache-dir your-custom-linter

# Install custom security scanner
RUN curl -o /usr/local/bin/scanner https://example.com/scanner \
    && chmod +x /usr/local/bin/scanner
```

## Image Sizes

Approximate sizes:
- **Minimal**: ~400MB (fast build, essential tools)
- **Full**: ~800MB (all tools included)

## Troubleshooting

### Build Fails

```bash
# Clean build cache
docker system prune -a

# Rebuild without cache
docker build --no-cache -f docker/Dockerfile -t ambient-sandbox:latest .
```

### Container Can't Access Files

Check file permissions:
```bash
# Ensure files are readable
chmod -R +r /path/to/repo

# Or run as root (not recommended)
docker run --user root ...
```

### Network Access Needed

Some tools need network (e.g., downloading dependencies):

```yaml
# In .ambient.yml
sandbox:
  network_mode: bridge  # Allow network access
```

**Warning**: Only enable network in trusted environments!

## Development

### Test Changes

```bash
# Build test image
docker build -f docker/Dockerfile -t ambient-sandbox:test .

# Test interactively
docker run --rm -it -v $PWD:/repo ambient-sandbox:test bash

# Inside container
cd /repo
pytest
ruff check .
mypy .
```

### Debugging

Enable debug output:
```bash
docker run --rm -it \
  -v $PWD:/repo \
  -e PYTHONVERBOSE=1 \
  ambient-sandbox:latest \
  bash -x -c "pytest -vv"
```

## CI/CD Integration

### GitHub Actions

```yaml
- name: Build sandbox
  run: |
    cd docker
    ./build.sh full

- name: Run checks
  run: ambient verify .
```

### GitLab CI

```yaml
build_sandbox:
  script:
    - cd docker
    - ./build.sh full
    - docker save ambient-sandbox:latest > sandbox.tar
  artifacts:
    paths:
      - sandbox.tar

verify:
  script:
    - docker load < sandbox.tar
    - ambient verify .
```

## Pre-built Images

For convenience, pre-built images are available:

```bash
# Pull from registry (if available)
docker pull ghcr.io/you/ambient-sandbox:latest

# Tag for local use
docker tag ghcr.io/you/ambient-sandbox:latest ambient-sandbox:latest
```

## License

MIT
