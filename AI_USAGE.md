# AI Usage

This file documents the use of AI systems within the AssureX project.

Record relevant information here, including:

- AI models and providers used
- prompts or agent workflows that materially affect outputs
- AI-assisted development or generated artifacts
- model evaluation and review procedures
- human-review requirements
- limitations, risks, and governance decisions
- data privacy and security considerations

Update this file as AI functionality is added or changed.

## Phase 2 declaration (2026-09-26)

- **Tool:** OpenAI Codex in ChatGPT Work.
- **Purpose and modules:** Assisted implementation of the SQLAlchemy ORM, Alembic migration, schemas, seed fixture, tests, and database documentation in `backend/`, `tests/`, `documentation/`, and configuration files.
- **Human team review needed:** Understand each model and constraint; inspect the generated migration and modify as appropriate for production; validate PostgreSQL deployment and follow-on API integration. The seed model versions are placeholders, not trained models.
- **Checks performed during generation:** Migration up/down/up on an isolated SQLite database, automated database tests, schema inspection, and import checks. Outcomes are reported in the implementation summary; this does not establish ML accuracy or production uptime.

## Policy and report deliverables declaration (2026-09-28)

- **Tool:** OpenAI ChatGPT.
- **Purpose of use:** Assisted in mapping the AssureX SRS policy/report requirements to the existing repository, drafting three configurable warranty-policy deliverables, and drafting evidence-based project/model reports.
- **Prompt/type of assistance:** Review the supplied AssureX SRS and add the required policy and reporting deliverables based on the current project implementation.
- **Files/modules affected:** `policies/*.json`, `policies/README.md`, `reports/*.md`, and this declaration.
- **Modifications performed by the team:** The team must review business-rule values, confirm policy assumptions, complete the missing paired GTM unseen-test results, and revise any wording or configuration that does not match the final implementation.
- **Testing/verification completed:** Policy JSON files were syntax-validated after creation; report metrics were taken from committed notebook CSV evidence rather than invented. Remaining compliance gaps are listed in `reports/requirements_traceability.md`.
- **Human verifier:** Pending team completion before final submission.

This declaration does not treat AI-generated material as automatically approved project evidence. Final submission requires team review, understanding, testing, and sign-off.
