# ADR-0001: Local-first, no cloud inference

- **Status:** Accepted
- **Date:** 2026-11-07
- **Spec reference:** v1.1 §1, §16

## Context

The collection contains children's and family photographs. The user wants zero recurring cost. Cloud vision services, including Azure AI Vision's tagging and smart-crop, would be faster to integrate but require uploading images or image-derived data.

## Decision

All analysis runs on the user's machine. The application contains no network code path: no cloud APIs, no Microsoft Graph, no telemetry. OneDrive is used only through files the OneDrive client has already synced locally. Model packages are installed through a separate, explicit, offline workflow.

## Consequences

- Privacy and cost guarantees are simple to state and to test.
- Model quality is limited to what runs acceptably on local hardware.
- Enforcement is layered: architecture test (no network imports), runtime network guard, optional firewall rule, and blocked-network end-to-end tests.

## Alternatives considered

| Option | Why not |
|---|---|
| Azure AI Vision / cloud multimodal models | Violates the privacy guarantee; recurring cost |
| Microsoft Graph for OneDrive access | Network dependency and OAuth; unnecessary when files are synced locally |
| Optional cloud fallback | Weakens a guarantee that must be absolute |
