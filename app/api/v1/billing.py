from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models import PaymentOrder, Plan, User
from app.schemas.billing import ConfirmIn, OrderIn, PlanOut
from app.schemas.common import ok
from app.services import billing_service as svc

router = APIRouter(prefix="/billing", tags=["billing"])


def _plan_out(p: Plan) -> PlanOut:
    return PlanOut(
        id=p.id, code=p.code, name=p.name, description=p.description,
        applies_to=p.applies_to, price_paise=p.price_paise,
        currency=p.currency, duration_days=p.duration_days,
        features=p.features, is_active=p.is_active,
        sort_order=p.sort_order)


@router.get("/plans")
def list_plans(applies_to: str | None = None,
               db: Session = Depends(get_db)):
    """Public plan catalogue — drives the pricing page."""
    q = select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)
    if applies_to:
        q = q.where(Plan.applies_to == applies_to.upper())
    return ok([_plan_out(p).model_dump() for p in db.scalars(q).all()])


@router.get("/me")
def my_billing(user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """My subscription + usage vs limits for both roles."""
    return ok(svc.usage_snapshot(db, user))


@router.post("/orders", status_code=201)
def create_order(payload: OrderIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return ok(svc.create_order(db, user, payload.plan_id))


@router.post("/orders/{order_id}/confirm")
def confirm_order(order_id: int, payload: ConfirmIn,
                  user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Mock payment confirm — real provider verifies signature here."""
    return ok(svc.confirm_order(db, user, order_id, payload.signature))


@router.get("/orders")
def my_orders(user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    rows = db.scalars(
        select(PaymentOrder)
        .where(PaymentOrder.user_id == user.id)
        .order_by(PaymentOrder.id.desc())).all()
    return ok([{
        "id": o.id, "plan_id": o.plan_id, "plan_code": o.plan.code,
        "amount_paise": o.amount_paise, "currency": o.currency,
        "status": o.status.value if o.status else None,
        "provider": o.provider, "created_at": o.created_at,
        "paid_at": o.paid_at,
    } for o in rows])
