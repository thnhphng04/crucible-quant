---
description: Check the EN design docs still mirror the VI originals 1:1, and that no doc link is broken
---

1. Run `uv run python scripts/check_doc_mirror.py`.
2. If it prints `OK`, report that in one line and stop.
3. For each `FAIL`:
   - **Line count / heading mismatch** — `research_docs_vi/` is the source of truth. Diff the pair section by section (compare headings first, then the lines under the first heading that diverges) and find what changed in the Vietnamese file that the English file lacks, or vice versa. Propose the exact English edit that restores a 1:1 mirror (same line count, same headings, same tables/code blocks line-for-line). Never "fix" the Vietnamese side to match the English unless the user says the English is right.
   - **Broken link** — find the intended target (renamed file? EN vs VI filename?) and propose the corrected link.
4. Show the proposed edits and apply them only after the user agrees. Re-run the check afterwards.

Reply to the user in Vietnamese. $ARGUMENTS
