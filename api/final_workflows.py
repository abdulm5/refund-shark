from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError

from shark.bundle import build_bundle
from shark.challenges import available, challenge
from shark.portfolio import PortfolioReplay
from shark.scan import RecoveryScan, ReviewFacts, AcceptProposal


class ScanInput(BaseModel):
    replay: bool = False


class ChallengeInput(BaseModel):
    action: Literal["missing_ticket", "late_ticket", "maintenance", "deadline", "duplicate", "missing_logs", "citation"]


def routes(store, pipeline, sample_text):
    router = APIRouter(prefix="/api")
    scan, portfolio = RecoveryScan(store, pipeline), PortfolioReplay(store, sample_text)

    def checked(fn, *args):
        try:
            return fn(*args)
        except (ValueError, OSError) as error:
            message = error.errors(include_input=False)[0]["msg"] if isinstance(error, ValidationError) else str(error)
            raise HTTPException(422, message) from error

    def context(scope):
        if scope == "workspace":
            return store, pipeline, scan
        value = portfolio.context()
        if value is None:
            raise HTTPException(409, "Start the isolated portfolio replay first.")
        return value

    @router.get("/recovery/scan")
    def scan_state():
        return checked(scan.state)

    @router.post("/recovery/scan")
    def start_scan(body: ScanInput):
        return checked(scan.run, True, body.replay)

    @router.get("/recovery/portfolio")
    def replay_state():
        return checked(portfolio.state)

    @router.post("/recovery/portfolio/reset")
    def reset_replay():
        return checked(portfolio.reset)

    @router.post("/recovery/portfolio/start")
    def start_replay():
        return checked(portfolio.start)

    @router.post("/recovery/portfolio/arrive")
    def evidence_arrival():
        return checked(portfolio.arrive)

    @router.post("/recovery/{scope}/proposals/{id}/review")
    def review(scope: Literal["workspace", "portfolio"], id: str, body: ReviewFacts):
        return checked(context(scope)[2].review, id, body)

    @router.post("/recovery/{scope}/proposals/{id}/accept")
    def accept(scope: Literal["workspace", "portfolio"], id: str, body: AcceptProposal):
        return checked(context(scope)[2].accept, id, body)

    @router.get("/recovery/portfolio/cases/{id}")
    def replay_case(id: str):
        p = context("portfolio")[1]
        if id not in p.scenarios:
            raise HTTPException(404, "Case not found in this isolated replay.")
        return p.detail(id)

    def scenario(scope, id):
        target_store, target_pipeline, _ = context(scope)
        s = target_pipeline.scenarios.get(id)
        if s is None:
            raise HTTPException(404, "Case not found.")
        return target_store, s

    @router.get("/recovery/{scope}/cases/{id}/challenges")
    def options(scope: Literal["workspace", "portfolio"], id: str):
        return {"challenges": checked(available, scenario(scope, id)[1])}

    @router.post("/recovery/{scope}/cases/{id}/challenges")
    def run_challenge(scope: Literal["workspace", "portfolio"], id: str, body: ChallengeInput):
        return checked(challenge, scenario(scope, id)[1], body.action)

    @router.get("/recovery/{scope}/cases/{id}/bundle")
    def bundle(scope: Literal["workspace", "portfolio"], id: str):
        target, s = scenario(scope, id)
        raw = checked(build_bundle, target, s, target.get_claim(id))
        return Response(raw, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="refund-shark-evidence.zip"'})

    return router
