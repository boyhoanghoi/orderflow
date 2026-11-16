"""Business acceptance checks for inventory traceability and reconciliation."""
import pytest
from test_api import client, setup_shop


def place(client, buyer, pid, qty=1, key="inventory-order-001"):
    return client.post('/orders', json={'items': [{'product_id': pid, 'quantity': qty}]},
                       headers={**buyer, 'Idempotency-Key': key})


def test_ledger_and_balance_through_sale_and_cancellation(client):
    admin, buyer, pid = setup_shop(client)
    first = place(client, buyer, pid, 2)
    assert first.status_code == 201
    assert place(client, buyer, pid, 2).json()['id'] == first.json()['id']
    before = client.get('/inventory/movements', headers=admin).json()
    assert [m['kind'] for m in before] == ['sale', 'opening']
    assert before[0]['order_id'] == first.json()['id']
    assert before[0]['actor_id'] != before[1]['actor_id']
    assert client.post(f"/orders/{first.json()['id']}/cancel", headers=buyer).status_code == 200
    assert client.post(f"/orders/{first.json()['id']}/cancel", headers=buyer).status_code == 409
    ledger = client.get('/inventory/movements', headers=admin).json()
    assert [m['delta'] for m in ledger] == [2, -2, 3]
    balance = client.get('/inventory/reconciliation', headers=admin).json()[0]
    assert balance == dict(product_id=pid, name='Mechanical keyboard', actual_stock=3, ledger_stock=3, difference=0)


def test_multiline_failure_rolls_back_stock_and_ledger(client):
    admin, buyer, pid = setup_shop(client)
    second = client.post('/products', json={'name': 'Empty item', 'price_cents': 200, 'stock': 0}, headers=admin).json()['id']
    response = client.post('/orders', headers={**buyer, 'Idempotency-Key': 'rollback-order-001'},
                           json={'items': [{'product_id': pid, 'quantity': 1}, {'product_id': second, 'quantity': 1}]})
    assert response.status_code == 409
    assert client.get('/products').json()[0]['stock'] == 3
    assert client.get('/orders', headers=buyer).json() == []
    assert all(m['kind'] == 'opening' for m in client.get('/inventory/movements', headers=admin).json())


def test_adjustment_records_only_successful_changes(client):
    admin, _, pid = setup_shop(client)
    assert client.patch(f'/products/{pid}/stock', headers=admin, json={'delta': 4}).status_code == 200
    assert client.patch(f'/products/{pid}/stock', headers=admin, json={'delta': -8}).status_code == 409
    assert client.patch(f'/products/{pid}/stock', headers=admin, json={'delta': 0}).status_code == 422
    assert client.get('/inventory/movements', headers=admin).json()[0]['delta'] == 4
    assert client.get('/inventory/reconciliation', headers=admin).json()[0]['ledger_stock'] == 7


@pytest.mark.parametrize('path', ['/inventory/movements', '/inventory/reconciliation'])
def test_inventory_reports_require_admin(client, path):
    _, buyer, _ = setup_shop(client)
    assert client.get(path, headers=buyer).status_code == 403
    assert client.get(path).status_code in (401, 403)


def test_ledger_filter_and_pagination(client):
    admin, buyer, pid = setup_shop(client)
    place(client, buyer, pid)
    assert len(client.get(f'/inventory/movements?product_id={pid}&limit=1', headers=admin).json()) == 1
    assert client.get('/inventory/movements?product_id=99999', headers=admin).json() == []
    assert len(client.get('/inventory/movements?offset=1', headers=admin).json()) == 1
    assert client.get('/inventory/reconciliation?limit=0', headers=admin).status_code == 422


def test_reconciliation_surfaces_external_stock_drift(client):
    admin, _, pid = setup_shop(client)
    from app import main
    from sqlalchemy import update
    with main.SessionLocal() as db:
        db.execute(update(main.Product).where(main.Product.id == pid).values(stock=9))
        db.commit()
    report = client.get('/inventory/reconciliation', headers=admin).json()[0]
    assert report['actual_stock'] == 9
    assert report['ledger_stock'] == 3
    assert report['difference'] == 6


def test_shipment_does_not_deduct_stock_twice(client):
    admin, buyer, pid = setup_shop(client)
    order = place(client, buyer, pid).json()
    assert client.post(f"/orders/{order['id']}/ship", headers=admin).status_code == 200
    assert client.post(f"/orders/{order['id']}/ship", headers=admin).status_code == 409
    assert len(client.get('/inventory/movements', headers=admin).json()) == 2
    assert client.get('/inventory/reconciliation', headers=admin).json()[0]['actual_stock'] == 2
