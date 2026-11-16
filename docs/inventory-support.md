# Inventory traceability and support

This is a personal learning project for Backend Python / ERP Support internships. It is a sales and inventory module, not a full ERP, client delivery or production deployment.

## Business rules

Every successful inventory mutation writes a `stock_movements` entry in the same transaction: product creation records opening quantity (including zero), manual adjustment records delta, order placement records negative quantities, and cancellation records a positive reversal. Shipment records no second deduction: stock was deducted when the order was placed.

Each movement contains product, actor, optional order, type, quantity and timestamp. The API offers no update/delete movement endpoints. This is application-level traceability, not a tamper-proof accounting ledger: someone with database write access can change it.

`GET /inventory/movements?product_id=1&limit=20&offset=0` lists recent movement rows; `GET /inventory/reconciliation?limit=20&offset=0` compares actual stock with the sum of ledger deltas. Both require an admin token. A nonzero difference is a support investigation signal and never triggers an automatic stock adjustment. Each response is paginated; clients must read all pages for a complete report.

## Reproduce the workflow

Install `requirements-dev.txt`, then run `python scripts/demo.py`. This creates an isolated temporary SQLite database with random demo credentials, performs requests through the real ASGI application, prints a report without secrets, disposes the engine and removes the demo database. It does not contact a deployed service.

Expected quantities: opening=10, sale=-3, replay creates no movement, current=7; cancellation=+3, final=10, difference=0. Price snapshot: 3 x 4999 = 14997 cents. CI runs this same script and uploads its output with the JUnit test report.

## Acceptance coverage

| Risk | Automated check in `tests/test_inventory.py` |
| --- | --- |
| Retry deducts twice | Sale/replay/cancel lifecycle test |
| Partial order leaves changed inventory | Multi-line failure verifies stock, order and movement rollback |
| Failed adjustment adds audit history | Adjustment test checks accepted and rejected quantities |
| Customer sees warehouse reports | Admin-only report tests |
| Support sees incomplete list | Product filter, offset and limit tests |
| External data edit goes undetected | Injected stock drift appears as difference +6 |
| Shipping deducts again | Shipment test verifies only opening and sale rows |

These are automated acceptance checks; no external business-user sign-off is claimed.

## Existing demo databases

Version 1.1 adds a ledger table. `create_all` does not backfill movement history for older products. Use a fresh, separately named local demo database for the walkthrough. An old database can show differences due to absent opening entries; do not fabricate historical movements or silently reset stock. A real upgrade needs an approved baseline/migration plan.

## Limits

SQLite single-process demonstration; no verified high-load/concurrency guarantee. Conditional updates and transactions are implemented, but PostgreSQL concurrency tests, migrations, login rate limiting, managed secrets, monitoring and backup procedures are needed before production use. There is no stock receiving/purchasing module, tax, invoicing or accounting integration.
