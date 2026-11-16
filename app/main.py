"""OrderFlow: inventory-aware order API."""

import os
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import (
    ForeignKey, Integer, String, UniqueConstraint, create_engine, func, select, update
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./orderflow.db")
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET or len(JWT_SECRET) < 32:
    raise RuntimeError("JWT_SECRET must contain at least 32 characters")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
hasher = PasswordHasher()
bearer = HTTPBearer()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="customer")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    price_cents: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(default=True)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="placed")
    total_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    items: Mapped[list["OrderItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(160))
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)


class StockMovement(Base):
    """Append-only application ledger, committed with its inventory mutation."""
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))
    delta: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(RegisterIn):
    pass


class ProductIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    price_cents: int = Field(gt=0)
    stock: int = Field(ge=0)


class StockIn(BaseModel):
    delta: int

    @field_validator("delta")
    @classmethod
    def nonzero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("delta must not be zero")
        return value


class ProductOut(ProductIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    active: bool


class OrderLineIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100)


class OrderIn(BaseModel):
    items: list[OrderLineIn] = Field(min_length=1, max_length=50)


class OrderLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: int
    product_name: str
    unit_price_cents: int
    quantity: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: Literal["placed", "cancelled", "shipped"]
    total_cents: int
    created_at: datetime
    items: list[OrderLineOut]


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    order_id: int | None
    actor_id: int
    kind: Literal["opening", "adjustment", "sale", "cancellation"]
    delta: int
    created_at: datetime


class ReconciliationOut(BaseModel):
    product_id: int
    name: str
    actual_stock: int
    ledger_stock: int
    difference: int


