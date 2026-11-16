"""Exercise the real application against a temporary database; no live service."""
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    with tempfile.TemporaryDirectory() as folder:
        password = secrets.token_urlsafe(24)
        os.environ.update(DATABASE_URL=f"sqlite:///{(Path(folder) / 'demo.db').as_posix()}",
                          JWT_SECRET=secrets.token_urlsafe(48),
                          ADMIN_EMAIL='admin@example.com', ADMIN_PASSWORD=password)
        from app import main as api
        from fastapi.testclient import TestClient
        try:
            with TestClient(api.app) as client:
                def call(method, path, expected, **kwargs):
                    result = getattr(client, method)(path, **kwargs)
                    if result.status_code != expected:
                        raise RuntimeError(f'{method} {path}: {result.status_code} {result.text}')
                    return result.json()
                token = call('post', '/auth/login', 200, json={'email': 'admin@example.com', 'password': password})['access_token']
                admin = {'Authorization': f'Bearer {token}'}
                buyer = {'email': 'buyer@example.com', 'password': secrets.token_urlsafe(20)}
                call('post', '/auth/register', 201, json=buyer)
                token = call('post', '/auth/login', 200, json=buyer)['access_token']
                headers = {'Authorization': f'Bearer {token}', 'Idempotency-Key': 'demo-checkout-001'}
                product = call('post', '/products', 201, headers=admin, json={'name': 'Mechanical keyboard', 'price_cents': 4999, 'stock': 10})
                body = {'items': [{'product_id': product['id'], 'quantity': 3}]}
                order = call('post', '/orders', 201, headers=headers, json=body)
                replay = call('post', '/orders', 201, headers=headers, json=body)
                if order['id'] != replay['id']:
                    raise RuntimeError('Replay created a second order')
                after_sale = call('get', '/inventory/reconciliation', 200, headers=admin)
                call('post', f"/orders/{order['id']}/cancel", 200, headers=headers)
                movements = call('get', '/inventory/movements', 200, headers=admin)
                final = call('get', '/inventory/reconciliation', 200, headers=admin)
                print(json.dumps(dict(opening_stock=10, order_total_cents=order['total_cents'],
                                      replay_same_order=True, after_sale=after_sale,
                                      movement_deltas=[m['delta'] for m in reversed(movements)], after_cancel=final), indent=2))
        finally:
            api.engine.dispose()


if __name__ == '__main__':
    main()
