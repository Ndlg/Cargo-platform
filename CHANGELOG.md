# Changelog

## 0.2.0-rc.1

- Preserves the field collection-to-export workflow and removes manual order-row review.
- Keeps waybill parsing in the independent parser service.
- Adds tenant fingerprint field selection and isolated local-AI rule learning.
- Requires administrator-confirmed AI candidates to generate declarative, replay-validated rules.
- Keeps known declarative rules available when the local AI service is offline.
- Fixes corrected text waybills whose product follows attributes in the source text.
- Removes unreachable parser copies from the main backend.
- Publishes version-matched backend, tenant UI, admin UI, parser, and AI images.
