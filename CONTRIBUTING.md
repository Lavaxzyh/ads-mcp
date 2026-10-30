# Maintaining the ADS 2025 branch

This work targets `feature/power-amplifier`. Keep upstream attribution and the MIT license. Use a separate local checkout for release preparation; do not copy a working ADS installation, virtualenv, private model library or runtime directory into Git.

1. Read README.zh-CN.md, SINGLE-SCHEMATIC.zh-CN.md and docs/PRIVACY.zh-CN.md.
2. Use the Python interpreter matching the installed ADS release. Set HPEESOF_DIR and keep outputs outside the checkout.
3. Run `python -B tools/run_unit_tests.py`. Unit tests do not prove native ADS compatibility.
4. Validate changed native paths in a fresh, independent test workspace. Never use a user's original PA project as the test fixture. Keep SP, EM, HB and measured evidence distinct.
5. Review `git diff --check`, `git diff --stat`, staged filenames and commit author/email. Review generated outputs separately before sharing them.
6. Submit a PR with **base `Lavaxzyh/ads-mcp:feature/power-amplifier`**. Do not target main unless agreed with the upstream maintainer. An upstream branch does not itself grant push permission; use a personal fork when necessary.

Describe actual conditions and limits. Preserve verified engineering inputs, reference planes, topology and port conditions. A tool name being retained does not prove every ADS release has been tested.
