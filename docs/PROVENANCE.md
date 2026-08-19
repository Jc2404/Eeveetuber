# Source provenance policy

Architecture ideas do not require per-file attribution, but directly copied or adapted code does.

Any Eeveetuber source file that directly borrows implementation code must begin with a short
comment containing:

1. the upstream project and license;
2. the pinned revision or release;
3. the exact upstream file path or URL;
4. a one-line summary of the adaptation.

For Open-LLM-VTuber, use this shape:

```python
# Source provenance: adapted from Open-LLM-VTuber v1.2.1 (commit 3afa410), MIT.
# Upstream: src/open_llm_vtuber/<path-to-file.py>
# Adaptation: <what was retained and what Eeveetuber changed>.
```

Keep the upstream license and copyright notice when the borrowed portion is substantial, and add
the dependency to `THIRD_PARTY_NOTICES.md`. Live2D sample models are separately licensed and must
never be treated as ordinary MIT project code.

Clean-room implementations based only on documented behavior or public interfaces should instead
describe the conceptual reference in an ADR or module docstring; they must not falsely claim copied
source provenance.

