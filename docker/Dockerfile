# Ambient Swarm Sandbox - Isolated execution environment
#
# Includes all quality tools for verification:
# - pytest (Python testing)
# - ruff (Python linting and formatting)
# - mypy (Python type checking)
# - semgrep (Static analysis)
# - trivy (Security scanning)

FROM python:3.11-slim

# Set labels
LABEL maintainer="Ambient Swarm"
LABEL description="Sandboxed execution environment for code quality checks"
LABEL version="2.0.0"

# Set working directory
WORKDIR /repo

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python quality tools
RUN pip install --no-cache-dir \
    pytest>=7.4.0 \
    pytest-cov>=4.1.0 \
    pytest-asyncio>=0.21.0 \
    ruff>=0.1.0 \
    mypy>=1.5.0 \
    black>=23.0.0 \
    flake8>=6.0.0 \
    pylint>=3.0.0 \
    isort>=5.12.0 \
    coverage>=7.0.0 \
    bandit>=1.7.0

# Install semgrep for static analysis
RUN pip install --no-cache-dir semgrep>=1.45.0

# Install trivy for security scanning
RUN curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin

# Install additional language tools (optional, can be extended)
# Uncomment as needed for your projects

# Node.js and npm (for JavaScript/TypeScript projects)
# RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
#     && apt-get install -y nodejs \
#     && npm install -g eslint prettier typescript

# Go tools (for Go projects)
# RUN wget https://go.dev/dl/go1.21.0.linux-amd64.tar.gz \
#     && tar -C /usr/local -xzf go1.21.0.linux-amd64.tar.gz \
#     && rm go1.21.0.linux-amd64.tar.gz
# ENV PATH=$PATH:/usr/local/go/bin

# Rust and cargo (for Rust projects)
# RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
# ENV PATH="/root/.cargo/bin:${PATH}"

# Create non-root user for safety
RUN useradd -m -u 1000 -s /bin/bash ambient && \
    chown -R ambient:ambient /repo

# Switch to non-root user
USER ambient

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Verify installations
RUN python --version && \
    pytest --version && \
    ruff --version && \
    mypy --version && \
    semgrep --version && \
    trivy --version

# Default command (overridden by sandbox runner)
CMD ["/bin/bash"]
