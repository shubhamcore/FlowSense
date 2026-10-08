"""FastAPI production control plane.

The hackathon preview keeps using server.py; this application is the upgrade
path for authenticated, multi-store deployments with Redis-backed jobs and a
Postgres/Timescale schema.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

try:
    from jose import JWTError, jwt
except ImportError:  # pragma: no cover - production dependency
    JWTError = Exception
    jwt = None

from .actions import ActionStore, OperationalAction
from .calibration import calibrate
from .causal import ExperimentSpec, analyze_continuous, preregister
from .flow_genome import build_flow_genome, learn_from_peers, peer_benchmark
from .storage import RetentionPolicy, tenant_key
from .worker import enqueue_video, redis_client, job_key

app = FastAPI(title="FlowSense AI API", version="1.0.0", docs_url="/docs", redoc_url="/redoc")
bearer = HTTPBearer(auto_error=False)
JWT_SECRET = os.environ.get("JWT_SECRET", "change-this-secret-before-production")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ENV = os.environ.get("ENVIRONMENT", "development")
ACTION_STORE = ActionStore()


class UserContext(BaseModel):
    sub: str
    tenant_id: str
    role: str = "viewer"
    allowed_stores: list[str] = Field(default_factory=list)


class CalibrationRequest(BaseModel):
    source_points: list[tuple[float, float]] = Field(min_length=4)
    floor_points: list[tuple[float, float]] = Field(min_length=4)
    max_error_m: float = Field(default=.35, gt=0, le=5)


class VideoJobRequest(BaseModel):
    camera_id: str
    object_key: str
    model_name: str = "yolo11n.pt"
    tracker_name: str = "bytetrack.yaml"
    device: str = "0"


class ExperimentRequest(BaseModel):
    name: str
    hypothesis: str
    primary_metric: str
    direction: str
    baseline: list[float] = Field(default_factory=list)
    variant: list[float] = Field(default_factory=list)
    minimum_sample_per_arm: int = Field(default=30, ge=2)
    guardrails: list[str] = Field(default_factory=list)


class ActionRequest(BaseModel):
    title: str
    problem: str
    intervention: str
    owner: str
    due_date: str
    success_metric: str
    hypothesis: str
    source_recommendation_id: str | None = None


class ActionMeasureRequest(BaseModel):
    baseline: list[float]
    after: list[float]
    minimum_sample_per_arm: int = Field(default=30, ge=2)


class TokenRequest(BaseModel):
    user_id: str = "demo-user"
    tenant_id: str = "demo-tenant"
    role: str = "admin"
    allowed_stores: list[str] = Field(default_factory=lambda: ["store-flagship"])


def make_token(payload: UserContext) -> str:
    if jwt is None:
        raise RuntimeError("Install python-jose for JWT support")
    claims = payload.model_dump()
    claims["exp"] = datetime.now(timezone.utc) + timedelta(hours=8)
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM)


def current_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> UserContext:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    try:
        claims = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return UserContext(sub=claims["sub"], tenant_id=claims["tenant_id"], role=claims.get("role", "viewer"), allowed_stores=claims.get("allowed_stores", []))
    except (JWTError, KeyError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def store_access(user: UserContext, store_id: str):
    if user.allowed_stores and store_id not in user.allowed_stores:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Store is outside the token scope")


def require_role(user: UserContext, *roles: str):
    if user.role not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role for this operation")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "flowsense-production-api", "environment": ENV}


@app.post("/api/v1/auth/dev-token")
def dev_token(request: TokenRequest):
    if ENV == "production":
        raise HTTPException(status_code=404, detail="Disabled in production")
    return {"access_token": make_token(UserContext(sub=request.user_id, tenant_id=request.tenant_id, role=request.role, allowed_stores=request.allowed_stores)), "token_type": "bearer", "expires_in": 28800}


@app.get("/api/v1/me")
def me(user: Annotated[UserContext, Depends(current_user)]):
    return user.model_dump()


@app.get("/api/v1/stores")
def stores(user: Annotated[UserContext, Depends(current_user)]):
    return {"items": [{"id": store_id, "name": "Harbor Market · Flagship", "tenant_id": user.tenant_id, "privacy": "behavior_only"} for store_id in (user.allowed_stores or ["store-flagship"])]}


@app.get("/api/v1/stores/{store_id}/cameras")
def cameras(store_id: str, user: Annotated[UserContext, Depends(current_user)]):
    store_access(user, store_id)
    return {"items": [{"id": "cam-01", "name": "Entrance / west", "status": "ONLINE", "calibration": "GOOD"}, {"id": "cam-02", "name": "Center aisle", "status": "ONLINE", "calibration": "GOOD"}, {"id": "cam-03", "name": "Checkout", "status": "DEGRADED", "calibration": "REVIEW"}]}


@app.post("/api/v1/stores/{store_id}/cameras/{camera_id}/calibration")
def save_calibration(store_id: str, camera_id: str, body: CalibrationRequest, user: Annotated[UserContext, Depends(current_user)]):
    store_access(user, store_id)
    require_role(user, "owner", "admin", "operator")
    if len(body.source_points) != len(body.floor_points):
        raise HTTPException(status_code=422, detail="Source and floor point counts must match")
    try:
        result = calibrate(body.source_points, body.floor_points, max_error_m=body.max_error_m)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"store_id": store_id, "camera_id": camera_id, "calibration": result.__dict__, "data_class": "MEASURED"}


@app.post("/api/v1/stores/{store_id}/jobs", status_code=202)
def create_job(store_id: str, body: VideoJobRequest, user: Annotated[UserContext, Depends(current_user)]):
    store_access(user, store_id)
    require_role(user, "owner", "admin", "operator")
    try:
        safe_key = tenant_key(user.tenant_id, store_id, "raw", body.object_key.rsplit("/", 1)[-1])
        job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        queue_id = enqueue_video(job_id=job_id, tenant_id=user.tenant_id, store_id=store_id, camera_id=body.camera_id, source_uri=safe_key, vision_config=body.model_dump(exclude={"camera_id", "object_key"}))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"job_id": job_id, "queue_id": queue_id, "status": "QUEUED", "model": body.model_name, "tracker": body.tracker_name}


@app.get("/api/v1/jobs/{job_id}")
def job_status(job_id: str, user: Annotated[UserContext, Depends(current_user)]):
    try:
        data = redis_client().hgetall(job_key(job_id))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not data:
        raise HTTPException(status_code=404, detail="Job not found")
    if data.get("tenant_id") and data["tenant_id"] != user.tenant_id:
        raise HTTPException(status_code=403, detail="Job outside tenant scope")
    return data


@app.post("/api/v1/stores/{store_id}/actions", status_code=201)
def create_action(store_id: str, body: ActionRequest, user: Annotated[UserContext, Depends(current_user)]):
    store_access(user, store_id)
    require_role(user, "owner", "admin", "analyst", "operator")
    action = OperationalAction(tenant_id=user.tenant_id, store_id=store_id, **body.model_dump())
    return ACTION_STORE.create(action)


@app.get("/api/v1/stores/{store_id}/actions")
def list_actions(store_id: str, user: Annotated[UserContext, Depends(current_user)]):
    store_access(user, store_id)
    return {"items": ACTION_STORE.list(user.tenant_id, store_id)}


@app.post("/api/v1/actions/{action_id}/complete")
def complete_action(action_id: str, user: Annotated[UserContext, Depends(current_user)]):
    try:
        action = ACTION_STORE.complete(action_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Action not found")
    if action["tenant_id"] != user.tenant_id:
        raise HTTPException(status_code=403, detail="Action outside tenant scope")
    return action


@app.post("/api/v1/actions/{action_id}/measure")
def measure_action(action_id: str, body: ActionMeasureRequest, user: Annotated[UserContext, Depends(current_user)]):
    try:
        action = ACTION_STORE.measure(action_id, body.baseline, body.after, minimum_sample_per_arm=body.minimum_sample_per_arm)
    except KeyError:
        raise HTTPException(status_code=404, detail="Action not found")
    if action["tenant_id"] != user.tenant_id:
        raise HTTPException(status_code=403, detail="Action outside tenant scope")
    return action


def demo_genome(store_id: str):
    return build_flow_genome(store_id=store_id, journeys=[{"pattern": "entry → produce → snacks → promo → checkout"}] * 42 + [{"pattern": "entry → beverages → exit"}] * 31 + [{"pattern": "entry → electronics → promo → checkout"}] * 19 + [{"pattern": "entry → homecare → exit"}] * 8, sections=[{"id": "produce", "dwell": 118}, {"id": "promo", "dwell": 143}, {"id": "homecare", "dwell": 18}], bottlenecks=[{"name": "Aisle 4", "score": 82, "severity": "HIGH"}], dead_zones=[{"name": "Zone F", "score": 89, "opportunity": 91}], outcomes=[{"intervention": "Move promo display", "decision": "PROMOTE", "effect": {"congestion": -0.18}}], data_class="SYNTHETIC")


@app.get("/api/v1/stores/{store_id}/flow-genome")
def flow_genome(store_id: str, user: Annotated[UserContext, Depends(current_user)]):
    store_access(user, store_id)
    genome = demo_genome(store_id).as_dict()
    peers = [demo_genome("peer-01").as_dict(), build_flow_genome(store_id="peer-02", journeys=[{"pattern": "entry → produce → checkout"}] * 60 + [{"pattern": "entry → beverages → exit"}] * 40, sections=[{"id": "produce", "dwell": 100}], bottlenecks=[{"name": "Checkout", "score": 61}], dead_zones=[{"name": "Seasonal", "score": 76, "opportunity": 78}], outcomes=[{"intervention": "Move end-cap", "decision": "PROMOTE", "effect": {"dwell": 0.12}}], data_class="SYNTHETIC").as_dict()]
    return {"genome": genome, "benchmark": peer_benchmark(genome, peers), "peer_learnings": learn_from_peers(genome, peers, minimum_similarity=.5)}


@app.post("/api/v1/experiments/preregister")
def experiment_preregister(body: ExperimentRequest, user: Annotated[UserContext, Depends(current_user)]):
    require_role(user, "owner", "admin", "analyst")
    spec = ExperimentSpec(body.name, body.hypothesis, body.primary_metric, body.direction, body.minimum_sample_per_arm, tuple(body.guardrails))
    return preregister(spec)


@app.post("/api/v1/experiments/analyze")
def experiment_analyze(body: ExperimentRequest, user: Annotated[UserContext, Depends(current_user)]):
    require_role(user, "owner", "admin", "analyst")
    spec = ExperimentSpec(body.name, body.hypothesis, body.primary_metric, body.direction, body.minimum_sample_per_arm, tuple(body.guardrails))
    return analyze_continuous(spec, body.baseline, body.variant)


@app.get("/api/v1/privacy/retention")
def retention(user: Annotated[UserContext, Depends(current_user)]):
    policy = RetentionPolicy(raw_hours=int(os.environ.get("RAW_RETENTION_HOURS", "24")), derived_days=int(os.environ.get("DERIVED_RETENTION_DAYS", "90")))
    return {"tenant_id": user.tenant_id, "policy": policy.__dict__, "raw_video": "private encrypted object storage", "faces": "never persisted"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
