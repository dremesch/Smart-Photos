# ADR-0009: Run on an owner-controlled Azure VM; agents never read photo data

- **Status:** Accepted
- **Date:** 2026-11-07
- **Spec reference:** v1.2 §3, §16; [CTO decisions](../team/decisions/2026-11-07-cto-decisions.md) C3, C6

## Context

The CTO does not want the curator running on a personal PC. Most of the work is done by Claude Code agents with little owner involvement. Two risks follow:

1. Photos move from a personal machine to a server.
2. Anything a Claude Code agent reads (file contents, images) is sent to the model as part of its context.

ADR-0001 (no cloud inference, no network path in the app) remains in force.

## Decision

- **Where it runs:** a Windows 11 VM in the **CTO's own Azure subscription**. Photos are synced or copied to the VM's encrypted data disk. They never leave the CTO's tenant through the app.
- **VM baseline:**
  - **Size and OS:** Windows 11 Pro or Enterprise x64, 8 vCPU and 32 GB RAM (minimum 4 vCPU and 16 GB). No GPU.
  - **Disk:** an encrypted SSD data disk of about 2× the event's photo size.
  - **Access:** no public RDP; access is through Azure Bastion or just-in-time access.
  - **Cost:** nightly auto-shutdown and a budget alert.
- **Network:** open during setup only (packages, model files, OneDrive). The app itself keeps its network guard; ADR-0001 is unchanged.
- **Agents never read photo data.** If Claude Code runs on the VM:
  - Project settings (`.claude/settings.json`) deny `Read` on image and video files, on OneDrive folders and on app data directories.
  - Agents run pipeline commands that print aggregate numbers only (D2).
  - Agents never open thumbnails, derivatives, crops or exports, and never upload them anywhere.
- **The UI is local:** the review UI runs on the VM desktop, or as a localhost-only web service reached through Bastion. No port is exposed publicly.

## Consequences

- The privacy boundary moves from "this PC" to "the CTO's tenant". Spec §16 is updated to say so.
- Deny rules only stop Claude Code's file-reading tools. A shell command could still read a photo. So the rule is also a non-negotiable in AGENTS.md, and the pipeline commands print aggregates only.
- OneDrive hydration on the VM is a non-issue once the event folder is fully local. Hydration safety (ADR-0004) still matters on any machine with Files On-Demand.
- Running costs are the VM hours and storage, controlled by auto-shutdown and the budget alert.

## Alternatives considered

| Option | Why not |
|---|---|
| CTO's personal PC | The CTO's explicit preference against it |
| Hyper-V VM at home | Viable and keeps photos off the cloud, but the CTO chose Azure |
| Azure Virtual Desktop or Windows 365 | More cost and setup than one VM for a single user |
| Let agents view photos to judge quality | Sends family photos to the model provider; violates the privacy intent |
