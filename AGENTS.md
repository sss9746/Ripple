# Ripple project instructions

## Project context

- Read `SPEC.md` before planning or implementing changes.
- Application code belongs in `ripple/`.
- The PostgreSQL schema lives in `sql/schema.sql`.
- Local infrastructure is defined in `docker-compose.yml`.
- Environment variable names are documented in `.env.example`.

## Development workflow

- Keep changes focused on the assigned task.
- Use the existing project structure and dependencies.
- Run `python -m pytest` after Python changes.
- Update tests when behavior changes.
- Explain any schema or dependency changes.

## Security

- Never commit `.env`.
- Never print or include secrets in responses, logs, or commits.
- Do not replace `.env.example` with real credentials.

## Git

- Do not modify unrelated files.
- Review `git diff` before committing.
- Make a descriptive commit after completing and testing a task.