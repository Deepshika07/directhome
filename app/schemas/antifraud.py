from pydantic import BaseModel, Field


# ---------------- user verification ----------------

class TermsAcceptIn(BaseModel):
    version: str = Field(min_length=1, max_length=20)


class OwnerDeclarationIn(BaseModel):
    role: str  # OWNER | AUTHORIZED_REPRESENTATIVE


class SendCodeIn(BaseModel):
    channel: str  # PHONE | EMAIL


class VerifyCodeIn(BaseModel):
    channel: str
    code: str = Field(min_length=4, max_length=8)


class VerificationDocIn(BaseModel):
    document_type_id: int
    file_url: str = Field(max_length=1000)


# ---------------- appeals ----------------

class AppealIn(BaseModel):
    message: str = Field(min_length=10, max_length=2000)
    property_id: int | None = None


# ---------------- admin ----------------

class ResolveReportIn(BaseModel):
    validated: bool = True  # validated complaints feed the risk engine
    note: str | None = Field(default=None, max_length=1000)


class DismissReportIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class DocRejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=255)
    ownership_mismatch: bool = False  # feeds +mismatch score on the owner


class RequestInfoIn(BaseModel):
    note: str = Field(min_length=3, max_length=1000)


class ModerationIn(BaseModel):
    action: str  # UNFLAG | SUSPEND | REINSTATE
    reason: str = Field(min_length=3, max_length=500)


class RestrictionIn(BaseModel):
    action: str  # RESTRICT | SUSPEND | UNSUSPEND | BAN
    reason: str = Field(min_length=3, max_length=500)


class AppealResolveIn(BaseModel):
    approved: bool
    note: str | None = Field(default=None, max_length=1000)


class DuplicateReviewIn(BaseModel):
    action: str  # CONFIRM | DISMISS


class FraudConfigUpdateIn(BaseModel):
    updates: dict[str, int]
