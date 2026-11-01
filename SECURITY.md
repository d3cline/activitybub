# Reporting security issues

Read [the 0.3.2 security review](docs/SECURITY-AUDIT.md) for verified protections,
the image-processing boundary, platform limits, and remaining risks. This app
has not received an independent penetration test or security certification.

For a vulnerability, use the repository's private security advisory feature if
enabled. Avoid posting private URLs, credentials, local database files, or other
users' post content in a public issue. If private reporting is unavailable,
open a minimal issue requesting a private contact without exploit details.

Activity Bub reads public APIs and untrusted image data. Reports about private-IP
access, unsafe redirects, parser/resource limits, visibility handling, or embedded
content rendering are especially useful. Include the version, platform, and a
minimal synthetic reproduction where possible.

There is no published long-term support window. Check the latest release before
reporting; updates are delivered through repository releases, not automatic
downloads inside the app.
