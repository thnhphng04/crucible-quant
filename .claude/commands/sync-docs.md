---
description: Check translated docs still mirror their originals 1:1 (research_docs, implement_docs), and that no doc link is broken
---

1. Run `uv run python scripts/check_doc_mirror.py`.
2. If it prints `OK`, report that in one line and stop.
3. For each `FAIL`:
   - **Line count / heading mismatch** — know which side is the original: for `research_docs*` it is `research_docs_vi/` (Vietnamese); for `implement_docs*` it is `implement_docs/` (English). The steps below describe the research pair; for the implement pair swap the languages.
     `research_docs_vi/` is the source of truth. Diff the pair section by section (compare headings first, then the lines under the first heading that diverges) and find what changed in the Vietnamese file that the English file lacks, or vice versa. Propose the exact English edit that restores a 1:1 mirror (same line count, same headings, same tables/code blocks line-for-line). Never "fix" the Vietnamese side to match the English unless the user says the English is right.
   - **Broken link** — find the intended target (renamed file? EN vs VI filename?) and propose the corrected link.
4. Show the proposed edits and apply them only after the user agrees. Re-run the check afterwards.

Reply to the user in Vietnamese. $ARGUMENTS
