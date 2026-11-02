# Security Review

Review date: 2026-11-02

## Scope

Repository security scan, with particular attention to custom alert URL handling.

## Findings

### Medium: Custom alert URLs can use unsafe schemes

- **Location:** `app.py:100, 797, 901-909`
- **Confidence:** 8/10
- **Details:** Custom alert URL values are accepted from the settings form and saved without validating their schemes. The map popup escapes these values for HTML attribute syntax but uses them as link destinations. HTML escaping does not prevent a `javascript:` URL from executing when a user clicks the link.
- **Impact:** A person able to add a custom alert could cause script to run in the map page's origin when another user follows the link.
- **Recommendation:** Validate URL schemes server-side and allow only `http` and `https` before storing or rendering custom alert links.
