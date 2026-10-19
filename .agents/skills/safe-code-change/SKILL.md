# Safe Code Change

Use this skill before changing any production code-bearing element, embedded script, estimator/calculator, tracking script, form logic, webhook logic, automation code, Elementor HTML widget, or other executable content.

## Mandatory sequence
1. Identify the exact production object and code-bearing field.
2. Capture the exact current working source before any write.
3. Store the backup persistently in version control with a timestamp or immutable reference.
4. Validate the backup can be decoded/read and contains the expected application marker.
5. Record the backup reference in the write operation.
6. Make the smallest isolated change. Never mix application-code changes with SEO/content edits.
7. Read back the stored source and compare it to the intended change.
8. Run the smallest functional test that proves the affected workflow, including end-to-end lead submission for the BTSA estimator.
9. If verification fails, restore the exact backup before doing anything else.
10. Never purge a cache that is preserving a known-working production version until the stored source has been verified safe.

## Hard rule
No persistent backup reference = no production code write. Diagnosis and recommendations may proceed read-only.

## BTSA estimator
Treat the quote estimator as protected revenue infrastructure. SEO/performance automation may audit it but must not rewrite, transpile, minify, compile, replace dependencies, or alter submission/pricing/attribution logic without a separately backed-up and functionally tested change.
