# Verification record

Executed on 17 November 2026, Windows, Python 3.12. Runtime requirements from `requirements.txt`, including `argon2-cffi-bindings==25.1.0`. GitHub-hosted runs were unable to start; this record reports local execution only.

```text
python -m pytest -q
11 passed, 1 warning in 5.67s

python scripts/demo.py
opening_stock: 10
order_total_cents: 14997
replay_same_order: true
after_sale actual_stock / ledger_stock / difference: 7 / 7 / 0
movement_deltas: [10, -3, 3]
after_cancel actual_stock / ledger_stock / difference: 10 / 10 / 0
```

The warning is Starlette's deprecation notice for the httpx test client. It did not skip tests or replace the application. The generated [demo JSON](demo-result.json) is captured from the real application using a temporary SQLite database. No tokens or passwords are included.

Coverage in this run: existing authentication/ownership/order scenarios, stock adjustment, movement history, pagination, multi-line rollback, cancellation replay, shipment behavior, and external-drift reconciliation. No load, PostgreSQL or production-environment tests were performed.
