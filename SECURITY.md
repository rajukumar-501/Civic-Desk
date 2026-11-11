# Security policy

This is an educational prototype. Do not use it to process sensitive institutional complaints without a formal security and privacy review.

- Do not commit `.env`, SQLite database files, real complaint records, credentials, or model artifacts.
- Passwords are hashed; CSRF protection and role-based access checks are enabled.
- Rotate/revoke any credential ever exposed in source code or Git history.
- Load only trusted joblib model artifacts.
- For deployment, use HTTPS, secure cookies, backups, access monitoring, rate limiting, retention/deletion controls, and a privacy notice.
- Report vulnerabilities privately to the repository maintainer rather than opening a public issue with exploit details.
