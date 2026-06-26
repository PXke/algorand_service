# Pera Flutter Auth (Feasibility Spike)

## Goal
Validate whether production-grade Pera wallet auth already exists for Flutter.

## Decision outcomes

1. **Existing package is sufficient**: integrate directly and contribute fixes upstream.
2. **Insufficient package coverage**: build and publish a dedicated open-source module.

## Acceptance criteria

- Connect/disconnect wallet session
- Sign backend nonce
- Works on Flutter Web (first target)
- Clear path for mobile support
