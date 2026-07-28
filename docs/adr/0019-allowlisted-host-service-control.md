# ADR 0019: Allowlisted host service control

## Status

Accepted.

## Context

The portal already reports Docker and workstation service health, but the
right context panel can be collapsed and a browser cannot safely run host
commands. Operators need one visible place to inspect and control previously
registered repositories, databases, ComfyUI, and AI-Toolkit. New services must
not become controllable through discovery alone.

## Decision

Run a small Windows host manager on loopback port `8791`. Its registry is an
operator-owned file under `C:\Docker\local-knowledge-portal\config`; it accepts
only validated service IDs and these fixed handler types:

- registered Docker Compose projects;
- registered WSL user services;
- registered GPUQ workloads;
- health-only HTTP processes;
- protected read-only services.

The browser calls only the portal API. The API authenticates to the host
manager with a server-side bearer token and proxies only status, start, and
stop for an allowlisted ID. It never returns that host token, command
arguments, or registry paths to the browser.

Start and stop use a two-request confirmation challenge. The first request
issues a random, 90-second, one-time token bound to the exact service ID and
action. The second request must include that token and `{"confirmed": true}`.
Tokens are consumed before the host request, cannot be replayed for another
service or action, and the in-memory token set is bounded. Both endpoints
require a permitted loopback UI origin. The dialog initially focuses
**Cancel**; opening or dismissing it never sends the control request.

The portal cannot stop itself. Ollama and the GPU scheduler are protected
health-only dependencies. ComfyUI starts through GPUQ and refuses a stop while
its own queue contains running or pending work. If its HTTP health endpoint
responds but GPUQ has no matching job, the manager reports `unmanaged` instead
of incorrectly reporting normal managed execution. It then permits only the
registered fallback stop helper. That helper verifies an empty ComfyUI queue,
the exact loopback listener, the configured
`E:\AI\Apps\ComfyUI\main.py` command line, port, and process owner before
stopping that one process. It never kills by process name. No endpoint accepts
arbitrary commands, paths, URLs, service names, or compose files.

The manager is installed as the per-user scheduled task
`\Codex\Local Knowledge Service Manager` and restarts after failure. Registry
generation occurs only on first install or an explicit `-RefreshRegistry`;
routine discovery cannot grant control to a newly found service.

Status probes use a short timeout. Mutating requests use a separate 150-second
proxy timeout because a registered service may need its bounded graceful-stop
window before returning. This prevents a completed host stop from being
misreported as a portal timeout.

## Consequences

- The left navigation can show a permanent aggregate health indicator and a
  dedicated service-management page without exposing host credentials.
- Existing registered services can be operated after a confirmation step.
- A UI focus transition or stale/replayed request cannot perform a control
  action without a fresh server-side challenge.
- ComfyUI started outside GPUQ remains visible and can be safely stopped
  without pretending that an absent scheduler job is healthy.
- Adding a new service requires a reviewed registry change requested by the
  operator.
- The manager is a separate localhost dependency; read-only portal health can
  still show its failure when control is unavailable.
