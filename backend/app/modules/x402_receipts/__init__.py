"""x402 signed fulfillment receipts: the free read side.

Generation and signing live in modules/x402/receipts.py (shared, hooked into
paid_request.run_with_refund) and storage in modules/x402/receipt_store.py --
both stay under the core x402 package, never here, so paid_request.py (core)
never has to depend on a leaf product module. This module only exposes the
free `GET /api/v1/x402/receipts/{receipt_id}` read endpoint. See CLAUDE.md
section 9 and docs/x402-execution-trust-evaluation.md item 1.
"""
