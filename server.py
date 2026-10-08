"""Minimal production-shaped demo API for FlowSense AI.

It intentionally uses the Python standard library so the offline hackathon demo
has zero package-install dependency. Replace DemoRepository methods with a
Postgres/Redis-backed repository and a worker queue for a pilot deployment.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from flowsense.core import simulate_layout
from flowsense.cv_pipeline import CVUnavailable, process_video
from production.calibration import calibrate
from production.counterfactual import Intervention, active_learning_priority, journey_entropy, rank_interventions
from production.actions import ActionStore, OperationalAction
from production.causal import ExperimentSpec, analyze_continuous, preregister
from production.flow_genome import build_flow_genome, learn_from_peers, peer_benchmark
from proof.metrics import proof_report

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"

OVERVIEW = {
    "status": "ready",
    "data_class": "SYNTHETIC",
    "snapshot_at": "2026-10-09T14:32:00+05:30",
    "visitors": {"value": 1248, "delta": 12.4, "label": "Visitors", "quality": "ESTIMATED"},
    "avg_dwell": {"value": 94, "unit": "sec", "delta": 8.1, "label": "Average dwell", "quality": "MEASURED"},
    "avg_journey": {"value": 6.8, "unit": "min", "delta": -4.2, "label": "Average journey", "quality": "MEASURED"},
    "bottlenecks": {"value": 3, "delta": -1, "label": "Active bottlenecks", "quality": "MEASURED"},
    "dead_zones": {"value": 2, "delta": 0, "label": "Dead zones", "quality": "ESTIMATED"},
    "flow_health": {"value": 78, "delta": 6, "label": "Flow health", "quality": "ESTIMATED"},
    "store": {"name": "Harbor Market · Flagship", "area_m2": 1840, "cameras_online": 3, "cameras_total": 4},
}

ACTIONS = ActionStore()

ANALYTICS = {
    "bottlenecks": [
        {"id": "bn-1", "name": "Aisle 4 intersection", "severity": "HIGH", "score": 82, "density": 184, "speed_reduction": 43, "dwell_increase": 67, "duration": 18, "affected": 327, "confidence": "MEDIUM", "cause": "Promo display intersects two high-volume paths."},
        {"id": "bn-2", "name": "Checkout approach", "severity": "MEDIUM", "score": 61, "density": 122, "speed_reduction": 26, "dwell_increase": 34, "duration": 11, "affected": 184, "confidence": "HIGH", "cause": "Queue spillback narrows the final approach."},
        {"id": "bn-3", "name": "Beverages turn", "severity": "LOW", "score": 44, "density": 108, "speed_reduction": 18, "dwell_increase": 21, "duration": 7, "affected": 89, "confidence": "MEDIUM", "cause": "Short dwell clusters around end-cap offer."},
    ],
    "dead_zones": [
        {"id": "dz-1", "name": "Zone F · Home care", "score": 89, "traffic": 7.8, "store_avg": 34.2, "dwell": 18, "dwell_avg": 71, "opportunity": 91, "cause": "Low visibility and weak path connectivity."},
        {"id": "dz-2", "name": "Zone H · Seasonal", "score": 76, "traffic": 11.4, "store_avg": 34.2, "dwell": 26, "dwell_avg": 71, "opportunity": 78, "cause": "Visitors pass the perimeter but rarely enter."},
    ],
    "sections": [
        {"id": "produce", "name": "Fresh produce", "visitors": 812, "dwell": 118, "traffic": 31.8, "speed": 0.62, "health": 91, "trend": [26, 28, 31, 34, 29, 32, 36, 31]},
        {"id": "beverages", "name": "Beverages", "visitors": 694, "dwell": 86, "traffic": 26.4, "speed": 0.58, "health": 78, "trend": [21, 23, 20, 24, 28, 27, 26, 30]},
        {"id": "snacks", "name": "Snacks", "visitors": 602, "dwell": 74, "traffic": 22.1, "speed": 0.71, "health": 84, "trend": [19, 20, 22, 19, 24, 23, 22, 25]},
        {"id": "promo", "name": "Promo island", "visitors": 488, "dwell": 143, "traffic": 18.7, "speed": 0.31, "health": 64, "trend": [16, 15, 18, 20, 21, 19, 23, 21]},
        {"id": "electronics", "name": "Electronics", "visitors": 354, "dwell": 97, "traffic": 12.8, "speed": 0.48, "health": 73, "trend": [12, 14, 13, 15, 16, 14, 17, 16]},
        {"id": "homecare", "name": "Home care", "visitors": 98, "dwell": 18, "traffic": 7.8, "speed": 0.84, "health": 34, "trend": [8, 7, 9, 8, 7, 10, 8, 8]},
    ],
}


def demo_genome(store_id="store-flagship"):
    return build_flow_genome(store_id=store_id, journeys=[{"pattern": "entry → produce → snacks → promo → checkout"}] * 42 + [{"pattern": "entry → beverages → exit"}] * 31 + [{"pattern": "entry → electronics → promo → checkout"}] * 19 + [{"pattern": "entry → homecare → exit"}] * 8, sections=[{"id": "produce", "dwell": 118}, {"id": "promo", "dwell": 143}, {"id": "homecare", "dwell": 18}], bottlenecks=[{"name": "Aisle 4", "score": 82, "severity": "HIGH"}], dead_zones=[{"name": "Zone F", "score": 89, "opportunity": 91}], outcomes=[{"intervention": "Move promo display", "decision": "PROMOTE", "effect": {"congestion": -0.18}}], data_class="SYNTHETIC")


def ensure_demo_action():
    if not ACTIONS.list("demo-tenant", "store-flagship"):
        ACTIONS.create(OperationalAction(tenant_id="demo-tenant", store_id="store-flagship", title="Move Aisle 4 promo display", problem="Two high-volume paths conflict near Promo Island", intervention="Move the display 0.8–1.2m away from the intersection", owner="Maya Chen", due_date="2026-10-12", success_metric="congestion", hypothesis="Moving the display reduces congestion without reducing checkout reach", source_recommendation_id="rec-1"))


class Handler(BaseHTTPRequestHandler):
    server_version = "FlowSenseDemo/1.0"

    def log_message(self, format, *args):  # keep preview logs quiet
        return

    def _send(self, payload, status=HTTPStatus.OK, content_type="application/json"):
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode()
        elif isinstance(payload, str):
            body = payload.encode()
        else:
            body = payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 1_000_000:
            raise ValueError("Request is too large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            return self._send({"status": "ok", "service": "flowsense-api"})
        if path == "/api/production/architecture":
            return self._send({"components": [{"name": "YOLO11 / RT-DETR", "status": "ADAPTER_READY", "detail": "person-only inference"}, {"name": "ByteTrack / BoT-SORT", "status": "ADAPTER_READY", "detail": "tracker selected per job"}, {"name": "Redis GPU queue", "status": "READY", "detail": "RQ worker contract"}, {"name": "PostgreSQL + TimescaleDB", "status": "SCHEMA_READY", "detail": "track_points hypertable"}, {"name": "JWT multi-store tenancy", "status": "READY", "detail": "store-scoped claims"}, {"name": "S3 / MinIO retention", "status": "READY", "detail": "24h raw default"}, {"name": "Prometheus observability", "status": "READY", "detail": "latency, queue, confidence, FPS"}]})
        if path == "/api/tenancy/me":
            return self._send({"tenant_id": "demo-tenant", "user_id": "demo-operator", "role": "admin", "stores": ["store-flagship"], "mode": "DEMO"})
        if path == "/api/proof/summary":
            gt = json.loads((ROOT / "proof" / "fixtures" / "ground_truth.json").read_text())
            pred = json.loads((ROOT / "proof" / "fixtures" / "predictions.json").read_text())
            return self._send(proof_report(gt_frames=gt["frames"], pred_frames=pred["frames"], gt_dwell=gt["dwell_seconds"], pred_dwell=pred["dwell_seconds"], projected_points=gt["projected_points"], target_points=gt["target_points"], latency_ms=gt["latency_ms"], provenance=gt["manifest"]["provenance"]))
        if path == "/api/actions":
            ensure_demo_action()
            return self._send({"items": ACTIONS.list("demo-tenant", "store-flagship"), "loop": "INSIGHT → OWNER → TEST → MEASURE → LEARN"})
        if path == "/api/flow-genome":
            genome = demo_genome().as_dict()
            peers = [demo_genome("peer-01").as_dict(), demo_genome("peer-02").as_dict()]
            return self._send({"genome": genome, "benchmark": peer_benchmark(genome, peers), "peer_learnings": learn_from_peers(genome, peers, minimum_similarity=.5)})
        if path == "/api/analytics/overview":
            return self._send({**OVERVIEW, "analytics": ANALYTICS})
        if path == "/api/analytics/bottlenecks":
            return self._send({"items": ANALYTICS["bottlenecks"], "data_class": "SYNTHETIC"})
        if path == "/api/analytics/dead-zones":
            return self._send({"items": ANALYTICS["dead_zones"], "data_class": "SYNTHETIC"})
        if path == "/api/analytics/sections":
            return self._send({"items": ANALYTICS["sections"], "data_class": "SYNTHETIC"})
        if path == "/api/analytics/heatmap":
            return self._send({"mode": "traffic", "grid_resolution": "2m", "points": [[.08,.42,.24],[.24,.33,.62],[.47,.56,.91],[.69,.27,.73],[.88,.72,.41]], "data_class": "SYNTHETIC"})
        if path == "/api/recommendations":
            return self._send({"items": [{"id": "rec-1", "problem": "Aisle 4 path conflict", "evidence": ["Density +184%", "Speed -43%", "Dwell +67%"], "intervention": "Move promo display 0.8–1.2m", "expected_effect": "Lower path conflict", "confidence": "MEDIUM"}, {"id": "rec-2", "problem": "Zone F underexposure", "evidence": ["Traffic 7.8% vs 34.2% average", "Dwell gap 53s"], "intervention": "Test a visibility-led display", "expected_effect": "Increase zone exposure", "confidence": "MEDIUM"}], "data_class": "GROUNDED ANALYTICS"})
        if path == "/api/analytics/action-frontier":
            items = [
                Intervention("promo-move", "Move promo display", "Reduce path conflict at Aisle 4", -23.8, .62, .95, 2, "Protect 1.2m clear path", ("Density +184%", "Speed -43%", "Dwell +67%")),
                Intervention("zone-f-visibility", "Activate Zone F sightline", "Increase discovery of underexposed space", 12.6, .54, .86, 1, "Do not block the adjacent path", ("Traffic 7.8% vs 34.2% average", "Dwell gap 53s")),
                Intervention("checkout-buffer", "Protect checkout buffer", "Reduce queue spillback", -15.2, .88, .44, 4, "Maintain checkout capacity", ("11 min persistence", "Speed -26%")),
            ]
            return self._send({"items": rank_interventions(items), "flow_fingerprint": {"journey_entropy": journey_entropy({"produce-snacks-promo-checkout": 42, "beverages-exit": 31, "electronics-promo-checkout": 19, "homecare-exit": 8}), "label": "STORE FLOW FINGERPRINT"}, "active_learning": active_learning_priority(confidence=.62, affected_visitors=327, intervention_effort=2), "data_class": "SIMULATED"})
        if path == "/api/experiments":
            return self._send({"items": [{"id": "exp-1", "name": "Promo display relocation", "status": "RUNNING", "confidence": "INSUFFICIENT_SAMPLE"}, {"id": "exp-2", "name": "Fresh produce end-cap", "status": "COMPLETE", "confidence": "MEDIUM"}]})
        if path == "/api/cameras":
            return self._send({"items": [
                {"id": "cam-01", "name": "Entrance / west", "status": "ONLINE", "fps": 24.0, "confidence": 0.94, "tracks": 16},
                {"id": "cam-02", "name": "Center aisle", "status": "ONLINE", "fps": 23.7, "confidence": 0.91, "tracks": 23},
                {"id": "cam-03", "name": "Checkout", "status": "DEGRADED", "fps": 12.2, "confidence": 0.68, "tracks": 11, "note": "Occlusion above threshold"},
                {"id": "cam-04", "name": "Home care", "status": "ONLINE", "fps": 24.1, "confidence": 0.93, "tracks": 3},
            ]})
        if path.startswith("/api/"):
            return self._send({"error": "Route not found", "path": path}, HTTPStatus.NOT_FOUND)
        return self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        # Real MP4 upload path. The UI may use the deterministic demo, but the
        # API can also process a local file with the offline OpenCV fallback.
        content_type = self.headers.get("Content-Type", "")
        if path == "/api/videos/process" and content_type.lower().startswith("multipart/form-data"):
            try:
                size = int(self.headers.get("Content-Length", 0))
                if size > 500 * 1024 * 1024:
                    return self._send({"error": "Upload exceeds the 500 MB limit."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                match = re.search(r"boundary=([^;]+)", content_type, re.I)
                if not match:
                    return self._send({"error": "Multipart boundary is missing."}, HTTPStatus.BAD_REQUEST)
                boundary = ("--" + match.group(1).strip().strip('"')).encode()
                body = self.rfile.read(size)
                file_bytes = None
                filename = ""
                for part in body.split(boundary):
                    if b"name=\"file\"" not in part or b"\r\n\r\n" not in part:
                        continue
                    header, payload = part.split(b"\r\n\r\n", 1)
                    header_text = header.decode("utf-8", "ignore")
                    name_match = re.search(r"filename=\"([^\"]*)\"", header_text, re.I)
                    filename = name_match.group(1) if name_match else ""
                    file_bytes = payload.rsplit(b"\r\n", 1)[0]
                    break
                if file_bytes is None or not re.search(r"\.mp4$", filename, re.I):
                    return self._send({"error": "Attach an MP4 in the file field."}, HTTPStatus.UNPROCESSABLE_ENTITY)
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    tmp.write(file_bytes)
                    temp_path = tmp.name
                try:
                    result = process_video(temp_path, max_frames=720)
                finally:
                    Path(temp_path).unlink(missing_ok=True)
                return self._send({"job_id": "job_cv_local_001", "status": result["status"], "pipeline": result["stages"], "analytics": {"tracks": len({x["track_id"] for x in result["observations"]}), "observations": len(result["observations"])}, "backend": result["backend"], "privacy": result["privacy"]}, HTTPStatus.ACCEPTED)
            except (KeyError, ValueError, CVUnavailable, OSError) as exc:
                return self._send({"error": str(exc), "fallback": "deterministic_demo_dataset"}, HTTPStatus.UNPROCESSABLE_ENTITY)
        try:
            payload = self._json_body()
        except (ValueError, json.JSONDecodeError) as exc:
            return self._send({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/calibration/solve":
            try:
                result = calibrate(payload.get("source_points", []), payload.get("floor_points", []), max_error_m=float(payload.get("max_error_m", .35)))
                return self._send({"calibration": result.__dict__, "data_class": "MEASURED"})
            except (TypeError, ValueError) as exc:
                return self._send({"error": str(exc)}, HTTPStatus.UNPROCESSABLE_ENTITY)
        if path == "/api/videos/process":
            filename = str(payload.get("filename", "demo.mp4"))
            if not re.search(r"\.mp4$", filename, re.I):
                return self._send({"error": "Only MP4 files are supported in demo mode."}, HTTPStatus.UNPROCESSABLE_ENTITY)
            return self._send({"job_id": "job_demo_001", "status": "queued", "fallback": "deterministic_demo_dataset"}, HTTPStatus.ACCEPTED)
        if path == "/api/actions":
            action = OperationalAction(tenant_id="demo-tenant", store_id=str(payload.get("store_id", "store-flagship")), title=str(payload.get("title", "Untitled action")), problem=str(payload.get("problem", "")), intervention=str(payload.get("intervention", "")), owner=str(payload.get("owner", "Store operator")), due_date=str(payload.get("due_date", "2026-10-20")), success_metric=str(payload.get("success_metric", "congestion")), hypothesis=str(payload.get("hypothesis", "")), source_recommendation_id=payload.get("source_recommendation_id"))
            return self._send(ACTIONS.create(action), HTTPStatus.CREATED)
        if path.startswith("/api/actions/") and path.endswith("/complete"):
            action_id = path.split("/")[3]
            try:
                return self._send(ACTIONS.complete(action_id))
            except KeyError:
                return self._send({"error": "Action not found"}, HTTPStatus.NOT_FOUND)
        if path.startswith("/api/actions/") and path.endswith("/measure"):
            action_id = path.split("/")[3]
            try:
                return self._send(ACTIONS.measure(action_id, payload.get("baseline", []), payload.get("after", []), minimum_sample_per_arm=int(payload.get("minimum_sample_per_arm", 30))))
            except (KeyError, ValueError) as exc:
                return self._send({"error": str(exc)}, HTTPStatus.UNPROCESSABLE_ENTITY)
        if path == "/api/experiments/preregister":
            spec = ExperimentSpec(str(payload.get("name", "Promo display relocation")), str(payload.get("hypothesis", "")), str(payload.get("primary_metric", "congestion")), str(payload.get("direction", "decrease")), int(payload.get("minimum_sample_per_arm", 30)), tuple(payload.get("guardrails", [])))
            return self._send(preregister(spec))
        if path == "/api/experiments/analyze":
            try:
                spec = ExperimentSpec(str(payload.get("name", "Promo display relocation")), str(payload.get("hypothesis", "")), str(payload.get("primary_metric", "congestion")), str(payload.get("direction", "decrease")), int(payload.get("minimum_sample_per_arm", 30)), tuple(payload.get("guardrails", [])))
                return self._send(analyze_continuous(spec, payload.get("baseline", []), payload.get("variant", [])))
            except (TypeError, ValueError) as exc:
                return self._send({"error": str(exc)}, HTTPStatus.UNPROCESSABLE_ENTITY)
        if path == "/api/simulation/run":
            result = simulate_layout(payload.get("baseline", {}), display_moved=bool(payload.get("display_moved")), aisle_widened=bool(payload.get("aisle_widened")))
            return self._send({"result": result, "data_class": "SIMULATED"})
        if path == "/api/ai/query":
            question = str(payload.get("question", "")).strip()[:300]
            q = question.lower()
            if "stuck" in q or "bottleneck" in q:
                answer = "Prioritize Aisle 4 intersection. Density is +184% versus baseline, speed is down 43%, and dwell is up 67% across 327 affected visitors. The evidence supports a path-conflict hypothesis near the promotional display."
                evidence = ["Density +184%", "Speed -43%", "Dwell +67%", "327 affected visitors"]
            elif "dead" in q or "underperform" in q:
                answer = "Zone F · Home care is the clearest underutilized opportunity: traffic is 7.8% versus a 34.2% store average, and dwell is 18 seconds versus 71 seconds. Test a visibility-led display intervention before changing the assortment."
                evidence = ["Traffic 7.8% vs 34.2% average", "Dwell 18s vs 71s average", "Opportunity 91/100"]
            elif "intervention" in q or "test" in q:
                answer = "Test moving the Aisle 4 promotional display 1 metre away from the intersection. The simulator estimates congestion falling from 42% to 32%, but this is a hypothesis to validate with an after-period experiment, not an observed result."
                evidence = ["Simulated congestion -23.8%", "Current bottleneck score 82/100", "Confidence: medium"]
            else:
                answer = "FlowSense sees 1,248 visitors in this snapshot. Fresh produce has the strongest section health at 91/100, while the most urgent friction is the Aisle 4 intersection. Ask about bottlenecks, dead zones, or an intervention to inspect the evidence."
                evidence = ["1,248 visitors", "Fresh produce health 91/100", "Aisle 4 score 82/100"]
            return self._send({"answer": answer, "evidence": evidence, "confidence": "MEDIUM", "data_class": "GROUNDED ANALYTICS"})
        if path.startswith("/api/"):
            return self._send({"error": "Route not found", "path": path}, HTTPStatus.NOT_FOUND)
        return self._send({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _serve_static(self, path):
        relative = path.lstrip("/") or "index.html"
        target = (PUBLIC / relative).resolve()
        if PUBLIC not in target.parents and target != PUBLIC:
            return self._send("Forbidden", HTTPStatus.FORBIDDEN, "text/plain")
        if not target.exists() or not target.is_file():
            target = PUBLIC / "index.html"
        body = target.read_bytes()
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        return self._send(body, content_type=mime)


def main():
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"FlowSense API + dashboard listening on 0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
