# Security Policy

This document outlines the security protocols and vulnerability reporting guidelines for the Plane MCP Server project. Ensuring the security of our systems is a top priority, and while we work diligently to maintain robust protection, vulnerabilities may still occur. We highly value the community's role in identifying and reporting security concerns to uphold the integrity of our systems and safeguard our users.

## Reporting a Vulnerability

If you have identified a security vulnerability, you can report it through either of the following channels:

1. **Email**: Submit your findings to [security@plane.so](mailto:security@plane.so).
2. **GitHub Security Advisory**: Open a private security advisory at [https://github.com/makeplane/plane-mcp-server/security/advisories/new](https://github.com/makeplane/plane-mcp-server/security/advisories/new).

Ensure your report includes all relevant information needed for us to reproduce and assess the issue. Include the IP address or URL of the affected system.

To ensure a responsible and effective disclosure process, please adhere to the following:

- Maintain confidentiality and refrain from publicly disclosing the vulnerability until we have had the opportunity to investigate and address the issue.
- Refrain from running automated vulnerability scans on our infrastructure or dashboard without prior consent. Contact us to set up a sandbox environment if necessary.
- Do not exploit any discovered vulnerabilities for malicious purposes, such as accessing or altering user data.
- Do not engage in physical security attacks, social engineering, distributed denial of service (DDoS) attacks, spam campaigns, or attacks on third-party applications as part of your vulnerability testing.

## Out of Scope

While we appreciate all efforts to assist in improving our security, please note that the following types of vulnerabilities are considered out of scope:

- Vulnerabilities requiring man-in-the-middle (MITM) attacks or physical access to a user's device.
- Content spoofing or text injection issues without a clear attack vector or the ability to modify HTML/CSS.
- Issues related to email spoofing.
- Missing DNSSEC, CAA, or CSP headers.
- Absence of secure or HTTP-only flags on non-sensitive cookies.

## Our Commitment

At Plane, we are committed to maintaining transparent and collaborative communication throughout the vulnerability resolution process. Here's what you can expect from us:

- **Response Time**
  We will acknowledge receipt of your vulnerability report within three business days and provide an estimated timeline for resolution.

- **Legal Protection**
  We will not initiate legal action against you for reporting vulnerabilities, provided you adhere to the reporting guidelines.

- **Confidentiality**
  Your report will be treated with confidentiality. We will not disclose your personal information to third parties without your consent.

- **Recognition**
  With your permission, we are happy to publicly acknowledge your contribution to improving our security once the issue is resolved.

- **Timely Resolution**
  We are committed to working closely with you throughout the resolution process, providing timely updates as necessary. Our goal is to address all reported vulnerabilities swiftly, and we will actively engage with you to coordinate a responsible disclosure once the issue is fully resolved.

We appreciate your help in ensuring the security of our platform. Your contributions are crucial to protecting our users and maintaining a secure environment. Thank you for working with us to keep Plane safe.
