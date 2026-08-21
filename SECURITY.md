# Security Policy

finding-ledger operates on local markdown/YAML files and makes no network calls.
The main risk surface is parsing untrusted files (YAML cases, ledger markdown).

- YAML is loaded exclusively with `yaml.safe_load`.
- If you find a vulnerability, please report it privately via GitHub Security
  Advisories ("Report a vulnerability" on the repository page) rather than a
  public issue. Expect an initial response within 7 days.
