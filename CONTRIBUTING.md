## Contributing to Fox Telegram Summarizer Bot

Thank you for considering contributing to this project! Contributions of all kinds are welcome: bug reports, feature requests, documentation improvements, and code changes.

### How to get started

1. **Fork** the repository to your GitHub account.
2. **Create a branch** for your change:

```bash
git checkout -b feature/short-description
```

3. **Set up the project locally** by following the steps in the `README.md` (installation, environment variables, running the bot).

### Reporting bugs

When opening a bug report, please include:

- Your OS and Python version.
- How you run the bot (local / Docker).
- Relevant configuration snippets (without secrets).
- Steps to reproduce the issue.
- Expected vs. actual behavior.

### Proposing features

For feature requests:

- Explain the problem you are trying to solve.
- Describe the proposed solution.
- Add examples of how the feature would be used.
- If possible, describe alternatives you considered.

### Coding guidelines

- Keep the code **simple, readable, and explicit**.
- Follow the existing **code style** and structure in the repo.
- Use **type hints** where it makes the behavior clearer.
- Avoid introducing new dependencies unless necessary; if you do, justify them in the PR description.

### Tests

- If you are changing summarization logic or critical behavior, please add or update tests in `test_summarizer.py`.
- Make sure all tests pass before submitting your PR:

```bash
python -m pytest
```

### Commit messages

- Use clear, descriptive commit messages, for example:
  - `fix: handle empty channels without errors`
  - `feat: add per-chat min_messages override`
  - `docs: improve README installation section`

### Opening a Pull Request

Before opening a PR:

1. Make sure your branch is up to date with `main`:

```bash
git fetch origin
git checkout main
git pull
git checkout feature/short-description
git rebase main
```

2. Run the bot or tests locally to verify your changes.
3. Open a Pull Request with:
   - A clear title (what the PR does).
   - A short description (what changed and why).
   - Any screenshots or logs that help understand the change.

Thank you for helping improve Fox Telegram Summarizer Bot!


