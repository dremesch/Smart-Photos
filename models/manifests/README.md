# Model manifests

One manifest per approved model. Weights are never committed; they are installed
through the explicit offline model-package workflow (spec v1.1 §16.2).

Each manifest records: model id and version, source URL, SHA-256 of the weights,
license and the review that approved it, input/output contract, and the
execution providers it was benchmarked on. A model without an approved manifest
may not be loaded (ADR-0007).
