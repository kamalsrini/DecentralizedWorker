FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update && apt-get install -y --no-install-recommends gh && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir jsonschema pyyaml anthropic openai

# Run as non-root user
RUN groupadd -r agentwork && useradd -r -g agentwork -m agentwork
COPY --chown=agentwork:agentwork agent_worker/ /app/agent_worker/
COPY --chown=agentwork:agentwork skills/ /app/skills/
WORKDIR /app
USER agentwork

# Drop all capabilities, read-only filesystem except /workspace
ENTRYPOINT ["python", "-m", "agent_worker"]
