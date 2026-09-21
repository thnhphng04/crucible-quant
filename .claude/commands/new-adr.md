---
description: Create the next numbered ADR in implement_docs/adr/ from the template
argument-hint: <short title of the decision>
---

Create an Architecture Decision Record for: $ARGUMENTS

1. List `implement_docs/adr/`, take the highest `NNNN` and add 1. File name: `NNNN-kebab-case-title.md`.
2. Copy the structure of `implement_docs/adr/0000-template.md`. Fill in Context / Decision / Consequences / Alternatives from the current conversation and code. Status `Proposed` unless the user has already agreed, then `Accepted`. Date = today.
3. Link the task (`P?-??`), the arch section, and the open item it closes, if any. If it closes an item in `implement_docs/05-OPEN-ITEMS.md`, move that row to "Closed" with a link to the ADR.
4. If the decision changes something the architecture states (schema, gate, layout), say so explicitly and list the arch edit needed — VI first (`research_docs_vi/`), then the EN mirror, then `/sync-docs`. Don't edit the architecture without the user's go-ahead.
5. Keep it short: an ADR is read in two minutes. Summarize to the user in Vietnamese.
