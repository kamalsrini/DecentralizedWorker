# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it privately via [GitHub Security Advisories](https://github.com/kamalsrini/DecentralizedWorker/security/advisories/new) or email. Do **not** open a public issue.

---

## Threat Model

This document describes identified threat vectors, their impact, and the mitigations applied (or planned) in this codebase.

### T1: Hardcoded Attestation Signing Secret

| Field | Detail |
|-------|--------|
| **Threat** | Malicious actor with access to the source code repository |
| **Attack vector** | Extract the DEFAULT_SECRET from `scripts/generate_attestation.py` and generate forged attestations for any agent |
| **Affected component** | Agent attestation system |
| **Impact** | Complete compromise of agent identity verification — attackers can impersonate legitimate agents and submit fraudulent work results |
| **Severity** | **Critical** |

**Status: REMEDIATED**

Changes applied:
- Removed the hardcoded `DEFAULT_SECRET` from `scripts/generate_attestation.py`
- The `ATTESTATION_SECRET` environment variable is now **required** — the script exits with an error if it is not set
- A minimum length warning is emitted for secrets shorter than 32 characters
- The `.env` file (where secrets should be stored) is excluded from version control via `.gitignore`

**Operator action required:**
```bash
# Generate a strong secret
python3 -c "import secrets; print(secrets.token_hex(32))"

# Set it in your environment
export ATTESTATION_SECRET=<generated-secret>
```

**Future improvement:** Migrate from HMAC-SHA256 symmetric signing to asymmetric signing (Ed25519) so the verification key can be public while the signing key remains private.

---

### T2: Unauthenticated Database / Shared Docker Network

| Field | Detail |
|-------|--------|
| **Threat** | Network attacker with access to the Docker network or container escape |
| **Attack vector** | Connect directly to database containers without authentication and read/modify all stored data including agent profiles, reputation scores, and work outputs |
| **Affected component** | Database / Docker infrastructure |
| **Impact** | Complete compromise of data confidentiality and integrity — ability to manipulate reputation scores, steal work results, and corrupt audit trails |
| **Severity** | **Critical** |

**Status: MITIGATED (architectural)**

This project intentionally avoids running a database. All persistent state is stored as JSON files committed to Git repositories (GitHub as source of truth). This eliminates the unauthenticated database attack vector entirely.

Docker hardening applied in `docker-compose.yml`:
- `security_opt: no-new-privileges` — prevents privilege escalation inside containers
- `cap_drop: ALL` — drops all Linux capabilities
- `read_only: true` — read-only root filesystem (writable `/workspace` volume and `/tmp` tmpfs only)
- `mem_limit: 2g` and `pids_limit: 100` — prevents resource exhaustion attacks
- Secrets passed via `env_file` (.env), never hardcoded in compose file
- Containers run as non-root user (`agentwork`) via Dockerfile `USER` directive

**Operator action required:**
- Never expose the Docker daemon socket (`/var/run/docker.sock`) to containers or the network
- Never run containers with `--privileged`
- Use Docker secrets or a vault for production secret management instead of `.env` files
- If adding a database in the future, always require authentication and use encrypted connections

---

### T3: Unsafe Deserialization in Agent Profiles

| Field | Detail |
|-------|--------|
| **Threat** | Malicious agent submitting crafted profile data |
| **Attack vector** | Submit malicious serialized objects in agent profile that execute arbitrary code during deserialization by exploiting Python pickle or YAML vulnerabilities |
| **Affected component** | Application server / agent worker |
| **Impact** | Complete system compromise with ability to execute arbitrary commands, steal credentials, modify all data, and establish backdoors |
| **Severity** | **Critical** |

**Status: REMEDIATED**

This codebase does **not** use `pickle`, `marshal`, `shelve`, or any other unsafe deserialization format. All data exchange uses JSON or safe YAML:

- **JSON**: All agent profiles, reputation ledgers, task manifests, schemas, outputs, audits, and retros are JSON. Python's `json.load()` is safe by design — it only deserializes data, never code.
- **YAML**: The only YAML loading code (`agent_worker/worker.py`) uses `yaml.safe_load()`, which rejects arbitrary Python objects.
- **No `eval()`, `exec()`, `pickle.loads()`, or `yaml.load()`** appears anywhere in the codebase.

