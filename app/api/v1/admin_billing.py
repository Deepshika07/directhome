from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.errors import bad_request, not_found
from app.models import Plan, User, UserSubscription
from app.schemas.billing import GrantIn, PlanIn, PlanOut, PlanUpdate
from app.schemas.common import PageMeta, PageParams, ok
from app.services import billing_service as svc
from app.services.audit_service import log_admin_action

router = APIRouter(prefix="/admin/billing", tags=["admin-billing"])


def _v(x):
    return x.value if hasattr(x, "value") else x


def _plan_out(p: Plan) -> dict:
    return PlanOut(
        id=p.id, code=p.code, name=p.name, description=p.description,
        applies_to=p.applies_to, price_paise=p.price_paise,
        currency=p.currency, duration_days=p.duration_days,
        features=p.features, is_active=p.is_active,
        sort_order=p.sort_order).model_dump()


# ---------------- plan CRUD ----------------

@router.get("/plans")
def admin_plans(admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    rows = db.scalars(select(Plan).order_by(Plan.sort_order)).all()
    return ok([_plan_out(p) for p in rows])


@router.post("/plans", status_code=201)
def create_plan(payload: PlanIn, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    if db.scalar(select(Plan.id).where(Plan.code == payload.code)):
        raise bad_request("PLAN_CODE_TAKEN", "Plan code already exists")
    plan = Plan(**payload.model_dump())
    db.add(plan)
    db.flush()
    log_admin_action(db, admin, "PLAN_CREATE", "plan", plan.id,
                     extra={"code": plan.code})
    db.commit()
    return ok(_plan_out(plan))


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, payload: PlanUpdate,
                admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise not_found("PLAN_NOT_FOUND", "Plan not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(plan, k, v)
    log_admin_action(db, admin, "PLAN_UPDATE", "plan", plan.id,
                     extra={"fields": sorted(data.keys())})
    db.commit()
    return ok(_plan_out(plan))


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise not_found("PLAN_NOT_FOUND", "Plan not found")
    in_use = db.scalar(
        select(func.count(UserSubscription.id)).where(
            UserSubscription.plan_id == plan.id)) or 0
    if in_use:
        plan.is_active = False          # soft-delete once subscribed
        log_admin_action(db, admin, "PLAN_DEACTIVATE", "plan", plan.id)
        db.commit()
        return ok({"id": plan.id, "deactivated": True,
                   "note": "plan has subscribers — deactivated, not deleted"})
    db.delete(plan)
    log_admin_action(db, admin, "PLAN_DELETE", "plan", plan_id)
    db.commit()
    return ok({"id": plan_id, "deleted": True})


# ---------------- subscriptions ----------------

@router.get("/subscriptions")
def subscriptions(status: str | None = None, plan_id: int | None = None,
                  page: PageParams = Depends(),
                  admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    q = select(UserSubscription).join(Plan, Plan.id == UserSubscription.plan_id)
    if status:
        q = q.where(UserSubscription.status == status.upper())
    if plan_id:
        q = q.where(UserSubscription.plan_id == plan_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(UserSubscription.id.desc())
                      .offset(page.offset).limit(page.limit)).all()
    return ok([{
        "id": s.id, "user_id": s.user_id, "plan_id": s.plan_id,
        "plan_code": s.plan.code, "status": _v(s.status),
        "source": _v(s.source), "starts_at": s.starts_at,
        "expires_at": s.expires_at,
    } for s in rows], PageMeta(page=page.page, limit=page.limit, total=total))


@router.post("/grants", status_code=201)
def grant_plan(payload: GrantIn, admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    sub = svc.grant_plan(db, admin, payload.user_id, payload.plan_id,
                         payload.days)
    return ok({"id": sub.id, "user_id": sub.user_id,
               "plan_id": sub.plan_id, "expires_at": sub.expires_at})


@router.post("/subscriptions/{sub_id}/revoke")
def revoke(sub_id: int, admin: User = Depends(require_admin),
           db: Session = Depends(get_db)):
    return ok(svc.revoke_subscription(db, admin, sub_id))


# ---------------- analytics ----------------

@router.get("/stats")
def billing_stats(admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    return ok(svc.stats(db))