def get_db():
    with SessionLocal() as db:
        yield db


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token") from None
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def token_for(user: User) -> str:
    return jwt.encode(
        {"sub": str(user.id), "exp": datetime.now(timezone.utc) + timedelta(hours=8)},
        JWT_SECRET,
        algorithm="HS256",
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    email = os.getenv("ADMIN_EMAIL")
    password = os.getenv("ADMIN_PASSWORD")
    if email and password:
        with SessionLocal() as db:
            if not db.scalar(select(User).where(User.email == email.lower())):
                db.add(User(email=email.lower(), password_hash=hasher.hash(password), role="admin"))
                db.commit()
    yield


app = FastAPI(title="OrderFlow API", version="1.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/register", status_code=201)
def register(body: RegisterIn, db: Annotated[Session, Depends(get_db)]):
    user = User(email=body.email.lower(), password_hash=hasher.hash(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered") from None
    return {"id": user.id, "email": user.email, "role": user.role}


@app.post("/auth/login")
def login(body: LoginIn, db: Annotated[Session, Depends(get_db)]):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    try:
        valid = user is not None and hasher.verify(user.password_hash, body.password)
    except VerifyMismatchError:
        valid = False
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"access_token": token_for(user), "token_type": "bearer"}


@app.get("/products", response_model=list[ProductOut])
def list_products(
    db: Annotated[Session, Depends(get_db)],
    q: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    stmt = select(Product).where(Product.active.is_(True)).order_by(Product.id)
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@app.post("/products", response_model=ProductOut, status_code=201)
def create_product(body: ProductIn, db: Annotated[Session, Depends(get_db)], actor: Annotated[User, Depends(admin_user)]):
    product = Product(**body.model_dump())
    db.add(product)
    db.flush()
    db.add(StockMovement(product_id=product.id, actor_id=actor.id, kind="opening", delta=product.stock))
    db.commit()
    db.refresh(product)
    return product


@app.patch("/products/{product_id}/stock", response_model=ProductOut)
def change_stock(product_id: int, body: StockIn, db: Annotated[Session, Depends(get_db)], actor: Annotated[User, Depends(admin_user)]):
    result = db.execute(
        update(Product).where(Product.id == product_id, Product.stock + body.delta >= 0)
        .values(stock=Product.stock + body.delta)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Product missing or insufficient stock")
    db.add(StockMovement(product_id=product_id, actor_id=actor.id, kind="adjustment", delta=body.delta))
    db.commit()
    return db.get(Product, product_id)


@app.get("/inventory/movements", response_model=list[MovementOut])
def movements(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(admin_user)],
    product_id: int | None = Query(None, gt=0),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    stmt = select(StockMovement).order_by(StockMovement.id.desc())
    if product_id is not None:
        stmt = stmt.where(StockMovement.product_id == product_id)
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@app.get("/inventory/reconciliation", response_model=list[ReconciliationOut])
def reconciliation(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(admin_user)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    ledger = select(
        StockMovement.product_id, func.sum(StockMovement.delta).label("balance")
    ).group_by(StockMovement.product_id).subquery()
    rows = db.execute(
        select(Product, func.coalesce(ledger.c.balance, 0))
        .outerjoin(ledger, Product.id == ledger.c.product_id)
        .order_by(Product.id).limit(limit).offset(offset)
    ).all()
    return [dict(product_id=p.id, name=p.name, actual_stock=p.stock,
                 ledger_stock=balance, difference=p.stock - balance) for p, balance in rows]


@app.post("/orders", response_model=OrderOut, status_code=201)
def place_order(
    body: OrderIn,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128, alias="Idempotency-Key")],
):
    ids = [line.product_id for line in body.items]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Duplicate products in order")
    normalized = sorted((line.product_id, line.quantity) for line in body.items)
    request_hash = hashlib.sha256(json.dumps(normalized).encode()).hexdigest()
    existing = db.scalar(select(Order).where(Order.user_id == user.id, Order.idempotency_key == idempotency_key))
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="Idempotency key reused with different items")
        return existing
    total = 0
    order_items = []
    for line in body.items:
        product = db.get(Product, line.product_id)
        if product is None or not product.active:
            db.rollback()
            raise HTTPException(status_code=404, detail=f"Product {line.product_id} unavailable")
        changed = db.execute(
            update(Product).where(Product.id == product.id, Product.stock >= line.quantity)
            .values(stock=Product.stock - line.quantity)
        )
        if changed.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail=f"Insufficient stock for product {product.id}")
        order_items.append(OrderItem(
            product_id=product.id, product_name=product.name,
            unit_price_cents=product.price_cents, quantity=line.quantity,
        ))
        total += product.price_cents * line.quantity
    order = Order(user_id=user.id, idempotency_key=idempotency_key, request_hash=request_hash, total_cents=total, items=order_items)
    db.add(order)
    try:
        db.flush()
        for line in body.items:
            db.add(StockMovement(product_id=line.product_id, order_id=order.id,
                                 actor_id=user.id, kind="sale", delta=-line.quantity))
        db.commit()
    except IntegrityError:
        db.rollback()
        # A concurrent request with the same key may have committed first.
        existing = db.scalar(select(Order).where(Order.user_id == user.id, Order.idempotency_key == idempotency_key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(status_code=409, detail="Idempotency key reused with different items")
            return existing
        raise
    db.refresh(order)
    return order


@app.get("/orders", response_model=list[OrderOut])
def list_orders(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    return db.scalars(
        select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()).limit(limit).offset(offset)
    ).all()


@app.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]):
    order = db.get(Order, order_id)
    if order is None or (order.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(current_user)]):
    order = db.get(Order, order_id)
    if order is None or (order.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Order not found")
    changed = db.execute(
        update(Order).where(Order.id == order_id, Order.status == "placed").values(status="cancelled")
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Only placed orders can be cancelled")
    for line in order.items:
        db.execute(update(Product).where(Product.id == line.product_id).values(stock=Product.stock + line.quantity))
        db.add(StockMovement(product_id=line.product_id, order_id=order.id,
                             actor_id=user.id, kind="cancellation", delta=line.quantity))
    db.commit()
    db.refresh(order)
    return order


@app.post("/orders/{order_id}/ship", response_model=OrderOut)
def ship_order(order_id: int, db: Annotated[Session, Depends(get_db)], _: Annotated[User, Depends(admin_user)]):
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    changed = db.execute(update(Order).where(Order.id == order_id, Order.status == "placed").values(status="shipped"))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Only placed orders can be shipped")
    db.commit()
    db.refresh(order)
    return order