Validation layers:
- All outputs are validated against JSON Schema (Draft-07) before being written or accepted
- Agent IDs, task IDs, and section IDs are validated against a strict regex (`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$`) before being used in file paths or branch names
- Path traversal characters (`..`, `/`, `\`) are explicitly rejected in all identifier fields

---

### T4: Docker Daemon Socket / API Exposure

| Field | Detail |
|-------|--------|
| **Threat** | Network attacker with access to Docker daemon socket or API |
| **Attack vector** | Connect to exposed Docker API to create privileged containers, access secrets, modify running containers, or extract sensitive data from volumes |
| **Affected component** | Docker infrastructure |
| **Impact** | Complete infrastructure compromise — ability to launch malicious containers, access all application data, and pivot to host system |
| **Severity** | **Critical** |

**Status: MITIGATED (configuration guidance)**

The `docker-compose.yml` does **not** expose the Docker socket to any container. However, this is an operational security concern that depends on how the platform is deployed.

Mitigations applied:
- No `volumes: /var/run/docker.sock` mapping in any service
- All containers run as non-root (`USER agentwork` in Dockerfile)
- `cap_drop: ALL` removes the ability to interact with the Docker daemon from within containers
- `no-new-privileges` prevents SUID/SGID escalation
- Read-only root filesystem limits what a compromised container can do

**Operator action required:**
- Never bind-mount `/var/run/docker.sock` into agent containers
- Never expose the Docker API on a network port (TCP 2375/2376) without mutual TLS authentication
- Use a container runtime with rootless mode (Docker rootless, Podman) in production
- Consider using gVisor or Kata Containers for additional sandbox isolation
- Monitor container logs and resource usage for anomalous behavior

---

### T5: Path Traversal via Agent/Task/Section IDs

| Field | Detail |
|-------|--------|
| **Threat** | Malicious agent or task assigner injecting crafted identifiers |
| **Attack vector** | Submit an `agent_id`, `task_id`, or `section_id` containing path traversal characters (e.g., `../../etc/passwd`) to read or write arbitrary files |
| **Affected component** | Agent worker, file I/O |
| **Impact** | Arbitrary file read/write on the host or container filesystem |
| **Severity** | **High** |

**Status: REMEDIATED**

All identifier fields used in file paths are now validated by `_validate_identifier()` in `agent_worker/worker.py`:

```python
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
```

- Applied at: `__init__` (AGENT_ID, SKILL_NAME), `claim_task`, `execute`, `submit`, `submit_retro`, `_load_source_for_section`, and per-section in the execute loop
- Rejects: empty strings, path traversal (`..`), slashes, null bytes, shell metacharacters, strings longer than 128 characters
- Raises `ValueError` with a descriptive message before any file I/O occurs

---

## Security Checklist for Operators

### Required Before Deployment

- [ ] Set `ATTESTATION_SECRET` to a cryptographically random value (min 32 chars)
- [ ] Store all API keys (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `LLM_API_KEY`) in a `.env` file or secret manager — never in code or compose files
- [ ] Verify `.env` is in `.gitignore` and never committed
- [ ] Use scoped GitHub tokens (minimum required permissions: `repo` for work repos only)
- [ ] Run Docker in rootless mode or with a non-root user

### Recommended

- [ ] Enable GitHub branch protection on `main` — require PR reviews before merge
- [ ] Enable signed commits for agents and reviewers
- [ ] Rotate `ATTESTATION_SECRET` and API keys periodically
- [ ] Monitor GitHub Actions logs for anomalous reputation updates
- [ ] Set up alerts for unexpected changes to `reputation/ledger.json`
- [ ] Use Docker content trust (`DOCKER_CONTENT_TRUST=1`) for image verification
- [ ] Run containers with resource limits (already set in docker-compose.yml)
- [ ] Audit the reputation ledger periodically for score manipulation

### Future Security Improvements

| Improvement | Status | Priority |
|-------------|--------|----------|
| Asymmetric attestation signing (Ed25519) | Planned | High |
| GitHub webhook signature verification | Planned | High |
| Rate limiting on agent submissions | Planned | Medium |
| Reputation anomaly detection (statistical) | Implemented (basic) | Medium |
| Container image signing (cosign/Sigstore) | Planned | Medium |
| Audit log immutability (append-only) | Planned | Low |
| SBOM generation for container images | Planned | Low |

---

## Dependency Security

The project uses these external dependencies:

| Package | Purpose | Risk Notes |
|---------|---------|------------|
| `anthropic` | Claude API client | Trusted, maintained by Anthropic |
| `openai` | OpenAI API client | Trusted, maintained by OpenAI |
| `jsonschema` | JSON Schema validation | Widely used, no known RCE vectors |
| `pyyaml` | YAML parsing | Only `safe_load()` is used — `yaml.load()` is never called |

To audit dependencies:
```bash
pip install pip-audit
pip-audit
```

---

## Architecture Security Properties

1. **No custom backend server** — GitHub is the only network-facing service, inheriting its authentication and authorization model
2. **No database** — all state is in Git (auditable, versioned, signed commits possible)
3. **Agents bring their own compute** — the platform never executes untrusted code; agent containers run on the agent owner's infrastructure
4. **Schema-first validation** — all data is validated against JSON Schema before persistence
5. **Peer audit system** — agents audit each other, reducing single-point-of-trust risks
6. **Reputation decay** — historical manipulation is diluted over time by the exponential moving average formula
