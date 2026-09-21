import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    os.environ["JWT_SECRET"] = "test-secret-only-with-at-least-32-bytes"
    os.environ["ADMIN_EMAIL"] = "admin@example.com"
    os.environ["ADMIN_PASSWORD"] = "strong-admin-password"
    from importlib import reload
    from app import main
    reload(main)
    with TestClient(main.app) as test_client:
        yield test_client


def auth(client, email, password):
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def setup_shop(client):
    customer = {"email": "buyer@example.com", "password": "buyer-password"}
    assert client.post("/auth/register", json=customer).status_code == 201
    admin_headers = auth(client, "admin@example.com", "strong-admin-password")
    buyer_headers = auth(client, **customer)
    product = client.post("/products", json={"name": "Mechanical keyboard", "price_cents": 4999, "stock": 3}, headers=admin_headers)
    assert product.status_code == 201, product.text
    return admin_headers, buyer_headers, product.json()["id"]


def test_order_idempotency_inventory_and_cancel(client):
    admin, buyer, product_id = setup_shop(client)
    headers = {**buyer, "Idempotency-Key": "checkout-001"}
    body = {"items": [{"product_id": product_id, "quantity": 2}]}
    first = client.post("/orders", json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert first.json()["total_cents"] == 9998
    again = client.post("/orders", json=body, headers=headers)
    assert again.status_code == 201
    assert again.json()["id"] == first.json()["id"]
    mismatched = client.post("/orders", json={"items": [{"product_id": product_id, "quantity": 1}]}, headers=headers)
    assert mismatched.status_code == 409
    assert client.get("/products").json()[0]["stock"] == 1
    failed = client.post("/orders", json=body, headers={**buyer, "Idempotency-Key": "checkout-002"})
    assert failed.status_code == 409
    assert client.post(f"/orders/{first.json()['id']}/cancel", headers=buyer).status_code == 200
    assert client.get("/products").json()[0]["stock"] == 3
    assert client.post(f"/orders/{first.json()['id']}/ship", headers=admin).status_code == 409


def test_permissions_and_validation(client):
    admin, buyer, product_id = setup_shop(client)
    assert client.post("/products", json={"name": "Mouse", "price_cents": 100, "stock": 1}, headers=buyer).status_code == 403
    assert client.post("/orders", json={"items": [{"product_id": product_id, "quantity": 1}]}, headers=buyer).status_code == 422
    assert client.post("/orders", json={"items": [{"product_id": product_id, "quantity": 1}, {"product_id": product_id, "quantity": 1}]}, headers={**buyer, "Idempotency-Key": "checkout-003"}).status_code == 422
    assert client.patch(f"/products/{product_id}/stock", json={"delta": -5}, headers=admin).status_code == 409
    assert client.get("/products").json()[0]["stock"] == 3


def test_order_access_and_shipping(client):
    admin, buyer, product_id = setup_shop(client)
    order = client.post("/orders", json={"items": [{"product_id": product_id, "quantity": 1}]}, headers={**buyer, "Idempotency-Key": "checkout-004"}).json()
    other = {"email": "other@example.com", "password": "other-password"}
    client.post("/auth/register", json=other)
    other_headers = auth(client, **other)
    assert client.get(f"/orders/{order['id']}", headers=other_headers).status_code == 404
    assert client.post(f"/orders/{order['id']}/ship", headers=buyer).status_code == 403
    assert client.post(f"/orders/{order['id']}/ship", headers=admin).json()["status"] == "shipped"
    assert client.post(f"/orders/{order['id']}/cancel", headers=buyer).status_code == 409
