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
