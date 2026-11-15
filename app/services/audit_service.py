from sqlalchemy.orm import Session

from app.models import AdminAuditLog, User


def log_admin_action(
    db: Session,
    admin: User | None,
    action: str,
    target_type: str,
    target_id,
    reason: str | None = None,
    extra: dict | None = None,
) -> None:
    """Record an admin (or system, admin=None) moderation action. Caller commits."""
    db.add(
        AdminAuditLog(
            admin_id=admin.id if admin else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            reason=reason,
            extra=extra or {},
        )
    )
