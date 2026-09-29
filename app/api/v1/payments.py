from fastapi import APIRouter, Depends, Response, status

from app.api.deps import CurrentUser, DbSession, WebhookServiceDep, verify_webhook_signature
from app.middleware.rate_limit import limit_payment, limit_webhook
from app.schemas.error import error_responses
from app.schemas.payment import PaymentCreate, PaymentRead
from app.schemas.webhook import WebhookOutcome, WebhookPayload, WebhookResponse
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post(
    "/",
    response_model=PaymentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Pay for a booking (simulated)",
    description=(
        "Simulates a payment for one of **your** PENDING bookings. The amount is taken from the "
        "booking. SUCCESS confirms the booking, FAILED marks it FAILED. A payment that "
        "completes with FAILED is still a successfully *processed* request, hence 201 with "
        "`status: FAILED` in the body. Use `simulate_outcome` to choose the result "
        "(`pending` leaves it for the webhook)."
    ),
    dependencies=[Depends(limit_payment)],
    responses=error_responses(400, 401, 403, 404, 409, 422, 429),
)
def create_payment(payload: PaymentCreate, user: CurrentUser, db: DbSession) -> PaymentRead:
    payment = PaymentService(db).pay(user, payload.booking_id, payload.simulate_outcome)
    return PaymentRead.model_validate(payment)


@router.post(
    "/webhook/",
    response_model=WebhookResponse,
    summary="Payment provider webhook (idempotent)",
    description=(
        "Called by the (simulated) provider. Safe to deliver any number of times: the first "
        "delivery of an `event_id` is applied, repeats return `result: duplicate` without "
        "touching any state. **200** = handled (processed / duplicate / ignored), **202** = "
        "transient failure, queued for retry with backoff. When `WEBHOOK_SIGNATURE_REQUIRED` is "
        "on, send `X-Webhook-Signature: sha256=<HMAC-SHA256 of the raw body>`."
    ),
    dependencies=[Depends(limit_webhook), Depends(verify_webhook_signature)],
    responses={
        202: {"model": WebhookResponse, "description": "Queued for retry"},
        **error_responses(400, 401, 404, 409, 422, 429, 503),
    },
)
def payment_webhook(
    payload: WebhookPayload, service: WebhookServiceDep, response: Response
) -> WebhookResponse:
    outcome = service.receive(payload)
    if outcome is WebhookOutcome.QUEUED:
        response.status_code = status.HTTP_202_ACCEPTED
    return WebhookResponse(event_id=payload.event_id, result=outcome)
